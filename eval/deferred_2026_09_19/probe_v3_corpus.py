"""Quick A/B probe on the labeled grounding_pairs_v3 corpus.

Establishes whether mamba-130m (default) embeddings separate labeled positives
from negatives on real labeled diffs. This is a *descriptive* baseline; the
advisory scoring layer's only hard-block role is when deterministic checks
fire, per AGENTS.md.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.pop("RC_EMBEDDER", None)

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


def main():
    rows = [json.loads(l) for l in CORPUS.read_text().splitlines() if l.strip()]
    print(f"corpus: n={len(rows)} pos={sum(1 for r in rows if r.get('label')==1)} "
          f"neg={sum(1 for r in rows if r.get('label')==0)}")

    t0 = time.time()
    ssm_backbone.embed("warmup")
    print(f"backbone warmup: {time.time()-t0:.2f}s")

    pos_cos, neg_cos, pos_cd, neg_cd = [], [], [], []

    t0 = time.time()
    for i, row in enumerate(rows):
        before, after = _extract_texts(row["hunk"])
        emb_b = ssm_backbone.embed(before or " ")
        emb_a = ssm_backbone.embed(after or " ")
        cos = s2_core._cosine_similarity(emb_b, emb_a)
        cd = s2_core._l2_distance(emb_b, emb_a)
        if row.get("label") == 1:
            pos_cos.append(cos); pos_cd.append(cd)
        else:
            neg_cos.append(cos); neg_cd.append(cd)
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(rows)} elapsed={time.time()-t0:.1f}s")

    def _summ(name, xs):
        if not xs:
            return f"{name}: empty"
        return (f"{name}: n={len(xs)} mean={statistics.mean(xs):.3f} "
                f"sd={statistics.pstdev(xs):.3f} min={min(xs):.3f} max={max(xs):.3f}")

    print(_summ("pos.cos", pos_cos))
    print(_summ("neg.cos", neg_cos))
    print(_summ("pos.cd  ", pos_cd))
    print(_summ("neg.cd  ", neg_cd))

    def _cohens_d(a, b):
        if len(a) < 2 or len(b) < 2:
            return 0.0
        ma, mb = statistics.mean(a), statistics.mean(b)
        va = statistics.pstdev(a) ** 2
        vb = statistics.pstdev(b) ** 2
        pooled = ((va + vb) / 2) ** 0.5
        return 0.0 if pooled == 0 else (ma - mb) / pooled

    print(f"cohens_d(cos): {_cohens_d(pos_cos, neg_cos):.3f}  "
          f"cohens_d(cd): {_cohens_d(pos_cd, neg_cd):.3f}")
    print(f"total runtime: {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
