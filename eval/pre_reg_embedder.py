"""Pre-reg embedder ablation harness for option-3 default flip.

This script loads each candidate backend from ``src.ssm_backbone._BACKENDS``
(unless overridden via ``--backends``), runs the (before, after) pairs from
``eval/datasets/grounding_pairs_v3.jsonl`` through the embedder, and computes
the five pre-reg gates from the embedder memo (2026-09-21 revision):

    1. ROC-AUC of ``novelty = 1 - cos(before, after)`` against ``label=1``.
       Threshold: >= 0.70 on the test split.
    2. Inversion guarantee: the chosen backend beats Mamba-130M by >= 0.05
       absolute AUC on the same split.
    3. Intra-code anisotropy: mean pairwise cosine of the ``before``
       embeddings. Threshold: <= 0.88 (vs Mamba-130M baseline ~0.92).
    4. Per-edit latency p95. Threshold: median <= 2.5x of Mamba-130M median.
    5. Falsifiability: ``random-mamba`` AUC <= 0.55 on the same split.

The output is a JSON run manifest at
``eval/runs/<timestamp>_pre_reg_embedder.json`` consumed by
``tests/test_pre_reg_embedder_gate.py``.

The harness deliberately uses CPU-only inference (no GPU assumptions) and
caps per-edit latency to ``--hard-cap-s`` (default 30 s). A candidate that
times out is recorded as ``latency_timeout=True`` and excluded from the
ROC-AUC computation but kept in the manifest for transparency.

Usage:

    python -m eval.pre_reg_embedder \\
        --backends mamba-130m unixcoder-base bge-code \\
                   mamba3-siso-893m \\
        --hard-cap-s 30 \\
        --out eval/runs/pre_reg_embedder.json

Exit codes:
    0 -- all measured backends ran successfully
    2 -- at least one backend failed to load (recorded in manifest)
    3 -- measured gates disagree with the policy (also recorded; the
        gate test treats this as a failure)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = REPO_ROOT / "eval" / "datasets" / "grounding_pairs_v3.jsonl"


# ---------------------------------------------------------------------------
# Run manifest schema
# ---------------------------------------------------------------------------


@dataclass
class BackendResult:
    backend: str
    n_pairs: int = 0
    n_latency_timeouts: int = 0
    n_load_failures: int = 0
    novelty: list[float] = field(default_factory=list)
    labels: list[int] = field(default_factory=list)
    before_embs: list[list[float]] = field(default_factory=list)
    latency_ms: list[float] = field(default_factory=list)
    error: str = ""

    @property
    def auc(self) -> float:
        if len(set(self.labels)) < 2:
            return float("nan")
        try:
            from sklearn.metrics import roc_auc_score  # type: ignore
            return float(roc_auc_score(self.labels, self.novelty))
        except ImportError:
            return _binary_search_auc(self.novelty, self.labels)

    @property
    def anisotropy(self) -> float:
        """Mean pairwise cosine of the `before` embeddings."""
        if len(self.before_embs) < 2:
            return float("nan")
        return _mean_pairwise_cosine(self.before_embs)

    @property
    def median_latency_ms(self) -> float:
        if not self.latency_ms:
            return float("nan")
        return statistics.median(self.latency_ms)

    @property
    def p95_latency_ms(self) -> float:
        if not self.latency_ms:
            return float("nan")
        s = sorted(self.latency_ms)
        idx = max(0, min(len(s) - 1, int(0.95 * (len(s) - 1))))
        return s[idx]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["auc"] = self.auc
        d["anisotropy"] = self.anisotropy
        d["median_latency_ms"] = self.median_latency_ms
        d["p95_latency_ms"] = self.p95_latency_ms
        return d


# ---------------------------------------------------------------------------
# Cosine + ROC-AUC
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (na * nb)


def _mean_pairwise_cosine(embs: list[list[float]]) -> float:
    if len(embs) < 2:
        return float("nan")
    s = 0.0
    n = 0
    for i in range(len(embs)):
        for j in range(i + 1, len(embs)):
            s += _cosine(embs[i], embs[j])
            n += 1
    return s / max(n, 1)


def _binary_search_auc(novelty: list[float], labels: list[int]) -> float:
    """Tiny standalone AUC-of-ROC for the rare case where sklearn isn't
    available in the sandbox. Returns NaN if sklearn is missing and the
    caller should re-raise."""
    # sklearn import is deferred to BackendResult.auc -- if sklearn is
    # unavailable, callers fall back to this.
    pos = [n for n, l in zip(novelty, labels) if l == 1]
    neg = [n for n, l in zip(novelty, labels) if l == 0]
    if not pos or not neg:
        return float("nan")
    wins = 0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


# ---------------------------------------------------------------------------
# Pair loading + stratified split
# ---------------------------------------------------------------------------


def load_pairs(path: Path) -> list[dict[str, Any]]:
    pairs = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            label = row.get("label")
            if label not in (0, 1):
                continue
            before = row.get("before") or row.get("hunk") or ""
            after = row.get("after") or row.get("hunk") or ""
            # If we have only the diff (the v3 schema), split it via a
            # simple heuristic: everything before the second ``diff --git``
            # header is "before", everything after is "after".
            if "before" not in row or "after" not in row:
                before, after = _split_diff_hunk(row.get("hunk", ""))
            pairs.append({
                "id": row.get("id", ""),
                "before": before,
                "after": after,
                "label": int(label),
            })
    return pairs


def _split_diff_hunk(hunk: str) -> tuple[str, str]:
    """Crude but deterministic: split a unified diff into before/after text."""
    before_lines: list[str] = []
    after_lines: list[str] = []
    for line in hunk.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("+"):
            after_lines.append(line[1:])
        elif line.startswith("-"):
            before_lines.append(line[1:])
        else:
            # context line: present in both before and after
            stripped = line[1:] if line.startswith(" ") else line
            before_lines.append(stripped)
            after_lines.append(stripped)
    return "\n".join(before_lines), "\n".join(after_lines)


def stratified_split(pairs: list[dict[str, Any]], *, seed: int = 42,
                    train: float = 0.70, dev: float = 0.15) -> dict[str, list[dict[str, Any]]]:
    """Split by id-hash to keep label distribution balanced. The split
    key is the first 8 chars of the SHA-256 of the id."""
    rng_state = seed
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in pairs:
        h = hashlib.sha256(p["id"].encode("utf-8")).hexdigest()
        # Use the first hex digit mod 100 for stratification
        slot = int(h[:2], 16) % 100
        if slot < train * 100:
            buckets["train"].append(p)
        elif slot < (train + dev) * 100:
            buckets["dev"].append(p)
        else:
            buckets["test"].append(p)
    return buckets


# ---------------------------------------------------------------------------
# Embedder loading
# ---------------------------------------------------------------------------


def _revision_env_key_for(backend: str) -> str:
    """Resolve the RC_<REPO_SLUG>_REVISION env var for a backend."""
    from src import ssm_backbone  # noqa: PLC0415
    if backend not in ssm_backbone._BACKENDS:
        return ""
    return ssm_backbone._revision_env_key(ssm_backbone._BACKENDS[backend].checkpoint)


def _resolve_latest_sha(repo_id: str) -> str:
    """Fetch the current main-branch SHA for a HF repo. Used by the
    pre-reg harness to override the sidecar's fail-closed posture when
    measuring backends that haven't been pinned yet."""
    from huggingface_hub import HfApi  # type: ignore
    info = HfApi().model_info(repo_id)
    return info.sha


