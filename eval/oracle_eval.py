"""Labeled oracle evaluation harness for reasoning-core.

Generates ~100 synthetic edits across five categories and measures how well
the Phase-1 contract compiler (``_plan_contract.Contract``) plus the Phase-2
execution oracles (``_oracles.run_oracles``) catch the bad ones.

Categories
----------
in_plan          : valid edits to files listed in the plan            (allowed)
scope_creep      : edits to files outside the plan                    (blocked)
import_violation : in-plan edits that add forbidden imports           (blocked)
syntax_error     : edits that introduce Python syntax errors          (blocked)
test_omission    : in-plan code edits missing required tests          (blocked;
                    currently a documented gap)

All edits are synthetic. No real-PR data is used; the report clearly labels
the corpus as synthetic-only.

Usage
-----
    python -m eval.oracle_eval
    python -m eval.oracle_eval --out eval/runs/oracle_eval.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
if str(REPO_ROOT / "src" / "hooks") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src" / "hooks"))

import _oracles  # type: ignore  # noqa: E402
import _plan_contract as pc  # type: ignore  # noqa: E402

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class EditCase:
    """One labeled edit in the eval corpus."""

    id: str
    category: str
    expected_block: bool
    file_path: str
    before_src: str
    after_src: str
    contract_dict: Optional[Dict[str, Any]] = None
    plan_text: Optional[str] = None
    description: str = ""


@dataclass
class CaseResult:
    """Outcome of running one EditCase through the contract + oracles."""

    case: EditCase
    blocked: bool
    reasons: List[str] = field(default_factory=list)
    violations: List[Dict[str, Any]] = field(default_factory=list)
    oracle_clean: bool = True
    oracle_annotations: List[Dict[str, Any]] = field(default_factory=list)
    elapsed_ms: float = 0.0


@dataclass
class EvalMetrics:
    """Aggregate precision/recall/FPR over the corpus."""

    n: int = 0
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    fpr: float = 0.0
    f1: float = 0.0
    per_category: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n": self.n,
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "precision": self.precision,
            "recall": self.recall,
            "fpr": self.fpr,
            "f1": self.f1,
            "per_category": self.per_category,
        }


# ---------------------------------------------------------------------------
# Base scenario
# ---------------------------------------------------------------------------


def base_contract_dict() -> Dict[str, Any]:
    """Return the explicit contract used for the synthetic corpus."""
    return {
        "version": "1.0",
        "allowed_paths": ["src/**/*.py", "tests/**/*.py"],
        "forbidden_paths": ["src/legacy.py", ".env"],
        "required_tests": ["tests/test_engine.py", "tests/test_handlers.py"],
        "import_rules": [
            {
                "id": "no_subprocess_core",
                "severity": "deny",
                "scope": "src/core/**",
                "forbidden_imports": ["subprocess"],
                "message": "Core modules must not shell out",
            },
            {
                "id": "no_requests_api",
                "severity": "deny",
                "scope": "src/api/**",
                "forbidden_imports": ["requests"],
                "message": "API layer must use the internal http client",
            },
        ],
        "invariants": [
            {
                "id": "no_shell_true",
                "severity": "deny",
                "description": "Avoid shell=True",
                "pattern": r"shell\s*=\s*True",
            }
        ],
    }


def base_plan_text() -> str:
    """Return the PLAN.md used for plan-derived cases in the corpus."""
    return """# PLAN

## Stage 1 — Core engine
- `src/core/engine.py` — ~120 LOC
- `tests/test_engine.py`

## Stage 2 — API handlers
- `src/api/handlers.py` — ~80 LOC
- `tests/test_handlers.py`

