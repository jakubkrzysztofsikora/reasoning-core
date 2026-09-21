"""Side-by-side discriminator probe on grounding_pairs_v3.

Runs the labeled corpus through two locally-cached embedders and reports
point-biserial separability (Cohen's d) of (cos, chord_distance) between
labeled positives and negatives. This is the first measurement of the
deferred audit workstream.

Embedders (offline-only):
  * mamba-130m-hf       -- current default. Causal text LM on The Pile.
  * all-mpnet-base-v2   -- sentence-transformers, mean-pooled BERT-style
                           encoder. Not code-specialised, but bidirectional
                           and trained with contrastive objectives, which
                           the audit cites as the missing property.

Per AGENTS.md: deterministic checks are the hard block; neural scoring is
advisory. This probe is descriptive, not load-bearing.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

from src import s2_core  # noqa: E402
from src import ssm_backbone  # noqa: E402

CORPUS = Path("eval/datasets/grounding_pairs_v3.jsonl")


def _extract_texts(hunk):
    before_lines, after_lines = [], []
    for line in hunk.splitlines():
        if (line.startswith("+++") or line.startswith("---")
                or line.startswith("diff ") or line.startswith("index ")
                or line.startswith("new file ") or line.startswith("@@")):
            continue
        if line.startswith("+"):
            after_lines.append(line[1:])
        elif line.startswith("-"):
            before_lines.append(line[1:])
        else:
            before_lines.append(line)
            after_lines.append(line)
    return "\n".join(before_lines), "\n".join(after_lines)


def _cohens_d(a, b):
    if len(a) < 2 or len(b) < 2:
        return 0.0
    ma, mb = statistics.mean(a), statistics.mean(b)
    va = statistics.pstdev(a) ** 2
    vb = statistics.pstdev(b) ** 2
    pooled = ((va + vb) / 2) ** 0.5
    return 0.0 if pooled == 0 else (ma - mb) / pooled


def _build_mpnet():
    import torch
    from transformers import AutoModel, AutoTokenizer

    snap_dir = (Path.home() / ".cache/huggingface/hub/models--sentence-transformers--all-mpnet-base-v2/snapshots")
    snap = next(p for p in snap_dir.iterdir() if p.is_dir())
    tok = AutoTokenizer.from_pretrained(str(snap))
    mdl = AutoModel.from_pretrained(str(snap))
    mdl.eval()

    def embed(text):
        text = text or " "
        enc = tok(text, return_tensors="pt", truncation=True, max_length=384, padding=False)
        with torch.no_grad():
            out = mdl(**enc)
        last = out.last_hidden_state
        mask = enc["attention_mask"].unsqueeze(-1).float()
        summed = (last * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1e-6)
        return (summed / counts).squeeze(0).cpu()

    return embed, "all-mpnet-base-v2"


def main():
    rows = [json.loads(l) for l in CORPUS.read_text().splitlines() if l.strip()]
    print(f"corpus: n={len(rows)} pos={sum(1 for r in rows if r.get('label')==1)} "
          f"neg={sum(1 for r in rows if r.get('label')==0)}", flush=True)

    embedders = []

    t0 = time.time()
    ssm_backbone.embed("warmup")
    print(f"[warmup mamba-130m] {time.time()-t0:.2f}s", flush=True)
    embedders.append(("mamba-130m", ssm_backbone.embed))

    t0 = time.time()
    mpnet_embed, mpnet_name = _build_mpnet()
    print(f"[load {mpnet_name}] {time.time()-t0:.2f}s", flush=True)
    embedders.append((mpnet_name, mpnet_embed))

    for name, embed in embedders:
        print(f"\n=== {name} ===", flush=True)
        pos_cos, neg_cos = [], []
        pos_cd, neg_cd = [], []
        t0 = time.time()
        for i, row in enumerate(rows):
            before, after = _extract_texts(row["hunk"])
            emb_b = embed(before or " ")
            emb_a = embed(after or " ")
            cos = s2_core._cosine_similarity(emb_b, emb_a)
            cd = s2_core._l2_distance(emb_b, emb_a)
            if row.get("label") == 1:
                pos_cos.append(cos); pos_cd.append(cd)
            else:
                neg_cos.append(cos); neg_cd.append(cd)
            if (i + 1) % 25 == 0:
                print(f"  {i+1}/{len(rows)} elapsed={time.time()-t0:.1f}s", flush=True)
        print(f"  pos.cos={statistics.mean(pos_cos):.3f} neg.cos={statistics.mean(neg_cos):.3f}  "
              f"d_cos={_cohens_d(pos_cos, neg_cos):.3f}", flush=True)
        print(f"  pos.cd ={statistics.mean(pos_cd):.3f} neg.cd ={statistics.mean(neg_cd):.3f}  "
              f"d_cd ={_cohens_d(pos_cd, neg_cd):.3f}", flush=True)
        print(f"  total {time.time()-t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
