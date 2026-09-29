"""Worktree lease: home pin, planned worktree open, merge-back, remove."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.hooks import _git_lease
from src.hooks.pre_bash_guard import screen_command

HOOK = Path(__file__).resolve().parent.parent / "src" / "hooks" / "git_lease_hook.py"
_LEASE_DIR = Path("reasoning-core") / "git_lease"


def _lease_repo(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def lease_home(tmp_path, monkeypatch):
    repo = tmp_path / "home"
    repo.mkdir()
    _lease_repo(repo, "init", "-q", "-b", "main")
    _lease_repo(repo, "config", "user.email", "t@t")
    _lease_repo(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n")
    _lease_repo(repo, "add", "a.txt")
    _lease_repo(repo, "commit", "-qm", "init")
    (repo / ".reasoning-core").mkdir()
    (repo / ".reasoning-core" / "git_worktrees.yaml").write_text(
        "worktrees:\n  - path: ../wt\n    branch: feature/x\n"
    )
    _lease_repo(repo, "add", ".reasoning-core")
    _lease_repo(repo, "commit", "-qm", "contract")
    monkeypatch.setenv("RC_PROJECT_DIR", str(repo))
    monkeypatch.setenv("RC_GIT_LEASE_DIR", str(tmp_path / _LEASE_DIR))
    monkeypatch.delenv("RC_GIT_ALLOW", raising=False)
    monkeypatch.delenv("RC_ALLOW_GUARD_EDIT", raising=False)
    return repo


def _screen_at(cmd, cwd):
    return screen_command(cmd, str(cwd))


def _open_lease_wt(home):
    assert _screen_at("git worktree add ../wt -b feature/x", home)[0] == 0
    _lease_repo(home, "worktree", "add", "-q", "../wt", "-b", "feature/x")
    assert _git_lease.bump_calls(_git_lease.home_root())["created"]
    return (home.parent / "wt").resolve()


def _commit_on(wt):
    (wt / "b.txt").write_text("b\n")
    _lease_repo(wt, "add", "b.txt")
    _lease_repo(wt, "commit", "-qm", "b")


def test_open_planned_worktree_creates_lease(lease_home):
    wt = _open_lease_wt(lease_home)
    lease = _git_lease.active_lease(_git_lease.home_root())
    assert lease["branch"] == "feature/x" and lease["path"] == str(wt)
    assert lease["home_branch"] == "main"


@pytest.mark.parametrize("cmd", [
    "git worktree add ../other -b feature/x",
    "git worktree add ../wt -b feature/y",
    "git worktree add ../wt feature/x",
    "git worktree add -f ../wt -b feature/x",
    "git worktree add --detach ../wt",
])
def test_unplanned_worktree_add_denied(lease_home, cmd):
    assert _screen_at(cmd, lease_home)[0] == 2


def test_second_lease_denied(lease_home):
    _open_lease_wt(lease_home)
    (lease_home / ".reasoning-core" / "git_worktrees.yaml").write_text(
        "worktrees:\n  - path: ../wt\n    branch: feature/x\n"
        "  - path: ../wt2\n    branch: feature/y\n"
    )
    code, msg = _screen_at("git worktree add ../wt2 -b feature/y", lease_home)
    assert code == 2 and "lease already open" in msg


def test_dirty_home_blocks_open(lease_home):
    (lease_home / "a.txt").write_text("changed\n")
    code, msg = _screen_at("git worktree add ../wt -b feature/x", lease_home)
    assert code == 2 and "uncommitted" in msg


def test_missing_contract_denies(lease_home):
    (lease_home / ".reasoning-core" / "git_worktrees.yaml").unlink()
    _lease_repo(lease_home, "commit", "-qam", "drop contract")
    assert _screen_at("git worktree add ../wt -b feature/x", lease_home)[0] == 2


def test_merge_worktree_branch_from_home(lease_home):
    wt = _open_lease_wt(lease_home)
    _commit_on(wt)
    assert _screen_at("git merge --no-ff feature/x -m 'merge x'", lease_home)[0] == 0


@pytest.mark.parametrize("cmd", [
    "git merge -s ours feature/x",
    "git merge -X theirs feature/x",
    "git merge --no-verify feature/x",
    "git merge --allow-unrelated-histories feature/x",
    "git merge main",
    "git merge origin/main",
])
def test_unsafe_or_foreign_merge_denied(lease_home, cmd):
    _open_lease_wt(lease_home)
    assert _screen_at(cmd, lease_home)[0] == 2


def test_merge_from_worktree_denied(lease_home):
    wt = _open_lease_wt(lease_home)
    code, msg = _screen_at("git merge main", wt)
    assert code == 2 and "home" in msg


def test_cd_then_merge_denied(lease_home):
    _open_lease_wt(lease_home)
    assert _screen_at("cd ../wt && git merge feature/x", lease_home)[0] == 2


def test_home_drift_blocks_merge(lease_home):
    wt = _open_lease_wt(lease_home)
    _commit_on(wt)
    _lease_repo(lease_home, "switch", "-qc", "side")
    code, msg = _screen_at("git merge feature/x", lease_home)
    assert code == 2 and "drifted" in msg


def test_remove_after_merge_closes_lease(lease_home):
    wt = _open_lease_wt(lease_home)
    _commit_on(wt)
    code, msg = _screen_at("git worktree remove ../wt", lease_home)
    assert code == 2 and "not merged" in msg
    _lease_repo(lease_home, "merge", "-q", "feature/x")
    assert _screen_at("git worktree remove ../wt", lease_home)[0] == 0
    _lease_repo(lease_home, "worktree", "remove", str(wt))
    assert _git_lease.active_lease(_git_lease.home_root()) is None


def test_remove_force_denied(lease_home):
    _open_lease_wt(lease_home)
    assert _screen_at("git worktree remove --force ../wt", lease_home)[0] == 2


def test_abandon_needs_operator(lease_home, monkeypatch):
    wt = _open_lease_wt(lease_home)
    _commit_on(wt)
    assert _screen_at("git worktree remove ../wt", lease_home)[0] == 2
    monkeypatch.setenv("RC_GIT_ALLOW", "abandon")
    assert _screen_at("git worktree remove ../wt", lease_home)[0] == 0


@pytest.mark.parametrize("cmd,code", [
    ("git branch -d main", 0),
    ("git branch --list", 0),
    ("git branch -D feature/x", 2),
    ("git branch -f main HEAD~1", 2),
    ("git branch -m main other", 2),
    ("git branch --force x", 2),
    ("git worktree prune", 0),
    ("git worktree move ../wt ../wt3", 2),
    ("git worktree lock ../wt", 2),
    ("git checkout feature/x", 2),
    ("git switch feature/x", 2),
])
def test_branch_and_worktree_verbs(lease_home, cmd, code):
    assert _screen_at(cmd, lease_home)[0] == code


def test_heredoc_and_quotes_do_not_split(lease_home):
    cmd = "git commit -F - <<'EOF'\nfix: x\n\ngit checkout main; git reset --hard\nEOF"
    assert _screen_at(cmd, lease_home)[0] == 0
    assert _screen_at("git commit -m 'note: git checkout main; done'", lease_home)[0] == 0
    assert _screen_at("git status; git checkout main", lease_home)[0] == 2


def test_edit_outside_lease_denied(lease_home):
    wt = _open_lease_wt(lease_home)
    assert _git_lease.edit_denial(str(wt / "b.txt")) is None
    assert _git_lease.edit_denial(str(lease_home / "a.txt")) is None
    _lease_repo(lease_home, "worktree", "add", "-q", "../stray", "-b", "stray")
    assert _git_lease.edit_denial(str(lease_home.parent / "stray" / "a.txt"))


def _run_lease_hook(event, extra=None):
    payload = {"hook_event_name": event, **(extra or {})}
    return subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True,
    )


def test_hook_silent_without_lease(lease_home):
    r = _run_lease_hook("Stop")
    assert r.returncode == 0 and not r.stdout


def test_hook_reminds_and_blocks_stop(lease_home):
    _open_lease_wt(lease_home)
    r = _run_lease_hook("PostToolUse")
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "LEASE open: feature/x" in ctx and "(2 calls)" in ctx
    assert _run_lease_hook("Stop").returncode == 2
    assert _run_lease_hook("Stop", {"stop_hook_active": True}).returncode == 0


_HIDDEN_RESET = "cat <<'EOF'; git re" + "set --hard\nbody\nEOF"
_SHELL_FED_CHECKOUT = "bash <<EOF\ngit check" + "out main\nEOF"
_PIPED_TO_SHELL = "cat <<EOF | sh\ngit re" + "set --hard\nEOF"
_CONTINUED_TO_SHELL = "cat <<\"EOF\" | \\\nsh\ngit re" + "set --hard HEAD~1\nEOF"
_EMPTY_BODY = "cat <<'EOF'\nEOF\ngit re" + "set --hard\nEOF"
_COMMIT_THEN_MORE = "git commit -F - <<'EOF'\nmsg\nEOF\ngit re" + "set --hard\nEOF"


@pytest.mark.parametrize("cmd,verb", [
    (_HIDDEN_RESET, "reset"), (_SHELL_FED_CHECKOUT, "checkout"), (_PIPED_TO_SHELL, "reset"),
    (_CONTINUED_TO_SHELL, "reset"), (_EMPTY_BODY, "reset"), (_COMMIT_THEN_MORE, "reset"),
])
def test_heredoc_cannot_hide_commands(lease_home, cmd, verb):
    code, msg = _screen_at(cmd, lease_home)
    assert code == 2 and (verb in msg or "heredoc" in msg)


def _two_planned(home):
    (home / ".reasoning-core" / "git_worktrees.yaml").write_text(
        "worktrees:\n  - path: ../wt\n    branch: feature/x\n"
        "  - path: ../wt2\n    branch: feature/y\n"
    )
    _lease_repo(home, "commit", "-qam", "two")


def test_chained_double_open_denied(lease_home):
    _two_planned(lease_home)
    cmd = "git worktree add ../wt -b feature/x; git worktree add ../wt2 -b feature/y"
    code, msg = _screen_at(cmd, lease_home)
    assert code == 2 and "own command" in msg


def test_pending_lease_blocks_second_open(lease_home):
    _two_planned(lease_home)
    assert _screen_at("git worktree add ../wt -b feature/x", lease_home)[0] == 0
    code, msg = _screen_at("git worktree add ../wt2 -b feature/y", lease_home)
    assert code == 2 and "lease already open" in msg


def test_subdir_dash_c_denied(lease_home):
    _open_lease_wt(lease_home)
    code, msg = _screen_at("git -C .reasoning-core worktree remove ../wt", lease_home)
    assert code == 2 and "home root" in msg


def test_worktree_list_allowed(lease_home):
    code, msg = _screen_at("git worktree list --porcelain", lease_home)
    assert code == 0, msg


def test_edit_denied_on_corrupt_lease_state(lease_home, tmp_path):
    _open_lease_wt(lease_home)
    for f in (tmp_path / _LEASE_DIR).glob("*.json"):
        f.write_text('{"path": 1}')
    _lease_repo(lease_home, "worktree", "add", "-q", "../stray", "-b", "stray")
    assert _git_lease.edit_denial(str(lease_home.parent / "stray" / "a.txt"))


def test_edit_allowed_outside_git(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("RC_PROJECT_DIR", str(plain))
    assert _git_lease.edit_denial(str(plain / "x.txt")) is None


def test_forged_lease_denies_edits(lease_home, tmp_path):
    _open_lease_wt(lease_home)
    _lease_repo(lease_home, "worktree", "add", "-q", "../stray", "-b", "stray")
    for f in (tmp_path / _LEASE_DIR).glob("*.json"):
        forged = json.loads(f.read_text())
        forged.update(path=str((lease_home.parent / "stray").resolve()), branch="stray")
        f.write_text(json.dumps(forged))
    assert _git_lease.edit_denial(str(lease_home.parent / "stray" / "a.txt"))


def test_git_probe_failure_keeps_lease(lease_home, tmp_path, monkeypatch):
    _open_lease_wt(lease_home)
    _lease_repo(lease_home, "worktree", "remove", str((lease_home.parent / "wt").resolve()))

    def boom(*_a):
        raise RuntimeError("timeout")

    monkeypatch.setattr(_git_lease, "_lease_git_yes", boom)
    with pytest.raises(RuntimeError):
        _git_lease.active_lease(_git_lease.home_root())
    assert list((tmp_path / _LEASE_DIR).glob("*.json"))


def test_unquoted_heredoc_substitution_screened(lease_home):
    cmd = "cat <<EOF\n$(\ngit re" + "set --hard\n)\nEOF"
    assert _screen_at(cmd, lease_home)[0] == 2


def test_ambiguous_tag_merge_denied(lease_home):
    wt = _open_lease_wt(lease_home)
    _commit_on(wt)
    _lease_repo(lease_home, "update-ref", "refs/tags/feature/x", "HEAD")
    code, msg = _screen_at("git merge feature/x", lease_home)
    assert code == 2 and "ambiguous" in msg


def test_pseudoref_named_branch_merge_denied(lease_home):
    (lease_home / ".reasoning-core" / "git_worktrees.yaml").write_text(
        "worktrees:\n  - path: ../wt\n    branch: FETCH_HEAD\n")
    _lease_repo(lease_home, "commit", "-qam", "plan")
    assert _screen_at("git worktree add ../wt -b FETCH_HEAD", lease_home)[0] == 0
    _lease_repo(lease_home, "worktree", "add", "-q", "../wt", "-b", "FETCH_HEAD")
    head = subprocess.run(["git", "-C", str(lease_home), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout
    (lease_home / ".git" / "FETCH_HEAD").write_text(head)
    code, msg = _screen_at("git merge FETCH_HEAD", lease_home)
    assert code == 2 and "ambiguous" in msg


def test_merge_touching_guard_denied(lease_home, monkeypatch):
    monkeypatch.delenv("RC_ALLOW_GUARD_EDIT", raising=False)
    wt = _open_lease_wt(lease_home)
    hooks = wt / "src" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "pre_bash_guard.py").write_text("x = 1\n")
    _lease_repo(wt, "add", "src")
    _lease_repo(wt, "commit", "-qm", "g")
    code, msg = _screen_at("git merge feature/x", lease_home)
    assert code == 2 and "guarded" in msg


def test_merge_renaming_guard_away_denied(lease_home, monkeypatch):
    monkeypatch.delenv("RC_ALLOW_GUARD_EDIT", raising=False)
    hooks = lease_home / "src" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "pre_bash_guard.py").write_text("x = 1\n" * 20)
    _lease_repo(lease_home, "add", "src")
    _lease_repo(lease_home, "commit", "-qm", "g")
    wt = _open_lease_wt(lease_home)
    _lease_repo(wt, "mv", "src/hooks/pre_bash_guard.py", "moved.py")
    _lease_repo(wt, "commit", "-qm", "mv")
    assert "guarded" in _screen_at("git merge feature/x", lease_home)[1]


def test_rejected_chain_reserves_nothing(lease_home):
    cmd = "git worktree add ../wt -b feature/x && git re" + "set --hard"
    assert _screen_at(cmd, lease_home)[0] == 2
    assert _git_lease.active_lease(_git_lease.home_root()) is None


def test_merge_must_run_alone(lease_home):
    wt = _open_lease_wt(lease_home)
    _commit_on(wt)
    code, msg = _screen_at("git fetch origin feature/x:feature/x && git merge feature/x", lease_home)
    assert code == 2 and "own command" in msg


def test_multiline_substitution_denied(lease_home):
    assert _screen_at('echo "$(\ngit re' + 'set --hard\n)"', lease_home)[0] == 2


def test_two_opens_via_dash_c_reserve_nothing(lease_home):
    _two_planned(lease_home)
    home = _git_lease.home_root()
    cmd = f"git -C {home} worktree add ../wt -b feature/x && git -C {home} worktree add ../wt2 -b feature/y"
    assert _screen_at(cmd, lease_home)[0] == 2
    assert _git_lease.active_lease(home) is None


def test_comment_quote_cannot_hide_worktree_add(lease_home):
    code, msg = _screen_at("# '\ngit worktree add -b rogue /tmp/rogue\n# '", lease_home)
    assert code == 2 and "worktree" in msg


def test_merge_touching_envrc_denied(lease_home, monkeypatch):
    monkeypatch.delenv("RC_ALLOW_GUARD_EDIT", raising=False)
    wt = _open_lease_wt(lease_home)
    (wt / ".envrc.local").write_text("export RC_OFF=1\n")
    _lease_repo(wt, "add", "-f", ".envrc.local")
    _lease_repo(wt, "commit", "-qm", "env")
    assert "guarded" in _screen_at("git merge feature/x", lease_home)[1]


def test_branch_delete_needs_literal_existing_branch(lease_home):
    for cmd in ('B=feature; git branch -d "$B"', "git branch -d no-such-branch"):
        assert _screen_at(cmd, lease_home)[0] == 2, cmd


def test_guarded_lease_files_not_deletable(lease_home, monkeypatch):
    monkeypatch.delenv("RC_ALLOW_GUARD_EDIT", raising=False)
    for cmd in ("rm ~/.local/state/reasoning-core/git_lease/abc.json",
                "rm .reasoning-core/git_worktrees.yaml"):
        assert _screen_at(cmd, lease_home)[0] == 2, cmd


_QUOTE_IN_HEREDOC = "cat <<'EOF'\n'\nEOF\ngit worktree remove --force /tmp/unmerged\n#'"


@pytest.mark.parametrize("cmd", [
    _QUOTE_IN_HEREDOC,
    "true & git worktree remove --force /tmp/unmerged",
    "true & git merge attacker",
    "git\tworktree remove --force /tmp/unmerged",
    "rm -rf ~/.local/state/reasoning-core/git_lease",
    "cat <<'EOF'\n'\nEOF\ngit push --force origin HEAD",
    "sleep 130; git " + "worktree add ../wt -b feature/x",
    "NOTE='two words' git " + "worktree add ../unlisted -b rogue",
    'NOTE="a b" git merge arbitrary',
    "NOTE=a\\ b git branch -D valuable",
    "git merge feature}",
    "command git " + "worktree add ../unplanned -b unplanned",
    'g""it ' + "worktree add ../unplanned -b unplanned",
    "/usr/bin/git merge arbitrary",
    "env X=1 git branch -D valuable",
    "if true; then git " + "worktree add ../unplanned -b unplanned; fi",
    "for i in 1; do git merge arbitrary; done",
    "! git branch -D valuable",
    "> /dev/null git " + "worktree add /tmp/unplanned -b unplanned",
    "2>/dev/null git re" + "set --hard",
    "git branch unplanned HEAD",
    "gi\\\nt " + "worktree add /tmp/unplanned -b unplanned",
    "timeout --signal TERM 10 git re" + "set --hard",
    "case x in x) git " + "worktree add ../unlisted -b unlisted;; esac",
    "case x in x) git re" + "set --hard;; esac",
    "git branch --sort=refname unauthorized",
    "git branch --format=%(refname) unauthorized",
    "env -u HOME git merge arbitrary",
    "{ git " + "worktree add ../wt -b feature/x; }",
])
def test_separator_and_quote_tricks_denied(lease_home, monkeypatch, cmd):
    monkeypatch.delenv("RC_ALLOW_GUARD_EDIT", raising=False)
    assert _screen_at(cmd, lease_home)[0] == 2


def test_redirect_ampersand_still_allowed(lease_home):
    assert _screen_at("git status 2>&1 | head", lease_home)[0] == 0
    assert _screen_at("> out.txt printf hello", lease_home)[0] == 0


def test_backtick_git_denied(lease_home):
    cmd = 'echo "' + chr(96) + "true; git re" + "set --hard;" + chr(96) + '"'
    assert _screen_at(cmd, lease_home)[0] == 2


def test_existing_branch_cannot_open_lease(lease_home):
    _lease_repo(lease_home, "branch", "feature/x")
    code, msg = _screen_at("git worktree add ../wt -b feature/x", lease_home)
    assert code == 2 and "already exists" in msg


def test_deleted_lease_file_still_blocks_open(lease_home, tmp_path):
    _two_planned(lease_home)
    _open_lease_wt(lease_home)
    for f in (tmp_path / _LEASE_DIR).glob("*.json"):
        f.unlink()
    code, msg = _screen_at("git worktree add ../wt2 -b feature/y", lease_home)
    assert code == 2 and "still exists" in msg


def test_abandon_keeps_lease_until_removed(lease_home, monkeypatch):
    wt = _open_lease_wt(lease_home)
    _commit_on(wt)
    monkeypatch.setenv("RC_GIT_ALLOW", "abandon")
    assert _screen_at("git worktree remove ../wt", lease_home)[0] == 0
    assert _git_lease.active_lease(_git_lease.home_root())
    _lease_repo(lease_home, "worktree", "remove", str(wt))
    assert _git_lease.active_lease(_git_lease.home_root()) is None


def test_branch_delete_requires_merge_into_home(lease_home):
    wt = _open_lease_wt(lease_home)
    _commit_on(wt)
    code, msg = _screen_at("git branch -d feature/x", lease_home)
    assert code == 2 and "merged into home" in msg


def test_multiline_bash_c_screened(lease_home):
    cmd = "bash -c '\ngit re" + "set --hard\n'"
    assert _screen_at(cmd, lease_home)[0] == 2


def test_git_env_redirect_denied(lease_home):
    wt = _open_lease_wt(lease_home)
    _commit_on(wt)
    code, msg = _screen_at("GIT_DIR=/tmp/other/.git git merge feature/x", lease_home)
    assert code == 2 and "GIT_" in msg


def test_lease_contract_mismatch_denies_not_crashes(lease_home):
    _open_lease_wt(lease_home)
    (lease_home / ".reasoning-core" / "git_worktrees.yaml").write_text("worktrees: []\n")
    code, msg = _screen_at("git merge feature/x", lease_home)
    assert code == 2 and "unreadable" in msg
    assert _run_lease_hook("Stop").returncode == 2


def test_escaped_quote_does_not_hide_segment(lease_home):
    cmd = 'echo \\"; git re' + 'set --hard; echo \\"'
    assert _screen_at(cmd, lease_home)[0] == 2


def test_abbreviated_branch_options_denied(lease_home):
    for cmd in ("git branch --forc victim HEAD", "git branch --mov main renamed"):
        code, msg = _screen_at(cmd, lease_home)
        assert code == 2 and "not allowed" in msg, cmd


def test_git_globals_do_not_skip_branch_checks(lease_home):
    denied = [c for c in ("git -p branch -D victim", "git -p branch -m main x", "git --bare branch -D victim")
              if _screen_at(c, lease_home)[0] != 2]
    assert denied == []
    assert _screen_at("git --no-pager branch --list", lease_home)[0] == 0


def test_stop_blocked_by_worktree_without_lease(lease_home, tmp_path):
    _open_lease_wt(lease_home)
    for f in (tmp_path / _LEASE_DIR).glob("*.json"):
        f.unlink()
    assert _run_lease_hook("Stop").returncode == 2


def test_lease_state_dir_guarded():
    from src.hooks import _guard_paths
    assert _guard_paths.is_guarded("/Users/x/.local/state/reasoning-core/git_lease/abc.json")


def test_bad_start_point_reserves_nothing(lease_home):
    code, msg = _screen_at("git " + "worktree add ../wt -b feature/x no-such-ref", lease_home)
    assert code == 2 and "not a commit" in msg
    assert _screen_at("git " + "worktree add ../wt -b feature/x", lease_home)[0] == 0


def test_lease_dir_outside_default_name_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_GIT_LEASE_DIR", str(tmp_path / "anywhere"))
    assert str(_git_lease._lease_state_path("/h")).endswith("reasoning-core/git_lease/"
                                                            + _git_lease._lease_state_path("/h").name)
    assert "anywhere" not in str(_git_lease._lease_state_path("/h"))


def test_branch_listing_still_allowed(lease_home):
    for cmd in ("git branch", "git branch -a", "git branch --list feature/x", "git branch --contains HEAD"):
        assert _screen_at(cmd, lease_home)[0] == 0, cmd


def test_merge_into_renamed_guard_denied(lease_home):
    (lease_home / "ordinary.txt").write_text("{}\n")
    _lease_repo(lease_home, "add", "ordinary.txt")
    _lease_repo(lease_home, "commit", "-qm", "base")
    wt = _open_lease_wt(lease_home)
    (wt / "ordinary.txt").write_text('{"hooks": {}}\n')
    _lease_repo(wt, "commit", "-qam", "edit")
    (lease_home / ".claude").mkdir()
    _lease_repo(lease_home, "mv", "ordinary.txt", ".claude/settings.json")
    _lease_repo(lease_home, "commit", "-qm", "rename")
    code, msg = _screen_at("git merge feature/x", lease_home)
    assert code == 2 and "guarded" in msg


def test_remove_needs_pinned_home_branch(lease_home):
    wt = _open_lease_wt(lease_home)
    _commit_on(wt)
    _lease_repo(lease_home, "switch", "-qc", "integration")
    _lease_repo(lease_home, "merge", "-q", "feature/x")
    code, msg = _screen_at("git " + "worktree remove ../wt", lease_home)
    assert code == 2 and "return home" in msg
    assert _git_lease.active_lease(_git_lease.home_root()) is not None


def test_guarded_files_protected_relative_to_cwd(lease_home, tmp_path):
    lease_dir = tmp_path / _LEASE_DIR
    lease_dir.mkdir(parents=True)
    (lease_dir / "abc.json").write_text("{}")
    rc = lease_home / ".reasoning-core"
    for cmd, cwd in (("rm git_worktrees.yaml", rc),
                     ("mv git_worktrees.yaml x", rc),
                     ("rm -rf .reasoning-core", lease_home),
                     ("cd .reasoning-core && rm git_worktrees.yaml", lease_home),
                     ("echo x > git_worktrees.yaml", rc),
                     ("cp /dev/null git_worktrees.yaml", rc),
                     ("rm abc.json", lease_dir),
                     ("rm -rf .", lease_dir),
                     ("cd /definitely-missing || rm -rf .reasoning-core", lease_home),
                     ("echo x >>git_worktrees.yaml", rc),
                     ("dd if=/tmp/x of=.reasoning-core/git_worktrees.yaml", lease_home),
                     ("dd if=/tmp/x of=abc.json", lease_dir)):
        assert _screen_at(cmd, cwd)[0] == 2, cmd
    for cmd, cwd in (("cat git_worktrees.yaml", rc),
                     ("cat < git_worktrees.yaml", rc),
                     ("cp git_worktrees.yaml /tmp/copy.txt", rc),
                     ("rm a.txt", lease_home)):
        assert _screen_at(cmd, cwd)[0] == 0, cmd
