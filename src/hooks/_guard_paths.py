"""Guard-path discovery for the pre_edit_guard layer-1 lock.

Extracted from `pre_edit_guard.py` (Phase 0 commit 1). Owns the substring
allowlist of files an agent must NOT modify without the operator's explicit
`RC_ALLOW_GUARD_EDIT=1` override.

Design:
  - Hook scripts and sidecar boot scripts are auto-discovered by glob from
    the project root so a new hook (e.g. `_new_layer.py`) is locked the
    moment it lands without requiring an edit to this file.
  - Higher-risk Python sources (sidecar core, supervisor, generative client,
    operator CLI) are listed explicitly — they live outside `src/hooks/`
    and a glob over `src/*.py` would over-capture.
  - Settings JSON files are listed explicitly.

Override surface:
  - `RC_ALLOW_GUARD_EDIT=1` — operator opt-in to allow direct edits during
    a single Claude session. Read at hook call time.
  - `RC_GUARD_PATHS_OVERRIDE` (comma-separated) — test-only escape hatch
    that REPLACES the discovered tuple. Used by the suite.
  - `RC_GUARD_PATHS_EXTRA` (comma-separated) — appends additional entries
    on top of the discovered tuple. Used by adopters extending coverage
    without a code edit.

The tuple `GUARDED_PATHS` is built once at import time. Tests that need
fresh discovery should call `discover()` directly with a project root.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Iterable, Tuple

# Files outside `src/hooks/` that must remain locked. Globs cannot safely
# capture these (a `src/*.py` glob would over-include).
_EXPLICIT_ALLOWLIST: Tuple[str, ...] = (
    "/.claude/settings.json",
    "/.claude/settings.local.json",
    # Sidecar + supervisor + generative client
    "/src/sidecar_supervisor.py",
    "/src/_supervisor_env.py",
    "/src/_supervisor_broker.py",
    "/src/_supervisor_recalibrate.py",
    "/src/gen_client.py",
    "/src/calibration.py",
    # Sidecar core
    "/src/s2_core.py",
    "/src/grammars.py",
    "/src/ssm_backbone.py",
    "/src/mcp_reasoner.py",
    # Operator CLI
    "/src/rc_cli.py",
)


def _project_root() -> Path:
    """Best-effort project-root resolver.

    Prefer `CLAUDE_PROJECT_DIR`; otherwise fall back to the directory two
    levels above this file (`src/hooks/_guard_paths.py` -> repo root).
    """
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parent.parent.parent


def _glob_hook_scripts(root: Path) -> Iterable[str]:
    """Yield substring entries for every hook script in `src/hooks/*.py`.

    Filters out `__init__.py` and any file matching `_*test*.py` so the
    glob does not over-capture test scaffolding that may legitimately
    coexist with hooks.
    """
    hook_dir = root / "src" / "hooks"
    for path in sorted(glob.glob(str(hook_dir / "*.py"))):
        name = os.path.basename(path)
        if name == "__init__.py":
            continue
        # Filter `_*test*.py` to avoid catching test scaffolding.
        if name.startswith("_") and "test" in name:
            continue
        yield f"/src/hooks/{name}"


def _glob_sidecar_scripts(root: Path) -> Iterable[str]:
    scripts_dir = root / "scripts"
    for path in sorted(glob.glob(str(scripts_dir / "start-*sidecar.sh"))):
        name = os.path.basename(path)
        yield f"/scripts/{name}"


def discover(project_root: Path | None = None) -> Tuple[str, ...]:
    """Return the substring allowlist for guarded paths.

    Combines the auto-discovered hook + sidecar globs with the explicit
    allowlist. Result order is: settings -> hooks -> sidecar boot ->
    explicit Python sources. De-duplicated, preserving first occurrence.
    """
    root = project_root or _project_root()
    settings = ("/.claude/settings.json", "/.claude/settings.local.json")
    seen: set = set()
    out: list = []

    def _push(entry: str) -> None:
        if entry not in seen:
            seen.add(entry)
            out.append(entry)

    for entry in settings:
        _push(entry)
    for entry in _glob_hook_scripts(root):
        _push(entry)
    for entry in _glob_sidecar_scripts(root):
        _push(entry)
    for entry in _EXPLICIT_ALLOWLIST:
        _push(entry)
    return tuple(out)


def _build_module_paths() -> Tuple[str, ...]:
    """Honour RC_GUARD_PATHS_OVERRIDE / RC_GUARD_PATHS_EXTRA at import time."""
    override = os.environ.get("RC_GUARD_PATHS_OVERRIDE")
    if override:
        return tuple(p for p in (s.strip() for s in override.split(",")) if p)
    base = list(discover())
    extra = os.environ.get("RC_GUARD_PATHS_EXTRA")
    if extra:
        for entry in (s.strip() for s in extra.split(",")):
            if entry and entry not in base:
                base.append(entry)
    return tuple(base)


GUARDED_PATHS: Tuple[str, ...] = _build_module_paths()


def is_guarded(file_path: str) -> bool:
    """Return True iff `file_path` matches a guarded substring.

    Normalizes to an absolute path before substring matching (round-2 fix:
    a relative path could otherwise sidestep the lock entirely when a
    non-Claude caller passes one in).
    """
    if not file_path:
        return False
    norm = os.path.abspath(file_path)
    return any(g in norm for g in GUARDED_PATHS)


def is_override_active() -> bool:
    """True iff the operator set RC_ALLOW_GUARD_EDIT=1 for this session."""
    return os.environ.get("RC_ALLOW_GUARD_EDIT") == "1"
