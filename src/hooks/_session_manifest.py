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
    """Atomic write — temp+rename so concurrent SessionStart hooks don't
    corrupt each other's manifest. Reviewer-flagged race condition.
    """
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        target = manifest_path(manifest["key"])
        tmp = target.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def language_for_path(file_path: str) -> Optional[str]:
    ext = Path(file_path).suffix.lower()
    return _LANG_FAMILY.get(ext)


_DEFAULT_PREFIX_EXEMPTIONS = ("scripts/", "tools/", "bin/")

# Round-2 P3 polyglot fix: families with >= MULTI_LANG_THRESHOLD share of the
# extension distribution are co-declared. Bimodal repos (e.g., 5884 .cs +
# 1884 .ts in circit-app — 24% TS share) keep BOTH languages active in the
# manifest, so frontend writes don't trip the lock.
MULTI_LANG_THRESHOLD = 0.10  # 10% of total file count


def _matches_prefix_exemption(file_path: str, prefixes: tuple) -> bool:
    """True if any path component starts with one of the exempt prefixes.

    Reviewer-flagged: substring `prefix in file_path` matches `src/scripts/`,
    `lib/tools/`, etc. — a silent bypass anywhere in the path. Use path
    components: only top-level OR repo-relative dirs like `scripts/foo.py`
    or `tools/x.py` qualify, NOT `src/scripts/evil.py`.
    """
    # Normalize: strip leading abs path; check repo-relative parts.
    norm = file_path.lstrip("/")
    parts = norm.split("/")
    for prefix in prefixes:
        clean = prefix.rstrip("/")
        # Top-level only (parts[0]) — substring matches at depth=0 only.
        if parts and parts[0] == clean:
            return True
    return False


def _path_exemptions_from_env() -> tuple:
    """Extra top-level prefixes from `RC_LANG_LOCK_PATH_EXEMPT` env (csv).
    Lets monorepos carve out subtrees per language: e.g.,
    `RC_LANG_LOCK_PATH_EXEMPT=Circit.Frontend/,docs/` whitelists those roots
    regardless of language family."""
    raw = os.environ.get("RC_LANG_LOCK_PATH_EXEMPT", "")
    extras = tuple(p.strip().rstrip("/") + "/" for p in raw.split(",") if p.strip())
    return _DEFAULT_PREFIX_EXEMPTIONS + extras


def _declared_set(manifest: Optional[dict]) -> set:
    """Normalize declared_language to a set of language families.

    Backwards-compat: legacy manifests stored a single string;
    polyglot-aware manifests store a list. Either form is accepted.
    Empty/None → empty set (treated as 'no lock')."""
    if not manifest:
        return set()
    decl = manifest.get("declared_language")
    if decl is None:
        return set()
    if isinstance(decl, str):
        return {decl}
    if isinstance(decl, (list, tuple, set)):
        return set(decl)
    return set()


def is_path_allowed(manifest: Optional[dict], file_path: str) -> bool:
    """True if the file_path's language matches the manifest's declared
    languages OR is on the allow-list OR sits under a TOP-LEVEL path-prefix
    exemption.

    Round-2 P3 polyglot fix: `declared_language` may be a list (e.g.,
    `["csharp", "javascript"]`) on bimodal repos. The gate accepts the file
    iff its language is in the declared set."""
    if not manifest:
        return True
    if not file_path:
        return True
    # Path-prefix exemptions: only top-level dir names. RC_LANG_LOCK_PATH_EXEMPT
    # extends the default scripts/ tools/ bin/ list with operator-supplied roots.
    if _matches_prefix_exemption(file_path, _path_exemptions_from_env()):
        return True
    declared = _declared_set(manifest)
    if not declared:
        return True
    file_lang = language_for_path(file_path)
    if file_lang is None:
        return True  # unknown extension; let the SSM scorer handle it
    if file_lang in declared:
        return True
    allow = set(manifest.get("lang_allow") or [])
    ext = Path(file_path).suffix.lower()
    if ext in allow:
        return True
    return False


def detect_initial_language(cwd: str, max_files: Optional[int] = None):
    """Walk cwd and report declared language family/families + extension distribution.

    Bounded by max_files (default RC_LANG_LOCK_MAX_FILES env, fallback 20000)
    to keep SessionStart latency under control on large monorepos. Excludes
    .git/node_modules/dist/.venv/etc.

    Round-2 P3 polyglot fix: when MORE THAN ONE language family clears
    `MULTI_LANG_THRESHOLD` (default 10% of files), returns the LIST of
    families instead of a single winner. Single-language repos keep the
    legacy string return for backwards-compat with consumers that branch
    on `isinstance(declared, str)`.

    Returns:
        (declared, ext_distribution): declared is `str | list[str] | None`.
        ext_distribution is a counts-by-extension dict.
    """
    if max_files is None:
        try:
            max_files = int(os.environ.get("RC_LANG_LOCK_MAX_FILES", "20000"))
        except ValueError:
            max_files = 20000
    counts: dict = {}
    seen = 0
    for root, dirnames, filenames in os.walk(cwd):
        dirnames[:] = [d for d in dirnames if d not in {
            ".git", "node_modules", "dist", "build", ".venv", "venv",
            "__pycache__", ".cache", "coverage", "target", "obj",
            "vendor", "_build", "Pods", ".gradle", ".next", "out",
            "packages",
        } and not d.startswith("cmake-build-")]
        for f in filenames:
            ext = Path(f).suffix.lower()
            if ext:
                counts[ext] = counts.get(ext, 0) + 1
            seen += 1
            if seen >= max_files:
                break
        if seen >= max_files:
            break
    family: dict = {}
    for ext, n in counts.items():
        fam = _LANG_FAMILY.get(ext)
        if fam:
            family[fam] = family.get(fam, 0) + n
    if not family:
        return None, counts
    total = sum(family.values())
    threshold = int(total * MULTI_LANG_THRESHOLD)
    above = sorted(
        [(fam, n) for fam, n in family.items() if n >= threshold],
        key=lambda kv: -kv[1],
    )
    if not above:
        # Defensive: total > 0 but threshold rounded everything out
        # (shouldn't happen with int() floor, but guard anyway).
        return max(family, key=family.get), counts
    if len(above) == 1:
        return above[0][0], counts
    # Polyglot: keep all families that cleared the threshold, ordered by share.
    return [fam for fam, _ in above], counts
