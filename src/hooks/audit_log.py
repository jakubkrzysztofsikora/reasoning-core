#!/usr/bin/env python3
"""Shared audit-log writer for the reasoning-core hook layer.

Every hook (pre_edit_guard, pre_bash_guard, pre_plan_guard, pre_task_guard,
post_bash_revive) and the operator CLI (``rc``) calls :func:`append_event` once
per invocation with a structured record. Records are appended as one JSON object
per line to::

    $RC_AUDIT_ROOT/<YYYY-MM-DD>/<session_id>.jsonl

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

Recognised ``decision`` values include the usual hook outcomes
(``allowed``, ``blocked``, ``warn``, ``shadow_blocked``, ``fail-open``,
``allowed_via_override``) and the operator ground-truth signals added in
Phase 0: ``operator_override`` and ``operator_confirmed``.

The module also exposes :func:`new_event` as a small helper that pre-fills
``ts``, ``decision_id``, ``session_id``, ``project_dir``, and ``host`` from the
environment so callers only need to add command-specific fields.
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

try:
    from src.hooks import _host_env  # type: ignore
except Exception:  # noqa: BLE001 - fallback when imported as a sibling
    try:
        import _host_env  # type: ignore
    except Exception:  # noqa: BLE001
        _host_env = None  # type: ignore

try:
    import portalocker  # type: ignore
except Exception:  # noqa: BLE001 - fallback to no-op lock if not installed
    portalocker = None  # type: ignore
    # Surface the fallback once per process when RC_AUDIT_WARN_PORTALOCKER=1.
    # Avoid writing to stderr by default: hooks run in agent-visible context
    # and the warning is not actionable for most users.
    if os.environ.get("RC_AUDIT_WARN_PORTALOCKER") == "1":
        sys.stderr.write(
            "[hybrid-reasoner] audit_log: portalocker not installed; "
            "concurrent multi-host writes may interleave. "
            "Run `pip install portalocker>=2.7`.\n"
        )

SCHEMA_VERSION = 3

# gate_id values for ablation attribution (Phase 1)
GATE_IDS = frozenset({
    "scorer", "plan_grounding", "rules", "calibration",
    "lang_lock", "mock_detector", "drift_gate",
})

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

    Routes through ``_host_env.session_id()`` so non-Claude hosts share the
    same retry-marker / per-session JSONL key as Claude. Falls back to the
    legacy CLAUDE_SESSION_ID-or-pid scheme if ``_host_env`` cannot import
    (degraded environment). Output is sanitised to a safe basename.
    """
    raw = ""
    if _host_env is not None:
        try:
            raw = _host_env.session_id()
        except Exception:  # noqa: BLE001
            raw = ""
    if not raw:
        sid = os.environ.get("CLAUDE_SESSION_ID")
        if sid and isinstance(sid, str):
            raw = sid
        else:
            seed = f"{os.getpid()}|{os.environ.get('CLAUDE_PROJECT_DIR', '')}"
            raw = "anon-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    clean = re.sub(r"[^A-Za-z0-9_\-]", "_", str(raw))[:64]
    return clean or "anon-unknown"


def _project_dir() -> str:
    if _host_env is not None:
        try:
            return str(_host_env.project_dir())
        except Exception:  # noqa: BLE001
            pass
    return os.environ.get("CLAUDE_PROJECT_DIR", "")


def _host_label() -> str:
    if _host_env is not None:
        try:
            return _host_env.host()
        except Exception:  # noqa: BLE001
            pass
    return "claude"


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
    gate_id: Optional[str] = None,
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
        "host": _host_label(),
        "schema_version": SCHEMA_VERSION,
        "tool_name": tool_name,
        "decision": decision,
    }
    if file_path is not None:
        base["file_path"] = file_path
    if gate_id is not None and gate_id in GATE_IDS:
        base["gate_id"] = gate_id
    base.update(fields)
    return base


