"""Session-manifest helpers for Phase 3 long-horizon hardening.

Key by (cwd_hash, task_spec_hash) so manifests survive `claude --resume`
(reviewer correction — CLAUDE_SESSION_ID is fresh per process). On
SessionStart, if a manifest with matching key < 24h old exists, rehydrate
it. Concurrent sessions in same cwd shard via an additional pid suffix.

Schema:
    {
        "key": "<sha256>",
        "cwd": "/abs/path/to/worktree",
        "cwd_hash": "<sha256-12>",
        "task_spec_hash": "<sha256-12>",
        "created_ts": 1700000000.0,
        "declared_language": "csharp" | "python" | "javascript" | ...,
        "framework": "xunit" | "pytest" | "jest" | ...,
        "ext_distribution": {".cs": 120, ".py": 4, ".md": 30, ...},
        "lang_allow": [".sh", ".sql", ...]
    }
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

_STATE_DIR = Path(os.environ.get(
    "RC_STATE_DIR",
    os.path.expanduser("~/.local/state/reasoning-core/sessions"),
))

_LANG_FAMILY = {
    ".cs": "csharp", ".csproj": "csharp", ".sln": "csharp",
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".ts": "javascript", ".tsx": "javascript", ".vue": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java", ".kt": "kotlin",
    ".rb": "ruby",
    ".sql": "sql",
}

_FRAMEWORK_HINTS = {
    "csharp": (("xunit", "Test.cs"), ("nunit", "Test.cs"), ("mstest", "Test.cs")),
    "python": (("pytest", "test_"), ("unittest", "test_")),
    "javascript": (("jest", "spec.ts"), ("vitest", "vitest"), ("cypress", "cy.ts")),
    "go": (("testing", "_test.go"),),
    "rust": (("cargo-test", "tests/"),),
}


def _hash12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def manifest_key(cwd: str, task_spec: str) -> str:
    return f"{_hash12(cwd)}_{_hash12(task_spec or '')}"


def manifest_path(key: str) -> Path:
    return _STATE_DIR / f"{key}.json"


def load(key: str) -> Optional[dict]:
    path = manifest_path(key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(manifest: dict) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        manifest_path(manifest["key"]).write_text(
            json.dumps(manifest, indent=2), encoding="utf-8",
        )
    except OSError:
        pass


def language_for_path(file_path: str) -> Optional[str]:
    ext = Path(file_path).suffix.lower()
    return _LANG_FAMILY.get(ext)


def is_path_allowed(manifest: Optional[dict], file_path: str) -> bool:
    """True if the file_path's language matches manifest's declared language
    OR is on the allow-list OR sits under a path-prefix exemption."""
    if not manifest:
        return True
    if not file_path:
        return True
    # Path-prefix exemptions for polyglot reality (scripts/, tools/)
    for prefix in ("scripts/", "tools/", "bin/"):
        if prefix in file_path:
            return True
    declared = manifest.get("declared_language")
    if not declared:
        return True
    file_lang = language_for_path(file_path)
    if file_lang is None:
        return True  # unknown extension; let the SSM scorer handle it
    if file_lang == declared:
        return True
    allow = set(manifest.get("lang_allow") or [])
    ext = Path(file_path).suffix.lower()
    if ext in allow:
        return True
    return False


def detect_initial_language(cwd: str) -> tuple[Optional[str], dict]:
    """Walk cwd and report dominant language family + extension distribution.

    O(file count) on the worktree; called once at SessionStart, not per-edit.
    """
    counts: dict = {}
    for root, dirnames, filenames in os.walk(cwd):
        # Don't descend into common build/cache dirs
        dirnames[:] = [d for d in dirnames if d not in {
            ".git", "node_modules", "dist", "build", ".venv", "venv",
            "__pycache__", ".cache", "coverage", "target", "obj", "bin",
        }]
        for f in filenames:
            ext = Path(f).suffix.lower()
            if ext:
                counts[ext] = counts.get(ext, 0) + 1
    # Aggregate by language family
    family: dict = {}
    for ext, n in counts.items():
        fam = _LANG_FAMILY.get(ext)
        if fam:
            family[fam] = family.get(fam, 0) + n
    declared = max(family, key=family.get) if family else None
    return declared, counts
