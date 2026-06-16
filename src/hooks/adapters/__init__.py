"""Per-host adapter layer for hook stdin envelopes.

Each adapter exposes ``parse_stdin(event) -> HookEnvelope``. Adapters MUST
NEVER raise — malformed stdin returns a HookEnvelope with ``tool_name=None``
so the orchestrator can audit + exit cleanly without blocking the user.
"""
from . import claude as claude  # noqa: F401
from . import codex as codex  # noqa: F401
from . import copilot as copilot  # noqa: F401
from . import gemini as gemini  # noqa: F401
from . import kimi as kimi  # noqa: F401
from . import vibe as vibe  # noqa: F401
