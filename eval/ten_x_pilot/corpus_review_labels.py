#!/usr/bin/env python3
"""Validate a complete, independently authored corpus-review decision."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKET_FIELDS = ("id", "stratum", "prompt", "seed_files", "plan", "policy", "protected_paths", "forbidden_imports", "required_path", "final_oracle", "oracle_validation")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid {label}: {path}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True,
                        help="JSON object with reviewer metadata and packet decisions")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"refusing to overwrite existing artifact: {args.out}")
    manifest = _read_json(args.manifest, "manifest")
    labels_payload = _read_json(args.labels, "labels")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 2:
        raise SystemExit("manifest must be schema_version 2")
    if not isinstance(labels_payload, dict):
        raise SystemExit("labels must be an object with reviewer and decisions")
    reviewer = labels_payload.get("reviewer")
    labels = labels_payload.get("decisions")
    packets = manifest.get("packets")
    tasks_path = Path(manifest.get("tasks_path", ""))
    packet_dir = Path(manifest.get("packet_dir", ""))
    if not isinstance(reviewer, dict) or not isinstance(reviewer.get("reviewer_id"), str) or not reviewer["reviewer_id"].strip() or not isinstance(reviewer.get("independence_statement"), str) or not reviewer["independence_statement"].strip():
        raise SystemExit("labels need reviewer_id and a non-empty independence_statement")
    if not isinstance(packets, list) or len(packets) != 150 or not isinstance(labels, list) or not tasks_path.is_file() or not packet_dir.is_dir():
        raise SystemExit("manifest or labels have invalid schema")
    if _sha256(tasks_path) != manifest.get("tasks_sha256"):
        raise SystemExit("manifest task hash no longer matches its corpus")
    corpus = _read_json(tasks_path, "corpus")
    if not isinstance(corpus, dict) or corpus.get("corpus_status") != "confirmatory_candidate" or not isinstance(corpus.get("tasks"), list) or len(corpus["tasks"]) != 150:
        raise SystemExit("review requires exactly the unfrozen 150-task candidate corpus")
    tasks_by_id = {task.get("id"): task for task in corpus["tasks"] if isinstance(task, dict)}
    packet_ids: set[str] = set()
    task_ids: set[str] = set()
    task_hashes: dict[str, str] = {}
    packet_task: dict[str, str] = {}
    for item in packets:
        if not isinstance(item, dict) or not isinstance(item.get("packet_id"), str) or not isinstance(item.get("task_id"), str) or not isinstance(item.get("sha256"), str):
            raise SystemExit("every manifest packet needs packet_id, task_id, and sha256")
        packet_id, task_id = item["packet_id"], item["task_id"]
        packet = packet_dir / f"{packet_id}.json"
        if packet_id in packet_ids or task_id in task_ids or not packet.is_file() or _sha256(packet) != item["sha256"]:
            raise SystemExit("packet manifest does not match a unique retained packet")
        task = tasks_by_id.get(task_id)
        if task is None:
            raise SystemExit("packet manifest does not cover the exact corpus task IDs")
        # The packets must be content-identical to a subset of the corpus being
        # frozen, and each label carries a per-task content hash so the review
        # stays verifiable after freeze rewrites corpus_status/frozen_at.
        if _json_sha256(_read_json(packet, "packet")) != _json_sha256({key: task[key] for key in PACKET_FIELDS if key in task}):
            raise SystemExit(f"packet {packet_id} no longer matches its corpus task")
        task_hashes[task_id] = _json_sha256(task)
        packet_task[packet_id] = task_id
        packet_ids.add(packet_id)
        task_ids.add(task_id)
    if task_ids != {task.get("id") for task in corpus["tasks"] if isinstance(task, dict)}:
        raise SystemExit("packet manifest does not cover the exact corpus task IDs")
    by_id: dict[str, dict[str, Any]] = {}
    for label in labels:
        if not isinstance(label, dict) or not isinstance(label.get("packet_id"), str) or type(label.get("approved")) is not bool or not isinstance(label.get("rationale"), str) or not label["rationale"].strip():
            raise SystemExit("every decision needs packet_id, boolean approved, and non-empty rationale")
        packet_id = label["packet_id"]
        if packet_id not in packet_ids or packet_id in by_id:
            raise SystemExit("decisions must contain every manifest packet exactly once")
        by_id[packet_id] = label
    if set(by_id) != packet_ids:
        raise SystemExit("decisions must contain every manifest packet exactly once")
    rejected = [label["packet_id"] for label in by_id.values() if not label["approved"]]
    for label in by_id.values():
        label["task_sha256"] = task_hashes[packet_task[label["packet_id"]]]
    payload = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "manifest_sha256": _sha256(args.manifest),
        "labels_sha256": _sha256(args.labels),
        "tasks_sha256": manifest["tasks_sha256"],
        "task_sha256": task_hashes,
        "packet_hashes_verified": True,
        "reviewer": reviewer,
        "review_passed": not rejected,
        "rejected_packet_ids": rejected,
        "labels": [by_id[item["packet_id"]] for item in packets],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0 if not rejected else 1


if __name__ == "__main__":
    raise SystemExit(main())
