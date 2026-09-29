"""Worktree lease rules for the git guard.

Home is the git toplevel of the project dir. The agent may:

* open ONE worktree listed in the operator-written
  ``.reasoning-core/git_worktrees.yaml`` (``git worktree add -b``),
* merge a branch checked out in a linked worktree (or the lease branch)
  into home,
* remove a worktree once its branch is merged into home HEAD,
* delete merged branches (``git branch -d``).

Branch switching stays denied everywhere. Any git failure denies.
Contract shape::

    worktrees:
      - path: ../rc-wt-feature
        branch: feature/x
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CONTRACT_REL = ".reasoning-core/git_worktrees.yaml"
ABANDON_TOKEN = "abandon"
_TIMEOUT_S = 3
_PENDING_S = 60
_LEASE_KEYS = ("home", "home_branch", "path", "branch")

Verdict = Tuple[bool, str]

_MERGE_FLAGS = frozenset({
    "--no-ff", "--ff", "--ff-only", "--no-edit", "--edit", "--log",
    "--no-log", "--stat", "--no-stat", "-q", "--quiet",
})
_MERGE_SOLO = frozenset({"--abort", "--continue"})
_BRANCH_SAFE_LONG = frozenset({
    "--list", "--delete", "--all", "--remotes", "--verbose", "--quiet",
    "--show-current", "--contains", "--no-contains", "--merged", "--no-merged",
    "--sort", "--format", "--points-at", "--color", "--no-color", "--column",
    "--no-column", "--abbrev", "--no-abbrev", "--ignore-case", "--omit-empty",
})
_BRANCH_SAFE_SHORT = set("dlarvqi")


def _lease_git(cwd: str, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True, text=True, timeout=_TIMEOUT_S,
    )
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or f"git {args[0]} failed")
    return r.stdout


def _lease_git_yes(cwd: str, *args: str) -> bool:
    """Exit 0 -> True, exit 1 -> False; anything else raises."""
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=_TIMEOUT_S)
    if r.returncode not in (0, 1):
        raise RuntimeError(r.stderr.strip() or f"git {args[0]} failed")
    return r.returncode == 0


def _real(path: str) -> str:
    return os.path.realpath(path)


def home_root() -> str:
    import _host_env  # type: ignore

    return _real(_lease_git(str(_host_env.project_dir()), "rev-parse", "--show-toplevel").strip())


def _lease_state_path(home: str) -> Path:
    default = os.path.expanduser("~/.local/state/reasoning-core/git_lease")
    base = Path(os.environ.get("RC_GIT_LEASE_DIR") or default)
    if not os.path.realpath(base).endswith("/reasoning-core/git_lease"):
        base = Path(default)
    return base / (hashlib.sha256(home.encode()).hexdigest()[:16] + ".json")


@contextlib.contextmanager
def lease_lock(home: str):
    path = _lease_state_path(home).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def _lease_load(home: str) -> Optional[dict]:
    """None when no lease file; raises on a corrupt or unplanned lease."""
    try:
        data = json.loads(_lease_state_path(home).read_text())
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in _LEASE_KEYS):
        raise ValueError("lease state malformed")
    if data["home"] != home or (data["path"], data["branch"]) not in contract(home):
        raise ValueError("lease state does not match the worktree contract")
    return data


def save_lease(lease: dict) -> None:
    path = _lease_state_path(lease["home"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(lease))
    os.replace(tmp, path)


def bump_calls(home: str) -> Optional[dict]:
    with lease_lock(home):
        lease = _active_lease_locked(home)
        if lease:
            lease["calls"] = int(lease.get("calls", 0)) + 1
            save_lease(lease)
        return lease


def _lease_clear(home: str) -> None:
    try:
        _lease_state_path(home).unlink()
    except FileNotFoundError:
        pass


def worktrees(home: str) -> Dict[str, Optional[str]]:
    """realpath -> branch name (None when detached)."""
    out: Dict[str, Optional[str]] = {}
    path = None
    for line in _lease_git(home, "worktree", "list", "--porcelain", "-z").split("\0"):
        if line.startswith("worktree "):
            path = _real(line[len("worktree "):])
            out[path] = None
        elif line.startswith("branch refs/heads/") and path:
            out[path] = line[len("branch refs/heads/"):]
    return out


def _branch_exists(home: str, branch: str) -> bool:
    return _lease_git_yes(home, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")


def _merged(home: str, branch: str, into: str = "HEAD") -> bool:
    return _lease_git_yes(home, "merge-base", "--is-ancestor", f"refs/heads/{branch}", into)


def _current_branch(home: str) -> Optional[str]:
    try:
        name = _lease_git(home, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None
    return name or None


def active_lease(home: str) -> Optional[dict]:
    """Open while the worktree exists, its branch exists unmerged, or it
    was reserved moments ago and git has not created it yet."""
    with lease_lock(home):
        return _active_lease_locked(home)


def _active_lease_locked(home: str) -> Optional[dict]:
    lease = _lease_load(home)
    if not lease:
        return None
    if lease["path"] in worktrees(home):
        if not lease.get("created"):
            lease = {**lease, "created": True}
            save_lease(lease)
        return lease
    if lease.get("abandoned"):
        _lease_clear(home)
        return None
    if not lease.get("created") and time.time() - float(lease.get("opened_at", 0)) < _PENDING_S:
        return lease
    branch = lease["branch"]
    if _branch_exists(home, branch) and not (
            _branch_exists(home, lease["home_branch"])
            and _merged(home, branch, f"refs/heads/{lease['home_branch']}")):
        return lease
    _lease_clear(home)
    return None


def contract(home: str) -> List[Tuple[str, str]]:
    path = Path(home) / CONTRACT_REL
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(path.read_text()) or {}
    except Exception:  # noqa: BLE001 - missing/unreadable contract allows nothing
        return []
    out = []
    for item in (data.get("worktrees") or []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("path") and item.get("branch"):
            out.append((_real(os.path.join(home, str(item["path"]))), str(item["branch"])))
    return out


def _abandon_allowed() -> bool:
    extra = os.environ.get("RC_GIT_ALLOW", "")
    return ABANDON_TOKEN in {t.strip() for t in extra.split(",")}


_HARMLESS_GLOBALS = frozenset({"-p", "-P", "--paginate", "--no-pager", "--no-optional-locks"})


def _parse_git_argv(seg: str, cwd: Optional[str]) -> Optional[Tuple[Optional[str], List[str]]]:
    """(effective dir, argv after git globals). None on unparseable input."""
    try:
        tokens = shlex.split(seg)
    except ValueError:
        return None
    if not tokens or tokens[0] != "git":
        return None
    i, where = 1, cwd
    while i < len(tokens) and tokens[i].startswith("-"):
        if tokens[i] in _HARMLESS_GLOBALS:
            i += 1
            continue
        if tokens[i] != "-C" or i + 1 >= len(tokens):
            return None
        where = os.path.join(where or os.getcwd(), tokens[i + 1]) if where is not None else None
        i += 2
    return where, tokens[i:]


def check_git_segment(seg: str, cwd: Optional[str]) -> Optional[Verdict]:
    """Verdict for lease-managed git verbs; None when the verb is not managed.

    ``cwd`` is None when the chain changes directory first; home-only
    verbs then deny.
    """
    parsed = _parse_git_argv(seg, cwd)
    if parsed is None:
        try:
            tokens = set(shlex.split(seg))
        except ValueError:
            tokens = set(seg.split())
        if tokens & {"branch", "merge", "worktree"}:
            return False, "unsupported git global options with branch/merge/worktree"
        return None
    if not parsed[1]:
        return None
    where, argv = parsed
    verb, args = argv[0], argv[1:]
    if verb not in ("branch", "merge", "worktree"):
        return None
    if any(ch in seg for ch in "$`*?[{~"):
        return False, f"'git {verb}' operands must be literal (no shell expansion)"
    try:
        if verb == "branch":
            return _check_branch(where, args)
        home = home_root()
        if where is None or _real(where) != home:
            return False, f"'git {verb}' must run from the home root ({home}) without cd"
        if verb == "merge":
            return _check_merge(home, args)
        return _check_worktree(home, args)
    except (RuntimeError, OSError, subprocess.SubprocessError, KeyError, ValueError) as exc:
        return False, f"git or lease state unreadable: {exc}"


def _check_branch(where: Optional[str], args: List[str]) -> Verdict:
    for a in args:
        if a.startswith("--") and a.split("=", 1)[0] not in _BRANCH_SAFE_LONG:
            return False, f"'branch {a}' not allowed"
        if a.startswith("-") and not a.startswith("--") and not set(a[1:]) <= _BRANCH_SAFE_SHORT:
            return False, f"'branch {a}' not allowed (only -d on merged branches)"
    if not any(a == "--delete" or (a.startswith("-") and not a.startswith("--") and "d" in a) for a in args):
        if any(not a.startswith("-") for a in args) and not any(
                a in ("--list", "-l") or a.startswith(("--contains", "--no-contains", "--merged", "--no-merged",
                                                        "--points-at")) for a in args):
            return False, "branch creation goes through 'git worktree add -b' in the contract"
        return True, ""
    home = home_root()
    if where is None or _real(where) != home:
        return False, f"'git branch -d' must run from the home root ({home}) without cd"
    for name in (a for a in args if not a.startswith("-")):
        if not _branch_exists(home, name) or not _merged(home, name):
            return False, f"'{name}' is not a local branch merged into home HEAD"
    return True, ""


def _check_merge(home: str, args: List[str]) -> Verdict:
    if len(args) == 1 and args[0] in _MERGE_SOLO:
        return True, ""
    names, i = [], 0
    while i < len(args):
        a = args[i]
        if a in ("-m", "--message"):
            i += 2
            continue
        if a.startswith("--message=") or a in _MERGE_FLAGS:
            i += 1
            continue
        if a.startswith("-"):
            return False, f"'merge {a}' not allowed"
        names.append(a)
        i += 1
    if len(names) != 1:
        return False, "merge exactly one worktree branch"
    target = names[0]
    current = _current_branch(home)
    if current is None or current == target:
        return False, "home must be on a branch other than the merge target"
    lease = active_lease(home)
    if lease and lease.get("home_branch") != current:
        return False, f"home drifted: lease pinned '{lease.get('home_branch')}', now '{current}'"
    linked = {b for p, b in worktrees(home).items() if p != home and b}
    if lease:
        linked.add(lease["branch"])
    if target not in linked:
        return False, f"'{target}' is not a worktree branch; only worktree merges into home allowed"
    full = _lease_git(home, "rev-parse", "--verify", "--quiet", "--symbolic-full-name", target).strip()
    if full != f"refs/heads/{target}":
        return False, f"'{target}' is ambiguous (resolves to {full or 'nothing'})"
    return _guarded_merge_denial(home, f"refs/heads/{target}")


def _guarded_merge_denial(home: str, ref: str) -> Verdict:
    import _guard_paths  # type: ignore

    if _guard_paths.is_override_active():
        return True, ""
    changed = _lease_git(home, "diff", "--no-renames", "--name-only", "-z", f"HEAD...{ref}").split("\0")
    r = subprocess.run(["git", "-C", home, "merge-tree", "--write-tree", "--no-messages", "HEAD", ref],
                       capture_output=True, text=True, timeout=_TIMEOUT_S)
    tree = r.stdout.split("\n", 1)[0].strip()
    if r.returncode not in (0, 1) or not tree:
        return False, "cannot compute the merge result; operator merges this"
    changed += _lease_git(home, "diff", "--no-renames", "--name-only", "-z", "HEAD", tree).split("\0")
    hit = [f for f in changed if f and (
        _guard_paths.is_guarded(os.path.join(home, f)) or os.path.basename(f).lower().startswith(".envrc"))]
    if hit:
        return False, f"merge touches guarded paths ({', '.join(hit[:3])}); operator merges these"
    return True, ""


def _check_worktree(home: str, args: List[str]) -> Verdict:
    sub, rest = (args[0], args[1:]) if args else ("", [])
    if sub == "list" and all(a in ("--porcelain", "-v", "--verbose", "-z") for a in rest):
        return True, ""
    if sub == "prune" and all(a in ("-n", "--dry-run", "-v", "--verbose") for a in rest):
        return True, ""
    if sub == "add":
        return _open_lease(home, rest)
    if sub == "remove":
        return _remove(home, rest)
    return False, f"'worktree {sub or '<none>'}' not allowed"


_deferred: Optional[List[Tuple[str, List[str]]]] = None


@contextlib.contextmanager
def deferred_reservations():
    """Collect lease openings instead of saving them until the caller commits."""
    global _deferred
    _deferred = []
    try:
        yield _deferred
    finally:
        _deferred = None


def reserve(pending: List[Tuple[str, List[str]]]) -> Verdict:
    """Re-check and save each deferred lease opening."""
    for home, rest in pending:
        try:
            ok, msg = _open_lease(home, rest)
        except Exception as exc:  # noqa: BLE001
            return False, f"git or lease state unreadable: {exc}"
        if not ok:
            return False, msg
    return True, ""


def _open_lease(home: str, rest: List[str]) -> Verdict:
    branch, positional, i = None, [], 0
    while i < len(rest):
        if rest[i] == "-b" and i + 1 < len(rest):
            branch = rest[i + 1]
            i += 2
            continue
        if rest[i].startswith("-"):
            return False, f"'worktree add {rest[i]}' not allowed; use -b <new-branch>"
        positional.append(rest[i])
        i += 1
    if not branch or not 1 <= len(positional) <= 2:
        return False, "use: git worktree add <path> -b <new-branch>"
    path = _real(os.path.join(home, positional[0]))
    if (path, branch) not in contract(home):
        return False, f"({positional[0]}, {branch}) not listed in {CONTRACT_REL}"
    home_branch = _current_branch(home)
    if home_branch is None:
        return False, "home HEAD is detached; check out the home branch first"
    if _branch_exists(home, branch):
        return False, f"branch '{branch}' already exists; the lease needs a fresh branch"
    if len(positional) == 2 and not _lease_git_yes(home, "rev-parse", "--verify", "--quiet",
                                                   f"{positional[1]}^{{commit}}"):
        return False, f"start point '{positional[1]}' is not a commit"
    with lease_lock(home):
        lease = _active_lease_locked(home)
        if lease:
            return False, f"lease already open: {lease['branch']} @ {lease['path']}; merge and remove it first"
        others = [t for t in worktrees(home) if t != home]
        if others:
            return False, f"linked worktree {others[0]} still exists; merge and remove it first"
        if _lease_git(home, "status", "--porcelain", "--untracked-files=no").strip():
            return False, "home has uncommitted changes; commit before opening a worktree"
        if _deferred is not None:
            _deferred.append((home, rest))
            return True, ""
        save_lease({
            "home": home, "home_branch": home_branch, "path": path,
            "branch": branch, "opened_at": time.time(), "calls": 0,
        })
    return True, f"lease opened: {branch} @ {path}"


def _remove(home: str, rest: List[str]) -> Verdict:
    if len(rest) != 1 or rest[0].startswith("-"):
        return False, "use: git worktree remove <path> (no --force)"
    path = _real(os.path.join(home, rest[0]))
    trees = worktrees(home)
    if path == home or path not in trees:
        return False, f"{rest[0]} is not a linked worktree"
    branch = trees[path]
    with lease_lock(home):
        lease = _active_lease_locked(home)
    if lease and lease["path"] == path and _current_branch(home) != lease["home_branch"]:
        return False, f"home left '{lease['home_branch']}'; return home to it before removing the worktree"
    if branch and _merged(home, branch):
        return True, ""
    if _abandon_allowed():
        with lease_lock(home):
            lease = _active_lease_locked(home)
            if lease and lease["path"] == path:
                save_lease({**lease, "abandoned": True})
        return True, "abandoned unmerged worktree"
    return False, f"'{branch or 'detached'}' not merged into home; merge first (operator: RC_GIT_ALLOW=abandon)"


def edit_denial(file_path: str) -> Optional[str]:
    """Deny edits inside a linked worktree that is not the open lease."""
    import _host_env  # type: ignore

    project = Path(_real(str(_host_env.project_dir())))
    if not any((d / ".git").exists() for d in (project, *project.parents)):
        return None
    try:
        home = home_root()
        trees = worktrees(home)
        lease = active_lease(home)
    except Exception as exc:  # noqa: BLE001 - git project with unreadable state
        return f"git worktree state unreadable ({exc}); edit denied"
    target = _real(file_path)
    allowed = {home, lease["path"] if lease else ""}
    for path in trees:
        if path in allowed:
            continue
        if target == path or target.startswith(path + os.sep):
            return f"{file_path} is inside worktree {path}, which is not the open lease"
    return None


def reminder(home: str, lease: dict) -> str:
    return (
        f"[rc] LEASE open: {lease['branch']} @ {lease['path']} ({lease.get('calls', 0)} calls). "
        f"Home: {lease.get('home_branch')} @ {home}. Close: from home run "
        f"`git merge {lease['branch']}`, then `git worktree remove {lease['path']}`."
    )
