#!/usr/bin/env python3
"""Offline random-mamba replay harness for reasoning-core audit events.

Phase 0 deliverable for the eval-random-mamba-engineer scope. The harness:

1. Reads JSONL audit events from ``~/.local/share/reasoning-core/events/``.
2. Checks whether those events contain replayable inputs (``before_src``,
   ``after_src``, ``ast_token_stream``, or ``diff_hunk``).
3. Scores the replayable inputs (plus built-in regression fixtures and any
   user-supplied fixture JSONL) under both ``RC_EMBEDDER=random-mamba`` and
   ``RC_EMBEDDER=mamba-130m``.
4. Emits per-embedder flag rates, an agreement matrix, and a labeling scaffold
   for ~200 block/would-be-block events.
5. Optionally reads back the labeled scaffold and computes precision.

The script is designed to run locally without network credentials: the
``random-mamba`` embedder uses randomly-initialised weights and a local
whitespace tokenizer. ``mamba-130m`` is only attempted when it is already
present in the local HuggingFace cache.

Usage:
    python3 eval/random_mamba_replay.py --output report.json --scaffold labels.csv
    python3 eval/random_mamba_replay.py --output report.json --scaffold labels.csv --fixtures fixtures.jsonl
    python3 eval/random_mamba_replay.py --labels-file labels.filled.csv --output report.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SCORE_WORKER = r"""
import json, os, sys
repo = sys.argv[1]
sys.path.insert(0, repo)
from src.s2_core import score_change
pairs = json.load(sys.stdin)
for pair in pairs:
    try:
        report = score_change(
            pair.get("path", "unknown.py"),
            pair.get("before", ""),
            pair.get("after", ""),
            session_id=pair.get("session_id"),
        )
        out = {
            "regression_detected": report.regression_detected,
            "ais": report.architectural_impact_score,
            "coherence_delta": report.coherence_delta,
            "risk_vector": report.risk_vector,
            "fired_conditions": report.fired_conditions,
            "fired_dims": report.fired_dims,
            "file_kind": report.file_kind,
        }
    except Exception as exc:
        out = {"error": str(exc), "type": type(exc).__name__}
    print(json.dumps(out, ensure_ascii=False), flush=True)
"""

REPLAYABLE_FIELDS = (
    "before_src",
    "after_src",
    "ast_token_stream",
    "diff_hunk",
)

BUILTIN_FIXTURES: list[dict[str, Any]] = [
    {
        "path": "builtin_benign.py",
        "before": "def f(n):\n    if not n:\n        return 0\n    return n + f(n - 1)\n",
        "after": "def f(n):\n    if n == 0:\n        return 0\n    return n + f(n - 1)\n",
        "ground_truth_label": "ok",
        "source": "builtin_fixture",
    },
    {
        "path": "builtin_regression.py",
        "before": "def f(n):\n    if not n:\n        return 0\n    return n + f(n - 1)\n",
        "after": "def f(n):\n"
        + "".join(f"    if n == {i}:\n        return {i}\n" for i in range(25))
        + "    return n + f(n + 1)\n",
        "ground_truth_label": "regression",
        "source": "builtin_fixture",
    },
    {
        "path": "builtin_empty_to_code.py",
        "before": "",
        "after": "def helper(x):\n    return x * 2\n",
        "ground_truth_label": "ok",
        "source": "builtin_fixture",
    },
]


def _default_audit_root() -> Path:
    return Path(os.path.expanduser("~/.local/share/reasoning-core/events"))


def _iter_audit_events(root: Path) -> list[dict[str, Any]]:
    """Yield every JSON object found under ``root`` (one per JSONL line)."""
    events: list[dict[str, Any]] = []
    if not root.exists():
        return events
    for day_dir in sorted(root.iterdir()):
        if not day_dir.is_dir():
            continue
        for jsonl in sorted(day_dir.iterdir()):
            if not jsonl.is_file() or not jsonl.name.endswith(".jsonl"):
                continue
            try:
                with jsonl.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(obj, dict):
                            events.append(obj)
            except OSError:
                continue
    return events


def _event_replayability(event: dict[str, Any]) -> dict[str, Any]:
    """Return which replayable fields are present and a human-readable verdict."""
    present = {f for f in REPLAYABLE_FIELDS if event.get(f) not in (None, "")}
    has_source = bool(event.get("before_src") and event.get("after_src"))
    has_tokens = bool(event.get("ast_token_stream"))
    has_diff = bool(event.get("diff_hunk"))
    replayable = has_source or has_tokens or has_diff
    return {
        "present_fields": sorted(present),
        "has_source_pair": has_source,
        "has_token_stream": has_tokens,
        "has_diff_hunk": has_diff,
        "is_replayable": replayable,
    }


def _extract_pair_from_event(event: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Convert a replayable audit event into a scoring pair, or None."""
    rep = _event_replayability(event)
    if not rep["is_replayable"]:
        return None
    path = event.get("file_path", "audit_event.py")
    if rep["has_source_pair"]:
        return {
            "path": path,
            "before": event["before_src"],
            "after": event["after_src"],
            "session_id": event.get("session_id"),
            "decision_id": event.get("decision_id"),
            "original_decision": event.get("decision"),
            "source": "audit_event",
        }
    if rep["has_token_stream"]:
        # Token streams are already AST-token strings; feed them as raw source
        # so the SSM embedder sees the same representation.
        return {
            "path": path,
            "before": event.get("ast_token_stream_before", ""),
            "after": event["ast_token_stream"],
            "session_id": event.get("session_id"),
            "decision_id": event.get("decision_id"),
            "original_decision": event.get("decision"),
            "source": "audit_token_stream",
        }
    if rep["has_diff_hunk"]:
        # Best-effort: score the diff hunk as the "after" state with empty before.
        return {
            "path": path,
            "before": "",
            "after": event["diff_hunk"],
            "session_id": event.get("session_id"),
            "decision_id": event.get("decision_id"),
            "original_decision": event.get("decision"),
            "source": "audit_diff_hunk",
        }
    return None


