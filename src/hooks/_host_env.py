"""Host-agnostic env shim for the hook layer.

Replaces direct ``os.environ.get("CLAUDE_PROJECT_DIR" / "CLAUDE_SESSION_ID")``
reads scattered through the hook chain. Centralises host detection so the
same dispatch chain serves Claude Code, Gemini CLI, GitHub Copilot CLI, and
Mistral Vibe CLI without leaking host-specific env names into shared code.

Detection contract (review-driven, 2026-05-08):

- ``host()`` returns ``os.environ["RC_HOST"]`` if set (each
  ``enable-in-repo-<host>.sh`` exports this in the repo's ``.envrc``).
- Else it falls back to whichever ``<HOST>_PROJECT_DIR`` env is set (one
  match -> that host). If multiple match -> raise ``HostAmbiguous`` (caller
  must set ``RC_HOST`` explicitly). If none -> ``"unknown"``.
- The "first session_id wins" heuristic was rejected after v3 review
  (spoofable when a Vibe child shell inherits ``CLAUDE_SESSION_ID``).

Priority chain for ``project_dir()``:
    RC_PROJECT_DIR > <host>_PROJECT_DIR > os.getcwd()

Priority chain for ``session_id()``:
    RC_SESSION_ID > <host>_SESSION_ID > stable per-launch UUID

Stable per-launch UUID is synthesised once and exported back into
``RC_SESSION_ID`` so child processes inherit it.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

_HOST_ENVS = {
    "claude": ("CLAUDE_PROJECT_DIR", "CLAUDE_SESSION_ID"),
    "gemini": ("GEMINI_PROJECT_DIR", "GEMINI_SESSION_ID"),
    "copilot": ("COPILOT_PROJECT_DIR", "COPILOT_SESSION_ID"),
    "vibe": ("VIBE_PROJECT_DIR", "VIBE_SESSION_ID"),
}


class HostAmbiguous(RuntimeError):
    """Raised when host detection is ambiguous and RC_HOST is unset."""


def host() -> str:
    explicit = os.environ.get("RC_HOST", "").strip().lower()
    if explicit in _HOST_ENVS:
        return explicit
    matches = [
        name for name, (proj_env, _) in _HOST_ENVS.items()
        if os.environ.get(proj_env)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise HostAmbiguous(
            f"Multiple host envs set ({matches}); export RC_HOST=<host> to disambiguate"
        )
    return "unknown"


def project_dir() -> Path:
    rc = os.environ.get("RC_PROJECT_DIR")
    if rc:
        return Path(rc)
    try:
        h = host()
    except HostAmbiguous:
        h = "unknown"
    if h in _HOST_ENVS:
        proj_env, _ = _HOST_ENVS[h]
        val = os.environ.get(proj_env)
        if val:
            return Path(val)
    return Path(os.getcwd())


def session_id() -> str:
    rc = os.environ.get("RC_SESSION_ID")
    if rc:
        return rc
    try:
        h = host()
    except HostAmbiguous:
        h = "unknown"
    if h in _HOST_ENVS:
        _, sid_env = _HOST_ENVS[h]
        val = os.environ.get(sid_env)
        if val:
            return val
    # Stable per-launch UUID. Use ppid + project_dir as the seed so that
    # all hooks invoked by the same parent process share an id, but two
    # separate launches of the same CLI get distinct ids.
    seed = f"{os.getppid()}|{project_dir()}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    sid = f"anon-{digest}"
    os.environ["RC_SESSION_ID"] = sid  # propagate to children
    return sid


def is_claude() -> bool:
    try:
        return host() == "claude"
    except HostAmbiguous:
        return False
