#!/usr/bin/env python3
"""Claude Code PreToolUse hook for plan-document writes.

Wired by ``.claude/settings.json`` with matcher ``Write``. The hook itself
filters by path: it only scores writes to plan-shaped Markdown (under
``thoughts/shared/plans/``, or any file named ``PLAN.md`` /
``*.plan.md``). For non-plan writes it exits 0 immediately without scoring.

Heuristics emit warnings to stderr (one JSON object per warning + a trailing
human summary line). The hook is informational by default: ``exit 0`` unless
``RC_PLAN_BLOCK=1`` is set, in which case any warning escalates to ``exit 2``.

This is the "left-shift" gate from HARDENING.md residual gap #3 — score the
plan before Claude tries to land 1500 lines into a single file.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make ``src.audit_log`` importable when the hook runs as a standalone script.
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import audit_log  # type: ignore  # noqa: E402

TOOL_NAME = "Plan"

# Path filters: case-insensitive substring / suffix match. Caller filter
# upstream is the matcher in settings.json; this is the second stage.
_PLAN_PATH_PATTERNS = (
    re.compile(r"thoughts/shared/plans/.*\.md$", re.IGNORECASE),
    re.compile(r"(?:^|/)PLAN\.md$", re.IGNORECASE),
    re.compile(r"\.plan\.md$", re.IGNORECASE),
)

# Default per-file LOC budgets.
_LOC_BUDGET_DEFAULT = 400
_LOC_BUDGET_TEST = 200
_LOC_BUDGET_BLOCK = 800  # severity=block when exceeded under RC_PLAN_BLOCK=1

# Boundary-crossing prose. Word-boundary aware, case-insensitive.
_BOUNDARY_PROSE_PATTERNS = (
    re.compile(r"\b(?:across|spanning?|covers?\s+both|crossing)\b", re.IGNORECASE),
    re.compile(r"\bin\s+one\s+(?:test|file)\b", re.IGNORECASE),
    re.compile(r"\bsingle\s+(?:test|file)\s+that\b", re.IGNORECASE),
)


def _is_plan_path(path: str) -> bool:
    if not path:
        return False
    for pat in _PLAN_PATH_PATTERNS:
        if pat.search(path):
            return True
    return False


def _read_payload() -> Optional[Dict[str, Any]]:
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


# ---------------------------------------------------------------------------
# Heuristic helpers
# ---------------------------------------------------------------------------

# Match lines like "- src/foo.py — ~120 LOC" or "* `src/foo.py`: ~80 lines".
_FILE_LINE_RE = re.compile(
    r"""
    [\-\*\+]\s*                          # bullet
    `?(?P<path>[A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,8})`?
    .*?                                  # any text in between
    (?:~?\s*(?P<loc>\d{2,5})\s*(?:LOC|lines?|locs?))
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Match section headers that introduce a file list, e.g. "### Files to create".
_FILE_SECTION_RE = re.compile(
    r"^#{1,6}\s*(?:files?\s+(?:to\s+)?(?:create|modify|change)|files_to_create|files_to_modify)",
    re.IGNORECASE | re.MULTILINE,
)

# Phase / step headings.
_PHASE_HEAD_RE = re.compile(r"^#{1,6}\s+(?:Phase|Step)\b", re.IGNORECASE | re.MULTILINE)


def _extract_files_with_loc(content: str) -> List[Tuple[str, int]]:
    """Find file_path + estimated LOC pairs in the plan body."""
    out: List[Tuple[str, int]] = []
    for m in _FILE_LINE_RE.finditer(content):
        try:
            loc = int(m.group("loc"))
        except (TypeError, ValueError):
            continue
        path = m.group("path") or ""
        if path:
            out.append((path, loc))
    return out


def _count_distinct_file_paths(content: str) -> int:
    paths = {p for p, _ in _extract_files_with_loc(content)}
    # Also consider bare paths in the plan body (no LOC annotation).
    bare = re.findall(r"`([A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,8})`", content)
    paths.update(bare)
    return len(paths)


def _is_test_file(path: str) -> bool:
    base = os.path.basename(path).lower()
    return (
        "test_" in base
        or base.endswith("_test.py")
        or base.endswith(".spec.ts")
        or base.endswith(".spec.js")
        or base.endswith(".cy.ts")
        or base.endswith(".cy.js")
        or "/tests/" in path
        or "/__tests__/" in path
    )


def _check_per_file_loc(content: str) -> List[Dict[str, Any]]:
    warnings: List[Dict[str, Any]] = []
    for path, loc in _extract_files_with_loc(content):
        budget = _LOC_BUDGET_TEST if _is_test_file(path) else _LOC_BUDGET_DEFAULT
        if loc > _LOC_BUDGET_BLOCK:
            warnings.append({
                "rule_id": "per_file_loc_block",
                "severity": "block",
                "file_path": path,
                "message": (
                    f"Estimated LOC {loc} exceeds hard block budget {_LOC_BUDGET_BLOCK} "
                    f"for {path}; split this file in the plan."
                ),
            })
        elif loc > budget:
            warnings.append({
                "rule_id": "per_file_loc",
                "severity": "warn",
                "file_path": path,
                "message": (
                    f"Estimated LOC {loc} exceeds budget {budget} for {path}; "
                    "consider splitting."
                ),
            })
    return warnings


