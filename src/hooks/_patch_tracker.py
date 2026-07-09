"""Cumulative patch tracker for Phase 2 execution-grounded oracles.

Tracks the session's pending diff in a private scratch directory and, when the
project is a git repo, maintains a persistent git worktree so oracles can
verify the cumulative patch before the user's real filesystem is touched.

Session key (stable within a run):
  - Prefer ``CLAUDE_SESSION_ID`` when present.
  - Otherwise fall back to ``<repo_root_hash>-<branch>-<ppid>-<pid>``.
  - Cross-restart persistence is only guaranteed with a stable session ID;
    the fallback key intentionally changes on restart to avoid chimera diffs.

Storage layout (mode 0700):
  ~/.cache/reasoning-core/rc-scratch/
    <session_key>/
      pending.patch       # cumulative unified diff of all edits this session
      worktree/           # git worktree (if project_root is a git repo)
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Optional


_DEFAULT_SCRATCH_PARENT = Path.home() / ".cache" / "reasoning-core" / "rc-scratch"


def _cache_dir() -> Path:
    """Return the parent scratch directory from env or default."""
    override = os.environ.get("RC_CACHE_DIR")
    if override:
        return Path(override) / "rc-scratch"
    return _DEFAULT_SCRATCH_PARENT


def _session_key(project_root: str) -> str:
    """Build a stable session key for the current agent session."""
    explicit = os.environ.get("CLAUDE_SESSION_ID")
    if explicit:
        # Sanitize: keys are directory names; strip path separators.
        return explicit.replace(os.sep, "_").replace("/", "_")[:128]

    root_hash = hashlib.sha256(project_root.encode("utf-8")).hexdigest()[:16]
    branch = "unknown"
    try:
        branch = (
            subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=project_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=2.0,
            )
            .stdout.strip()
            .replace(os.sep, "_")
        )
    except Exception:  # noqa: BLE001
        pass
    ppid = os.getppid()
    pid = os.getpid()
    return f"{root_hash}-{branch}-{ppid}-{pid}"


def scratch_dir(project_root: str, session_key: Optional[str] = None) -> Path:
    """Return the session-private scratch directory."""
    key = session_key or _session_key(project_root)
    d = _cache_dir() / key
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def pending_patch_path(project_root: str, session_key: Optional[str] = None) -> Path:
    """Path to the cumulative pending patch for this session."""
    return scratch_dir(project_root, session_key) / "pending.patch"


def worktree_dir(project_root: str, session_key: Optional[str] = None) -> Path:
    """Path to the session worktree (created on demand)."""
    return scratch_dir(project_root, session_key) / "worktree"


def _is_git_repo(project_root: str) -> bool:
    """True if ``project_root`` is inside a git worktree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except Exception:  # noqa: BLE001
        return False


def _ensure_worktree(project_root: str, session_key: Optional[str] = None) -> Optional[Path]:
    """Create a persistent git worktree for this session.

    Returns the worktree path on success, or None if the project is not a git
    repo or git is unavailable.
    """
    if not _is_git_repo(project_root):
        return None

    wt = worktree_dir(project_root, session_key)
    if (wt / ".git").exists():
        return wt

    try:
        result = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt)],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30.0,
        )
        if result.returncode == 0:
            return wt
    except Exception:  # noqa: BLE001
        pass
    return None


def _reset_worktree(wt: Path) -> bool:
    """Reset the worktree to a clean state."""
    try:
        subprocess.run(
            ["git", "reset", "--hard"],
            cwd=str(wt),
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=str(wt),
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def _apply_patch(wt: Path, patch_text: str) -> tuple[bool, str]:
    """Apply a unified diff to the worktree. Returns (ok, stderr)."""
    try:
        result = subprocess.run(
            ["git", "apply", "--unidiff-zero", "-"],
            cwd=str(wt),
            input=patch_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
        return result.returncode == 0, result.stderr
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _diff_hunk(file_path: str, before_src: str, after_src: str) -> str:
    """Build a minimal unified diff hunk for one edit pair.

    Git apply is forgiving about the index line; we emit a stripped header so
    the patch applies without requiring the exact pre-image SHA.
    """
    # Normalize line endings so the diff is well-formed.
    before_lines = before_src.splitlines(keepends=True)
    after_lines = after_src.splitlines(keepends=True)
    if before_lines and not before_lines[-1].endswith("\n"):
        before_lines[-1] += "\n"
    if after_lines and not after_lines[-1].endswith("\n"):
        after_lines[-1] += "\n"

    lines = [
        f"--- a/{file_path}\n",
        f"+++ b/{file_path}\n",
        f"@@ -1,{len(before_lines)} +1,{len(after_lines)} @@\n",
    ]
    for line in before_lines:
        lines.append(f"-{line}")
    for line in after_lines:
        lines.append(f"+{line}")
    return "".join(lines)


def append_edit(
    project_root: str,
    file_path: str,
    before_src: str,
    after_src: str,
    session_key: Optional[str] = None,
) -> Path:
    """Append one edit pair to the session's cumulative pending patch.

    Returns the path to the pending patch file.
    """
    patch_path = pending_patch_path(project_root, session_key)
    hunk = _diff_hunk(file_path, before_src, after_src)
    with open(patch_path, "a", encoding="utf-8") as f:
        f.write(hunk)
        f.write("\n")
    return patch_path


def reset_pending_patch(project_root: str, session_key: Optional[str] = None) -> None:
    """Clear the pending patch (e.g., on commit or task boundary)."""
    patch_path = pending_patch_path(project_root, session_key)
    patch_path.write_text("", encoding="utf-8")


def run_in_worktree(
    project_root: str,
    patch_text: str,
    session_key: Optional[str] = None,
) -> tuple[Optional[Path], str]:
    """Ensure a worktree, reset it, and apply the cumulative patch.

    Returns (worktree_path, error_message). If a worktree cannot be created,
    worktree_path is None and error_message explains why.
    """
    wt = _ensure_worktree(project_root, session_key)
    if wt is None:
        return None, "not a git repository or git worktree failed"

    if not _reset_worktree(wt):
        return wt, "worktree reset failed"

    ok, err = _apply_patch(wt, patch_text)
    if not ok:
        return wt, f"patch apply failed: {err}"

    return wt, ""


__all__ = [
    "_session_key",
    "scratch_dir",
    "pending_patch_path",
    "worktree_dir",
    "append_edit",
    "reset_pending_patch",
    "run_in_worktree",
]
