"""Centralized RC_SHADOW_MODE handling (P4 tracker #34).

Reviewer-flagged: hooks read RC_SHADOW_MODE inline at multiple sites
with subtly different semantics. This module is the single source of
truth.

Usage:
    from _shadow_mode import is_active, decide

    if is_active():
        decision = decide(default_when_off="blocked", in_shadow="shadow_blocked")

The semantics promoted to enforce when RC_SHADOW_MODE != "1":
  - default_when_off is the real decision (block / fail-closed / etc.)
  - in_shadow is the audit-row decision when shadow is on
"""
from __future__ import annotations

import os
from typing import Optional


def is_active() -> bool:
    return os.environ.get("RC_SHADOW_MODE") == "1"


def decide(*, default_when_off: str, in_shadow: str) -> str:
    """Return the audit-row decision string."""
    return in_shadow if is_active() else default_when_off


def should_enforce(default_when_off: str = "blocked") -> bool:
    """True if the gate should hard-enforce (exit 2) instead of advisory."""
    return not is_active()