def append_event(event: Dict[str, Any], *, fsync: bool = False) -> Optional[Dict[str, Any]]:
    """Append ``event`` to the per-session JSONL. Best-effort, never raises.

    Side effects:
      - Creates ``/tmp/rc-events/<YYYY-MM-DD>/`` if missing.
      - Opens the session file in append mode and writes one line.
      - On any OSError / unwritable target, writes a single warning to stderr
        and returns silently.

    Returns the emitted event (with decision_id), or None on failure.
    """
    try:
        # Auto-inject host + schema_version if caller skipped new_event().
        if "host" not in event:
            event = {**event, "host": _host_label()}
        if "schema_version" not in event:
            event = {**event, "schema_version": SCHEMA_VERSION}
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
            if portalocker is not None:
                try:
                    portalocker.lock(fh, portalocker.LOCK_EX)
                except Exception:  # noqa: BLE001 - lock is best-effort
                    pass
            fh.write(line + "\n")
            if fsync:
                try:
                    fh.flush()
                    os.fsync(fh.fileno())
                except Exception:  # noqa: BLE001
                    pass
            if portalocker is not None:
                try:
                    portalocker.unlock(fh)
                except Exception:  # noqa: BLE001
                    pass
        try:
            from _audit_rotation import rotate  # type: ignore
            rotate(_AUDIT_ROOT, today)
        except Exception:  # noqa: BLE001
            pass
        return redacted
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
    return None


def last_event() -> Optional[Dict[str, Any]]:
    """Return the most recent event in today's session log, or None."""
    try:
        path = Path(_AUDIT_ROOT) / _today() / f"{_session_id()}.jsonl"
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return None
        return json.loads(lines[-1])
    except Exception:  # noqa: BLE001
        return None


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




def _get_git_head() -> Optional[str]:
    """Best-effort git HEAD hash capture. Returns None if not in a git repo."""
    import subprocess
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()[:8]
    except Exception:  # noqa: BLE001
        pass
    return None


def record_override(file_path: str, blocked_decision_id: str, *, now: Optional[float] = None) -> None:
    """Persist override link: override at file_path reversed block decision_id."""
    if not file_path or not blocked_decision_id:
        return
    import time as _time, json as _json2

    now_ts = now if now is not None else _time.time()
    state_dir = _os.path.expanduser("~/.local/state/reasoning-core")
    try:
        _os.makedirs(state_dir, exist_ok=True)
    except OSError:
        return
    path = _os.path.join(state_dir, "override_links.json")
    links: dict = {}
    try:
        if _os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                links = _json2.loads(fh.read() or "{}")
    except (OSError, ValueError):
        links = {}
    key = f"{file_path}:{int(now_ts)}"
    links[key] = {"file_path": file_path, "blocked_decision_id": blocked_decision_id, "ts": now_ts}
    cutoff = now_ts - 86400 * 7
    links = {k: v for k, v in links.items() if isinstance(v, dict) and v.get("ts", 0) >= cutoff}
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_json2.dumps(links))
    except OSError:
        pass

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


def record_operator_override(
    reason: str = "bypass_next_armed",
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit an explicit ``operator_override`` audit event.

    Used by ``rc bypass-next`` so the audit log carries ground-truth that the
    operator intentionally overrode a guard decision. The event is emitted when
    the operator arms the bypass, i.e. before the next edit is allowed through.
    """
    append_event(new_event(
        tool_name="rc",
        decision="operator_override",
        reason=reason,
        **(extra or {}),
    ))


def record_operator_confirmed(
    reason: str = "confirm_next_armed",
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit an explicit ``operator_confirmed`` audit event.

    Used by ``rc confirm-next`` so the audit log carries ground-truth that the
    operator agreed a block was correct.
    """
    append_event(new_event(
        tool_name="rc",
        decision="operator_confirmed",
        reason=reason,
        **(extra or {}),
    ))


def record_shadow_block(file_path: str, *, now: Optional[float] = None) -> None:
    """Like record_block but lives in a separate namespace.

    Reviewer fix: shadow-mode blocks must NOT poison is_retry_after_block,
    otherwise legit operator retries after a shadow-block get tagged as
    retry_after_block=True misclassifying audit data. Shadow-block markers
    live in shadow_markers.json; consumers that want the shadow-retry
    signal can read it explicitly.
    """
    if not file_path:
        return
    import json as _json
    import os as _os
    import time as _time

    now_ts = now if now is not None else _time.time()
    state_dir = _os.path.expanduser("~/.local/state/reasoning-core")
    try:
        _os.makedirs(state_dir, exist_ok=True)
    except OSError:
        return
    path = _os.path.join(state_dir, "shadow_markers.json")
    markers: dict = {}
    try:
        if _os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                markers = _json.loads(fh.read() or "{}")
    except (OSError, ValueError):
        markers = {}
    markers[file_path] = float(now_ts)
    cutoff = now_ts - 3600
    markers = {k: v for k, v in markers.items() if isinstance(v, (int, float)) and v >= cutoff}
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_json.dumps(markers))
    except OSError:
        pass


__all__ = [
    "append_event",
    "is_retry_after_block",
    "new_event",
    "record_block",
    "record_operator_confirmed",
    "record_operator_override",
    "record_shadow_block",
]
