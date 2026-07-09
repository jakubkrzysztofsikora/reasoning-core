"""Tests for src/hooks/pre_plan_guard.py.

Drives the hook via subprocess with a synthetic Write payload on stdin.
Avoids touching the SSM backbone — the hook treats any embed failure as a
``novelty_unavailable`` info advisory, so tests work in environments without
torch/transformers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_PATH = os.path.join(REPO_ROOT, "src", "hooks", "pre_plan_guard.py")
HOOKS_DIR = os.path.join(REPO_ROOT, "src", "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import pre_plan_guard  # type: ignore  # noqa: E402


def _run(payload, *, env_extra=None, timeout=20):
    env = os.environ.copy()
    env.pop("RC_PLAN_BLOCK", None)
    env.pop("RC_EMBEDDER", None)
    env["RC_EMBEDDER"] = "random-mamba"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _plan_payload(content: str, *, path: str = "thoughts/shared/plans/foo.plan.md"):
    return {
        "tool_name": "Write",
        "tool_input": {
            "file_path": path,
            "content": content,
        },
    }


# --- path filter -----------------------------------------------------------

def test_non_plan_path_exits_zero_silently():
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "/Users/x/repo/src/foo.py",
            "content": "# huge file\n" + ("x = 1\n" * 5000),
        },
    }
    r = _run(payload)
    assert r.returncode == 0
    assert r.stderr.strip() == ""


def test_non_write_tool_exits_zero():
    payload = {"tool_name": "Edit", "tool_input": {"file_path": "PLAN.md", "new_string": "x"}}
    r = _run(payload)
    assert r.returncode == 0


# --- heuristics ------------------------------------------------------------

def test_balanced_plan_no_warnings():
    content = (
        "# Plan: small refactor\n\n"
        "## Phase 1: introduce helper\n\n"
        "- `src/util/foo.py` — ~80 LOC\n\n"
        "## Phase 2: tests\n\n"
        "- `tests/test_foo.py` — ~60 LOC\n"
    )
    r = _run(_plan_payload(content))
    # Allow novelty_unavailable info-level warning; should never be 'block'.
    assert r.returncode == 0
    if r.stderr.strip():
        # Only info-severity warnings allowed for balanced plan.
        for line in r.stderr.strip().splitlines():
            if line.startswith("{"):
                rec = json.loads(line)
                assert rec.get("severity") in {"info"}


def test_per_file_loc_over_budget_warns():
    content = (
        "# Plan: huge file\n\n"
        "## Phase 1\n\n"
        "- `src/big.py` — ~600 LOC\n"
    )
    r = _run(_plan_payload(content))
    assert r.returncode == 0
    parsed = [
        json.loads(l) for l in r.stderr.splitlines() if l.startswith("{")
    ]
    assert any(p.get("rule_id") == "per_file_loc" for p in parsed)


def test_per_file_loc_block_severity_exits_two_under_env():
    # Fixture must exceed the block budget (default 1200, strict `>`), so use
    # 1500. Previously pinned at 1200 which silently stopped tripping the gate
    # once the budget was raised 800→1200.
    content = (
        "# Plan: enormous file\n\n"
        "## Phase 1\n\n"
        "- `src/monster.py` — ~1500 LOC\n"
    )
    r = _run(_plan_payload(content), env_extra={"RC_PLAN_BLOCK": "1"})
    assert r.returncode == 2
    parsed = [
        json.loads(l) for l in r.stderr.splitlines() if l.startswith("{")
    ]
    assert any(p.get("severity") == "block" for p in parsed)


def test_per_file_loc_block_default_exits_zero():
    content = (
        "# Plan\n\n"
        "## Phase 1\n\n"
        "- `src/monster.py` — ~1500 LOC\n"
    )
    r = _run(_plan_payload(content))
    # Default (RC_PLAN_BLOCK unset) -> exit 0 even with block-severity warning.
    assert r.returncode == 0


def test_phase_to_file_ratio_warns():
    content = (
        "# Plan\n\n"
        "## Phase 1\n\n"
        "## Phase 2\n\n"
        "## Phase 3\n\n"
        "## Phase 4\n\n"
        "## Phase 5\n\n"
        "- `src/a.py` — ~50 LOC\n"
        "- `src/b.py` — ~50 LOC\n"
    )
    r = _run(_plan_payload(content))
    assert r.returncode == 0
    parsed = [
        json.loads(l) for l in r.stderr.splitlines() if l.startswith("{")
    ]
    assert any(p.get("rule_id") == "phase_file_ratio" for p in parsed)


def test_boundary_crossing_prose_warns():
    content = (
        "# Plan\n\n"
        "We will refactor across every module in one file that touches auth.\n\n"
        "## Phase 1\n\n"
        "- `src/a.py` — ~40 LOC\n"
    )
    r = _run(_plan_payload(content))
    assert r.returncode == 0
    parsed = [
        json.loads(l) for l in r.stderr.splitlines() if l.startswith("{")
    ]
    assert any(p.get("rule_id") == "boundary_crossing_prose" for p in parsed)


def test_test_file_loc_uses_smaller_budget():
    """Test files have the 200-LOC budget (vs 400 for non-test)."""
    content = (
        "# Plan\n\n"
        "## Phase 1\n\n"
        "- `tests/test_huge.py` — ~250 LOC\n"
    )
    r = _run(_plan_payload(content))
    assert r.returncode == 0
    parsed = [
        json.loads(l) for l in r.stderr.splitlines() if l.startswith("{")
    ]
    assert any(p.get("rule_id") == "per_file_loc" for p in parsed)


def test_plan_md_path_recognized():
    content = "# small plan\n\n- `src/x.py` — ~10 LOC\n"
    payload = _plan_payload(content, path="/some/repo/PLAN.md")
    r = _run(payload)
    assert r.returncode == 0


def test_malformed_payload_exits_zero():
    env = os.environ.copy()
    r = subprocess.run(
        [sys.executable, HOOK_PATH],
        input="not json{",
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert r.returncode == 0


# --- _muzzle_stderr fd hygiene (adversarial-review regressions) ------------

def test_muzzle_swallows_and_restores_fd2():
    """fd-level redirect hides writes inside the context, restores fd 2 after."""
    r, w = os.pipe()
    saved = os.dup(2)
    os.dup2(w, 2)
    os.close(w)
    try:
        with pre_plan_guard._muzzle_stderr():
            os.write(2, b"SHOULD_BE_SWALLOWED\n")
        os.write(2, b"AFTER_RESTORE\n")
    finally:
        os.dup2(saved, 2)
        os.close(saved)
    captured = os.read(r, 4096).decode()
    os.close(r)
    assert "SHOULD_BE_SWALLOWED" not in captured
    assert "AFTER_RESTORE" in captured


def test_muzzle_no_fd_leak_across_invocations():
    """Repeated muzzle entries must not leak file descriptors."""
    probe_before = os.dup(0)
    os.close(probe_before)
    for _ in range(50):
        with pre_plan_guard._muzzle_stderr():
            os.write(2, b"noise\n")
    probe_after = os.dup(0)
    os.close(probe_after)
    # Same lowest-free fd number => no descriptors leaked.
    assert probe_after == probe_before


def test_muzzle_devnull_open_failure_falls_through_without_leak(monkeypatch):
    """If /dev/null can't be opened, run un-muzzled rather than leak saved_fd."""
    real_open = os.open

    def boom(path, *a, **k):
        if path == os.devnull:
            raise OSError("simulated /dev/null open failure")
        return real_open(path, *a, **k)

    probe_before = os.dup(0)
    os.close(probe_before)
    monkeypatch.setattr(os, "open", boom)
    with pre_plan_guard._muzzle_stderr():
        pass  # body runs un-muzzled; must not raise
    monkeypatch.undo()
    probe_after = os.dup(0)
    os.close(probe_after)
    assert probe_after == probe_before


