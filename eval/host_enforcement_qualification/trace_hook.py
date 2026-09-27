#!/usr/bin/env python3
"""Record exact PreToolUse payloads for host-enforcement qualification."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path


def main() -> int:
    destination = Path(sys.argv[1])
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"malformed": True}
    # Payloads are retained only in disposable local qualification artifacts.
    record = {"timestamp_ns": time.monotonic_ns(), "payload_sha256": hashlib.sha256(raw.encode()).hexdigest(), "tool_name": payload.get("tool_name") if isinstance(payload, dict) else None, "tool_input": payload.get("tool_input") if isinstance(payload, dict) else None, "cwd": payload.get("cwd") if isinstance(payload, dict) else None}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
