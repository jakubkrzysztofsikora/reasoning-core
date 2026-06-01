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
from _plan_paths import (  # type: ignore  # noqa: E402
    distinct_file_paths as _distinct_file_paths,
    extract_files_with_loc as _shared_extract_files_with_loc,
)

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
        from src.hooks.adapters import claude as _claude_adapter  # type: ignore
        env = _claude_adapter.parse_stdin("PreToolUse")
        if env.tool_name is None and not env.raw:
            return None
        return dict(env.raw)
    except Exception:  # noqa: BLE001
        pass
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

# File-line regex moved to _plan_paths._FILE_LINE_RE (iter-3 single source of truth).

# Match section headers that introduce a file list, e.g. "### Files to create".
_FILE_SECTION_RE = re.compile(
    r"^#{1,6}\s*(?:files?\s+(?:to\s+)?(?:create|modify|change)|files_to_create|files_to_modify)",
    re.IGNORECASE | re.MULTILINE,
)

# Phase / step headings.
_PHASE_HEAD_RE = re.compile(r"^#{1,6}\s+(?:Phase|Step)\b", re.IGNORECASE | re.MULTILINE)


def _extract_files_with_loc(content: str) -> List[Tuple[str, int]]:
    """Find file_path + estimated LOC pairs in the plan body.

    Iter-3 refactor: delegates to ``_plan_paths.extract_files_with_loc``
    (single source of truth shared with the plan-grounding gate).
    Behavior preserved byte-for-byte; tests live at ``test_plan_paths.py``.
    """
    return _shared_extract_files_with_loc(content)


def _count_distinct_file_paths(content: str) -> int:
    """Iter-3 refactor: delegates to ``_plan_paths.distinct_file_paths``."""
    return len(_distinct_file_paths(content))


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


def _check_ood(content: str) -> List[Dict[str, Any]]:
    """P4 OOD plan detector. Advisory only — never blocks; flags for human."""
    if os.environ.get("RC_PLAN_QUALITY") != "1":
        return []
    try:
        from pathlib import Path as _Path
        hooks_dir = _Path(__file__).resolve().parent
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))
        import _ood_detector  # type: ignore
        repo_root = _Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
        if _ood_detector.is_ood(content, repo_root):
            return [{
                "rule_id": "ood_plan",
                "severity": "warn",
                "message": "Plan is far from the manifold of approved plans; recommended for human review.",
            }]
    except Exception:  # noqa: BLE001
        pass
    return []


def _check_specificity(content: str) -> List[Dict[str, Any]]:
    """P2 plan-quality: composite gate score over heuristic specificity signals.

    Honored under RC_PLAN_QUALITY=1. Magic comments # rc:skip / # rc:skip-quality
    bypass this check (operator-authored opt-out for legitimately terse plans).
    """
    if os.environ.get("RC_PLAN_QUALITY") != "1":
        return []
    try:
        import _magic_comments  # type: ignore
        directive = _magic_comments.parse(content)
        if _magic_comments.bypasses(directive, "quality"):
            return []
    except ImportError:
        pass
    try:
        import _plan_quality as pq  # type: ignore
    except ImportError:
        return []
    # Length cap: composite_gate_score scans full text via regex. 256KB is a
    # generous spec-doc ceiling; truncate to avoid worst-case backtracking.
    if len(content) > 256_000:
        content = content[:256_000]
    res = pq.composite_gate_score(content)
    severity = "block" if res.decision == "reject" else "warn" if res.decision == "warn" else None
    if severity is None:
        return []
    return [{
        "rule_id": "plan_specificity",
        "severity": severity,
        "cgs": res.cgs,
        "ard": res.ard,
        "nrd": res.nrd,
        "gpas": res.gpas,
        "slr": res.slr,
        "message": f"Plan specificity below threshold (CGS={res.cgs:.2f}, decision={res.decision}); add file paths, named risks, drop generic checklist phrases.",
    }]


_FRAMEWORK_PIVOT_HINTS = {
    "csharp": (re.compile(r"\b(?:pip install|requirements\.txt|pytest|import unittest|venv|virtualenv)\b"), "python"),
    "python": (re.compile(r"\b(?:dotnet (?:add|new|build)|nuget|<PackageReference)\b"), "csharp"),
    "javascript": (re.compile(r"\b(?:pip install|requirements\.txt|venv|virtualenv|gem install)\b"), "python_or_ruby"),
}


def _check_framework_pivot(content: str) -> List[Dict[str, Any]]:
    """P3 Invariant 5: framework pivot in plan markdown.

    If a plan declares technology specific to a different language family
    than the session manifest's declared language, surface a block warning.
    Only fires under RC_LANG_LOCK=1 + a manifest exists.

    Override: # rc:skip-framework or # rc:skip-lang on first 20 lines of
    the plan markdown.
    """
    if os.environ.get("RC_LANG_LOCK") != "1":
        return []
    try:
        from pathlib import Path as _Path
        hooks_dir = _Path(__file__).resolve().parent
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))
        import _magic_comments  # type: ignore
        directive = _magic_comments.parse(content)
        if _magic_comments.bypasses(directive, "framework") or _magic_comments.bypasses(directive, "lang"):
            return []
        import _session_manifest  # type: ignore
        cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        task_spec = os.environ.get("RC_TASK_SPEC") or ""
        key = _session_manifest.manifest_key(cwd, task_spec)
        mani = _session_manifest.load(key)
        if not mani:
            return []
        declared = mani.get("declared_language")
        if not declared or declared not in _FRAMEWORK_PIVOT_HINTS:
            return []
        pattern, foreign = _FRAMEWORK_PIVOT_HINTS[declared]
        matches = pattern.findall(content)
        if not matches:
            return []
        return [{
            "rule_id": "framework_pivot_in_plan",
            "severity": "block",
            "declared_language": declared,
            "foreign_indicator": foreign,
            "matched": matches[:5],
            "message": f"Plan references {foreign}-family tooling but session declared {declared}.",
        }]
    except Exception:  # noqa: BLE001
        return []


def _gather_warnings(content: str, project_dir: str) -> List[Dict[str, Any]]:
    warnings: List[Dict[str, Any]] = []
    warnings.extend(_check_per_file_loc(content))
    warnings.extend(_check_phase_file_ratio(content))
    warnings.extend(_check_boundary_prose(content))
    warnings.extend(_check_novelty(content, project_dir))
    warnings.extend(_check_specificity(content))
    warnings.extend(_check_framework_pivot(content))
    warnings.extend(_check_ood(content))
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
    # P3-fix: honor RC_SHADOW_MODE — auditor-flagged that pre_plan_guard
    # ignored shadow flag entirely, breaking §P4 success criterion.
    try:
        from pathlib import Path as _P
        hooks_dir = _P(__file__).resolve().parent
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))
        import _shadow_mode  # type: ignore
        shadow_active = _shadow_mode.is_active()
    except Exception:  # noqa: BLE001
        shadow_active = os.environ.get("RC_SHADOW_MODE") == "1"
    will_block = block_env and has_warn and not shadow_active
    decision = "shadow_blocked" if (block_env and has_warn and shadow_active) else (
        "blocked" if will_block else ("degraded" if warnings else "allowed")
    )

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
        gate_id="plan_grounding",
    ))

    if will_block:
        _exit(2, stderr_lines=lines)
    _exit(0, stderr_lines=lines if warnings else None)


if __name__ == "__main__":
    main()
