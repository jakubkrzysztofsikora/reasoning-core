"""RC-SCORE-01: Condition-number floor prevents hair-trigger detector.

Verifies that near-degenerate corpora (all rows nearly identical + jitter)
are refused (threshold = +inf) while healthy corpora still arm.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = str(REPO_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


class TestConditionNumberFloor:
    """RC-SCORE-01: eigenvalue ratio floor prevents near-degenerate arming."""

    def test_near_degenerate_jitter_1e12_inert(self):
        """JIT=1e-12 should be inert (threshold = +inf)."""
        from src import s2_core
        
        rng = np.random.default_rng(7)
        base = rng.normal(size=(768,)).astype(np.float32)
        N = 5
        rows = {
            f"f{i}.py": torch.from_numpy(
                (base + rng.normal(scale=1e-12, size=(768,))).astype(np.float32)
            )
            for i in range(N)
        }
        
        baselines = dict(rows)
        s2_core._maybe_promote_session_to_corpus("__test__", baselines)
        
        thr = baselines.get("__mahal_threshold__", 0.0)
        assert thr == float("inf"), f"Expected inert (inf), got thr={thr}"

    def test_near_degenerate_jitter_1e9_inert(self):
        """JIT=1e-9 should be inert (threshold = +inf)."""
        from src import s2_core
        
        rng = np.random.default_rng(7)
        base = rng.normal(size=(768,)).astype(np.float32)
        N = 5
        rows = {
            f"f{i}.py": torch.from_numpy(
                (base + rng.normal(scale=1e-9, size=(768,))).astype(np.float32)
            )
            for i in range(N)
        }
        
        baselines = dict(rows)
        s2_core._maybe_promote_session_to_corpus("__test__", baselines)
        
        thr = baselines.get("__mahal_threshold__", 0.0)
        assert thr == float("inf"), f"Expected inert (inf), got thr={thr}"

    def test_near_degenerate_jitter_1e3_inert(self):
        """JIT=1e-3 should be inert (threshold = +inf)."""
        from src import s2_core
        
        rng = np.random.default_rng(7)
        base = rng.normal(size=(768,)).astype(np.float32)
        N = 5
        rows = {
            f"f{i}.py": torch.from_numpy(
                (base + rng.normal(scale=1e-3, size=(768,))).astype(np.float32)
            )
            for i in range(N)
        }
        
        baselines = dict(rows)
        s2_core._maybe_promote_session_to_corpus("__test__", baselines)
        
        thr = baselines.get("__mahal_threshold__", 0.0)
        assert thr == float("inf"), f"Expected inert (inf), got thr={thr}"

    def test_healthy_corpus_arms(self):
        """Healthy corpus (10 distinct unit-variance rows) should still arm."""
        from src import s2_core
        
        rng = np.random.default_rng(42)
        N = 10
        rows = {
            f"f{i}.py": torch.from_numpy(
                rng.normal(size=(768,)).astype(np.float32)
            )
            for i in range(N)
        }
        
        baselines = dict(rows)
        s2_core._maybe_promote_session_to_corpus("__test__", baselines)
        
        thr = baselines.get("__mahal_threshold__", 0.0)
        assert thr != float("inf"), f"Healthy corpus should arm, got thr={thr}"
        assert np.isfinite(thr), f"Threshold should be finite, got {thr}"
        assert thr > 0, f"Threshold should be positive, got {thr}"

    def test_exact_degenerate_still_inert(self):
        """Exact degenerate (identical rows) should remain inert."""
        from src import s2_core
        
        rng = np.random.default_rng(7)
        base = rng.normal(size=(768,)).astype(np.float32)
        N = 5
        # All rows identical (JIT=0)
        rows = {
            f"f{i}.py": torch.from_numpy(base.copy())
            for i in range(N)
        }
        
        baselines = dict(rows)
        s2_core._maybe_promote_session_to_corpus("__test__", baselines)
        
        thr = baselines.get("__mahal_threshold__", 0.0)
        assert thr == float("inf"), f"Exact degenerate should be inert, got thr={thr}"
