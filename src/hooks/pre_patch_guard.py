#!/usr/bin/env python3
"""Preview a Codex apply_patch call and score its resulting files before use."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


def _deny(reason: str) -> int:
    sys.stderr.write(f"[reasoning-core] BLOCKED: {reason}\n")
    return 2


def _paths(command: str) -> list[PurePosixPath]:
    if not command.startswith("*** Begin Patch\n") or "*** End Patch" not in command:
        raise ValueError("invalid patch envelope")
    paths: list[PurePosixPath] = []
    for line in command.splitlines():
        if line.startswith("*** Move to:") or line.startswith("*** Delete File:"):
            raise ValueError("move and delete patches are unsupported")
        if line.startswith(("*** Update File: ", "*** Add File: ")):
            rel = PurePosixPath(line.split(": ", 1)[1])
            if rel.is_absolute() or ".." in rel.parts or not rel.parts:
                raise ValueError("unsafe patch path")
            paths.append(rel)
    if not paths:
        raise ValueError("patch contains no supported file paths")
    return list(dict.fromkeys(paths))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, TypeError):
        return _deny("invalid hook payload")
    if not isinstance(payload, dict) or payload.get("tool_name") != "apply_patch":
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return _deny("missing patch command")
    try:
        paths = _paths(command)
    except ValueError as exc:
        return _deny(str(exc))
    root = Path(os.environ.get("RC_PROJECT_DIR") or payload.get("cwd") or os.getcwd()).resolve()
    patch_bin = shutil.which("apply_patch")
    if patch_bin is None:
        return _deny("apply_patch preview tool unavailable")

    with tempfile.TemporaryDirectory(prefix="rc-patch-preview-") as temp:
        scratch = Path(temp)
        for rel in paths:
            source = root / str(rel)
            if not source.resolve().is_relative_to(root):
                return _deny("unsafe patch path")
            target = scratch / str(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.exists():
                if not source.is_file():
                    return _deny("patch target is not a regular file")
                shutil.copy2(source, target)
        try:
            preview = subprocess.run(
                [patch_bin], cwd=scratch, input=command, text=True,
                capture_output=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return _deny("patch preview failed")
        if preview.returncode:
            return _deny("patch preview could not apply")
        env = {**os.environ, "RC_PROJECT_DIR": str(root), "RC_HOST": "codex"}
        guard = Path(__file__).with_name("pre_edit_guard.py")
        for rel in paths:
            candidate = scratch / str(rel)
            if not candidate.is_file():
                return _deny("patch preview did not produce a file")
            try:
                after_src = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return _deny("patch result is not UTF-8 source")
            synthetic = {
                "tool_name": "Write",
                "tool_input": {"file_path": str(root / str(rel)), "content": after_src},
                "cwd": str(root), "session_id": payload.get("session_id"),
            }
            try:
                checked = subprocess.run(
                    [sys.executable, str(guard)], cwd=root, env=env,
                    input=json.dumps(synthetic), text=True, capture_output=True,
                    timeout=60,
                )
            except (OSError, subprocess.TimeoutExpired):
                return _deny("pre-write validation unavailable")
            if checked.returncode:
                return _deny(checked.stderr.strip()[:1200] or "pre-write validation failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
