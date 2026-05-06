"""kNN density estimator for plan markdown (P4 tracker #33).

LLM-scientist correction folded in: we DON'T auto-reject OOD plans.
Plans that fall far from the manifold of approved plans are routed to
human review via a flag in the audit log; the gate stays advisory.

Usage:
    from _ood_detector import is_ood
    if is_ood(plan_text, repo_root):
        # log signal_source=ood; do not block
        ...
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional


def _approved_corpus_dir(repo_root: Path) -> Path:
    return repo_root / "thoughts" / "shared" / "plans"


def _list_approved_plans(repo_root: Path) -> list:
    d = _approved_corpus_dir(repo_root)
    if not d.exists():
        return []
    return [p for p in d.glob("*.md") if p.is_file()]


def _embed_text(text: str):
    """Defer to ssm_backbone; same interface validate_embedder uses."""
    try:
        from src import ssm_backbone  # type: ignore
        return ssm_backbone.embed(text[:8000])
    except Exception:  # noqa: BLE001
        return None


def _cosine(a, b) -> float:
    try:
        import torch
        na = float(torch.linalg.norm(a))
        nb = float(torch.linalg.norm(b))
        if na == 0.0 or nb == 0.0:
            return 1.0
        return float(torch.dot(a, b) / (na * nb))
    except Exception:  # noqa: BLE001
        return 1.0


def _knn_distance(plan_vec, corpus_vecs: Iterable, k: int = 3) -> float:
    """Mean cosine-distance to the k nearest approved plans."""
    if plan_vec is None or not corpus_vecs:
        return 0.0
    sims = sorted([_cosine(plan_vec, v) for v in corpus_vecs], reverse=True)[:k]
    if not sims:
        return 0.0
    mean_sim = sum(sims) / len(sims)
    return 1.0 - mean_sim


def _corpus_cache_path(repo_root: Path) -> Path:
    """Disk-persistent corpus cache. Survives per-hook process restarts."""
    import hashlib
    digest = hashlib.sha1(str(repo_root).encode("utf-8")).hexdigest()[:12]
    return Path.home() / ".local" / "state" / "reasoning-core" / f"ood_corpus.{digest}.npz"


def _build_or_load_corpus(repo_root: Path, approved: list):
    """Load corpus_vecs from cache if fresh, else embed once + persist.

    Reviewer-flagged: prior code embedded all approved plans on every
    is_ood() call → O(N) SSM forwards per hook fire (~150-250s on 50-plan
    corpus). Hot path now reads cache; (re)build only when plan-corpus
    mtime advances past the cache mtime.
    """
    cache = _corpus_cache_path(repo_root)
    try:
        if cache.exists():
            cache_mtime = cache.stat().st_mtime
            stale = any(p.stat().st_mtime > cache_mtime for p in approved)
            if not stale:
                import numpy as np
                data = np.load(cache, allow_pickle=False)
                return [data[k] for k in data.files]
    except (OSError, Exception):  # noqa: BLE001
        pass
    # Rebuild
    corpus_vecs = []
    for p in approved:
        try:
            v = _embed_text(p.read_text(encoding="utf-8", errors="replace"))
            if v is not None:
                corpus_vecs.append(v)
        except OSError:
            continue
    try:
        import numpy as np
        cache.parent.mkdir(parents=True, exist_ok=True)
        # Convert torch tensors to numpy for npz
        np.savez(cache, *[v.detach().cpu().numpy() if hasattr(v, "detach") else v for v in corpus_vecs])
    except Exception:  # noqa: BLE001
        pass
    return corpus_vecs


def is_ood(plan_text: str, repo_root: Optional[Path] = None, k: int = 3, threshold: float = 0.40) -> bool:
    """True if plan is far from the manifold of approved plans.

    Threshold is a v0 placeholder — recalibrated in P7 once labeled corpus
    accumulates. Returning True doesn't block; consumer routes to human.
    Disk-cache for corpus embeddings prevents O(N) SSM forwards on every
    call.
    """
    if not plan_text:
        return False
    if repo_root is None:
        repo_root = Path.cwd()
    approved = _list_approved_plans(repo_root)
    if len(approved) < 5:
        return False
    plan_vec = _embed_text(plan_text)
    if plan_vec is None:
        return False
    corpus_vecs = _build_or_load_corpus(repo_root, approved)
    dist = _knn_distance(plan_vec, corpus_vecs, k=k)
    return dist > threshold
