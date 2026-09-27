#!/usr/bin/env python3
"""Create compact arm-blind corpus-review packets for a candidate corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    if args.out_dir.exists() or args.manifest.exists():
        raise SystemExit("review packet directory and manifest must not exist")
    corpus = json.loads(args.tasks.read_text(encoding="utf-8"))
    if corpus.get("corpus_status") != "confirmatory_candidate" or not isinstance(corpus.get("tasks"), list):
        raise SystemExit("requires a confirmatory_candidate corpus")
    tasks = list(corpus["tasks"])
    random.Random(args.seed).shuffle(tasks)
    args.out_dir.mkdir(parents=True)
    manifest: list[dict[str, Any]] = []
    for number, task in enumerate(tasks, 1):
        # Review only task semantics and validation states. The arm is not part
        # of a corpus packet and no treatment implementation is disclosed.
        packet = {key: task[key] for key in ("id", "stratum", "prompt", "seed_files", "plan", "policy", "protected_paths", "forbidden_imports", "required_path", "final_oracle", "oracle_validation")}
        packet_id = f"corpus-{number:03d}"
        path = args.out_dir / f"{packet_id}.json"
        path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
        manifest.append({"packet_id": packet_id, "task_id": task["id"], "sha256": _sha256(path)})
    payload = {"schema_version": 2, "tasks_path": str(args.tasks.resolve()), "tasks_sha256": _sha256(args.tasks), "packet_dir": str(args.out_dir.resolve()), "seed": args.seed, "packets": manifest}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