def _load_fixtures(path: Optional[Path]) -> list[dict[str, Any]]:
    """Load user-supplied fixture JSONL."""
    if path is None or not path.exists():
        return []
    fixtures: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "before" in obj and "after" in obj:
                obj.setdefault("source", "user_fixture")
                fixtures.append(obj)
    return fixtures


def _score_pairs(pairs: list[dict[str, Any]], embedder: str) -> list[dict[str, Any]]:
    """Score all pairs with one embedder in a fresh subprocess.

    A subprocess is used so the embedder singleton loads exactly once per
    backend and there is no in-process state contamination between backends.
    """
    env = os.environ.copy()
    env["RC_EMBEDDER"] = embedder
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["HF_HUB_OFFLINE"] = "1"
    # Suppress the mamba-ssm fast-path warning that pollutes stdout.
    env["PYTHONWARNINGS"] = "ignore"

    proc = subprocess.run(
        [sys.executable, "-c", SCORE_WORKER, str(REPO_ROOT)],
        input=json.dumps(pairs),
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    results: list[dict[str, Any]] = []
    if proc.returncode != 0:
        # Worker died wholesale; surface the stderr once.
        return [{"error": proc.stderr.strip(), "type": "worker_crash"} for _ in pairs]
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            results.append({"error": line, "type": "json_decode"})
    # Pad if the worker emitted fewer lines than pairs.
    while len(results) < len(pairs):
        results.append({"error": "missing_result", "type": "worker_short"})
    return results


def _build_corpus(
    events: list[dict[str, Any]],
    user_fixtures: list[dict[str, Any]],
    limit: Optional[int],
) -> list[dict[str, Any]]:
    """Build a corpus from replayable events, builtins, and user fixtures."""
    corpus: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for event in events:
        pair = _extract_pair_from_event(event)
        if pair is None:
            continue
        pair_id = pair.get("decision_id") or f"{pair['path']}:{len(corpus)}"
        if pair_id in seen_ids:
            continue
        seen_ids.add(pair_id)
        pair["pair_id"] = pair_id
        corpus.append(pair)

    for fixture in BUILTIN_FIXTURES + user_fixtures:
        pair_id = fixture.get("decision_id") or f"fixture:{fixture.get('path', len(corpus))}"
        if pair_id in seen_ids:
            continue
        seen_ids.add(pair_id)
        pair = {
            "path": fixture.get("path", "fixture.py"),
            "before": fixture["before"],
            "after": fixture["after"],
            "session_id": fixture.get("session_id"),
            "decision_id": pair_id,
            "original_decision": fixture.get("original_decision"),
            "ground_truth_label": fixture.get("ground_truth_label"),
            "source": fixture.get("source", "fixture"),
        }
        pair["pair_id"] = pair_id
        corpus.append(pair)

    if limit is not None and limit > 0:
        corpus = corpus[:limit]
    return corpus


def _agreement(real_flags: list[bool], rand_flags: list[bool]) -> dict[str, Any]:
    """Return a simple 2x2 agreement table between two boolean vectors."""
    n = len(real_flags)
    both = sum(1 for r, x in zip(real_flags, rand_flags) if r and x)
    only_real = sum(1 for r, x in zip(real_flags, rand_flags) if r and not x)
    only_random = sum(1 for r, x in zip(real_flags, rand_flags) if not r and x)
    neither = sum(1 for r, x in zip(real_flags, rand_flags) if not r and not x)
    return {
        "n": n,
        "both_flagged": both,
        "only_real_flagged": only_real,
        "only_random_flagged": only_random,
        "neither_flagged": neither,
        "agreement_rate": round((both + neither) / max(1, n), 4),
        "cohen_kappa": _cohen_kappa(real_flags, rand_flags),
    }


def _cohen_kappa(a: list[bool], b: list[bool]) -> float:
    """Cohen's kappa for two raters with binary decisions."""
    n = len(a)
    if n == 0:
        return 0.0
    p_a = sum(1 for x, y in zip(a, b) if x == y) / n
    p_a1 = sum(a) / n
    p_b1 = sum(b) / n
    p_e = p_a1 * p_b1 + (1 - p_a1) * (1 - p_b1)
    if p_e >= 0.9999:
        return 1.0 if p_a > 0.9999 else 0.0
    return round((p_a - p_e) / (1 - p_e), 4)


def _compute_precision(
    labels: list[str],
    flags: list[bool],
) -> dict[str, Any]:
    """Compute precision/recall for the SSM regression flag against labels.

    Labels must be one of: ``regression`` (positive), ``ok`` (negative), or
    blank/unknown (ignored).
    """
    tp = fp = fn = tn = 0
    for label, flag in zip(labels, flags):
        if label == "regression":
            if flag:
                tp += 1
            else:
                fn += 1
        elif label == "ok":
            if flag:
                fp += 1
            else:
                tn += 1
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    f1 = 2 * tp / max(1, 2 * tp + fp + fn)
    return {
        "n_labeled": tp + fp + fn + tn,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def _write_scaffold(
    path: Path,
    rows: list[dict[str, Any]],
    embedders: list[str],
    max_rows: int = 200,
) -> int:
    """Write a CSV scaffold for hand-labeling block/would-be-block events."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep only rows that are blocks or would-be-blocks under at least one embedder.
    scaffold_rows = [
        r for r in rows
        if any(r.get(f"{e}_regression_detected") for e in embedders)
        or r.get("original_decision") == "blocked"
    ]
    scaffold_rows = scaffold_rows[:max_rows]

    fieldnames = [
        "row_id",
        "pair_id",
        "embedder",
        "file_path",
        "source",
        "original_decision",
        "regression_detected",
        "coherence_delta",
        "ais",
        "fired_dims",
        "ground_truth_label",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in scaffold_rows:
            for embedder in embedders:
                prefix = f"{embedder}_"
                writer.writerow({
                    "row_id": uuid.uuid4().hex[:12],
                    "pair_id": row.get("pair_id", ""),
                    "embedder": embedder,
                    "file_path": row.get("path", ""),
                    "source": row.get("source", ""),
                    "original_decision": row.get("original_decision", ""),
                    "regression_detected": row.get(f"{prefix}regression_detected", ""),
                    "coherence_delta": row.get(f"{prefix}coherence_delta", ""),
                    "ais": row.get(f"{prefix}ais", ""),
                    "fired_dims": ",".join(row.get(f"{prefix}fired_dims", []) or []),
                    "ground_truth_label": row.get("ground_truth_label", ""),
                    "notes": "",
                })
    return len(scaffold_rows)


def _read_labels(path: Path) -> dict[tuple[str, str], str]:
    """Read a labeled scaffold CSV keyed by (pair_id, embedder)."""
    labels: dict[tuple[str, str], str] = {}
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            label = (row.get("ground_truth_label") or "").strip().lower()
            if label in ("regression", "ok"):
                key = (row.get("pair_id", ""), row.get("embedder", ""))
                labels[key] = label
    return labels


def run_replay(
    *,
    audit_root: Path,
    fixtures_path: Optional[Path],
    output_path: Optional[Path],
    scaffold_path: Optional[Path],
    labels_path: Optional[Path],
    embedders: list[str],
    limit: Optional[int],
) -> dict[str, Any]:
    """Run the full offline replay and return the report dict."""
    started = time.time()
    events = _iter_audit_events(audit_root)
    replayability = Counter(_event_replayability(e)["is_replayable"] for e in events)
    field_presence = Counter()
    for e in events:
        for f in _event_replayability(e)["present_fields"]:
            field_presence[f] += 1

    user_fixtures = _load_fixtures(fixtures_path)
    corpus = _build_corpus(events, user_fixtures, limit)

    per_embedder: dict[str, list[dict[str, Any]]] = {}
    for embedder in embedders:
        per_embedder[embedder] = _score_pairs(corpus, embedder)

    # Enrich corpus rows with per-embedder scores.
    enriched_rows: list[dict[str, Any]] = []
    for pair, *embedder_results in zip(corpus, *per_embedder.values()):
        row = dict(pair)
        for embedder, result in zip(embedders, embedder_results):
            prefix = f"{embedder}_"
            for key, value in result.items():
                row[f"{prefix}{key}"] = value
        enriched_rows.append(row)

    embedder_summaries: dict[str, Any] = {}
    for embedder in embedders:
        results = per_embedder[embedder]
        flags = [bool(r.get("regression_detected")) for r in results]
        errors = sum(1 for r in results if "error" in r)
        n = len(results) - errors
        embedder_summaries[embedder] = {
            "n_scored": n,
            "n_errors": errors,
            "regression_flags": sum(flags),
            "regression_rate": round(sum(flags) / max(1, n), 4),
        }

    agreement = None
    if len(embedders) == 2:
        a_flags = [bool(r.get("regression_detected")) for r in per_embedder[embedders[0]]]
        b_flags = [bool(r.get("regression_detected")) for r in per_embedder[embedders[1]]]
        agreement = _agreement(a_flags, b_flags)

    precision = None
    if labels_path and labels_path.exists():
        labels = _read_labels(labels_path)
        precision = {}
        for embedder in embedders:
            ordered = [
                labels.get((r.get("pair_id", ""), embedder), "")
                for r in enriched_rows
            ]
            flags = [bool(r.get(f"{embedder}_regression_detected")) for r in enriched_rows]
            precision[embedder] = _compute_precision(ordered, flags)

    real_backend = embedders[0] if len(embedders) >= 1 else ""
    random_backend = embedders[1] if len(embedders) >= 2 else ""
    real_rate = embedder_summaries.get(real_backend, {}).get("regression_rate", 0.0)
    random_rate = embedder_summaries.get(random_backend, {}).get("regression_rate", 0.0)
    real_errors = embedder_summaries.get(real_backend, {}).get("n_errors", 0)
    random_errors = embedder_summaries.get(random_backend, {}).get("n_errors", 0)
    # Falsifiability rule from scripts/run_random_mamba_control.py, extended to
    # require both backends to run without wholesale errors.
    is_signal_non_null = (
        len(embedders) >= 2
        and random_rate < real_rate * 0.5
        and real_errors == 0
        and random_errors == 0
        and real_rate > 0
    )

    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "audit_root": str(audit_root),
        "n_audit_events": len(events),
        "replayability": {
            "replayable_events": replayability.get(True, 0),
            "non_replayable_events": replayability.get(False, 0),
            "field_presence": dict(field_presence),
        },
        "corpus": {
            "n_pairs": len(corpus),
            "n_builtin_fixtures": len(BUILTIN_FIXTURES),
            "n_user_fixtures": len(user_fixtures),
            "n_from_audit": len(corpus) - len(BUILTIN_FIXTURES) - len(user_fixtures),
        },
        "embedders": embedder_summaries,
        "agreement": agreement,
        "precision": precision,
        "verdict": {
            "is_ssm_signal_non_null": is_signal_non_null,
            "text": (
                "SSM signal is non-null (random-mamba flags significantly fewer regressions)"
                if is_signal_non_null
                else "SSM signal may be null (random-mamba flags similar rate or backends errored)"
            ),
        },
        "per_pair": enriched_rows,
        "duration_seconds": round(time.time() - started, 2),
    }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)

    if scaffold_path:
        n_scaffold = _write_scaffold(scaffold_path, enriched_rows, embedders)
        report["labeling_scaffold"] = {
            "path": str(scaffold_path),
            "rows_written": n_scaffold,
        }

    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-root",
        type=Path,
        default=_default_audit_root(),
        help="Root directory containing JSONL audit events (default: ~/.local/share/reasoning-core/events/)",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=None,
        help="Optional JSONL file with user-supplied {path, before, after} pairs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to write the JSON report.",
    )
    parser.add_argument(
        "--scaffold",
        type=Path,
        default=None,
        help="Path to write the labeling scaffold CSV.",
    )
    parser.add_argument(
        "--labels-file",
        type=Path,
        default=None,
        help="Labeled scaffold CSV; if provided, precision is computed.",
    )
    parser.add_argument(
        "--embedders",
        type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        default=["mamba-130m", "random-mamba"],
        help="Comma-separated embedder backends to compare (default: mamba-130m,random-mamba).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of pairs to score (useful for quick smoke tests).",
    )
    args = parser.parse_args(argv)

    report = run_replay(
        audit_root=args.audit_root,
        fixtures_path=args.fixtures,
        output_path=args.output,
        scaffold_path=args.scaffold,
        labels_path=args.labels_file,
        embedders=args.embedders,
        limit=args.limit,
    )

    # Human-readable summary on stdout.
    print(json.dumps({
        "n_audit_events": report["n_audit_events"],
        "replayable_events": report["replayability"]["replayable_events"],
        "corpus_size": report["corpus"]["n_pairs"],
        "embedders": report["embedders"],
        "agreement": report["agreement"],
        "precision": report["precision"],
        "verdict": report["verdict"],
        "duration_seconds": report["duration_seconds"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
