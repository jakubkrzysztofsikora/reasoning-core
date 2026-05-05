"""Phase -1 day-zero ergonomics tests."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src" / "hooks"))


def test_magic_comments_basic():
    import _magic_comments as mc

    assert mc.parse("") is None
    assert mc.parse("nothing here") is None
    assert mc.parse("# rc:skip").name == "skip"
    assert mc.parse("// rc:skip-lang").name == "skip-lang"
    assert mc.parse("<!-- rc:skip-quality -->").name == "skip-quality"
    d = mc.parse("# rc:override one-line typo fix")
    assert d.name == "override" and "typo" in d.reason


def test_magic_comments_only_first_20_lines():
    import _magic_comments as mc

    payload = "\n".join(["x"] * 30 + ["# rc:skip"])
    assert mc.parse(payload) is None


def test_magic_comments_bypass_helpers():
    import _magic_comments as mc

    assert mc.bypasses_all(mc.parse("# rc:skip")) is True
    assert mc.bypasses_all(mc.parse("// rc:skip-mock")) is False
    assert mc.bypasses(mc.parse("# rc:skip-lang"), "lang") is True
    assert mc.bypasses(mc.parse("# rc:skip-lang"), "mock") is False
    assert mc.bypasses(mc.parse("# rc:override fix"), "lang") is True


def test_kill_switches_bypass_next_consumed():
    import importlib
    with tempfile.TemporaryDirectory() as td:
        os.environ["RC_STATE_DIR"] = td
        os.environ.pop("RC_BYPASS_NEXT", None)
        import _kill_switches as ks
        importlib.reload(ks)
        assert ks.consume_bypass_next() is False
        ks.set_bypass_next(True)
        assert ks.consume_bypass_next() is True
        assert ks.consume_bypass_next() is False


def test_kill_switches_env_fallback():
    import importlib
    with tempfile.TemporaryDirectory() as td:
        os.environ["RC_STATE_DIR"] = td
        os.environ["RC_BYPASS_NEXT"] = "1"
        import _kill_switches as ks
        importlib.reload(ks)
        try:
            assert ks.consume_bypass_next() is True
        finally:
            os.environ.pop("RC_BYPASS_NEXT", None)


def test_kill_switches_skip_files():
    import importlib
    with tempfile.TemporaryDirectory() as td:
        os.environ["RC_STATE_DIR"] = td
        import _kill_switches as ks
        importlib.reload(ks)
        ks.add_skip_file("/foo/bar.py")
        assert ks.is_file_skipped("/foo/bar.py") is True
        assert ks.is_file_skipped("/foo/other.py") is False
        ks.remove_skip_file("/foo/bar.py")
        assert ks.is_file_skipped("/foo/bar.py") is False


def test_audit_log_decision_id_present():
    import audit_log
    e = audit_log.new_event(tool_name="Edit", decision="allowed")
    assert "decision_id" in e
    assert len(e["decision_id"]) == 12
    assert e["decision"] == "allowed"


def test_audit_rotation_no_op_on_missing_dir():
    import _audit_rotation
    _audit_rotation.rotate("/tmp/_rc_definitely_missing_dir", "2026-05-06")


def test_magic_comment_anchored_to_line_start():
    """Reviewer G1: directive in string literal / trailing comment must NOT match."""
    import _magic_comments as mc

    # Trailing on code — must NOT match
    assert mc.parse('foo()  # rc:skip') is None
    # Inside a string literal — must NOT match
    assert mc.parse('x = "# rc:skip"') is None
    assert mc.parse("'''# rc:skip'''") is None
    # Indented comment on its own line — IS valid
    d = mc.parse("    # rc:skip")
    assert d is not None and d.name == "skip"


def test_kill_switches_disable_until_utc():
    """Reviewer G5: disable_until must parse as UTC, not local."""
    import importlib
    import _kill_switches as ks
    with tempfile.TemporaryDirectory() as td:
        os.environ["RC_STATE_DIR"] = td
        importlib.reload(ks)
        # Past timestamp → not disabled
        ks.set_disable_until("2000-01-01T00:00:00Z")
        assert ks.is_disabled_globally() is False
        # Far-future timestamp → disabled
        ks.set_disable_until("2099-01-01T00:00:00Z")
        assert ks.is_disabled_globally() is True


def test_audit_rotation_once_per_day_sentinel():
    """Reviewer G7: rotation must skip if today's sentinel exists."""
    import _audit_rotation
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sentinel = root / ".last_rotate_2026-05-06"
        sentinel.touch()
        before_mtime = sentinel.stat().st_mtime
        _audit_rotation.rotate(str(root), "2026-05-06")
        # Sentinel mtime unchanged → rotate short-circuited
        assert sentinel.stat().st_mtime == before_mtime
        # Different day → sentinel is created
        _audit_rotation.rotate(str(root), "2026-05-07")
        assert (root / ".last_rotate_2026-05-07").exists()


