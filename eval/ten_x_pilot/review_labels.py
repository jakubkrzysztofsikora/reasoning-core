#!/usr/bin/env python3
"""Validate blinded completion labels and join them after adjudication.

Reviewers receive only packet IDs. This command must run only after both
independent label files are complete; it uses the restricted unblinding map to
produce a retained, post-adjudication record for the analysis step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_labels(path: Path, packet_ids: set[str], label_name: str) -> dict[str, bool]:
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label_name} label file: {path}") from exc
    if not isinstance(values, list):
        raise ValueError(f"{label_name} label file must be a JSON array")
    output: dict[str, bool] = {}
    for value in values:
        if not isinstance(value, dict):
            raise ValueError(f"{label_name} contains a non-object label")
        packet_id = value.get("packet_id")
        completion = value.get("compliant_task_completion")
        if not isinstance(packet_id, str) or type(completion) is not bool:
            raise ValueError(f"{label_name} labels need packet_id and boolean compliant_task_completion")
        if packet_id not in packet_ids or packet_id in output:
            raise ValueError(f"{label_name} has an unknown or duplicate packet ID: {packet_id}")
        output[packet_id] = completion
    if set(output) != packet_ids:
        raise ValueError(f"{label_name} must label every packet exactly once")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unblinding-map", type=Path, required=True)
    parser.add_argument("--reviewer-a", type=Path, required=True)
    parser.add_argument("--reviewer-b", type=Path, required=True)
    parser.add_argument("--adjudications", type=Path, required=True,
                        help="JSON array containing a final boolean label for every disagreement")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"refusing to overwrite existing artifact: {args.out}")
    try:
        mapping = json.loads(args.unblinding_map.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid unblinding map: {args.unblinding_map}") from exc
    if not isinstance(mapping, list) or not mapping:
        raise SystemExit("unblinding map must be a non-empty JSON array")
    packet_ids = {row.get("packet_id") for row in mapping}
    if len(packet_ids) != len(mapping) or not all(isinstance(value, str) for value in packet_ids):
        raise SystemExit("unblinding map packet IDs must be unique strings")
    try:
        reviewer_a = _load_labels(args.reviewer_a, packet_ids, "reviewer A")
        reviewer_b = _load_labels(args.reviewer_b, packet_ids, "reviewer B")
        adjudicated = _load_labels(args.adjudications, {packet for packet in packet_ids if reviewer_a[packet] != reviewer_b[packet]}, "adjudications")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    labels: list[dict[str, Any]] = []
    for item in mapping:
        packet_id = item["packet_id"]
        disagreement = reviewer_a[packet_id] != reviewer_b[packet_id]
        final = adjudicated[packet_id] if disagreement else reviewer_a[packet_id]
        labels.append({
            "row_index": item["row_index"],
            "packet_id": packet_id,
            "reviewer_a": reviewer_a[packet_id],
            "reviewer_b": reviewer_b[packet_id],
            "disagreement": disagreement,
            "adjudicated": adjudicated.get(packet_id),
            "compliant_task_completion": final,
        })
    payload = {
        "schema_version": 1,
        "unblinding_map_sha256": _sha256(args.unblinding_map),
        "reviewer_a_sha256": _sha256(args.reviewer_a),
        "reviewer_b_sha256": _sha256(args.reviewer_b),
        "adjudications_sha256": _sha256(args.adjudications),
        "labels": sorted(labels, key=lambda item: item["row_index"]),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