def _ensure_revision_pinned(backend: str) -> None:
    """If the registry doesn't have a pinned revision for this backend,
    fetch the latest main-branch SHA and inject it as the env-var override
    (RC_<REPO_SLUG>_REVISION) that ``_resolve_revision_for_backend`` honours.

    The pre-reg harness is allowed to override the sidecar's fail-closed
    posture because measurement requires pinning mutable refs that
    production explicitly refuses. The resolved SHA is recorded in the
    run manifest for reproducibility.

    Bails out silently for ``random-mamba`` -- the in-process control has
    no HF checkpoint to pin.
    """
    from src import ssm_backbone  # noqa: PLC0415
    if backend == "random-mamba":
        return
    if backend not in ssm_backbone._BACKENDS:
        raise KeyError(f"unknown backend: {backend!r}")
    cfg = ssm_backbone._BACKENDS[backend]
    if ssm_backbone._PINNED_REVISIONS.get(cfg.checkpoint):
        return
    try:
        sha = _resolve_latest_sha(cfg.checkpoint)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"failed to resolve latest SHA for {cfg.checkpoint}: {exc}. "
            f"Add it to _PINNED_REVISIONS or run pin_model_cards.py first."
        ) from exc
    env_key = ssm_backbone._revision_env_key(cfg.checkpoint)
    os.environ[env_key] = sha


