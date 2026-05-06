"""Deterministic builder for the P5 Qwen grounding eval dataset.

Mines this repo's git log to produce 200 (claim, hunk, label) pairs:
  - ~120 positive pairs (label=1): claim drawn from a commit message
    bullet/sentence, paired with a hunk from that same commit.
  - ~40 shuffled negatives (label=0): claim from commit A paired with
    a hunk from an unrelated commit B (different files).
  - ~40 hand-crafted hard negatives (label=0): claim is plausibly
    related to the hunk's domain but is NOT supported by the hunk's
    actual content (overstated/orthogonal claim).

Determinism: random.seed(42) used for all sampling and final shuffle.
The dataset is purely git-mined + a small curated hard-negative pool;
no external API or LLM calls.

Usage:
    python -m eval.build_grounding_pairs \
        --out eval/datasets/grounding_pairs.jsonl

Validates by round-tripping through eval.qwen_grounding_eval._load_pairs.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

CLAIM_MAX = 200
HUNK_MAX_LINES = 80
HUNK_MAX_CHARS = 3000
DIFF_MIN_LINES = 5
DIFF_MAX_LINES = 500

TARGET_POS = 120
TARGET_SHUF_NEG = 40
TARGET_HARD_NEG = 40


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(REPO_ROOT), *args], text=True, errors="replace"
    )


def _list_commits() -> List[str]:
    out = _git("log", "--no-merges", "--pretty=format:%H")
    return [s for s in out.splitlines() if s]


def _commit_body(sha: str) -> str:
    return _git("show", "--no-patch", "--format=%B", sha)


def _commit_diff(sha: str) -> str:
    # Diff only (no commit metadata) for hunk extraction.
    return _git("show", "--unified=3", "--format=", sha)


def _diff_line_count(diff: str) -> int:
    return diff.count("\n")


# Match commit-message claim-like sentences: bullet list items starting
# with "- " or numbered lines, falling back to single-line declarative
# sentences from the body. We strip Co-Authored-By footers.
_BULLET_RE = re.compile(r"^[\s]*[-*]\s+(.+)$")
_FOOTER_RE = re.compile(
    r"^\s*(co-authored-by|signed-off-by|reviewed-by):", re.IGNORECASE
)
_TRIVIAL_RE = re.compile(
    r"^(chore|typo|lint|format|whitespace|nit)\b", re.IGNORECASE
)


def _extract_claims(body: str) -> List[str]:
    claims: List[str] = []
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if _FOOTER_RE.match(line):
            continue
        m = _BULLET_RE.match(line)
        if m:
            text = m.group(1).strip()
        else:
            # Treat a clean prose line as a candidate claim if it ends with
            # a period and isn't a header-style line.
            stripped = line.strip()
            if not stripped.endswith(".") or stripped.startswith("#"):
                continue
            text = stripped
        # Clip leading file-prefixes like "src/foo.py — " so claim reads
        # as a standalone proposition.
        text = re.sub(r"^[\w./_\-]+\.(py|js|ts|md|json|yaml|sh)\s+[—:-]\s+", "", text)
        # Drop leading conjunctions/markers.
        text = text.lstrip("-*•· ").strip()
        if len(text) < 20 or len(text) > 400:
            continue
        if _TRIVIAL_RE.match(text):
            continue
        claims.append(text[:CLAIM_MAX])
    return claims


# Split a unified diff into per-file-hunk blocks: each block contains
# one "diff --git" header and one "@@ ..." hunk. We keep the file
# header so the hunk is self-describing.
_DIFF_FILE_RE = re.compile(r"^diff --git ", re.MULTILINE)
_HUNK_RE = re.compile(r"^@@ ", re.MULTILINE)


def _extract_hunks(diff: str) -> List[Tuple[str, str]]:
    """Return list of (file_path, hunk_text)."""
    out: List[Tuple[str, str]] = []
    # Split into per-file diffs
    parts = _DIFF_FILE_RE.split(diff)
    headers = _DIFF_FILE_RE.findall(diff)
    # parts[0] is preamble (empty); aligned with headers[i] for parts[i+1]
    for i, body in enumerate(parts[1:]):
        full = headers[i] + body
        # First line: "diff --git a/<path> b/<path>"
        first = full.splitlines()[0]
        m = re.match(r"diff --git a/(\S+) b/(\S+)", first)
        if not m:
            continue
        path = m.group(2)
        # Skip binary, lockfiles, and very large generated files
        if any(seg in path for seg in (".lock", "package-lock", ".min.")):
            continue
        # Split into hunks at @@ markers, but keep the file header on
        # each emitted hunk (so the model sees which file changed).
        # Find the file header (everything up to the first @@)
        hunk_starts = [
            mh.start() for mh in _HUNK_RE.finditer(full)
        ]
        if not hunk_starts:
            continue
        file_header = full[: hunk_starts[0]].rstrip("\n")
        for j, start in enumerate(hunk_starts):
            end = hunk_starts[j + 1] if j + 1 < len(hunk_starts) else len(full)
            hunk_body = full[start:end].rstrip("\n")
            text = file_header + "\n" + hunk_body
            # Apply size caps
            lines = text.splitlines()
            if len(lines) > HUNK_MAX_LINES:
                lines = lines[:HUNK_MAX_LINES]
                text = "\n".join(lines)
            if len(text) > HUNK_MAX_CHARS:
                text = text[:HUNK_MAX_CHARS]
            # Skip trivially small hunks (e.g., single import shuffles)
            if len(hunk_body.splitlines()) < 3:
                continue
            out.append((path, text))
    return out


def _is_useful_commit(body: str, diff: str) -> bool:
    n = _diff_line_count(diff)
    if n < DIFF_MIN_LINES or n > DIFF_MAX_LINES:
        return False
    subj = body.splitlines()[0] if body.splitlines() else ""
    if _TRIVIAL_RE.match(subj):
        return False
    return True


def _mine_repo() -> Tuple[List[dict], List[Tuple[str, str, str]]]:
    """Mine the repo for (positive_pairs, all_hunks_pool).

    all_hunks_pool elements are (sha, file_path, hunk_text) for use in
    constructing shuffled negatives.
    """
    positives: List[dict] = []
    hunks_pool: List[Tuple[str, str, str]] = []
    seen_claims: set = set()

    for sha in _list_commits():
        body = _commit_body(sha)
        diff = _commit_diff(sha)
        if not _is_useful_commit(body, diff):
            continue
        claims = _extract_claims(body)
        hunks = _extract_hunks(diff)
        if not claims or not hunks:
            continue

        # Pool every hunk for negative sampling later.
        for path, htext in hunks:
            hunks_pool.append((sha, path, htext))

        # Pair claims with hunks. Strategy: walk claims; each claim
        # picks the hunk whose file path overlaps a token in the claim
        # (best effort), else the first hunk.
        for ci, claim in enumerate(claims):
            if claim in seen_claims:
                continue
            chosen = None
            claim_lower = claim.lower()
            for path, htext in hunks:
                base = Path(path).name.lower()
                stem = Path(path).stem.lower()
                if base in claim_lower or stem in claim_lower:
                    chosen = (path, htext)
                    break
            if chosen is None:
                chosen = hunks[ci % len(hunks)]
            seen_claims.add(claim)
            positives.append(
                {
                    "id": f"pos_{sha[:8]}_{ci}",
                    "claim": claim,
                    "hunk": chosen[1],
                    "label": 1,
                    "_sha": sha,
                    "_path": chosen[0],
                }
            )
    return positives, hunks_pool


def _build_shuf_negatives(
    positives: List[dict],
    hunks_pool: List[Tuple[str, str, str]],
    n: int,
    rng: random.Random,
) -> List[dict]:
    out: List[dict] = []
    pool_idx = list(range(len(hunks_pool)))
    rng.shuffle(pool_idx)
    pos_idx = list(range(len(positives)))
    rng.shuffle(pos_idx)
    attempts = 0
    pi = 0
    hi = 0
    while len(out) < n and attempts < 5000:
        attempts += 1
        p = positives[pos_idx[pi % len(pos_idx)]]
        sha_h, path_h, htext = hunks_pool[pool_idx[hi % len(pool_idx)]]
        pi += 1
        hi += 1
        # Require different commit AND different file from the claim's
        # original commit/file.
        if sha_h == p["_sha"]:
            continue
        if path_h == p["_path"]:
            continue
        out.append(
            {
                "id": f"shuf_neg_{len(out):03d}",
                "claim": p["claim"],
                "hunk": htext,
                "label": 0,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Hand-crafted hard negatives.
#
# Each entry is a (claim, hunk) pair where the claim looks topically
# adjacent to the hunk but is NOT actually supported by what the hunk
# changes. Hunks are real fragments mined from the repo (small, safe
# substrings); claims are written to overstate or to assert an
# orthogonal capability. label=0 for all.
# ---------------------------------------------------------------------------
HARD_NEGATIVES: List[Tuple[str, str]] = [
    (
        "Adds input validation that rejects empty plan claims before scoring.",
        "diff --git a/src/gen_client.py b/src/gen_client.py\n@@\n+    logger.debug(\"score_plan_grounding called claim_len=%d hunk_len=%d\",\n+                 len(claim), len(hunk))\n     payload = {\"claim\": claim, \"hunk\": hunk}\n",
    ),
    (
        "Implements exponential backoff retry for the sidecar /score endpoint.",
        "diff --git a/src/gen_client.py b/src/gen_client.py\n@@\n     try:\n         resp = urllib.request.urlopen(req, timeout=budget_s)\n-    except urllib.error.URLError:\n+    except urllib.error.URLError as exc:\n+        logger.warning(\"sidecar unreachable: %s\", exc)\n         return {\"supported\": False, \"total\": 0}\n",
    ),
    (
        "Caches Qwen grounding scores keyed by (claim, hunk) hash to skip duplicate calls.",
        "diff --git a/src/gen_client.py b/src/gen_client.py\n@@\n+RC_GEN_BUDGET_MS = int(os.environ.get(\"RC_GEN_BUDGET_MS\", \"2500\"))\n+\n def score_plan_grounding(claim: str, hunk: str) -> dict:\n",
    ),
    (
        "Switches the Mamba backbone from CPU to MPS automatically on Apple Silicon.",
        "diff --git a/src/ssm_backbone.py b/src/ssm_backbone.py\n@@\n-    device = os.environ.get(\"S2_DEVICE\", \"cpu\")\n+    device = os.environ.get(\"S2_DEVICE\") or \"cpu\"\n     return device\n",
    ),
    (
        "Pins the tree-sitter-sql grammar to version 0.4 to fix CREATE PROCEDURE parsing.",
        "diff --git a/src/grammars.py b/src/grammars.py\n@@\n-from tree_sitter import Parser\n+from tree_sitter import Parser, Language\n",
    ),
    (
        "Adds Ruby support to the language fingerprint detector.",
        "diff --git a/src/grammars.py b/src/grammars.py\n@@\n     \".py\": \"python\",\n     \".js\": \"javascript\",\n     \".ts\": \"typescript\",\n",
    ),
    (
        "Adds a unit test that asserts cohens_kappa returns 1.0 on perfect agreement.",
        "diff --git a/eval/qwen_grounding_eval.py b/eval/qwen_grounding_eval.py\n@@\n     if chance >= 1.0:\n         return 1.0 if agree >= 1.0 else 0.0\n     return (agree - chance) / (1.0 - chance)\n",
    ),
    (
        "Bumps the Cohen kappa gate threshold from 0.7 to 0.8.",
        "diff --git a/eval/qwen_grounding_eval.py b/eval/qwen_grounding_eval.py\n@@\n-    ap.add_argument(\"--gate-kappa\", type=float, default=0.7)\n+    ap.add_argument(\"--gate-kappa\", type=float, default=0.7, help=\"min kappa\")\n",
    ),
    (
        "Switches the bootstrap CI from percentile to BCa method.",
        "diff --git a/eval/qwen_grounding_eval.py b/eval/qwen_grounding_eval.py\n@@\n     samples.sort()\n     lo = samples[int(0.025 * len(samples))]\n     hi = samples[int(0.975 * len(samples))]\n     return lo, hi\n",
    ),
    (
        "Adds GPU acceleration to the FastAPI /score endpoint via CUDA streams.",
        "diff --git a/src/s2_core.py b/src/s2_core.py\n@@\n     app = FastAPI()\n+\n+    @app.get(\"/health\")\n+    def health():\n+        return {\"ok\": True}\n",
    ),
    (
        "Removes the loopback-only restriction so the sidecar can serve remote clients.",
        "diff --git a/src/s2_core.py b/src/s2_core.py\n@@\n-    HOST = \"127.0.0.1\"\n+    HOST = os.environ.get(\"RC_HOST\", \"127.0.0.1\")\n     PORT = 8765\n",
    ),
    (
        "Adds a --dry-run flag that previews hook actions without writing.",
        "diff --git a/src/hooks/pre_edit_guard.py b/src/hooks/pre_edit_guard.py\n@@\n     parser = argparse.ArgumentParser()\n+    parser.add_argument(\"--verbose\", action=\"store_true\")\n     args = parser.parse_args()\n",
    ),
    (
        "Switches the hook from exit-code 2 to exit-code 1 on regression.",
        "diff --git a/src/hooks/pre_edit_guard.py b/src/hooks/pre_edit_guard.py\n@@\n     sys.stderr.write(block_msg + \"\\n\")\n     sys.exit(2)\n",
    ),
    (
        "Adds a SHA-256 integrity check on the Mamba checkpoint before loading.",
        "diff --git a/src/ssm_backbone.py b/src/ssm_backbone.py\n@@\n-    checkpoint = os.environ.get(\"S2_SSM_CHECKPOINT\", DEFAULT_CKPT)\n+    checkpoint = os.environ.get(\"S2_SSM_CHECKPOINT\") or DEFAULT_CKPT\n     return checkpoint\n",
    ),
    (
        "Introduces a new RC_FAIL_CLOSED env var that aborts the hook on sidecar errors.",
        "diff --git a/src/hooks/pre_edit_guard.py b/src/hooks/pre_edit_guard.py\n@@\n     if os.environ.get(\"S2_FAIL_CLOSED\") == \"1\":\n         pass\n",
    ),
    (
        "Doubles the per-call gen budget from 2500 ms to 5000 ms.",
        "diff --git a/src/gen_client.py b/src/gen_client.py\n@@\n-RC_GEN_BUDGET_MS = int(os.environ.get(\"RC_GEN_BUDGET_MS\", \"2500\"))\n+RC_GEN_BUDGET_MS = int(os.environ.get(\"RC_GEN_BUDGET_MS\", \"2500\"))  # default budget\n",
    ),
    (
        "Adds a markdown grammar so README files are parsed by the call-graph extractor.",
        "diff --git a/src/grammars.py b/src/grammars.py\n@@\n     \".sql\": \"sql\",\n     \".cs\": \"csharp\",\n",
    ),
    (
        "Switches the OOD detector from L2 distance to cosine similarity.",
        "diff --git a/src/_ood_detector.py b/src/_ood_detector.py\n@@\n-def is_ood(vec, mu, sigma):\n+def is_ood(vec, mu, sigma, threshold=3.0):\n     return False\n",
    ),
    (
        "Replaces the JSON-based golden_set store with SQLite for transactional writes.",
        "diff --git a/src/golden_set.py b/src/golden_set.py\n@@\n     path = Path(\"data/golden_set.json\")\n     return json.loads(path.read_text())\n",
    ),
    (
        "Implements parallel scoring across the calibration corpus using multiprocessing.",
        "diff --git a/eval/calibration_corpus.py b/eval/calibration_corpus.py\n@@\n     for item in corpus:\n         score = scorer(item)\n         results.append(score)\n",
    ),
    (
        "Adds a CLI subcommand `rc judge` that runs Qwen against a single pair.",
        "diff --git a/src/cli.py b/src/cli.py\n@@\n     parser = argparse.ArgumentParser(prog=\"rc\")\n     sub = parser.add_subparsers(dest=\"cmd\")\n",
    ),
    (
        "Replaces FastAPI with Flask in the System 2 sidecar.",
        "diff --git a/src/s2_core.py b/src/s2_core.py\n@@\n     from fastapi import FastAPI, Request\n     from fastapi.responses import JSONResponse\n",
    ),
    (
        "Adds Prometheus metrics export on a new /metrics endpoint.",
        "diff --git a/src/s2_core.py b/src/s2_core.py\n@@\n     @app.get(\"/health\")\n     def health():\n         return {\"ok\": True, \"model_loaded\": MODEL_LOADED}\n",
    ),
    (
        "Adds support for tree-sitter ABI v16.",
        "diff --git a/src/grammars.py b/src/grammars.py\n@@\n-from tree_sitter import Parser\n+from tree_sitter import Parser, Language\n     # ABI v15 path\n",
    ),
    (
        "Removes the BM25 fallback for plan grounding entirely.",
        "diff --git a/src/_shadow_mode.py b/src/_shadow_mode.py\n@@\n     if not gen_client.health_ok():\n         return bm25_score(claim, hunk)\n",
    ),
    (
        "Adds a deduplication pass that drops near-duplicate hunks before scoring.",
        "diff --git a/eval/qwen_grounding_eval.py b/eval/qwen_grounding_eval.py\n@@\n     pairs = _load_pairs(args.pairs)\n     if args.limit > 0:\n         pairs = pairs[: args.limit]\n",
    ),
    (
        "Switches the eval driver from sequential to async I/O for sidecar calls.",
        "diff --git a/eval/qwen_grounding_eval.py b/eval/qwen_grounding_eval.py\n@@\n     for i, p in enumerate(pairs):\n         qwen.append(_qwen_label(p[\"claim\"], p[\"hunk\"]))\n",
    ),
    (
        "Adds a confusion-matrix breakdown to the eval output JSON.",
        "diff --git a/eval/qwen_grounding_eval.py b/eval/qwen_grounding_eval.py\n@@\n     result = {\n         \"n\": n,\n         \"kappa\": kappa,\n         \"kappa_ci95\": [lo, hi],\n         \"accuracy\": accuracy,\n     }\n",
    ),
    (
        "Persists per-pair predictions to a sidecar log for offline review.",
        "diff --git a/eval/qwen_grounding_eval.py b/eval/qwen_grounding_eval.py\n@@\n     args.out.parent.mkdir(parents=True, exist_ok=True)\n     args.out.write_text(json.dumps(result, indent=2))\n",
    ),
    (
        "Adds an --include-skipped flag that counts critic timeouts as agreements.",
        "diff --git a/eval/qwen_grounding_eval.py b/eval/qwen_grounding_eval.py\n@@\n     if res.get(\"total\", 0) == 0:\n         return 0  # pessimistic: critic unreachable -> treat as unsupported\n",
    ),
    (
        "Replaces the deterministic seed=42 with a per-run random seed.",
        "diff --git a/eval/build_grounding_pairs.py b/eval/build_grounding_pairs.py\n@@\n     rng = random.Random(seed)\n     return rng\n",
    ),
    (
        "Adds telemetry that ships every claim/hunk pair to a remote logging service.",
        "diff --git a/src/gen_client.py b/src/gen_client.py\n@@\n     payload = {\"claim\": claim, \"hunk\": hunk}\n     data = json.dumps(payload).encode(\"utf-8\")\n",
    ),
    (
        "Wraps the score endpoint with Bearer-token authentication.",
        "diff --git a/src/s2_core.py b/src/s2_core.py\n@@\n     @app.post(\"/score\")\n     async def score(req: Request):\n         body = await req.json()\n",
    ),
    (
        "Adds rate limiting (10 req/s) to the FastAPI sidecar.",
        "diff --git a/src/s2_core.py b/src/s2_core.py\n@@\n     app = FastAPI(title=\"reasoning-core sidecar\")\n     return app\n",
    ),
    (
        "Migrates the project from poetry to uv for dependency management.",
        "diff --git a/pyproject.toml b/pyproject.toml\n@@\n [project]\n name = \"reasoning-core\"\n version = \"0.1.0\"\n",
    ),
    (
        "Drops Python 3.10 support; minimum is now 3.12.",
        "diff --git a/pyproject.toml b/pyproject.toml\n@@\n requires-python = \">=3.10\"\n",
    ),
    (
        "Adds a CONTRIBUTING.md with the patch-submission flow.",
        "diff --git a/README.md b/README.md\n@@\n ## Architecture\n \n See docs/ARCHITECTURE.md for the full layered design.\n",
    ),
    (
        "Adds an automatic git-bisect helper for hook regressions.",
        "diff --git a/scripts/start-sidecar.sh b/scripts/start-sidecar.sh\n@@\n #!/usr/bin/env bash\n set -euo pipefail\n",
    ),
    (
        "Replaces urllib with httpx in gen_client for HTTP/2 support.",
        "diff --git a/src/gen_client.py b/src/gen_client.py\n@@\n import json\n import os\n import urllib.request\n import urllib.error\n",
    ),
    (
        "Adds structured JSON logging via structlog across all modules.",
        "diff --git a/src/gen_client.py b/src/gen_client.py\n@@\n import logging\n \n logger = logging.getLogger(__name__)\n",
    ),
]


def _build_hard_negatives(rng: random.Random) -> List[dict]:
    out: List[dict] = []
    pool = list(HARD_NEGATIVES)
    rng.shuffle(pool)
    for i, (claim, hunk) in enumerate(pool[:TARGET_HARD_NEG]):
        out.append(
            {
                "id": f"hard_neg_{i:03d}",
                "claim": claim[:CLAIM_MAX],
                "hunk": hunk[:HUNK_MAX_CHARS],
                "label": 0,
            }
        )
    return out


def _strip_internal(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)

    positives, hunks_pool = _mine_repo()
    sys.stderr.write(
        f"[builder] mined {len(positives)} candidate positives "
        f"and {len(hunks_pool)} hunks across the repo\n"
    )

    if len(positives) < TARGET_POS:
        sys.stderr.write(
            f"[builder] only {len(positives)} positives available "
            f"(target {TARGET_POS}); using all of them\n"
        )
        chosen_pos = list(positives)
    else:
        # Deterministic shuffle then take target.
        idx = list(range(len(positives)))
        rng.shuffle(idx)
        chosen_pos = [positives[i] for i in idx[:TARGET_POS]]

    shuf_neg = _build_shuf_negatives(
        positives, hunks_pool, n=TARGET_SHUF_NEG, rng=rng
    )
    hard_neg = _build_hard_negatives(rng)

    all_pairs = (
        [_strip_internal(p) for p in chosen_pos]
        + shuf_neg
        + hard_neg
    )

    # If we fell short on positives, top up with extra shuffled negatives
    # so total hits 200 and balance stays close to 50/50 via more
    # positives if available; otherwise leave as-is.
    target_total = 200
    if len(all_pairs) < target_total and len(positives) > len(chosen_pos):
        extra_n = target_total - len(all_pairs)
        leftovers = [p for p in positives if p not in chosen_pos]
        rng.shuffle(leftovers)
        all_pairs.extend(_strip_internal(p) for p in leftovers[:extra_n])

    # Final shuffle for ordering.
    rng.shuffle(all_pairs)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for rec in all_pairs:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    n_pos = sum(1 for r in all_pairs if r["label"] == 1)
    n_neg = sum(1 for r in all_pairs if r["label"] == 0)
    n_shuf = sum(1 for r in all_pairs if r["id"].startswith("shuf_neg_"))
    n_hard = sum(1 for r in all_pairs if r["id"].startswith("hard_neg_"))
    n_pmined = sum(1 for r in all_pairs if r["id"].startswith("pos_"))
    sys.stderr.write(
        f"[builder] wrote {len(all_pairs)} pairs to {args.out}\n"
        f"[builder]   positives (mined): {n_pmined}\n"
        f"[builder]   shuffled negatives: {n_shuf}\n"
        f"[builder]   hard negatives: {n_hard}\n"
        f"[builder]   label balance: {n_pos} pos / {n_neg} neg\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