def test_muzzle_disabled_by_env(monkeypatch):
    monkeypatch.setenv("RC_PLAN_NOVELTY_MUZZLE", "0")
    r, w = os.pipe()
    saved = os.dup(2)
    os.dup2(w, 2)
    os.close(w)
    try:
        with pre_plan_guard._muzzle_stderr():
            os.write(2, b"VISIBLE_WHEN_DISABLED\n")
    finally:
        os.dup2(saved, 2)
        os.close(saved)
    captured = os.read(r, 4096).decode()
    os.close(r)
    assert "VISIBLE_WHEN_DISABLED" in captured


# --- adaptive novelty math (adversarial-review regressions) ----------------

def _patch_novelty(monkeypatch, peer_vecs, plan_key, plan_vec):
    """Wire _embed_safe/_gather_recent_plans to deterministic vectors.

    Disables the persistent embedding cache so a sha1 collision with a real
    cached vector can't bypass the patched _embed_safe, and patches
    _embed_cached to route peers through the same in-memory table.
    """
    table = dict(peer_vecs)
    table[plan_key] = plan_vec

    def fake_embed(t):
        return (table[t], None) if t in table else (None, "novelty_unavailable")

    monkeypatch.setenv("RC_PLAN_NOVELTY_CACHE", "0")
    monkeypatch.setattr(pre_plan_guard, "_embed_safe", fake_embed)
    monkeypatch.setattr(pre_plan_guard, "_embed_cached", fake_embed)
    monkeypatch.setattr(
        pre_plan_guard, "_gather_recent_plans",
        lambda pd, limit=8: list(peer_vecs.keys()),
    )


def test_novelty_nan_plan_does_not_fire(monkeypatch):
    torch = pytest.importorskip("torch")
    torch.manual_seed(7)
    peers = {f"p{i}": torch.randn(64) * 5 for i in range(5)}
    nan_vec = torch.full((64,), float("nan"))
    _patch_novelty(monkeypatch, peers, "__nan__", nan_vec)
    out = pre_plan_guard._check_novelty("__nan__", "/x")
    assert not any(w.get("rule_id") == "novelty_drift" for w in out)