def _check_phase_file_ratio(content: str) -> List[Dict[str, Any]]:
    phases = len(_PHASE_HEAD_RE.findall(content))
    files = _count_distinct_file_paths(content)
    if phases > 0 and files > 0 and phases > files:
        return [{
            "rule_id": "phase_file_ratio",
            "severity": "warn",
            "file_path": None,
            "message": (
                f"Plan has {phases} phases but only {files} distinct files; "
                "phase-to-file ratio suggests too-broad / too-thin scope."
            ),
        }]
    return []


def _check_boundary_prose(content: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pat in _BOUNDARY_PROSE_PATTERNS:
        m = pat.search(content)
        if m:
            out.append({
                "rule_id": "boundary_crossing_prose",
                "severity": "warn",
                "file_path": None,
                "match": m.group(0),
                "message": (
                    "Plan prose suggests boundary-crossing scope: "
                    f"matched {m.group(0)!r}."
                ),
            })
            break  # one warn per plan is enough
    return out


def _embed_safe(text: str):
    """Return a torch tensor or None if backbone unavailable."""
    try:
        # Local import — keep the hook fast on the non-novelty path.
        from src.ssm_backbone import BackboneUnavailableError, embed  # type: ignore
    except Exception:
        return None, "novelty_unavailable"
    try:
        return embed(text), None
    except BackboneUnavailableError:
        return None, "novelty_unavailable"
    except Exception:  # noqa: BLE001
        return None, "novelty_unavailable"


def _gather_recent_plans(project_dir: str, limit: int = 5) -> List[str]:
    """Return up to ``limit`` recent plan markdown contents from the project."""
    if not project_dir:
        return []
    plans_dir = Path(project_dir) / "thoughts" / "shared" / "plans"
    if not plans_dir.is_dir():
        return []
    try:
        files = sorted(
            plans_dir.glob("**/*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []
    out: List[str] = []
    for f in files[:limit]:
        try:
            out.append(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return out


def _check_novelty(content: str, project_dir: str) -> List[Dict[str, Any]]:
    others = _gather_recent_plans(project_dir, limit=5)
    plan_vec, err = _embed_safe(content)
    if plan_vec is None:
        return [{
            "rule_id": "novelty_unavailable",
            "severity": "info",
            "file_path": None,
            "message": err or "Backbone unavailable; novelty scoring skipped.",
        }]
    if not others:
        return []
    try:
        import torch

        peers = []
        for o in others:
            v, _ = _embed_safe(o)
            if v is not None:
                peers.append(v)
        if not peers:
            return []
        mean = torch.stack(peers).mean(dim=0)
        drift = float(torch.linalg.norm(plan_vec - mean))
    except Exception:  # noqa: BLE001
        return []
    if drift > 3.0:
        return [{
            "rule_id": "novelty_drift",
            "severity": "warn",
            "file_path": None,
            "drift": drift,
            "message": (
                f"Plan novelty drift {drift:.2f} > 3.0 vs recent {len(peers)} plans; "
                "verify scope is intentional."
            ),
        }]
    return []


def _gather_warnings(content: str, project_dir: str) -> List[Dict[str, Any]]:
    warnings: List[Dict[str, Any]] = []
    warnings.extend(_check_per_file_loc(content))
    warnings.extend(_check_phase_file_ratio(content))
    warnings.extend(_check_boundary_prose(content))
    warnings.extend(_check_novelty(content, project_dir))
    return warnings


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _exit(code: int, *, stderr_lines: Optional[List[str]] = None) -> None:
    if stderr_lines:
        sys.stderr.write("\n".join(stderr_lines) + "\n")
    sys.exit(code)


def main() -> None:
    started = time.time()
    payload = _read_payload()
    if payload is None:
        _exit(0)
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    if tool_name != "Write" or not isinstance(tool_input, dict):
        _exit(0)

    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not isinstance(file_path, str) or not _is_plan_path(file_path):
        _exit(0)

    content = tool_input.get("content")
    if not isinstance(content, str):
        content = ""

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    warnings = _gather_warnings(content, project_dir)

    # Build stderr report.
    lines: List[str] = []
    for w in warnings:
        lines.append(json.dumps(w, ensure_ascii=False, sort_keys=True))
    has_block = any(w.get("severity") == "block" for w in warnings)
    has_warn = any(w.get("severity") in ("warn", "block") for w in warnings)
    summary = (
        f"[hybrid-reasoner] plan-guard: {len(warnings)} warning(s); "
        f"block={has_block} file={file_path}"
    )
    if warnings:
        lines.append(summary)

    latency_ms = int((time.time() - started) * 1000)

    # Decide exit code first; audit always runs.
    block_env = os.environ.get("RC_PLAN_BLOCK", "0") == "1"
    will_block = block_env and has_warn
    decision = "blocked" if will_block else ("degraded" if warnings else "allowed")

    audit_log.append_event(audit_log.new_event(
        tool_name=TOOL_NAME,
        decision=decision,
        file_path=file_path,
        language="markdown",
        latency_ms=latency_ms,
        warnings=warnings,
        rc_plan_block=block_env,
        before_bytes=0,
        after_bytes=len(content.encode("utf-8", errors="replace")),
    ))

    if will_block:
        _exit(2, stderr_lines=lines)
    _exit(0, stderr_lines=lines if warnings else None)


if __name__ == "__main__":
    main()
