"""Tests for src/_supervisor_env.py — env allowlist for child processes."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "_supervisor_env", REPO_ROOT / "src" / "_supervisor_env.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def test_allowlist_strips_rc_allow_guard_edit(monkeypatch):
    monkeypatch.setenv("RC_ALLOW_GUARD_EDIT", "1")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = mod.build_child_env()
    assert "RC_ALLOW_GUARD_EDIT" not in env
    assert env["PATH"] == "/usr/bin"


def test_allowlist_strips_api_keys(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY",
              "GITHUB_TOKEN"):
        monkeypatch.setenv(k, "secret")
    env = mod.build_child_env()
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY",
              "GITHUB_TOKEN"):
        assert k not in env, f"{k} leaked into child env"


def test_allowlist_keeps_rc_gen_vars(monkeypatch):
    monkeypatch.setenv("RC_GEN_PORT", "8766")
    monkeypatch.setenv("RC_REASONER_BACKEND", "mlx")
    env = mod.build_child_env()
    assert env["RC_GEN_PORT"] == "8766"
    assert env["RC_REASONER_BACKEND"] == "mlx"


def test_extra_env_overrides(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    env = mod.build_child_env({"MLX_DEFAULT_DEVICE": "cpu"})
    assert env["MLX_DEFAULT_DEVICE"] == "cpu"
    assert env["PATH"] == "/usr/bin"


def test_gen_extra_env_pins_cpu_when_unset(monkeypatch):
    monkeypatch.delenv("MLX_DEFAULT_DEVICE", raising=False)
    assert mod.gen_extra_env()["MLX_DEFAULT_DEVICE"] == "cpu"


def test_gen_extra_env_respects_operator_override(monkeypatch):
    monkeypatch.setenv("MLX_DEFAULT_DEVICE", "gpu")
    assert "MLX_DEFAULT_DEVICE" not in mod.gen_extra_env()
