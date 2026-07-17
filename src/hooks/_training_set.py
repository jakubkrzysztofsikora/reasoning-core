"""Distributed training-set label collection for reasoning-core.

The eval protocol (`docs/EVAL_PROTOCOL.md`) needs a labeled training set of
~10 examples per label (5 positive, 5 negative) before the n=100 SWE-bench
eval can run. Rather than waiting for a one-shot labeling session, this
module implements **distributed, in-flow label collection** that grows the
training set across many real coding sessions on this machine.

Mechanics:
  - **Audit-log discovery**: every PreToolUse decision writes an audit row
    (decision_id, file_path, before_src, after_src, decision, signal_source).
    These are the candidate examples.
  - **Auto-prompt sampling**: when `RC_TRAINING_PROMPT_RATE` is set (e.g. 0.05),
    a small fraction of decisions are flagged as "needs label". The Stop
    hook surfaces a prompt for the user with the decision_id and a one-line
    `rc label` command. The user runs it whenever convenient.
  - **Manual contribution**: `rc label <decision-id>` reads the audit row,
    prompts the operator for the 5 labels interactively, and writes the
    label to `~/.local/share/reasoning-core/training_set.jsonl`.
  - **Random sampling**: `rc label --random` picks an unlabeled decision from
    recent audit and labels it — useful for filling quota without waiting
    for an auto-prompt.
  - **Progress visibility**: `rc label-stats` shows how many labels per
    category have been collected and what's left to hit the 10-per-label
    target.

All data stays local on this machine. No external sync. No telemetry.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
import random
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

_LABELS = ("scope_drift", "plan_violation", "structural_regression",
           "syntax_type_error", "test_failure")

_DEFAULT_STORE = Path(os.path.expanduser(
    "~/.local/share/reasoning-core/training_set.jsonl"
))

_TARGET_PER_LABEL = int(os.environ.get("RC_TRAINING_TARGET_PER_LABEL", "10"))
_PROMPT_RATE = float(os.environ.get("RC_TRAINING_PROMPT_RATE", "0.05"))


@dataclass
class TrainingLabel:
    """A single labeled audit decision."""
    decision_id: str
    session_id: str
    file_path: str
    decision: str
    signal_source: str = ""
    labels: dict[str, bool] = field(default_factory=dict)
    notes: str = ""
    labeler_id: str = ""
    ts: str = ""
    rationale_quality_failure: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TrainingLabel":
        defaults: dict[str, Any] = {}
        for f in cls.__dataclass_fields__.values():
            if f.default_factory is not None and f.default_factory is not dataclasses.MISSING:
                defaults[f.name] = f.default_factory()
            else:
                defaults[f.name] = f.default
        kwargs = {name: d.get(name, defaults[name]) for name in cls.__dataclass_fields__}
        # Normalise labels: JSON booleans come back as bool already, but
        # be defensive in case the store contains numeric 0/1.
        if "labels" in kwargs and isinstance(kwargs["labels"], dict):
            kwargs["labels"] = {k: bool(v) for k, v in kwargs["labels"].items()}
        return cls(**kwargs)


def store_path() -> Path:
    return Path(os.environ.get(
        "RC_TRAINING_SET_FILE",
        str(_DEFAULT_STORE),
    ))


def _read_all() -> list[TrainingLabel]:
    """Read all training labels from the store. Best-effort, never raises."""
    path = store_path()
    if not path.is_file():
        return []
    out: list[TrainingLabel] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(TrainingLabel.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
    except OSError:
        pass
    return out


def _append(label: TrainingLabel) -> bool:
    """Append one label to the store. Returns True on success."""
    path = store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(label.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except OSError as exc:
        sys.stderr.write(f"failed to append training label: {exc}\n")
        return False


def label_decision_id(decision_id: str, labels: dict[str, bool],
                      labeler_id: str = "operator",
                      notes: str = "") -> TrainingLabel:
    """Record a label for a decision_id. Looks up the audit row to attach context."""
    audit_row = _lookup_audit_row(decision_id)
    label = TrainingLabel(
        decision_id=decision_id,
        session_id=audit_row.get("session_id", ""),
        file_path=audit_row.get("file_path", ""),
        decision=audit_row.get("decision", ""),
        signal_source=audit_row.get("signal_source", ""),
        labels={k: bool(v) for k, v in labels.items() if k in _LABELS},
        notes=notes,
        labeler_id=labeler_id,
        ts=_dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        rationale_quality_failure=any(
            labels.get(k) for k in ("scope_drift", "plan_violation", "structural_regression")
        ),
    )
    _append(label)
    return label


def already_labeled(decision_id: str) -> bool:
    """Return True if this decision_id is already in the training set."""
    return any(l.decision_id == decision_id for l in _read_all())


def count_per_label() -> dict[str, int]:
    """Return count of positive labels per category across all stored labels."""
    counts = {label: 0 for label in _LABELS}
    for l in _read_all():
        for k, v in l.labels.items():
            if v:
                counts[k] = counts.get(k, 0) + 1
    return counts


def progress() -> dict[str, Any]:
    """Return progress toward the per-label target."""
    counts = count_per_label()
    return {
        "counts": counts,
        "target_per_label": _TARGET_PER_LABEL,
        "remaining": {
            k: max(0, _TARGET_PER_LABEL - counts.get(k, 0))
            for k in _LABELS
        },
        "total_stored": len(_read_all()),
    }


def _audit_root() -> Path:
    return Path(os.environ.get(
        "RC_AUDIT_ROOT",
        os.path.expanduser("~/.local/share/reasoning-core/events"),
    ))


def _lookup_audit_row(decision_id: str, days: int = 7) -> dict[str, Any]:
    """Find an audit row by decision_id in the last `days` days."""
    root = _audit_root()
    if not root.is_dir():
        return {}
    today = _dt.datetime.now(_dt.timezone.utc).date()
    for i in range(days):
        day = today - _dt.timedelta(days=i)
        day_dir = root / day.strftime("%Y-%m-%d")
        if not day_dir.is_dir():
            continue
        for log in list(day_dir.glob("*.jsonl")) + list(day_dir.glob("*.jsonl.gz")):
            try:
                if log.suffix == ".gz":
                    import gzip
                    fh = gzip.open(log, "rt", encoding="utf-8")
                else:
                    fh = log.open("r", encoding="utf-8")
                with fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            ev = json.loads(line)
                        except ValueError:
                            continue
                        if ev.get("decision_id") == decision_id:
                            return ev
            except OSError:
                continue
    return {}


def _candidate_decisions(days: int = 7, only_unlabeled: bool = True) -> list[dict[str, Any]]:
    """Return candidate audit rows that could be labeled."""
    import gzip
    labeled_ids = {l.decision_id for l in _read_all()} if only_unlabeled else set()
    out: list[dict[str, Any]] = []
    root = _audit_root()
    if not root.is_dir():
        return out
    today = _dt.datetime.now(_dt.timezone.utc).date()
    for i in range(days):
        day = today - _dt.timedelta(days=i)
        day_dir = root / day.strftime("%Y-%m-%d")
        if not day_dir.is_dir():
            continue
        for log in list(day_dir.glob("*.jsonl")) + list(day_dir.glob("*.jsonl.gz")):
            try:
                if log.suffix == ".gz":
                    fh = gzip.open(log, "rt", encoding="utf-8")
                else:
                    fh = log.open("r", encoding="utf-8")
                with fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            ev = json.loads(line)
                        except ValueError:
                            continue
                        if ev.get("file_path") and ev.get("decision_id"):
                            if ev["decision_id"] not in labeled_ids:
                                out.append(ev)
            except OSError:
                continue
    return out


def pick_random_unlabeled(days: int = 7) -> Optional[dict[str, Any]]:
    """Pick one random unlabeled audit row from the last `days` days."""
    candidates = _candidate_decisions(days=days, only_unlabeled=True)
    if not candidates:
        return None
    return random.choice(candidates)


def should_prompt_for_label(rng: random.Random | None = None) -> bool:
    """Decide whether to prompt the user for a label on this decision.

    Returns True with probability `RC_TRAINING_PROMPT_RATE` (default 0.05).
    Also returns False when the per-label target is already met.
    """
    p = progress()
    if all(p["remaining"][k] == 0 for k in _LABELS):
        return False
    r = rng or random
    return r.random() < _PROMPT_RATE