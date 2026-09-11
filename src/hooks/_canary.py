#!/usr/bin/env python3
"""Synthetic evidence canary for `rc doctor`.

Executes the installed post-edit hook against an isolated temp audit root,
verifies the resulting receipt schema and correlation fields, then deletes the
whole artifact. Canary paths use the ``rc-doctor-canary-`` prefix so
:func:`src.hooks._session_correlator._is_synthetic_fixture` excludes them from
product evidence even if a copy leaks.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

_CANARY_PREFIX = "rc-doctor-canary-"

_REQUIRED_FIELDS = (
    "decision_id",
    "session_id",
    "project_dir",
    "verification_kind",
    "verification_status",
    "deterministic",
    "correlation_schema_version",
)


def _load_canary_events(events_root: Path, session_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(events_root.glob(f"*/{session_id}.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def run_evidence_canary(*, timeout: float = 20.0) -> tuple[bool, dict[str, Any]]:
    """Run the synthetic canary. Returns (ok, report) and always cleans up."""
    report: dict[str, Any] = {
        "synthetic": True,
        "receipts": 0,
        "kinds": [],
        "correlation_ok": False,
        "missing_fields": [],
        "cleaned_up": False,
        "temp_dir": None,
        "error": None,
    }
    base = Path(tempfile.mkdtemp(prefix=_CANARY_PREFIX))
    report["temp_dir"] = str(base)
    try:
        sample = base / "canary_sample.py"
        sample.write_text("value = 1\n", encoding="utf-8")
        session_id = _CANARY_PREFIX + uuid.uuid4().hex[:12]
        hook = Path(__file__).resolve().parent / "post_edit_check.py"

        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "RC_AUDIT_ROOT": str(base / "events"),
            "RC_SESSION_ID": session_id,
        }
        payload = {
            "tool_name": "Edit",
            "cwd": str(base),
            "session_id": session_id,
            "tool_input": {"file_path": str(sample)},
        }
        result = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(base),
            env=env,
            timeout=timeout,
            check=False,
        )

        receipts = [
            event for event in _load_canary_events(base / "events", session_id)
            if event.get("event_type") == "verification_recorded"
        ]
        report["receipts"] = len(receipts)
        report["kinds"] = sorted({
            str(event.get("verification_kind")) for event in receipts
        })
        report["missing_fields"] = sorted({
            key
            for receipt in receipts
            for key in _REQUIRED_FIELDS
            if receipt.get(key) in (None, "")
        })
        expected_project = str(base.resolve())
        report["correlation_ok"] = bool(receipts) and not report["missing_fields"] and all(
            event.get("session_id") == session_id
            and str(event.get("project_dir")) == expected_project
            and event.get("association_type") == "session_level"
            for event in receipts
        )
        ok = bool(receipts) and report["correlation_ok"] and result.returncode == 0
        if not ok:
            report["error"] = (
                result.stderr or result.stdout or "canary produced no valid receipt"
            )[:512]
        return ok, report
    except Exception as exc:  # noqa: BLE001 - doctor must report, not raise
        report["error"] = f"{type(exc).__name__}: {exc}"
        return False, report
    finally:
        shutil.rmtree(base, ignore_errors=True)
        report["cleaned_up"] = not base.exists()


__all__ = ["run_evidence_canary"]
