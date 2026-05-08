"""Per-host adapter layer for hook stdin envelopes.

Each adapter exposes ``parse_stdin(event) -> HookEnvelope``. Adapters MUST
NEVER raise — malformed stdin returns a HookEnvelope with ``tool_name=None``
so the orchestrator can audit + exit cleanly without blocking the user.
"""
from . import claude as claude  # noqa: F401