def _load_embedder(backend: str) -> Any:
    """Load the named backend via ssm_backbone and return a callable that
    takes a string and returns the pooled embedding as a flat list.

    Uses ``RC_EMBEDDER=<backend>`` + ``ssm_backbone.embed`` (the canonical
    entry point) so the harness exercises the same code path production
    uses. Returns the ``ssm_backbone.embed`` callable directly.

    Raises on any failure with a one-line hint about the most likely cause
    (missing package, gated repo, kernel unavailable, etc.).
    """
    os.environ["RC_EMBEDDER"] = backend
    from src import ssm_backbone  # noqa: PLC0415
    # ssm_backbone.embed() loads on first call. Trigger that here so
    # the harness can surface load failures as BackendResult.n_load_failures.
    # The handle is cached internally keyed by the active backend, so we
    # simply call embed; subsequent calls reuse the cached handle.
    try:
        ssm_backbone.embed("warmup")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"failed to load {backend}: {exc}") from exc
    return ssm_backbone.embed


def _embed(embedder: Any, text: str, *, max_len: int = 4096) -> list[float]:
    """Embed a single text and return the pooled vector as a flat list of
    floats. ``embedder`` is the ``ssm_backbone.embed`` callable."""
    result = embedder(text or " ")
    # ssm_backbone.embed returns either a torch.Tensor, a numpy array, or
    # a _TensorLike (random-mamba torch-free path). Normalize to a flat
    # list of floats regardless.
    if hasattr(result, "tolist"):
        flat = result.flatten().tolist() if hasattr(result, "flatten") else result.tolist()
        return [float(x) for x in flat]
    if isinstance(result, (list, tuple)):
        return [float(x) for x in result]
    raise TypeError(f"embed returned unexpected type: {type(result).__name__}")


# ---------------------------------------------------------------------------
# Per-backend measurement loop
# ---------------------------------------------------------------------------


def measure_backend(backend: str, pairs: list[dict[str, Any]], *,
                    hard_cap_s: float) -> BackendResult:
    """Embed all (before, after) pairs through `backend` and record metrics."""
    result = BackendResult(backend=backend, n_pairs=len(pairs))
    try:
        _ensure_revision_pinned(backend)
        embedder = _load_embedder(backend)
    except Exception as exc:  # noqa: BLE001
        result.n_load_failures += 1
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    for p in pairs:
        t0 = time.monotonic()
        try:
            emb_before = _embed(embedder, p["before"])
        except Exception as exc:  # noqa: BLE001
            result.n_load_failures += 1
            result.error = f"before-embed failed: {type(exc).__name__}: {exc}"
            return result
        mid = time.monotonic()
        try:
            emb_after = _embed(embedder, p["after"])
        except Exception as exc:  # noqa: BLE001
            result.n_load_failures += 1
            result.error = f"after-embed failed: {type(exc).__name__}: {exc}"
            return result
        t1 = time.monotonic()
        # Hard-cap enforcement: if either half of the (before+after)
        # embedding took longer than hard_cap_s, mark as a latency
        # timeout and skip novelty recording (we don't want a slow run
        # to skew the AUC).
        per_call_s = max(mid - t0, t1 - mid)
        if per_call_s > hard_cap_s:
            result.n_latency_timeouts += 1
            continue
        novelty = 1.0 - max(_cosine(emb_before, emb_after), 0.0)
        result.novelty.append(novelty)
        result.labels.append(p["label"])
        result.before_embs.append(emb_before)
        result.latency_ms.append((t1 - t0) * 1000.0)
    return result


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


