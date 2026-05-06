"""Smoke tests for eval/qwen_grounding_eval.py (math only, no sidecar)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "eval"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

spec = importlib.util.spec_from_file_location(
    "qwen_grounding_eval", EVAL_DIR / "qwen_grounding_eval.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def test_kappa_perfect_agreement():
    a = [0, 1, 0, 1, 1, 0]
    assert abs(mod._cohens_kappa(a, a) - 1.0) < 1e-9


def test_kappa_chance_agreement():
    # 50/50 random labels with 50% agreement → kappa near 0
    a = [0, 1, 0, 1, 0, 1, 0, 1]
    b = [0, 0, 1, 1, 0, 0, 1, 1]
    k = mod._cohens_kappa(a, b)
    assert -0.1 < k < 0.1


def test_kappa_full_disagreement():
    a = [0, 1, 0, 1]
    b = [1, 0, 1, 0]
    assert mod._cohens_kappa(a, b) < 0


def test_bootstrap_ci_brackets_estimate():
    a = [1] * 10 + [0] * 10
    b = [1] * 9 + [0] + [0] * 9 + [1]  # near-perfect
    lo, hi = mod._bootstrap_ci(a, b, n_boot=200, seed=1)
    point = mod._cohens_kappa(a, b)
    assert lo <= point <= hi
