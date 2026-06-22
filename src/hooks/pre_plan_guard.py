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


def _loc_budget_block() -> int:
    """Block-tier LOC budget. 2026-06-02 research §6 raised 800→1200
    (iter-3 empirics showed 800 was below p75 file size in production
    work). Override via RC_PLAN_LOC_BLOCK per repo as needed.
    """
    try:
        return int(os.environ.get("RC_PLAN_LOC_BLOCK", "1200"))
    except ValueError:
        return 1200


# Back-compat constant for tests and reads (mutable lookup goes through the
# function above so env-override still wins per invocation).
_LOC_BUDGET_BLOCK = 1200  # severity=block when exceeded under RC_PLAN_BLOCK=1

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
    block_budget = _loc_budget_block()
    for path, loc in _extract_files_with_loc(content):
        budget = _LOC_BUDGET_TEST if _is_test_file(path) else _LOC_BUDGET_DEFAULT
        if loc > block_budget:
            warnings.append({
                "rule_id": "per_file_loc_block",
                "severity": "block",
                "file_path": path,
                "loc": loc,
                "block_budget": block_budget,
                "message": (
                    f"Estimated LOC {loc} exceeds hard block budget {block_budget} "
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


import contextlib  # noqa: E402


@contextlib.contextmanager
def _muzzle_stderr():
    """Redirect the C-level stderr fd to /dev/null for the duration.

    The novelty path imports ``transformers`` which prints model-load
    warnings (e.g. Mamba "fast path is not available, falling back to the
    sequential implementation") straight to fd 2. In the harness build this
    hook runs under, non-empty stderr on an exit-0 PreToolUse hook is surfaced
    to the agent as a "PreToolUse:Write hook error" — so an advisory
    (block=False) reads as a tool failure and agents try to work around it.
    (Per the documented Claude Code contract the *decision* keys on exit code,
    not stderr; but observed harness behavior surfaces exit-0 stderr, which is
    what this muzzle defends against. Muzzling at the fd level — not just
    ``logging`` / ``warnings`` — is the only thing that also catches
    C-extension prints.)

    Disable with RC_PLAN_NOVELTY_MUZZLE=0 when debugging the backbone itself.

    fd hygiene: ``saved_fd`` and ``devnull_fd`` are acquired and released so
    that a failure at any single os.* call cannot (a) leak an fd or (b) leave
    fd 2 pointing at /dev/null for the rest of the process — which would
    silently swallow the verdict banner this hook emits later.
    """
    if os.environ.get("RC_PLAN_NOVELTY_MUZZLE", "1") != "1":
        yield
        return
    sys.stderr.flush()
    saved_fd = os.dup(2)
    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        # Could not open /dev/null — run un-muzzled rather than leak saved_fd.
        os.close(saved_fd)
        yield
        return
    try:
        os.dup2(devnull_fd, 2)
        yield
    finally:
        # Restore fd 2 FIRST and in its own guard, so even if dup2 raises we
        # still attempt to close both fds rather than leaking them.
        try:
            sys.stderr.flush()
            os.dup2(saved_fd, 2)
        finally:
            os.close(devnull_fd)
            os.close(saved_fd)


def _embed_safe(text: str):
    """Return a torch tensor or None if backbone unavailable.

    Wrapped in ``_muzzle_stderr`` so library load warnings never leak onto
    the hook's stderr channel (which the harness reads as a tool error). Any
    failure — BackboneUnavailableError, import error, OOM, native abort caught
    as a Python exception — collapses to the same advisory: novelty scoring is
    skipped this run.
    """
    try:
        with _muzzle_stderr():
            # Local import — keep the hook fast on the non-novelty path.
            from src.ssm_backbone import embed  # type: ignore
            return embed(text), None
    except Exception:  # noqa: BLE001
        return None, "novelty_unavailable"


def _embed_cache_path() -> "Path":
    """Disk location for the content-addressed peer-embedding cache."""
    return (
        Path.home() / ".local" / "state" / "reasoning-core" / "plan_embed_cache.npz"
    )


# In-process memo so repeated peers within one invocation hit RAM, not disk.
_EMBED_MEMO: Dict[str, Any] = {}


def _embed_cached(text: str):
    """``_embed_safe`` with a content-addressed cache (key = sha1(text)).

    Motivation: ``_check_novelty`` embeds up to 8 peer plans on every plan
    Write, and ``ssm_backbone.embed`` runs a real SSM forward each time with
    no internal caching — so the same prior plans were re-embedded on every
    save. An individual plan's embedding never changes, so we key on the
    content hash rather than the recency set (the set rotates on every Write;
    the per-plan vector does not). Mirrors ``_ood_detector``'s disk cache but
    at per-text granularity.

    Returns the same ``(vec, err)`` tuple as ``_embed_safe``. Disable with
    RC_PLAN_NOVELTY_CACHE=0 (tests set this to avoid cross-case contamination
    from the persistent store).
    """
    if os.environ.get("RC_PLAN_NOVELTY_CACHE", "1") != "1":
        return _embed_safe(text)
    import hashlib

    key = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()
    if key in _EMBED_MEMO:
        return _EMBED_MEMO[key], None
    # Disk lookup.
    try:
        import numpy as np
        import torch

        cache_file = _embed_cache_path()
        if cache_file.exists():
            data = np.load(cache_file, allow_pickle=False)
            if key in data.files:
                vec = torch.from_numpy(data[key])
                _EMBED_MEMO[key] = vec
                return vec, None
    except Exception:  # noqa: BLE001
        pass  # corrupt/missing cache → fall through to a live embed
    vec, err = _embed_safe(text)
    if vec is None:
        return None, err
    _EMBED_MEMO[key] = vec
    _persist_embed(key, vec)
    return vec, None


def _persist_embed(key: str, vec: Any) -> None:
    """Merge one embedding into the on-disk cache, capped to bound growth."""
    _CACHE_CAP = 256
    try:
        import numpy as np

        cache_file = _embed_cache_path()
        existing: Dict[str, Any] = {}
        if cache_file.exists():
            try:
                data = np.load(cache_file, allow_pickle=False)
                existing = {k: data[k] for k in data.files}
            except Exception:  # noqa: BLE001
                existing = {}
        arr = vec.detach().cpu().numpy() if hasattr(vec, "detach") else np.asarray(vec)
        existing[key] = arr
        # Bound size: keep the most-recently-inserted CACHE_CAP entries.
        if len(existing) > _CACHE_CAP:
            for stale_key in list(existing.keys())[: len(existing) - _CACHE_CAP]:
                existing.pop(stale_key, None)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache_file, **existing)
    except Exception:  # noqa: BLE001
        pass  # caching is best-effort; never fail the gate over it


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


def _novelty_ratio_threshold() -> float:
    """Drift ratio cutoff: flag when the new plan sits this many times farther
    from the recent-plan centroid than recent plans typically do. Default 1.8
    (≈80% farther than the median peer). Override per-repo via
    RC_PLAN_NOVELTY_RATIO; raise it to tolerate broader scope, lower it to
    police drift more tightly.
    """
    try:
        return float(os.environ.get("RC_PLAN_NOVELTY_RATIO", "1.8"))
    except ValueError:
        return 1.8


def _novelty_min_spread_frac() -> float:
    """Floor for the novelty-drift denominator, as a fraction of the peers'
    own vector scale. Prevents a near-duplicate peer cluster (median spread
    → 0) from exploding the distance ratio and false-firing on a barely
    different plan. Default 0.05 (5% of typical embedding magnitude). Override
    via RC_PLAN_NOVELTY_MIN_SPREAD.
    """
    try:
        return float(os.environ.get("RC_PLAN_NOVELTY_MIN_SPREAD", "0.05"))
    except ValueError:
        return 0.05


def _check_novelty(content: str, project_dir: str) -> List[Dict[str, Any]]:
    """Adaptive novelty drift.

    Iter-4 recalibration (2026-06-21): the previous gate compared a RAW L2
    distance ``‖plan − mean(peers)‖`` against a FIXED ``3.0``. Raw L2 scales
    with the embedder's hidden-dim and vector magnitude, so 3.0 was
    meaningless for this backbone — audit history showed every firing landed
    at 13.7–18.1 (4–6× over), i.e. it fired on essentially every
    feature-introducing plan. A plan that introduces a feature SHOULD drift; a
    fixed absolute cutoff cannot tell "new feature" from "off-the-rails".

    The fix is self-calibrating and robust to high-dimensional distance
    concentration (which makes MAD-style spread estimates degenerate with only
    a handful of peers): we take the new plan's distance to the recent-plan
    centroid and divide by the MEDIAN peer-to-centroid distance. The result is
    a unit-free ratio — "how many times farther out than usual is this plan?"
    A plan flags only when it sits substantially beyond the repo's own recent
    rhythm, not merely "not identical to last week". The raw distance the old
    gate keyed on would give ratio ≈ 1.0 for every one of the 5 historical
    false-fires, so those are now silent.
    """
    # Embed the plan FIRST: if the backbone is unavailable we return here
    # without doing the up-to-8-file peer read that would otherwise be wasted.
    plan_vec, err = _embed_safe(content)
    if plan_vec is None:
        return [{
            "rule_id": "novelty_unavailable",
            "severity": "info",
            "file_path": None,
            "message": err or "Backbone unavailable; novelty scoring skipped.",
        }]
    others = _gather_recent_plans(project_dir, limit=8)
    if not others:
        return []
    try:
        import torch

        peers = []
        for o in others:
            # Peers use the content-addressed cache — the same prior plans are
            # re-embedded on every Write otherwise.
            v, _ = _embed_cached(o)
            if v is not None:
                peers.append(v)
        # Need ≥3 peers for a stable median baseline; below that, stay silent
        # rather than fire on a sample too small to define "typical".
        if len(peers) < 3:
            return []
        import math

        stack = torch.stack(peers)
        centroid = stack.mean(dim=0)
        peer_dists = torch.linalg.norm(stack - centroid, dim=1)
        plan_dist = float(torch.linalg.norm(plan_vec - centroid))
        # True median (averages the middle two for even N). torch.median()
        # returns the lower-middle element, which biases the denominator down
        # and inflates every ratio — use quantile(0.5) for the real median.
        median = float(peer_dists.quantile(0.5))
        # The denominator is the *typical* peer spread, but it must be FLOORED:
        # when recent plans are tight near-duplicates the median collapses
        # toward 0, and `plan_dist / median` explodes into the hundreds for a
        # plan that is barely different — re-creating the exact false-fire this
        # gate was recalibrated to eliminate (concentration of measure: with a
        # near-zero spread, every plan looks like an outlier). Floor the spread
        # at a small fraction of the peers' own vector scale so a degenerate
        # cluster can't manufacture a huge ratio. This also dissolves the old
        # special-case "<1e-6" branch into one continuous metric.
        peer_scale = float(stack.norm(dim=1).quantile(0.5))
        denom = max(median, _novelty_min_spread_frac() * peer_scale)
        # NaN/inf guard: a degenerate embedding can make plan_dist or denom
        # non-finite. ``NaN > cut`` is False, which would SILENTLY pass exactly
        # the off-the-rails plan this gate exists to catch — bail to "skipped".
        if not math.isfinite(plan_dist) or not math.isfinite(denom):
            return []
        if denom < 1e-9:
            # Both spread AND scale are ~0 — embeddings are degenerate (all
            # near the origin). No meaningful baseline; don't fire on noise.
            return []
        ratio = plan_dist / denom
        if not math.isfinite(ratio):
            return []
    except Exception:  # noqa: BLE001
        return []
    cut = _novelty_ratio_threshold()
    if ratio > cut:
        return [{
            "rule_id": "novelty_drift",
            "severity": "warn",
            "file_path": None,
            "drift": round(plan_dist, 3),
            "ratio": round(ratio, 2),
            "peer_median": round(median, 3),
            "denom": round(denom, 3),
            "peer_count": len(peers),
            "message": (
                f"Plan novelty drift: it sits {ratio:.1f}× farther from the recent "
                f"plan cluster than usual ({plan_dist:.1f} vs baseline {denom:.1f}; "
                f"threshold {cut:.1f}×, {len(peers)} peers). This is ADVISORY — the "
                f"write PROCEEDED. Confirm the broad scope is intentional, or split "
                f"the plan if it spans unrelated work. Tune via RC_PLAN_NOVELTY_RATIO."
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


def _format_decompose_recipe(blocking: List[Dict[str, Any]]) -> str:
    """Build an agent-actionable decomposition recipe (research 2026-06-02 §6).

    Returns an empty string when no recognised LOC-block rule fired — the
    caller still emits the generic block stderr in that case.
    """
    if os.environ.get("RC_PLAN_DECOMPOSE", "1") != "1":
        return ""
    block_budget = _loc_budget_block()
    loc_rows: List[Tuple[str, int]] = []
    for w in blocking:
        if w.get("rule_id") != "per_file_loc_block":
            continue
        fp = w.get("file_path") or "?"
        loc_val = w.get("loc")
        if not isinstance(loc_val, int):
            m = re.search(r"LOC (\d+)", w.get("message", "") or "")
            loc_val = int(m.group(1)) if m else 0
        loc_rows.append((fp, loc_val))
    if not loc_rows:
        return ""
    lines = ["[plan-guard] DECOMPOSITION REQUIRED:"]
    for fp, loc in loc_rows:
        phases = max(2, (loc // block_budget) + 1)
        lines.append(
            f"  - {fp}: ~{loc} LOC → split into {phases} sequential phases "
            f"of ≤ {block_budget} LOC each."
        )
    lines.append(
        "Rewrite PLAN.md (or use PLAN-phase-1.md / PLAN-phase-2.md). "
        "Each phase must be net-additive before introducing breaking changes; "
        "earlier phases land tests that later phases must keep green."
    )
    lines.append(
        "Override (operator only): RC_PLAN_LOC_BLOCK=N to raise the budget, "
        "or RC_PLAN_DECOMPOSE=0 to silence this hint."
    )
    return "\n".join(lines)


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

    # Build stderr report. The machine-readable JSON warning objects come
    # first; the human banner is appended LAST (after the block decision is
    # known) so it can state the real outcome — see below.
    lines: List[str] = []
    for w in warnings:
        lines.append(json.dumps(w, ensure_ascii=False, sort_keys=True))
    has_block = any(w.get("severity") == "block" for w in warnings)
    has_warn = any(w.get("severity") in ("warn", "block") for w in warnings)

    # 2026-06-02 §B: emit a decomposition recipe when LOC-block fires so the
    # agent has an actionable recovery path instead of just "BLOCK: too big".
    decompose_recipe = ""
    if has_block:
        blocking = [w for w in warnings if w.get("severity") == "block"]
        decompose_recipe = _format_decompose_recipe(blocking)
        if decompose_recipe:
            lines.append(decompose_recipe)

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

    # When the recipe fires alongside a block, tag the signal_source so
    # downstream tooling (rc reasoning-efficiency, audit triage) can
    # distinguish "blocked with recovery hint" from "blocked, agent stuck".
    plan_signal_source = (
        "plan_decompose_hint" if (will_block and decompose_recipe) else "plan_grounding"
    )
    plan_reason = "decomposition_required" if (will_block and decompose_recipe) else ""

    # Human-readable banner — appended last so it states the ACTUAL outcome.
    # Prior versions emitted a terse `block=False`, which agents misread as a
    # tool error and tried to "work around" (re-issuing the Write, shelling out
    # to `cat`, etc.). Be explicit: say whether the write proceeded, that
    # warnings are advisory unless block=True, and what each severity means.
    if warnings:
        warn_ct = sum(1 for w in warnings if w.get("severity") == "warn")
        block_ct = sum(1 for w in warnings if w.get("severity") == "block")
        info_ct = sum(1 for w in warnings if w.get("severity") == "info")
        rule_ids = ", ".join(sorted({str(w.get("rule_id")) for w in warnings}))
        if will_block:
            verdict = (
                "BLOCKED — the Write did NOT proceed. RC_PLAN_BLOCK=1 is set and "
                "a block-severity rule fired. Fix the plan per the rule message(s) "
                "above and retry; this is a real gate, not a tool error."
            )
        elif shadow_active and block_env and has_warn:
            verdict = (
                "ADVISORY (shadow mode) — the Write PROCEEDED. A block-severity "
                "rule fired but RC_SHADOW_MODE suppresses enforcement. No action "
                "required; logged for calibration."
            )
        else:
            verdict = (
                "ADVISORY — the Write PROCEEDED and the plan file was saved. "
                "These are non-blocking quality signals, NOT a tool failure and "
                "NOT something to work around. Read them, decide if the flagged "
                "scope is intentional, and continue. To enforce, set "
                "RC_PLAN_BLOCK=1."
            )
        lines.append(
            f"[hybrid-reasoner] plan-guard: {len(warnings)} signal(s) "
            f"[{block_ct} block / {warn_ct} warn / {info_ct} info] "
            f"rules=[{rule_ids}] file={file_path}"
        )
        lines.append(f"[hybrid-reasoner] plan-guard verdict: {verdict}")

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
        signal_source=plan_signal_source,
        reason=plan_reason,
        gate_id="plan_grounding",
    ))

    if will_block:
        _exit(2, stderr_lines=lines)
    _exit(0, stderr_lines=lines if warnings else None)


if __name__ == "__main__":
    main()
