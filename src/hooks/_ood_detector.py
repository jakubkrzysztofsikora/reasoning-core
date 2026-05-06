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


def is_ood(plan_text: str, repo_root: Optional[Path] = None, k: int = 3, threshold: float = 0.40) -> bool:
    """True if plan is far from the manifold of approved plans.

    Threshold is a v0 placeholder — recalibrated in P7 once labeled corpus
    accumulates. Returning True doesn't block; consumer routes to human.
    """
    if not plan_text:
        return False
    if repo_root is None:
        repo_root = Path.cwd()
    approved = _list_approved_plans(repo_root)
    if len(approved) < 5:
        # Insufficient corpus for kNN; abstain.
        return False
    plan_vec = _embed_text(plan_text)
    if plan_vec is None:
        return False
    corpus_vecs = []
    for p in approved:
        try:
            v = _embed_text(p.read_text(encoding="utf-8", errors="replace"))
            if v is not None:
                corpus_vecs.append(v)
        except OSError:
            continue
    dist = _knn_distance(plan_vec, corpus_vecs, k=k)
    return dist > threshold
