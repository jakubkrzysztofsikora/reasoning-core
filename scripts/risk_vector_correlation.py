#!/usr/bin/env python3
"""Empirical risk-vector dim-redundancy tool over the audit log.

Audit 2026-06-01 §Summary #1 / §B5: three of the 11 risk dims
(`novelty`, `session_centroid_drift`, `project_coupling`) derive from
the same SSM embedding and may be redundant. Compute pairwise Pearson
correlation across all events in the audit log; pairs with |r|>0.7 are
candidates for pruning.

Usage:
    python scripts/risk_vector_correlation.py \
        [--events-root ~/.local/share/reasoning-core/events] \
        [--since 14d] [--min-events 50] [--out -]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import gzip
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a hard dep of the repo
    sys.stderr.write("[risk_vector_correlation] numpy missing; pip install numpy\n")
    sys.exit(2)


_DEFAULT_EVENTS_ROOT = os.path.expanduser("~/.local/share/reasoning-core/events")


def _risk_labels(default_dim: int) -> List[str]:
    """Try to use the live RISK_LABELS from s2_core; fall back to indices."""
    try:
        from src import s2_core  # type: ignore
        labels = list(s2_core.RISK_LABELS)
    except Exception:
        labels = []
    if not labels:
        return [f"dim_{i}" for i in range(default_dim)]
    if default_dim > len(labels):
        labels = labels + [f"dim_{i}" for i in range(len(labels), default_dim)]
    return labels[:default_dim]


def _parse_since(spec: str) -> _dt.datetime:
    """Parse a `Nd` / `Nh` window. Defaults to 14 days."""
    spec = spec.strip().lower()
    if spec.endswith("d"):
        days = int(spec[:-1])
        delta = _dt.timedelta(days=days)
    elif spec.endswith("h"):
        hours = int(spec[:-1])
        delta = _dt.timedelta(hours=hours)
    else:
        delta = _dt.timedelta(days=int(spec))
    return _dt.datetime.now(tz=_dt.timezone.utc) - delta


def _iter_event_files(events_root: str, since: _dt.datetime) -> List[Path]:
    root = Path(events_root)
    out: List[Path] = []
    for day_dir in sorted(root.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]")):
        try:
            day = _dt.datetime.strptime(day_dir.name, "%Y-%m-%d").replace(
                tzinfo=_dt.timezone.utc
            )
        except ValueError:
            continue
        if day < since.replace(hour=0, minute=0, second=0, microsecond=0):
            continue
        for f in day_dir.iterdir():
            if f.suffix in (".jsonl", ".gz") or f.name.endswith(".jsonl.gz"):
                out.append(f)
    return out


def _load_risk_vectors(files: List[Path]) -> "np.ndarray":
    vecs: List[List[float]] = []
    for f in files:
        opener = gzip.open if f.name.endswith(".gz") else open
        try:
            with opener(f, "rt", encoding="utf-8") as fh:  # type: ignore[call-overload]
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    rv = ev.get("risk_vector")
                    if isinstance(rv, list) and rv and all(isinstance(x, (int, float)) for x in rv):
                        vecs.append([float(x) for x in rv])
        except OSError:
            continue
    if not vecs:
        return np.zeros((0, 0))
    max_d = max(len(v) for v in vecs)
    arr = np.full((len(vecs), max_d), np.nan, dtype=np.float64)
    for i, v in enumerate(vecs):
        arr[i, : len(v)] = v
    return arr


def _pearson_pairwise(arr: "np.ndarray") -> "np.ndarray":
    """Pairwise Pearson correlation, NaN-tolerant column-by-column."""
    n, d = arr.shape
    out = np.full((d, d), np.nan, dtype=np.float64)
    for i in range(d):
        out[i, i] = 1.0
        for j in range(i + 1, d):
            mask = ~np.isnan(arr[:, i]) & ~np.isnan(arr[:, j])
            if mask.sum() < 3:
                continue
            xi = arr[mask, i]
            xj = arr[mask, j]
            # Constant columns produce 0/0; numpy returns nan, suppress warning.
            with np.errstate(invalid="ignore", divide="ignore"):
                num = ((xi - xi.mean()) * (xj - xj.mean())).sum()
                den = np.sqrt(((xi - xi.mean()) ** 2).sum() * ((xj - xj.mean()) ** 2).sum())
            r = float(num / den) if den else float("nan")
            out[i, j] = r
            out[j, i] = r
    return out


def _render_markdown(arr: "np.ndarray", labels: List[str]) -> str:
    d = arr.shape[0]
    head = "| dim | " + " | ".join(labels) + " |"
    sep = "|" + "---|" * (d + 1)
    rows = [head, sep]
    for i in range(d):
        cells = []
        for j in range(d):
            v = arr[i, j]
            cells.append("nan" if np.isnan(v) else f"{v:.3f}")
        rows.append(f"| {labels[i]} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _find_redundant_pairs(arr: "np.ndarray", labels: List[str], thresh: float = 0.7) -> List[str]:
    d = arr.shape[0]
    out: List[str] = []
    for i in range(d):
        for j in range(i + 1, d):
            v = arr[i, j]
            if not np.isnan(v) and abs(v) > thresh:
                out.append(f"- {labels[i]} <-> {labels[j]}: r={v:+.3f}")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--events-root", default=_DEFAULT_EVENTS_ROOT)
    p.add_argument("--since", default="14d")
    p.add_argument("--min-events", type=int, default=50)
    p.add_argument("--threshold", type=float, default=0.7)
    p.add_argument("--out", default="-", help="output file ('-' = stdout)")
    args = p.parse_args(argv)

    since = _parse_since(args.since)
    files = _iter_event_files(args.events_root, since)
    arr = _load_risk_vectors(files)
    n, d = arr.shape if arr.size else (0, 0)
    if n < args.min_events:
        sys.stderr.write(
            f"warning: only {n} risk-vector events under {args.events_root} "
            f"since {since.date()}; need {args.min_events}. exiting clean.\n"
        )
        return 0

    labels = _risk_labels(d)
    corr = _pearson_pairwise(arr)
    md = ["# Risk-vector pairwise Pearson correlation", ""]
    md.append(f"- events: {n}")
    md.append(f"- dims: {d}")
    md.append(f"- since: {since.date()}")
    md.append("")
    md.append(_render_markdown(corr, labels))
    md.append("")
    redundant = _find_redundant_pairs(corr, labels, thresh=args.threshold)
    md.append(f"## Redundant pairs (|r|>{args.threshold})")
    md.append("")
    md.extend(redundant if redundant else ["- (none)"])
    text = "\n".join(md) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