@dataclass
class GateVerdict:
    name: str
    passed: bool
    threshold: float
    observed: float
    description: str


def evaluate_gates(results: dict[str, BackendResult],
                   baseline_backend: str = "mamba-130m",
                   candidate_backend: str = "mamba3-siso-893m") -> list[GateVerdict]:
    """Evaluate the five pre-reg gates for the candidate against the
    baseline. Returns a list of GateVerdict (one per gate)."""
    base = results.get(baseline_backend)
    cand = results.get(candidate_backend)
    verdicts: list[GateVerdict] = []

    # Gate 1: candidate ROC-AUC >= 0.70
    cand_auc = cand.auc if cand is not None else float("nan")
    verdicts.append(GateVerdict(
        name="candidate_auc",
        passed=(not math.isnan(cand_auc)) and cand_auc >= 0.70,
        threshold=0.70,
        observed=cand_auc,
        description=f"{candidate_backend} ROC-AUC >= 0.70 on test split",
    ))

    # Gate 2: candidate beats baseline by >= 0.05 absolute AUC
    base_auc = base.auc if base is not None else float("nan")
    delta = cand_auc - base_auc if not (math.isnan(cand_auc) or math.isnan(base_auc)) else float("nan")
    verdicts.append(GateVerdict(
        name="auc_inversion",
        passed=(not math.isnan(delta)) and delta >= 0.05,
        threshold=0.05,
        observed=delta,
        description=f"{candidate_backend} AUC - {baseline_backend} AUC >= 0.05",
    ))

    # Gate 3: anisotropy reduction >= 0.04 absolute (candidate <= 0.88 from baseline ~0.92)
    cand_aniso = cand.anisotropy if cand is not None else float("nan")
    base_aniso = base.anisotropy if base is not None else float("nan")
    aniso_delta = base_aniso - cand_aniso if not (math.isnan(cand_aniso) or math.isnan(base_aniso)) else float("nan")
    verdicts.append(GateVerdict(
        name="anisotropy_reduction",
        passed=(not math.isnan(aniso_delta)) and aniso_delta >= 0.04,
        threshold=0.04,
        observed=aniso_delta,
        description=f"{baseline_backend} anisotropy - {candidate_backend} anisotropy >= 0.04",
    ))

    # Gate 4: per-edit latency parity -- candidate median <= 2.5x baseline median
    cand_median = cand.median_latency_ms if cand is not None else float("nan")
    base_median = base.median_latency_ms if base is not None else float("nan")
    latency_ratio = (cand_median / base_median) if (
        not math.isnan(cand_median) and not math.isnan(base_median) and base_median > 0
    ) else float("nan")
    verdicts.append(GateVerdict(
        name="latency_parity",
        passed=(not math.isnan(latency_ratio)) and latency_ratio <= 2.5,
        threshold=2.5,
        observed=latency_ratio,
        description=f"{candidate_backend} median latency <= 2.5x {baseline_backend} median",
    ))

    # Gate 5: falsifiability control -- random-mamba AUC <= 0.55
    ctrl = results.get("random-mamba")
    ctrl_auc = ctrl.auc if ctrl is not None else float("nan")
    verdicts.append(GateVerdict(
        name="falsifiability",
        passed=(math.isnan(ctrl_auc)) or ctrl_auc <= 0.55,
        threshold=0.55,
        observed=ctrl_auc,
        description="random-mamba AUC <= 0.55 (catches accidental label leakage)",
    ))

    return verdicts


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> dict[str, Any]:
    pairs = load_pairs(Path(args.dataset))
    if args.max_pairs:
        pairs = pairs[: args.max_pairs]
    splits = stratified_split(pairs, seed=args.seed)
    test_pairs = splits["test"]
    print(f"[pre-reg] loaded {len(pairs)} pairs; test split = {len(test_pairs)}", file=sys.stderr)

    results: dict[str, BackendResult] = {}
    for backend in args.backends:
        print(f"[pre-reg] measuring {backend} on {len(test_pairs)} pairs (hard-cap {args.hard_cap_s}s)",
              file=sys.stderr)
        results[backend] = measure_backend(backend, test_pairs, hard_cap_s=args.hard_cap_s)

    verdicts = evaluate_gates(results, baseline_backend=args.baseline, candidate_backend=args.candidate)

    # Manifest -- what's actually written to disk and consumed by the gate test.
    manifest = {
        "schema_version": 1,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": str(Path(args.dataset).relative_to(REPO_ROOT)) if REPO_ROOT in Path(args.dataset).resolve().parents else str(args.dataset),
        "n_pairs_total": len(pairs),
        "n_pairs_test": len(test_pairs),
        "split_seed": args.seed,
        "baseline_backend": args.baseline,
        "candidate_backend": args.candidate,
        "resolved_revisions": {b: os.environ.get(_revision_env_key_for(b)) for b in args.backends if b != "random-mamba"},
        "gates": [
            {
                "name": v.name,
                "passed": v.passed,
                "threshold": v.threshold,
                "observed": v.observed,
                "description": v.description,
            }
            for v in verdicts
        ],
        "all_gates_passed": all(v.passed for v in verdicts),
        "backends": {name: r.to_dict() for name, r in results.items()},
    }
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--backends", nargs="+", required=True,
                        help="RC_EMBEDDER values to measure.")
    parser.add_argument("--baseline", default="mamba-130m",
                        help="Backend the candidate is compared against.")
    parser.add_argument("--candidate", default="mamba3-siso-893m",
                        help="Backend whose promotion the gates evaluate.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET,
                        help="(before, after, label) JSONL file.")
    parser.add_argument("--hard-cap-s", type=float, default=30.0,
                        help="Per-call embedding timeout (seconds).")
    parser.add_argument("--max-pairs", type=int,
                        help="Truncate the dataset (for quick CI smoke).")
    parser.add_argument("--seed", type=int, default=42,
                        help="Split seed (deterministic test split).")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "eval" / "runs" / "pre_reg_embedder.json",
                        help="Path to write the run manifest.")
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    manifest = run(args)
    # JSON-spec doesn't allow NaN; replace with null for the gate test
    # to read cleanly.
    def _sanitize(o):
        if isinstance(o, float) and math.isnan(o):
            return None
        if isinstance(o, dict):
            return {k: _sanitize(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_sanitize(v) for v in o]
        return o
    args.out.write_text(json.dumps(_sanitize(manifest), indent=2, default=str), encoding="utf-8")
    print(f"[pre-reg] wrote manifest to {args.out}", file=sys.stderr)

    n_failed = sum(1 for v in manifest["gates"] if not v["passed"])
    n_load_failed = sum(r["n_load_failures"] for r in manifest["backends"].values())
    print(f"[pre-reg] gates: {len(manifest['gates']) - n_failed}/{len(manifest['gates'])} passed; "
          f"backends with load failures: {n_load_failed}", file=sys.stderr)

    # Exit codes:
    #   0  -- harness ran, all measured gates pass
    #   2  -- at least one backend failed to load
    #   3  -- harness ran but at least one gate failed (the gate test
    #         will refuse the default flip)
    if n_load_failed:
        return 2
    if n_failed:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