def test_novelty_inf_plan_does_not_emit_inf_banner(monkeypatch):
    torch = pytest.importorskip("torch")
    torch.manual_seed(7)
    peers = {f"p{i}": torch.randn(64) * 5 for i in range(5)}
    inf_vec = torch.full((64,), float("inf"))
    _patch_novelty(monkeypatch, peers, "__inf__", inf_vec)
    out = pre_plan_guard._check_novelty("__inf__", "/x")
    assert not any(w.get("rule_id") == "novelty_drift" for w in out)


def test_novelty_duplicate_peers_fires_on_far_plan(monkeypatch):
    """Near-identical peers + a far-out plan still fires via the floored
    denominator (no special duplicate branch needed)."""
    torch = pytest.importorskip("torch")
    torch.manual_seed(7)
    base = torch.randn(64) * 3
    peers = {f"d{i}": base.clone() for i in range(5)}
    far = base + torch.randn(64) * 50
    _patch_novelty(monkeypatch, peers, "__far__", far)
    out = pre_plan_guard._check_novelty("__far__", "/x")
    assert any(w.get("rule_id") == "novelty_drift" for w in out)


def test_novelty_duplicate_peers_quiet_on_near_plan(monkeypatch):
    """Identical peers + a near-identical plan must NOT fire — the denominator
    floor stops the collapsed-spread ratio from exploding."""
    torch = pytest.importorskip("torch")
    torch.manual_seed(7)
    base = torch.randn(64) * 3
    peers = {f"d{i}": base.clone() for i in range(5)}
    near = base + torch.randn(64) * 0.001
    _patch_novelty(monkeypatch, peers, "__near__", near)
    out = pre_plan_guard._check_novelty("__near__", "/x")
    assert not any(w.get("rule_id") == "novelty_drift" for w in out)


def test_novelty_tight_cluster_quiet_on_trivial_change(monkeypatch):
    """REGRESSION (code-review finding): near-duplicate peers with median
    spread ~1e-3 (above the old 1e-6 floor) plus a trivially-changed plan must
    NOT fire. Before the denominator floor this produced ratio ~270× and a
    false positive — the exact bug the recalibration was meant to remove."""
    torch = pytest.importorskip("torch")
    torch.manual_seed(0)
    base = torch.ones(64) * 3.0
    peers = {f"p{i}": base + torch.randn(64) * 1e-4 for i in range(5)}
    trivial = base + torch.randn(64) * 0.02
    _patch_novelty(monkeypatch, peers, "__trivial__", trivial)
    out = pre_plan_guard._check_novelty("__trivial__", "/x")
    assert not any(w.get("rule_id") == "novelty_drift" for w in out)


def test_novelty_silent_below_three_peers(monkeypatch):
    torch = pytest.importorskip("torch")
    torch.manual_seed(7)
    peers = {f"p{i}": torch.randn(64) * 5 for i in range(2)}
    plan = torch.randn(64) * 50
    _patch_novelty(monkeypatch, peers, "__p__", plan)
    out = pre_plan_guard._check_novelty("__p__", "/x")
    assert not any(w.get("rule_id") == "novelty_drift" for w in out)


# --- embedding cache (efficiency fix) --------------------------------------

def test_embed_cache_avoids_recompute(monkeypatch, tmp_path):
    """Second embed of the same text hits the cache and does NOT re-run the
    backbone — the whole point of the content-addressed peer cache."""
    torch = pytest.importorskip("torch")
    calls = {"n": 0}

    def counting_embed(text):
        calls["n"] += 1
        return torch.ones(8), None

    # Fresh disk cache + memo so the test is isolated.
    monkeypatch.setenv("RC_PLAN_NOVELTY_CACHE", "1")
    monkeypatch.setattr(pre_plan_guard, "_embed_safe", counting_embed)
    monkeypatch.setattr(
        pre_plan_guard, "_embed_cache_path",
        lambda: tmp_path / "embed_cache.npz",
    )
    monkeypatch.setattr(pre_plan_guard, "_EMBED_MEMO", {}, raising=False)

    v1, e1 = pre_plan_guard._embed_cached("some plan text")
    v2, e2 = pre_plan_guard._embed_cached("some plan text")
    assert e1 is None and e2 is None
    assert calls["n"] == 1, "backbone should run once, then cache"

    # New process (cold memo) still hits the persisted disk cache.
    monkeypatch.setattr(pre_plan_guard, "_EMBED_MEMO", {}, raising=False)
    v3, e3 = pre_plan_guard._embed_cached("some plan text")
    assert e3 is None and calls["n"] == 1, "disk cache should serve cold memo"


def test_embed_cache_disabled_by_env(monkeypatch, tmp_path):
    torch = pytest.importorskip("torch")
    calls = {"n": 0}

    def counting_embed(text):
        calls["n"] += 1
        return torch.ones(8), None

    monkeypatch.setenv("RC_PLAN_NOVELTY_CACHE", "0")
    monkeypatch.setattr(pre_plan_guard, "_embed_safe", counting_embed)
    pre_plan_guard._embed_cached("x")
    pre_plan_guard._embed_cached("x")
    assert calls["n"] == 2, "cache disabled → embed every time"
