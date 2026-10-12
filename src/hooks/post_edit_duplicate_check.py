"""PostToolUse duplicate check: after an edit, look for functions in the edited
file that the repo already has.

Opt-in via ``RC_DUP_ORACLE=1`` (default off). Runs after an Edit/Write/MultiEdit,
reads the edited file as it now is, and hands it to
``duplicate_detection.check.check_edit``, which:

  - records every duplicate in the per-repo findings ledger, so nothing
    evaporates -- the Stop hook (``stop_duplicate_check.py``) reviews them;
  - treats a match in another file of the working diff as a likely move;
  - returns an inline message only for a duplicate inside the edited file.

**Never blocks** -- always exits 0, fails open on any error with a stderr note.
Blocking happens at session end, in the Stop hook. See ``docs/dup-oracle.md``.

The repo index is persisted to disk per repo (``load_or_build_dup_index``), so
a fresh hook process re-embeds only changed files. ``RC_DUP_ORACLE_CACHE=0``
disables the disk cache.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_IMPORT_ERROR: Optional[str] = None
try:  # a hook must never crash at import, even when it's disabled
    from src.duplicate_detection import findings as ledger
    from src.duplicate_detection.check import check_edit
    from src.duplicate_detection.repo_index import DupOracleIndex, load_or_build_dup_index
    from src.grammars import UnsupportedLanguageError
except Exception as import_error:  # noqa: BLE001 - hook must never crash
    # Capture WHY so that, when the feature is enabled but a dependency is
    # missing, main() says so instead of silently checking nothing.
    _IMPORT_ERROR = repr(import_error)
    ledger = check_edit = load_or_build_dup_index = None  # type: ignore
    DupOracleIndex = UnsupportedLanguageError = None  # type: ignore

_INDEX_CACHE: Dict[str, Any] = {}


def _read_payload() -> Dict[str, Any]:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError) as err:
        # Unreadable or non-JSON stdin: nothing to check, but say so.
        _warn_stderr(f"could not read the hook payload ({err!r})")
        return {}


def _emit_additional_context(text: str) -> None:
    json.dump(
        {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}},
        sys.stdout,
    )


def _get_index(repo_root: str):
    index = _INDEX_CACHE.get(repo_root)
    if index is None:
        index = load_or_build_dup_index(repo_root)
        _INDEX_CACHE[repo_root] = index
    return index


def _embedder():
    # Lazy, monkey patchable seam: the real embedder (torch) is imported only
    # here, so offline tests substitute a stub.
    from src.duplicate_detection.embed import embed_function

    return embed_function


def _warn_stderr(msg: str) -> None:
    """Best-effort one-liner to stderr; never raises (fail-open hook)."""
    try:
        print(f"dup-oracle: {msg}", file=sys.stderr)
    except (OSError, ValueError):  # silent-ok: stderr itself is unavailable; nowhere left to report
        pass


def _audit(payload: Dict[str, Any], decision: str, started: float, **fields: Any) -> None:
    """One audit event per run, so "never found anything" is distinguishable
    from "failed on every edit". append_event is best-effort and never raises."""
    try:
        from src.hooks import audit_log

        audit_log.append_correlated_event(
            event_type="dup_check",
            tool_name=payload.get("tool_name") or "PostToolUse",
            decision=decision,
            payload=payload,
            file_path=(payload.get("tool_input") or {}).get("file_path"),
            latency_ms=int((time.monotonic() - started) * 1000),
            **fields,
        )
    except Exception as exc:  # noqa: BLE001
        _warn_stderr(f"audit event not written: {exc!r}")


def main() -> None:
    if os.environ.get("RC_DUP_ORACLE") != "1":
        sys.exit(0)  # feature off -> silent, do nothing
    started = time.monotonic()
    payload: Dict[str, Any] = {}
    if check_edit is None or ledger is None or load_or_build_dup_index is None:
        msg = f"enabled (RC_DUP_ORACLE=1) but inactive — import failed: {_IMPORT_ERROR}"
        _warn_stderr(msg)
        _audit(payload, "fail-open", started, error=msg)
        sys.exit(0)
    try:
        payload = _read_payload()
        file_path = (payload.get("tool_input") or {}).get("file_path") or ""
        if not file_path or not os.path.isfile(file_path):
            _audit(payload, "skipped", started, reason="no file")  # or the edit deleted it
            sys.exit(0)
        repo_root = payload.get("cwd") or os.getcwd()
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        text = check_edit(
            file_path, source, _get_index(repo_root),
            embed_fn=_embedder(),
            repo_root=repo_root,
            changed=ledger.changed_files(repo_root),
            ledger_file=ledger.ledger_path(repo_root),
        )
        if text:
            _emit_additional_context(text)
        _audit(payload, "found" if text else "clean", started)
    except UnsupportedLanguageError:
        _audit(payload, "skipped", started, reason="unsupported language")
    except Exception as exc:  # noqa: BLE001
        # Fail open -- this hook must never block an edit -- but leave a trace.
        _warn_stderr(f"duplicate check skipped (fail-open): {exc!r}")
        _audit(payload, "fail-open", started, error=repr(exc))
    sys.exit(0)


if __name__ == "__main__":
    main()
