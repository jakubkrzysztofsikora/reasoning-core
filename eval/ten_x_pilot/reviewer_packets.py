#!/usr/bin/env python3
"""Create arm-blind final-patch packets after collection has completed."""
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
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--unblinding-map", type=Path, required=True, help="restricted path outside reviewer packet directory")
    args = parser.parse_args()
    raw = args.run_dir / "raw.jsonl"
    if not raw.is_file() or args.out_dir.exists() or args.unblinding_map.exists():
        raise SystemExit("raw.jsonl must exist; packet directory and unblinding map must not exist")
    if args.unblinding_map.resolve().is_relative_to(args.out_dir.resolve()):
        raise SystemExit("unblinding map must be outside the reviewer packet directory")
    rows = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
    labels = [(index, row) for index, row in enumerate(rows, 1)]
    random.Random(args.seed).shuffle(labels)
    args.out_dir.mkdir(parents=True)
    mapping: list[dict[str, Any]] = []
    for packet_number, (index, row) in enumerate(labels, 1):
        case = args.run_dir / "runs" / f"repeat-{row['repeat']}" / f"{row['task_id']}-{row['arm']}"
        patch = (case / "final_diff.patch").read_text(encoding="utf-8")
        if "treatment-policy" in patch or "-treatment" in patch:
            raise SystemExit(f"arm label leaked into retained patch for row {index}")
        packet_id = f"packet-{packet_number:04d}"
        packet = {"packet_id": packet_id, "task_id": row["task_id"], "stratum": row["stratum"], "final_diff": patch, "final_oracle": row.get("final_oracle_command_result")}
        packet_path = args.out_dir / f"{packet_id}.json"
        packet_path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
        mapping.append({"packet_id": packet_id, "row_index": index, "task_id": row["task_id"], "arm": row["arm"], "sha256": _sha256(packet_path)})
    args.unblinding_map.parent.mkdir(parents=True, exist_ok=True)
    args.unblinding_map.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    print(args.unblinding_map)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
