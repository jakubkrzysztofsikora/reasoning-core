#!/usr/bin/env python3
"""Shared audit-log writer for the reasoning-core hook layer.

Every hook (pre_edit_guard, pre_bash_guard, pre_plan_guard, pre_task_guard,
post_bash_revive) calls :func:`append_event` once per invocation with a
structured record. Records are appended as one JSON object per line to::

    /tmp/rc-events/<YYYY-MM-DD>/<session_id>.jsonl

Hard contracts (see board RC-203):

- Best-effort: any IO error is swallowed; writing audit must NEVER raise into
  the calling hook because the hook itself must remain deterministic.
- Redaction:
    * file_path matching one of the secret-shaped patterns is replaced with
      the literal ``"[REDACTED]"`` and ``before_bytes``/``after_bytes`` are
      zeroed.
    * ``human_summary`` and ``command`` strings are scrubbed for inline
      secrets (``sk-...``, ``ghp_...``, ``Bearer ...``, ``password=...``).
- Schema is intentionally permissive: missing fields are allowed; readers
  should rely on ``decision``/``tool_name`` and treat the rest as advisory.

The module also exposes :func:`new_event` as a small helper that pre-fills
``ts``, ``session_id``, and ``project_dir`` from the environment so callers
only need to add hook-specific fields.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

# Audit log lives under XDG state per v2 plan; falls back to /tmp on legacy
# operators. Override via RC_AUDIT_ROOT for tests.
_DEFAULT_ROOT = os.path.expanduser("~/.local/share/reasoning-core/events")
_AUDIT_ROOT = os.environ.get("RC_AUDIT_ROOT", _DEFAULT_ROOT)
_RETENTION_DAYS = int(os.environ.get("RC_AUDIT_RETENTION_DAYS", "90"))
_DISK_CAP_BYTES = int(os.environ.get("RC_AUDIT_CAP_BYTES", str(5 * 1024 * 1024 * 1024)))  # 5 GB

# Filename / basename patterns that mark a path as containing secrets.
_SECRET_PATH_PATTERNS = (
    re.compile(r"\.env(?:\.|$)", re.IGNORECASE),
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.key$", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
)

# Inline-secret scrub patterns. Matches are replaced with ``[REDACTED]``.
_SECRET_INLINE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"password\s*=\s*\S+", re.IGNORECASE),
)


def _now_iso() -> str:
    """Return ISO-8601 UTC timestamp with trailing 'Z'."""
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _session_id() -> str:
    """Stable per-process session id.

    Prefers ``CLAUDE_SESSION_ID`` (set by Claude Code), falls back to a hash
    of (pid, project_dir, boot-time-ish marker) so the file name is at least
    consistent within a single hook process tree.
    """
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if sid and isinstance(sid, str):
        # Limit to a safe basename character set.
        clean = re.sub(r"[^A-Za-z0-9_\-]", "_", sid)[:64]
        if clean:
            return clean
    seed = f"{os.getpid()}|{os.environ.get('CLAUDE_PROJECT_DIR', '')}"
    return "anon-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _project_dir() -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR", "")


def _is_secret_path(path: str) -> bool:
    if not path:
        return False
    base = os.path.basename(path)
    for pat in _SECRET_PATH_PATTERNS:
        if pat.search(base) or pat.search(path):
            return True
    return False


def _scrub_inline(text: Any) -> Any:
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pat in _SECRET_INLINE_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def _redact(event: Dict[str, Any]) -> Dict[str, Any]:
    """Apply redaction rules in place to a copy of ``event`` and return it."""
    e = dict(event)
    fp = e.get("file_path")
    if isinstance(fp, str) and _is_secret_path(fp):
        e["file_path"] = "[REDACTED]"
        # Don't leak size signal either.
        if "before_bytes" in e:
            e["before_bytes"] = 0
        if "after_bytes" in e:
            e["after_bytes"] = 0
    # Inline-secret scrub on free-text fields.
    for key in ("human_summary", "reason", "command"):
        if key in e:
            e[key] = _scrub_inline(e[key])
    return e


def new_event(
    *,
    tool_name: str,
    decision: str,
    file_path: Optional[str] = None,
    **fields: Any,
) -> Dict[str, Any]:
    """Build a baseline event dict with ts/session_id/project_dir filled in.

    Callers add hook-specific keys via ``**fields``. Returns a fresh dict; the
    caller may mutate it (e.g. set ``latency_ms`` once the hook is done).
    """
    base: Dict[str, Any] = {
        "ts": _now_iso(),
        "decision_id": uuid.uuid4().hex[:12],
        "session_id": _session_id(),
        "project_dir": _project_dir(),
        "tool_name": tool_name,
        "decision": decision,
    }
    if file_path is not None:
        base["file_path"] = file_path
    base.update(fields)
    return base


def append_event(event: Dict[str, Any]) -> None:
    """Append ``event`` to the per-session JSONL. Best-effort, never raises.

    Side effects:
      - Creates ``/tmp/rc-events/<YYYY-MM-DD>/`` if missing.
      - Opens the session file in append mode and writes one line.
      - On any OSError / unwritable target, writes a single warning to stderr
        and returns silently.
    """
    try:
        redacted = _redact(event)
    except Exception:  # noqa: BLE001
        # If redaction itself blows up, skip the record entirely rather than
        # leaking unredacted data.
        return

    try:
        today = _today()
        day_dir = Path(_AUDIT_ROOT) / today
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{_session_id()}.jsonl"
        line = json.dumps(redacted, ensure_ascii=False, sort_keys=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        try:
            from _audit_rotation import rotate  # type: ignore
            rotate(_AUDIT_ROOT, today)
        except Exception:  # noqa: BLE001
            pass
    except OSError as exc:
        try:
            sys.stderr.write(
                f"[hybrid-reasoner] audit_log: append failed ({exc}); skipping.\n"
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001 - never raise into a hook
        try:
            sys.stderr.write(f"[hybrid-reasoner] audit_log: unexpected ({exc}).\n")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Retry-detection helpers used by pre_edit_guard.
# ---------------------------------------------------------------------------

_RETRY_WINDOW_SECONDS = 120


def _retry_marker_path() -> Path:
    return Path(_AUDIT_ROOT) / f"{_session_id()}.last_block"


def _load_retry_markers() -> Dict[str, float]:
    p = _retry_marker_path()
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8") or "{}")
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_retry_markers(data: Dict[str, float]) -> None:
    p = _retry_marker_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def is_retry_after_block(file_path: str, *, now: Optional[float] = None) -> bool:
    """Return True if ``file_path`` was blocked within the retry window.

    Reads the persisted marker map and checks whether the last block timestamp
    for ``file_path`` falls inside ``_RETRY_WINDOW_SECONDS``. Never raises.
    """
    if not file_path:
        return False
    import time as _time

    now_ts = now if now is not None else _time.time()
    markers = _load_retry_markers()
    last = markers.get(file_path)
    try:
        return bool(last) and (now_ts - float(last)) < _RETRY_WINDOW_SECONDS
    except (TypeError, ValueError):
        return False


def record_block(file_path: str, *, now: Optional[float] = None) -> None:
    """Persist ``file_path`` as recently blocked so the next call can detect retry."""
    if not file_path:
        return
    import time as _time

    now_ts = now if now is not None else _time.time()
    markers = _load_retry_markers()
    markers[file_path] = float(now_ts)
    # Garbage-collect markers older than 1 hour to keep file small.
    cutoff = now_ts - 3600
    markers = {k: v for k, v in markers.items() if isinstance(v, (int, float)) and v >= cutoff}
    _save_retry_markers(markers)


__all__ = [
    "append_event",
    "is_retry_after_block",
    "new_event",
    "record_block",
]
