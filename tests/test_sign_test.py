"""Tests for eval.stats.sign_test — iter-2 falsifiable-goal binomial."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from eval.stats import sign_test  # noqa: E402


def test_8_of_8_matches_plan_p_value():
    # Plan claims 8/8 → p=0.0039 (one-sided binomial @ P=0.5).
    p = sign_test(8, 8, alternative="greater")
    assert abs(p - (1.0 / 256.0)) < 1e-9
    assert abs(p - 0.00390625) < 1e-9


def test_7_of_8_matches_plan_p_value():
    # Plan claims 7/8 → p=0.035.
    p = sign_test(7, 8, alternative="greater")
    # P(X>=7 | n=8, p=0.5) = (8 + 1) / 256 = 9/256 ≈ 0.0352
    assert abs(p - (9.0 / 256.0)) < 1e-9
    assert 0.034 < p < 0.036


def test_6_of_8_above_alpha():
    # 6/8 should be above α=0.05 — failure of the relaxed criterion.
    p = sign_test(6, 8, alternative="greater")
    assert p > 0.05


def test_4_of_8_is_chance():
    # 4/8 = pure chance under null.
    p = sign_test(4, 8, alternative="greater")
    assert 0.5 <= p <= 0.7  # P(X>=4) = 163/256 ≈ 0.637


def test_two_sided_8_of_8():
    p_g = sign_test(8, 8, alternative="greater")
    p_2 = sign_test(8, 8, alternative="two-sided")
    # Two-sided ≈ 2× one-sided when extreme.
    assert abs(p_2 - 2 * p_g) < 1e-9


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        sign_test(-1, 8)
    with pytest.raises(ValueError):
        sign_test(9, 8)
    with pytest.raises(ValueError):
        sign_test(0, 0)
    with pytest.raises(ValueError):
        sign_test(4, 8, alternative="bogus")