## Files I will explicitly NOT touch
- `src/legacy.py`
- `.env`
"""


# ---------------------------------------------------------------------------
# Fixture generator
# ---------------------------------------------------------------------------


def _valid_snippet(variant: int) -> str:
    """Return a deterministic, syntactically valid Python snippet."""
    snippets = [
        "def helper(x):\n    return x + 1\n",
        "class Engine:\n    def start(self):\n        return True\n",
        "import os\n\ndef run():\n    return os.getcwd()\n",
        "from pathlib import Path\n\nDATA = Path('/tmp')\n",
        "async def fetch(url):\n    return {'ok': True}\n",
        "def compute(a, b):\n    return a * b + 1\n",
        "logger = __import__('logging').getLogger(__name__)\n",
        "try:\n    result = 1 / 1\nexcept ZeroDivisionError:\n    result = 0\n",
        "def parse(data):\n    if not data:\n        return None\n    return data[0]\n",
        "VALUES = [1, 2, 3, 4, 5]\n",
    ]
    return snippets[variant % len(snippets)]


def _syntax_error_snippet(variant: int) -> str:
    """Return a deterministic Python snippet containing a syntax error."""
    errors = [
        "def broken(\n    return 1\n",
        "class Foo\n    pass\n",
        "if True\n    print('yes')\n",
        "import\n",
        "def f():\n    yield return 1\n",
        "lambda :\n",
        "print('hello\n",
        "async def f():\n    await 1 +\n",
        "try:\n    pass\nfinally\n    pass\n",
        "from import foo\n",
    ]
    return errors[variant % len(errors)]


def _import_violation_snippet(file_path: str, variant: int) -> str:
    """Return an in-plan file with a forbidden import for its scope."""
    if "core" in file_path:
        return "import subprocess\n\ndef run(cmd):\n    return subprocess.run(cmd)\n"
    if "api" in file_path:
        return "import requests\n\ndef get(url):\n    return requests.get(url)\n"
    # Fallback: forbidden in any scope via a wildcard rule is not in the base
    # contract, so pick a snippet that is syntactically valid but still labelled
    # as an import violation for measurement bookkeeping.  The oracle will not
    # block it (the contract has no matching rule), which becomes a false
    # negative in the report.
    return "import requests\n\nX = 1\n"


def _in_plan_files() -> List[str]:
    return ["src/core/engine.py", "src/api/handlers.py", "tests/test_engine.py"]


def _scope_creep_files() -> List[str]:
    return [
        "docs/readme.md",
        "scripts/deploy.sh",
        "src/legacy.py",
        ".env",
        "README.md",
        "pyproject.toml",
        "config/prod.yaml",
        "Makefile",
        ".github/workflows/ci.yml",
    ]


def _required_test_for(file_path: str) -> Optional[str]:
    if "core/engine" in file_path:
        return "tests/test_engine.py"
    if "api/handlers" in file_path:
        return "tests/test_handlers.py"
    return None


def _make_contract(
    contract_dict: Optional[Dict[str, Any]], plan_text: Optional[str]
) -> pc.Contract:
    """Build a Contract from an explicit dict, plan text, or return empty."""
    if contract_dict is not None:
        return pc.Contract.from_dict(contract_dict)
    if plan_text is not None:
        return pc.Contract.from_plan(plan_text)
    return pc.Contract()


def generate_edits(
    *,
    per_category: int = 20,
    contract_dict: Optional[Dict[str, Any]] = None,
    plan_text: Optional[str] = None,
) -> List[EditCase]:
    """Generate a deterministic, labeled edit corpus.

    Defaults to 5 categories * 20 edits = 100 edits.
    """
    if contract_dict is None and plan_text is None:
        contract_dict = base_contract_dict()

    cases: List[EditCase] = []

    for i in range(per_category):
        # in_plan: valid edit to an allowed file.
        fpath = _in_plan_files()[i % len(_in_plan_files())]
        cases.append(
            EditCase(
                id=f"in_plan_{i:03d}",
                category="in_plan",
                expected_block=False,
                file_path=fpath,
                before_src="pass\n",
                after_src=_valid_snippet(i),
                contract_dict=contract_dict,
                plan_text=plan_text,
                description="valid edit to a plan-allowed file",
            )
        )

        # scope_creep: edit to a file outside the allowed set.
        creep_file = _scope_creep_files()[i % len(_scope_creep_files())]
        cases.append(
            EditCase(
                id=f"scope_creep_{i:03d}",
                category="scope_creep",
                expected_block=True,
                file_path=creep_file,
                before_src="pass\n",
                after_src=_valid_snippet(i),
                contract_dict=contract_dict,
                plan_text=plan_text,
                description="edit to a file outside the contract scope",
            )
        )

        # import_violation: in-plan file with a forbidden import.
        # Restrict to production files that fall under the import-rule scopes
        # (src/core/** and src/api/**); tests are outside those scopes and would
        # not trigger a rule, so using them would create misleading labels.
        imp_file = _in_plan_files()[i % 2]
        cases.append(
            EditCase(
                id=f"import_violation_{i:03d}",
                category="import_violation",
                expected_block=True,
                file_path=imp_file,
                before_src="pass\n",
                after_src=_import_violation_snippet(imp_file, i),
                contract_dict=contract_dict,
                plan_text=plan_text,
                description="in-plan edit introducing a forbidden import",
            )
        )

        # syntax_error: any file with a Python syntax error.
        syn_file = _in_plan_files()[i % len(_in_plan_files())]
        cases.append(
            EditCase(
                id=f"syntax_error_{i:03d}",
                category="syntax_error",
                expected_block=True,
                file_path=syn_file,
                before_src="pass\n",
                after_src=_syntax_error_snippet(i),
                contract_dict=contract_dict,
                plan_text=plan_text,
                description="in-plan edit containing a syntax error",
            )
        )

        # test_omission: in-plan production code edit whose required test is
        # empty/missing.  The base contract lists required_tests but does not
        # enforce them, so these cases are expected positives that currently
        # become false negatives.
        code_file = _in_plan_files()[i % 2]  # only production files
        cases.append(
            EditCase(
                id=f"test_omission_{i:03d}",
                category="test_omission",
                expected_block=True,
                file_path=code_file,
                before_src="pass\n",
                after_src=_valid_snippet(i),
                contract_dict=contract_dict,
                plan_text=plan_text,
                description=f"{code_file} edited but {_required_test_for(code_file) or 'test'} missing",
            )
        )

    return cases


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _violation_to_dict(v: pc.Violation) -> Dict[str, Any]:
    return v.to_dict()


def _annotation_to_dict(a: _oracles.Annotation) -> Dict[str, Any]:
    return {
        "tool": a.tool,
        "file_path": a.file_path,
        "line": a.line,
        "column": a.column,
        "message": a.message,
        "severity": a.severity,
    }


def evaluate_case(case: EditCase) -> CaseResult:
    """Run one edit through the contract and the oracles."""
    t0 = time.monotonic()
    contract = _make_contract(case.contract_dict, case.plan_text)

    reasons: List[str] = []
    violations: List[pc.Violation] = []

    # Path check.
    path_hit = contract.check_path(case.file_path)
    if path_hit is not None:
        violations.append(path_hit)
        if path_hit.severity == "deny":
            reasons.append(f"path:{path_hit.rule_id}")

    # Import check.
    import_hits = contract.check_imports(case.file_path, case.after_src)
    for v in import_hits:
        violations.append(v)
        if v.severity == "deny":
            reasons.append(f"import:{v.rule_id}")

    # Invariant check.
    invariant_hits = contract.check_invariants(case.file_path, case.after_src)
    for v in invariant_hits:
        violations.append(v)
        if v.severity == "deny":
            reasons.append(f"invariant:{v.rule_id}")

    # Execution oracles (T1 + T2).
    report = _oracles.run_oracles(case.file_path, case.after_src)
    if not report.clean:
        for a in report.annotations:
            if a.severity == "error":
                reasons.append(f"oracle:{a.tool}:{a.message}")

    blocked = bool(reasons)
    elapsed = (time.monotonic() - t0) * 1000.0

    return CaseResult(
        case=case,
        blocked=blocked,
        reasons=sorted(set(reasons)),
        violations=[_violation_to_dict(v) for v in violations],
        oracle_clean=report.clean,
        oracle_annotations=[_annotation_to_dict(a) for a in report.annotations],
        elapsed_ms=elapsed,
    )


def compute_metrics(results: List[CaseResult]) -> EvalMetrics:
    """Compute precision/recall/FPR from case results."""
    metrics = EvalMetrics()
    metrics.n = len(results)

    per_cat: Dict[str, Dict[str, int]] = {}

    for r in results:
        cat = r.case.category
        per_cat.setdefault(cat, {"tp": 0, "fp": 0, "tn": 0, "fn": 0})
        expected = r.case.expected_block
        actual = r.blocked
        if expected and actual:
            metrics.tp += 1
            per_cat[cat]["tp"] += 1
        elif not expected and actual:
            metrics.fp += 1
            per_cat[cat]["fp"] += 1
        elif not expected and not actual:
            metrics.tn += 1
            per_cat[cat]["tn"] += 1
        else:  # expected and not actual
            metrics.fn += 1
            per_cat[cat]["fn"] += 1

    denom_p = metrics.tp + metrics.fp
    denom_r = metrics.tp + metrics.fn
    denom_fpr = metrics.fp + metrics.tn
    metrics.precision = metrics.tp / denom_p if denom_p else 0.0
    metrics.recall = metrics.tp / denom_r if denom_r else 0.0
    metrics.fpr = metrics.fp / denom_fpr if denom_fpr else 0.0
    if metrics.precision + metrics.recall:
        metrics.f1 = (
            2
            * metrics.precision
            * metrics.recall
            / (metrics.precision + metrics.recall)
        )
    metrics.per_category = per_cat
    return metrics


def run_eval(
    *,
    per_category: int = 20,
    contract_dict: Optional[Dict[str, Any]] = None,
    plan_text: Optional[str] = None,
) -> List[CaseResult]:
    """Generate the corpus and evaluate every case."""
    cases = generate_edits(
        per_category=per_category,
        contract_dict=contract_dict,
        plan_text=plan_text,
    )
    return [evaluate_case(c) for c in cases]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_report(results: List[CaseResult]) -> None:
    """Print a human-readable evaluation report to stdout."""
    metrics = compute_metrics(results)

    print("=" * 60)
    print("reasoning-core oracle eval report")
    print("=" * 60)
    print(f"corpus:      {metrics.n} synthetic edits")
    print(
        f"categories:  in_plan, scope_creep, import_violation, syntax_error, test_omission"
    )
    print(f"true PR subset: none (synthetic-only)")
    print()
    print("aggregate metrics")
    print(f"  TP={metrics.tp} FP={metrics.fp} TN={metrics.tn} FN={metrics.fn}")
    print(f"  precision={metrics.precision:.3f}")
    print(f"  recall   ={metrics.recall:.3f}")
    print(f"  f1       ={metrics.f1:.3f}")
    print(f"  FPR      ={metrics.fpr:.3f}")
    print()
    print("per category")
    for cat, counts in metrics.per_category.items():
        print(f"  {cat:20s} {counts}")
    print()

    # Show a sample of failures for quick triage.
    false_positives = [r for r in results if not r.case.expected_block and r.blocked]
    false_negatives = [r for r in results if r.case.expected_block and not r.blocked]
    if false_positives:
        print("false positives (first 5)")
        for r in false_positives[:5]:
            print(f"  {r.case.id}: {r.case.file_path} -> {r.reasons}")
        print()
    if false_negatives:
        print("false negatives (first 5)")
        for r in false_negatives[:5]:
            print(f"  {r.case.id}: {r.case.file_path} -> {r.reasons}")
        print()


def results_to_dict(results: List[CaseResult]) -> Dict[str, Any]:
    """Serialize results + metrics to a plain dict."""
    metrics = compute_metrics(results)
    return {
        "metadata": {
            "corpus": "synthetic",
            "real_pr_subset": False,
            "n_cases": len(results),
        },
        "metrics": metrics.to_dict(),
        "cases": [
            {
                "id": r.case.id,
                "category": r.case.category,
                "expected_block": r.case.expected_block,
                "file_path": r.case.file_path,
                "blocked": r.blocked,
                "reasons": r.reasons,
                "violations": r.violations,
                "oracle_clean": r.oracle_clean,
                "oracle_annotations": r.oracle_annotations,
                "elapsed_ms": round(r.elapsed_ms, 3),
            }
            for r in results
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Labeled oracle evaluation harness for reasoning-core"
    )
    parser.add_argument(
        "--per-category",
        type=int,
        default=20,
        help="number of edits per category (default: 20 -> 100 total)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="path to write JSON results",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress stdout report",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    results = run_eval(per_category=args.per_category)

    if not args.quiet:
        print_report(results)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(results_to_dict(results), indent=2, default=str),
            encoding="utf-8",
        )
        if not args.quiet:
            print(f"wrote {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
