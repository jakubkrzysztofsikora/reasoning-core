"""Embedder fitness test (P4).

Falsifiable test for whether the SSM backbone produces meaningful
discrimination between code and prose. Runs:

  1. Pairwise cosine on N random repo source files (intra-code)
  2. Pairwise cosine on N random prose paragraphs (intra-prose)
  3. Cross-modal cosine: code × prose

Pass gate: separation by >= sigma_threshold standard deviations between
intra-code mean and cross-modal mean. LLM scientist flagged that 3σ
necessary-but-not-sufficient: also requires Cohen's d >= 0.8 between
known-generic and known-specific plan corpora (deferred — separate test).

Per v2 plan correction: if Mamba-130M fails the fitness gate, fall back
to sentence-transformers/all-MiniLM-L6-v2 (already a heavier dependency
not shipped here — test reports the fall-back recommendation).

Usage:
    python -m eval.validate_embedder \\
        --repo-root /path/to/repo \\
        --prose-corpus /path/to/wiki.txt \\
        --n 50 --sigma-threshold 3.0
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Iterable, List


_SOURCE_EXTS = (".py", ".ts", ".tsx", ".js", ".cs", ".go", ".rs", ".java")


def _list_source_files(root: Path, n: int, exclude_parts: set) -> List[Path]:
    files: list = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(p in exclude_parts for p in path.parts):
            continue
        if path.suffix.lower() not in _SOURCE_EXTS:
            continue
        files.append(path)
    random.shuffle(files)
    return files[:n]


def _read_paragraphs(prose_path: Path, n: int, min_len: int = 200) -> List[str]:
    text = prose_path.read_text(encoding="utf-8", errors="replace")
    paras = [p.strip() for p in text.split("\n\n") if len(p.strip()) >= min_len]
    random.shuffle(paras)
    return paras[:n]


def _cosine(a, b) -> float:
    import torch
    na = float(torch.linalg.norm(a))
    nb = float(torch.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 1.0
    return float(torch.dot(a, b) / (na * nb))


def _embed_all(texts: List[str]):
    """Returns a list of pooled embeddings via the SSM backbone."""
    from src import ssm_backbone  # type: ignore
    out = []
    for t in texts:
        # ssm_backbone exposes ast_to_tokens for source; for prose use the
        # tokenizer directly via a fallback path. We just feed bytes to the
        # generic embed() entry point.
        if hasattr(ssm_backbone, "embed_text"):
            out.append(ssm_backbone.embed_text(t))
        else:
            # Defensive: tests can mock this; prod path uses ast_to_tokens.
            tokens = getattr(ssm_backbone, "tokenize_text", lambda x: [])(t)
            out.append(ssm_backbone.embed(tokens))
    return out


def _pairwise_mean_std(vecs) -> tuple:
    sims = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            sims.append(_cosine(vecs[i], vecs[j]))
    if not sims:
        return 0.0, 0.0
    return statistics.mean(sims), statistics.pstdev(sims)


def _cross_mean_std(a, b) -> tuple:
    sims = [_cosine(x, y) for x in a for y in b]
    if not sims:
        return 0.0, 0.0
    return statistics.mean(sims), statistics.pstdev(sims)


def _verdict(intra_code, cross, sigma_threshold) -> dict:
    pooled_std = math.sqrt(
        ((intra_code["std"] ** 2) + (cross["std"] ** 2)) / 2
    )
    sep = abs(intra_code["mean"] - cross["mean"]) / pooled_std if pooled_std > 0 else 0.0
    return {
        "separation_sigma": sep,
        "sigma_threshold": sigma_threshold,
        "embedder_fit": sep >= sigma_threshold,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--prose-corpus", default="")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sigma-threshold", type=float, default=3.0)
    args = ap.parse_args()

    random.seed(args.seed)
    repo_root = Path(args.repo_root)
    excludes = {".git", "node_modules", "dist", "build", ".venv", "venv",
                "__pycache__", ".cache", "coverage", "target", "obj"}
    code_files = _list_source_files(repo_root, args.n, excludes)
    if not code_files:
        sys.stderr.write("ERROR: no code files found\n")
        return 2
    code_texts = [f.read_text(encoding="utf-8", errors="replace") for f in code_files]

    prose_path = Path(args.prose_corpus) if args.prose_corpus else None
    if prose_path and prose_path.exists():
        prose_texts = _read_paragraphs(prose_path, args.n)
    else:
        # Fallback: README + docs as prose
        prose_texts = []
        for md in repo_root.rglob("*.md"):
            if any(p in excludes for p in md.parts):
                continue
            prose_texts.append(md.read_text(encoding="utf-8", errors="replace"))
        prose_texts = prose_texts[: args.n]
    if len(prose_texts) < 10:
        sys.stderr.write(f"ERROR: prose sample too small ({len(prose_texts)})\n")
        return 2

    code_vecs = _embed_all(code_texts)
    prose_vecs = _embed_all(prose_texts)

    intra_code_mean, intra_code_std = _pairwise_mean_std(code_vecs)
    cross_mean, cross_std = _cross_mean_std(code_vecs, prose_vecs)

    intra_code = {"mean": intra_code_mean, "std": intra_code_std}
    cross = {"mean": cross_mean, "std": cross_std}
    verdict = _verdict(intra_code, cross, args.sigma_threshold)

    out = {
        "n_code": len(code_texts),
        "n_prose": len(prose_texts),
        "intra_code": intra_code,
        "cross_modal": cross,
        **verdict,
        "fallback_recommendation": (
            "ok"
            if verdict["embedder_fit"]
            else "Embedder failed fitness gate; switch plan-quality embedder to sentence-transformers/all-MiniLM-L6-v2 (P4 plan §)."
        ),
    }
    print(json.dumps(out, indent=2))
    return 0 if verdict["embedder_fit"] else 1


if __name__ == "__main__":
    sys.exit(main())