def test_pre_bash_guard_blocks_agent_self_arming():
    """P0 vuln: agent must not arm rc bypass-next via shell."""
    import subprocess, json as _json
    payload = lambda cmd: _json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    repo = _ROOT
    # Scrub override env vars so the test sees the same posture as a normal session.
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in {"RC_ALLOW_GUARD_EDIT", "RC_ALLOW_SUBAGENT_GUARD_EDIT", "RC_BYPASS_NEXT"}}
    cases = [
        "rc bypass-next",
        "python3 -m src.rc_cli bypass-next",
        "RC_BYPASS_NEXT=1 echo hello",
        "echo '{}' > ~/.local/state/reasoning-core/kill_switches.json",
    ]
    for cmd in cases:
        out = subprocess.run(
            ["python3", str(repo / "src/hooks/pre_bash_guard.py")],
            input=payload(cmd),
            capture_output=True, text=True,
            env=clean_env,
        )
        assert out.returncode == 2, f"expected block for {cmd!r}, got rc={out.returncode}"


def test_shadow_mode_emits_shadow_blocked_decision():
    """RC_SHADOW_MODE=1 must convert blocked→shadow_blocked in pre_edit_guard."""
    # We don't run the real pipeline here; we just confirm the decision string
    # is wired and the env var lookup exists in the source.
    src = (_ROOT / "src/hooks/pre_edit_guard.py").read_text()
    assert "RC_SHADOW_MODE" in src
    assert "shadow_blocked" in src


def test_lang_lock_prefix_exemption_top_level_only():
    """Reviewer fix: substring match silently bypassed src/scripts/, lib/tools/."""
    import sys
    sys.path.insert(0, str(_ROOT / "src" / "hooks"))
    import _session_manifest as sm
    mani = {"declared_language": "csharp", "lang_allow": []}
    # Top-level exemption — allowed
    assert sm.is_path_allowed(mani, "scripts/migrate.py") is True
    assert sm.is_path_allowed(mani, "tools/cli.py") is True
    # Substring at depth — NOT allowed (was the security hole)
    assert sm.is_path_allowed(mani, "src/scripts/evil.py") is False
    assert sm.is_path_allowed(mani, "lib/tools/x.py") is False
    # Same-language file regardless of path — allowed
    assert sm.is_path_allowed(mani, "src/auth/login.cs") is True


def test_lang_lock_max_files_cap():
    """Reviewer fix: detect_initial_language must early-exit on huge worktrees."""
    import sys, tempfile
    sys.path.insert(0, str(_ROOT / "src" / "hooks"))
    import _session_manifest as sm
    with tempfile.TemporaryDirectory() as td:
        # Create 100 .py files
        for i in range(100):
            (Path(td) / f"f{i}.py").write_text("# x\n")
        # Cap at 10 → counts should reflect early exit
        declared, counts = sm.detect_initial_language(td, max_files=10)
        assert declared == "python"
        assert sum(counts.values()) <= 10


def test_record_shadow_block_separate_namespace():
    """Reviewer fix: shadow blocks must NOT poison is_retry_after_block markers."""
    import importlib
    with tempfile.TemporaryDirectory() as td:
        # Point both retry-marker file and shadow-marker dir into td
        os.environ["HOME"] = td
        os.environ["RC_AUDIT_ROOT"] = td
        import audit_log as al
        importlib.reload(al)
        al.record_shadow_block("/foo/bar.py")
        # is_retry_after_block reads the MAIN markers, not shadow ones
        assert al.is_retry_after_block("/foo/bar.py") is False
        # And the shadow marker file lives separately under HOME state dir
        shadow_path = Path(td) / ".local/state/reasoning-core/shadow_markers.json"
        assert shadow_path.exists()
