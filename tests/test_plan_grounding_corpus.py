"""Standalone benchmark harness for the plan-grounding gate (iter-3 Phase 4).

Goal: make the plan-grounding lever publishable as a coherence-loop primitive
independent of any A-vs-B eval verdict (newsletter reviewer requirement).

Method:
  1. ``tests/fixtures/plans/<task>/PLAN.md`` — frozen real iter-2 plans
     (P0, T2, T5, T8, T9) drawn from the v3 sweep.
  2. ``tests/fixtures/plans/<task>/edits.jsonl`` — hand-labeled edit attempts
     with ground truth ``expected_in_plan`` boolean.
  3. For each fixture, this test runs ``_dispatch.gate_plan_grounding`` in
     warn mode and compares its decision against the ground truth.
  4. Asserts precision ≥ 0.90 and recall ≥ 0.80 across the corpus.

CI failure on this test means the path-extraction regex regressed against
real-world plan vocabulary — surface immediately, do not require an
external eval to detect.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src" / "hooks"))

import _dispatch  # type: ignore  # noqa: E402

_FIXTURE_ROOT = _ROOT / "tests" / "fixtures" / "plans"

# Minimum corpus size — plan §Phase 4 requires ≥5 fixtures.
_MIN_FIXTURES = 5

# Precision/recall floors — plan §Phase 4 acceptance criterion.
_PRECISION_FLOOR = 0.90
_RECALL_FLOOR = 0.80


def _iter_fixtures():
    for fix_dir in sorted(_FIXTURE_ROOT.iterdir()):
        if not fix_dir.is_dir():
            continue
        plan = fix_dir / "PLAN.md"
        edits = fix_dir / "edits.jsonl"
        if not plan.exists() or not edits.exists():
            continue
        yield fix_dir.name, fix_dir, edits


def test_corpus_size_meets_floor():
    found = sum(1 for _ in _iter_fixtures())
    assert found >= _MIN_FIXTURES, (
        f"corpus has {found} fixtures, plan §Phase 4 requires ≥{_MIN_FIXTURES}"
    )


def test_corpus_precision_recall(monkeypatch):
    """Aggregate precision/recall across all fixtures.

    For each labeled edit:
      - gate ``action == "pass"`` (in-plan) AND ``expected_in_plan=True``  → TP
      - gate ``action == "pass"`` AND ``expected_in_plan=False``           → FP
      - gate fires (stderr_only/exit_block) AND ``expected_in_plan=False`` → TN
      - gate fires AND ``expected_in_plan=True``                            → FN
    """
    monkeypatch.setenv("RC_PLAN_GROUNDING", "1")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    tp = fp = tn = fn = 0
    fixtures_seen = 0
    per_fixture: dict[str, dict[str, int]] = {}

    for name, fix_dir, edits_path in _iter_fixtures():
        fixtures_seen += 1
        monkeypatch.setenv("RC_RUN_DIR", str(fix_dir))
        local = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
        for line in edits_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            outcome = _dispatch.gate_plan_grounding(file_path=rec["file_path"])
            gate_says_in_plan = (outcome.action == "pass")
            truth = bool(rec["expected_in_plan"])
            if gate_says_in_plan and truth:
                tp += 1
                local["tp"] += 1
            elif gate_says_in_plan and not truth:
                fp += 1
                local["fp"] += 1
            elif (not gate_says_in_plan) and (not truth):
                tn += 1
                local["tn"] += 1
            else:
                fn += 1
                local["fn"] += 1
        per_fixture[name] = local

    assert fixtures_seen >= _MIN_FIXTURES, f"only {fixtures_seen} fixtures loaded"

    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)

    msg = (
        f"corpus tp={tp} fp={fp} tn={tn} fn={fn} "
        f"precision={precision:.3f} recall={recall:.3f}\n"
        f"per-fixture: {per_fixture}"
    )
    assert precision >= _PRECISION_FLOOR, f"precision {precision:.3f} < {_PRECISION_FLOOR}; {msg}"
    assert recall >= _RECALL_FLOOR, f"recall {recall:.3f} < {_RECALL_FLOOR}; {msg}"
