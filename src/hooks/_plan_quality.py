"""Plan-quality scoring (P2). Composite gate score (CGS) over 6 signals.

Per v2 plan §P2 with reviewer corrections folded in:
- Default embedder for GPAS = sentence-transformers/all-MiniLM-L6-v2 (Mamba
  is fallback; the inverse of v1) — but P2 ships heuristic-only signals
  (ARD, NRD, GPAS-blocklist, SLR) that need no embedder. WWDS+CDGS require
  the generative critic from P5; gracefully degrade when unavailable.
- CGS weights uniform 1/6 + L2 prior until n>=60 labeled plans (per LLM
  scientist correction — the v1 hand-picked weights were single-point
  overfit).

Signals:
  ARD  — Artifact-Reference Density (file paths + endpoints + line refs)
  NRD  — Named-Risk Density (lexicon: N+1, race, TOCTOU, deadlock, ...)
  GPAS — Generic-Phrase Anti-Pattern Score (blocklist + cosine if available)
  WWDS — What/Why Differentiation (target + consequence per step) [Phase 5]
  CDGS — Claim-to-Diff Grounding [Phase 5]
  SLR  — Specificity-to-Length Ratio (sentences with >=1 artifact ref)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

# --- Lexicons --------------------------------------------------------------

_NAMED_RISKS = (
    "N+1", "race condition", "race-condition", "TOCTOU", "deadlock",
    "SQL injection", "SQLi", "XSS", "CSRF", "dead code",
    "integer overflow", "null dereference", "unchecked exception",
    "missing index", "coverage gap", "off-by-one", "double-free",
    "use-after-free", "unbounded pagination", "missing auth check",
    "open redirect", "ReDoS", "TOCTTOU", "type confusion",
)

_GENERIC_PHRASES = (
    "Read the diff",
    "Read full diff",
    "Re-read by file",
    "Check edge cases",
    "Verify it works",
    "Look for potential issues",
    "Make sure tests pass",
    "Review the changes",
    "Check for correctness",
    "Ensure code quality",
    "Make sure the code is clean",
)

# --- Regexes --------------------------------------------------------------

_FILE_REF_RE = re.compile(
    r"\b[\w./-]+\.(?:ts|tsx|js|mjs|cjs|cs|py|vue|go|rs|sql|sh|json|yaml|yml|md|toml|ini)\b"
)
# Endpoint shape: leading slash + at least one path segment
_ENDPOINT_RE = re.compile(r"\B/[a-zA-Z][\w-]*(?:/[\w{}:.-]+)+\b")
_LINE_REF_RE = re.compile(r"(?::\d{2,}|line\s+\d+|L\d{2,})\b")
# Sentence split: ., !, ? followed by whitespace + capital, or newline-newline.
_SENT_SPLIT_RE = re.compile(r"(?:[.!?](?=\s+[A-Z])|\n\n+|\n[-*]\s+)")


def _sentences(plan: str) -> list[str]:
    if not plan:
        return []
    parts = [p.strip() for p in _SENT_SPLIT_RE.split(plan) if p.strip()]
    return parts


# --- Signal 1: ARD ---------------------------------------------------------

class _Counts(NamedTuple):
    files: int
    endpoints: int
    lines: int
    total: int

    @property
    def has_any(self) -> bool:
        return self.total > 0


def _count_artifacts(text: str) -> _Counts:
    files = len(_FILE_REF_RE.findall(text))
    endpoints = len(_ENDPOINT_RE.findall(text))
    lines = len(_LINE_REF_RE.findall(text))
    return _Counts(files, endpoints, lines, files + endpoints + lines)


def ard(plan: str) -> float:
    sents = _sentences(plan)
    if not sents:
        return 0.0
    total_artifacts = _count_artifacts(plan).total
    return total_artifacts / len(sents)


# --- Signal 2: NRD ---------------------------------------------------------

def nrd(plan: str) -> float:
    if not plan:
        return 0.0
    sents = _sentences(plan)
    if not sents:
        return 0.0
    lo = plan.lower()
    hits = sum(1 for r in _NAMED_RISKS if r.lower() in lo)
    return hits / len(sents)


# --- Signal 3: GPAS --------------------------------------------------------

def gpas(plan: str) -> float:
    """Generic-Phrase Anti-Pattern Score: hits in blocklist / sentence count."""
    if not plan:
        return 0.0
    sents = _sentences(plan)
    if not sents:
        return 0.0
    lo = plan.lower()
    hits = sum(1 for ph in _GENERIC_PHRASES if ph.lower() in lo)
    return min(hits / len(sents), 1.0)


# --- Signal 6: SLR ---------------------------------------------------------

def slr(plan: str) -> float:
    sents = _sentences(plan)
    if not sents:
        return 0.0
    with_anchor = sum(1 for s in sents if _count_artifacts(s).has_any)
    return with_anchor / len(sents)


# --- Composite ------------------------------------------------------------

class CGSResult(NamedTuple):
    cgs: float
    ard: float
    nrd: float
    gpas: float
    slr: float
    decision: str  # accept | warn | reject


def _kappa_gate_passed() -> bool:
    """CDGS only trusts Qwen judgments after the κ gate passes."""
    sentinel_path = os.environ.get(
        "RC_QWEN_KAPPA_SENTINEL",
        str(Path(__file__).resolve().parent.parent.parent
            / "eval" / "runs" / "qwen_kappa_gate.json"),
    )
    try:
        with open(sentinel_path) as f:
            return bool(json.load(f).get("gate_pass"))
    except (OSError, ValueError):
        return False


_CLAIM_LINE_RE = re.compile(r"^[ \t]*[-*+]\s+(.+)$", re.MULTILINE)


def _extract_claims(plan: str, *, limit: int = 8) -> List[str]:
    """Pull bullet-point claims from a plan markdown. Caps at `limit` to bound
    Qwen budget per gate (8 * 2.5s = 20s p99)."""
    claims = [m.group(1).strip() for m in _CLAIM_LINE_RE.finditer(plan)]
    claims = [c for c in claims if 20 <= len(c) <= 500]
    return claims[:limit]


def cdgs(plan: str, diff_hunks: List[str]) -> Tuple[float, str]:
    """Claim-to-Diff Grounding Score via P5 generative critic.

    Returns (score, source) where source ∈ {"qwen", "bm25", "skipped"}.
    Skipped if κ gate hasn't passed or backend is off — caller treats as
    a non-signal (no penalty, no reward)."""
    if not _kappa_gate_passed():
        return 0.0, "skipped"
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import gen_client  # type: ignore
    except ImportError:
        return 0.0, "skipped"
    if not gen_client._backend_active() or not diff_hunks:
        return 0.0, "skipped"

    claims = _extract_claims(plan)
    if not claims:
        return 0.0, "skipped"

    supported = 0
    total = 0
    for claim in claims:
        # Score against best-matching hunk (any-of). Cap hunks per claim
        # to bound budget.
        for hunk in diff_hunks[:4]:
            res = gen_client.score_plan_grounding(claim, hunk)
            if res["total"] == 0:
                continue
            total += 1
            if res["supported"]:
                supported += 1
                break  # one supporting hunk is enough per claim
    if total == 0:
        return 0.0, "bm25"  # all calls failed — caller falls back
    return supported / total, "qwen"


def composite_gate_score(plan: str) -> CGSResult:
    """Heuristic-only CGS. WWDS+CDGS require P5 critic and are not yet wired.

    Per LLM scientist correction: weights are uniform until n>=60 labeled
    plans accumulate; the four heuristic signals get equal weight 0.25 each
    in this interim.

    Empty plans short-circuit to CGS=0 so gpas's vacuous "no generic phrases
    found" pass-bit doesn't lift the score.
    """
    if not plan or not _sentences(plan):
        return CGSResult(cgs=0.0, ard=0.0, nrd=0.0, gpas=0.0, slr=0.0, decision="reject")
    a = ard(plan)
    n = nrd(plan)
    g = gpas(plan)
    s = slr(plan)
    # Pass-bit per signal:
    pass_a = 1.0 if a >= 0.4 else 0.0
    pass_n = 1.0 if n >= 0.2 else 0.0
    pass_g = 1.0 if g < 0.15 else 0.0
    pass_s = 1.0 if s >= 0.35 else 0.0
    cgs = 0.25 * (pass_a + pass_n + pass_g + pass_s)
    if cgs >= 0.75:
        decision = "accept"
    elif cgs >= 0.5:
        decision = "warn"
    else:
        decision = "reject"
    return CGSResult(cgs=cgs, ard=a, nrd=n, gpas=g, slr=s, decision=decision)
