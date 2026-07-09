"""PRM shadow promotion tracker for Phase 3.

Tracks PRM shadow events across repo installs and decides whether the PRM gate
has collected enough evidence to be promoted from shadow (audit-only) to block.

Promotion criteria (configurable via env):
  - RC_PRM_PROMO_MIN_REPOS    default 5
  - RC_PRM_PROMO_MIN_EVENTS   default 1000
  - RC_PRM_PROMO_MIN_DAYS     default 14

State is stored in ``$RC_CACHE_DIR/prm-shadow-state.jsonl`` (default
``~/.cache/reasoning-core/prm-shadow-state.jsonl``), mode 0600. Each event is
one JSON line: ``{"repo_hash": "...", "ts": 1234567890.0, "score": 0.42}``.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PromotionStatus:
    promoted: bool
    repo_count: int
    event_count: int
    first_ts: Optional[float]
    days_elapsed: float
    reason: str


def _cache_dir() -> Path:
    override = os.environ.get("RC_CACHE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "reasoning-core"


def _state_path() -> Path:
    p = _cache_dir() / "prm-shadow-state.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def _repo_hash(project_root: str) -> str:
    return hashlib.sha256(project_root.encode("utf-8")).hexdigest()[:16]


def _min_repos() -> int:
    try:
        return int(os.environ.get("RC_PRM_PROMO_MIN_REPOS", "5"))
    except ValueError:
        return 5


def _min_events() -> int:
    try:
        return int(os.environ.get("RC_PRM_PROMO_MIN_EVENTS", "1000"))
    except ValueError:
        return 1000


def _min_days() -> float:
    try:
        return float(os.environ.get("RC_PRM_PROMO_MIN_DAYS", "14"))
    except ValueError:
        return 14.0


def _read_state() -> List[Dict[str, Any]]:
    p = _state_path()
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def record_shadow_event(project_root: str, score: float) -> None:
    """Append one PRM shadow event to the promotion log."""
    p = _state_path()
    event = {
        "repo_hash": _repo_hash(project_root),
        "ts": time.time(),
        "score": float(score),
    }
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    try:
        p.chmod(0o600)
    except OSError:
        pass


def promotion_status() -> PromotionStatus:
    """Return current promotion status based on recorded shadow events."""
    events = _read_state()
    if not events:
        return PromotionStatus(
            promoted=False,
            repo_count=0,
            event_count=0,
            first_ts=None,
            days_elapsed=0.0,
            reason="no shadow events recorded",
        )

    repos: set[str] = set()
    first_ts: Optional[float] = None
    for ev in events:
        repos.add(ev.get("repo_hash", ""))
        ts = ev.get("ts")
        if isinstance(ts, (int, float)):
            if first_ts is None or ts < first_ts:
                first_ts = float(ts)

    now = time.time()
    days = (now - first_ts) / 86400.0 if first_ts else 0.0
    repo_count = len(repos)
    event_count = len(events)

    reasons = []
    if repo_count < _min_repos():
        reasons.append(f"repos {repo_count}/{_min_repos()}")
    if event_count < _min_events():
        reasons.append(f"events {event_count}/{_min_events()}")
    if days < _min_days():
        reasons.append(f"days {days:.1f}/{_min_days()}")

    if reasons:
        return PromotionStatus(
            promoted=False,
            repo_count=repo_count,
            event_count=event_count,
            first_ts=first_ts,
            days_elapsed=days,
            reason="; ".join(reasons),
        )

    return PromotionStatus(
        promoted=True,
        repo_count=repo_count,
        event_count=event_count,
        first_ts=first_ts,
        days_elapsed=days,
        reason="promotion criteria met",
    )


__all__ = [
    "PromotionStatus",
    "record_shadow_event",
    "promotion_status",
]
