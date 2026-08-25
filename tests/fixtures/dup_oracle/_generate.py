#!/usr/bin/env python3
"""ONE-TIME generator for the date-fns near-duplicate fixture. NOT a test.

Run with the code embedder available (the ONLY place the model runs):

    RC_EMBEDDER=unixcoder-base .venv/bin/python tests/fixtures/dup_oracle/_generate.py

Writes, next to this file:
  - date_fns_functions.json : [{path, name, line, source}, ...]  (n functions)
  - date_fns_vectors.npy    : (n, 768) float32, L2-normalised, row-aligned
  - PROVENANCE.md           : upstream commit + regeneration command

The .json / .npy pair is the frozen fixture the OFFLINE integration test loads,
so the test never touches the model or the network.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from src.dup_embed import embed_function  # noqa: E402
from src.dup_index import extract_functions  # noqa: E402

HERE = Path(__file__).resolve().parent
DATE_FNS_URL = "https://github.com/date-fns/date-fns.git"
EXCLUDE = ("/locale/", "/fp/", "/_lib/")


def _collect(core: Path) -> list[dict]:
    files = sorted(
        str(p)
        for p in core.rglob("index.ts")
        if not any(x in str(p) for x in EXCLUDE)
        and not str(p).endswith(".d.ts")
        and ".tp.ts" not in str(p)
        and "test" not in p.name.lower()
    )
    records: list[dict] = []
    seen: set[str] = set()
    for f in files:
        src = Path(f).read_text(encoding="utf-8", errors="replace")
        rel = os.path.relpath(f, core)
        for name, line, fsrc in extract_functions(f, src):
            key = fsrc.strip()
            if key in seen:
                continue
            seen.add(key)
            records.append({"path": rel, "name": name, "line": line, "source": fsrc})
    return records


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["git", "clone", "--depth", "1", DATE_FNS_URL, tmp],
            check=True, capture_output=True,
        )
        sha = subprocess.run(
            ["git", "-C", tmp, "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        records = _collect(Path(tmp) / "pkgs" / "core" / "src")

    print(f"extracted {len(records)} functions; embedding...", file=sys.stderr)
    vectors = np.stack([embed_function(r["source"]) for r in records]).astype(np.float32)

    (HERE / "date_fns_functions.json").write_text(json.dumps(records, indent=1))
    np.save(HERE / "date_fns_vectors.npy", vectors)
    (HERE / "PROVENANCE.md").write_text(
        "# date-fns dup-oracle fixture\n\n"
        "Frozen snapshot for the offline near-duplicate integration test "
        "(`tests/test_dup_oracle_integration.py`). Not a live dependency.\n\n"
        f"- Source: date-fns/date-fns @ `{sha}`\n"
        f"- Scope: `pkgs/core/src/**/index.ts`, excluding {', '.join(EXCLUDE)}\n"
        f"- Functions: {len(records)}\n"
        f"- Embedder: `unixcoder-base` (CLS pooling), vectors L2-normalised, "
        f"shape {tuple(vectors.shape)}\n\n"
        "Regenerate:\n\n"
        "    RC_EMBEDDER=unixcoder-base .venv/bin/python "
        "tests/fixtures/dup_oracle/_generate.py\n"
    )
    print(f"wrote fixture: {len(records)} functions, vectors {tuple(vectors.shape)}", file=sys.stderr)


if __name__ == "__main__":
    main()
