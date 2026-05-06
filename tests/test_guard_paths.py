"""Tests for src.hooks._guard_paths (Phase 0 commit 1)."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "src" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))


def _fresh_module(monkeypatch: pytest.MonkeyPatch):
    """Reload `_guard_paths` after env mutation so module-level discovery re-runs."""
    sys.modules.pop("_guard_paths", None)
    import _guard_paths  # type: ignore

    return _guard_paths


def test_discover_includes_known_hooks(tmp_path: Path) -> None:
    # Build a fake project tree.
    (tmp_path / "src" / "hooks").mkdir(parents=True)
    (tmp_path / "src" / "hooks" / "pre_edit_guard.py").write_text("# x")
    (tmp_path / "src" / "hooks" / "audit_log.py").write_text("# x")
    (tmp_path / "src" / "hooks" / "__init__.py").write_text("")
    # Filtered: name starts with `_` and has `test`.
    (tmp_path / "src" / "hooks" / "_helpers_for_test.py").write_text("# x")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "start-sidecar.sh").write_text("#!/bin/sh")
    (tmp_path / "scripts" / "start-gen-sidecar.sh").write_text("#!/bin/sh")
    # Should NOT be discovered (does not match start-*sidecar.sh).
    (tmp_path / "scripts" / "deploy.sh").write_text("#!/bin/sh")

    sys.modules.pop("_guard_paths", None)
    import _guard_paths  # type: ignore

    paths = _guard_paths.discover(project_root=tmp_path)
    assert "/src/hooks/pre_edit_guard.py" in paths
    assert "/src/hooks/audit_log.py" in paths
    assert "/scripts/start-sidecar.sh" in paths
    assert "/scripts/start-gen-sidecar.sh" in paths
    # Settings + explicit allowlist still present.
    assert "/.claude/settings.json" in paths
    assert "/src/sidecar_supervisor.py" in paths
    assert "/src/calibration.py" in paths
    # Filtered.
    assert not any("__init__.py" in p for p in paths)
    assert not any("_helpers_for_test.py" in p for p in paths)
    assert "/scripts/deploy.sh" not in paths


def test_is_guarded_normalizes_relative(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A relative path ending in a guarded substring must still match."""
    monkeypatch.delenv("RC_GUARD_PATHS_OVERRIDE", raising=False)
    monkeypatch.delenv("RC_GUARD_PATHS_EXTRA", raising=False)
    mod = _fresh_module(monkeypatch)

    # An abspath of `pre_edit_guard.py` lands at cwd which doesn't match.
    # But `src/hooks/pre_edit_guard.py` abspath'd will end in the substring.
    monkeypatch.chdir(tmp_path)
    rel = "src/hooks/pre_edit_guard.py"
    assert mod.is_guarded(rel) is True


def test_is_guarded_empty_path() -> None:
    sys.modules.pop("_guard_paths", None)
    import _guard_paths  # type: ignore

    assert _guard_paths.is_guarded("") is False


def test_is_guarded_unrelated_file(tmp_path: Path) -> None:
    sys.modules.pop("_guard_paths", None)
    import _guard_paths  # type: ignore

    assert _guard_paths.is_guarded(str(tmp_path / "random_user_file.txt")) is False


def test_is_override_active(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.modules.pop("_guard_paths", None)
    import _guard_paths  # type: ignore

    monkeypatch.delenv("RC_ALLOW_GUARD_EDIT", raising=False)
    assert _guard_paths.is_override_active() is False
    monkeypatch.setenv("RC_ALLOW_GUARD_EDIT", "1")
    assert _guard_paths.is_override_active() is True
    monkeypatch.setenv("RC_ALLOW_GUARD_EDIT", "0")
    assert _guard_paths.is_override_active() is False


def test_override_replaces_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RC_GUARD_PATHS_OVERRIDE", "/foo/bar.py,/baz/qux.py")
    monkeypatch.delenv("RC_GUARD_PATHS_EXTRA", raising=False)
    mod = _fresh_module(monkeypatch)
    assert mod.GUARDED_PATHS == ("/foo/bar.py", "/baz/qux.py")
    assert mod.is_guarded("/some/path/foo/bar.py") is True
    assert mod.is_guarded("/src/hooks/pre_edit_guard.py") is False


def test_extra_appends_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RC_GUARD_PATHS_OVERRIDE", raising=False)
    monkeypatch.setenv("RC_GUARD_PATHS_EXTRA", "/extra/locked.py")
    mod = _fresh_module(monkeypatch)
    assert "/extra/locked.py" in mod.GUARDED_PATHS
    # Standard entries still present.
    assert "/.claude/settings.json" in mod.GUARDED_PATHS


def test_module_level_paths_cover_existing_guarded_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: the new auto-discovery must cover every entry from the
    pre-Phase-0 hard-coded GUARDED_PATHS tuple. If a new hook lands without an
    explicit allowlist entry, this test will keep passing — but if the
    allowlist regresses, the original list of entries protects us."""
    monkeypatch.delenv("RC_GUARD_PATHS_OVERRIDE", raising=False)
    monkeypatch.delenv("RC_GUARD_PATHS_EXTRA", raising=False)
    mod = _fresh_module(monkeypatch)
    must_cover = {
        "/.claude/settings.json",
        "/.claude/settings.local.json",
        "/src/hooks/pre_edit_guard.py",
        "/src/hooks/pre_bash_guard.py",
        "/src/hooks/post_bash_revive.py",
        "/src/hooks/pre_plan_guard.py",
        "/src/hooks/pre_task_guard.py",
        "/src/hooks/audit_log.py",
        "/src/hooks/_calibration_gate.py",
        "/src/hooks/_kill_switches.py",
        "/src/hooks/_magic_comments.py",
        "/src/hooks/_session_manifest.py",
        "/src/hooks/_shadow_mode.py",
        "/src/hooks/_mock_detector.py",
        "/src/hooks/_plan_quality.py",
        "/src/hooks/_audit_rotation.py",
        "/src/hooks/_ood_detector.py",
        "/src/hooks/pre_compact_guard.py",
        "/src/hooks/session_start_manifest.py",
        "/src/hooks/session_resume_inject.py",
        "/src/hooks/post_batch_lang_audit.py",
        "/src/sidecar_supervisor.py",
        "/src/_supervisor_env.py",
        "/src/_supervisor_broker.py",
        "/src/_supervisor_recalibrate.py",
        "/src/gen_client.py",
        "/src/calibration.py",
        "/src/s2_core.py",
        "/src/grammars.py",
        "/src/ssm_backbone.py",
        "/src/mcp_reasoner.py",
        "/src/rc_cli.py",
        "/scripts/start-sidecar.sh",
        "/scripts/start-gen-sidecar.sh",
    }
    missing = must_cover - set(mod.GUARDED_PATHS)
    assert not missing, f"GUARDED_PATHS regressed; missing: {missing}"
