"""MCP tool: validate_imports.

Resolves every import in a Python / TypeScript snippet against the
project's symbol_index and import_index (already populated when
``RC_PROJECT_INDEX=1``). Flags imports that point to modules or symbols
not present in the repo — i.e., hallucinated APIs.

Addresses the community pain documented in
``thoughts/shared/research/2026-06-02-community-pain-points.md`` §6
(HN #43663777 "AI can't stop making up software dependencies"). The
forbid_import rule engine is **deny-list** only; this is the
**allow-list dual** that catches what no rule explicitly forbids.

Returns a dict (never raises):
  - ok: bool — True if every import resolved or matched a known third-party
  - unresolved: list[{module, kind, suggestion?}]
        kind in {"unknown_module", "unknown_symbol"}
        suggestion lists up to 3 nearest matches from symbol_index
  - resolved_count: int
  - notes: list[str] — context (third-party allowance, etc.)

The "unknown_module" check is conservative: a module name appearing
nowhere in ``import_index`` AND not matching a known stdlib / installed
third-party is flagged. This avoids false positives on standard imports.
"""
from __future__ import annotations

import ast
import difflib
import os
import re
import sys
from typing import Any, Dict, List, Optional, Set

__all__ = ["validate_imports"]


def _stdlib_top_levels() -> Set[str]:
    """Top-level stdlib module names — used to suppress false positives."""
    try:
        return set(sys.stdlib_module_names)  # 3.10+
    except AttributeError:
        # Conservative fallback: enumerate a minimal core.
        return {
            "os", "sys", "re", "json", "math", "time", "datetime", "pathlib",
            "typing", "collections", "itertools", "functools", "subprocess",
            "threading", "multiprocessing", "asyncio", "logging", "io",
            "contextlib", "dataclasses", "enum", "abc", "ast", "warnings",
            "copy", "string", "struct", "hashlib", "uuid", "tempfile",
            "shutil", "socket", "ssl", "urllib", "http", "email", "random",
            "secrets", "base64", "zlib", "gzip", "csv", "sqlite3", "argparse",
            "platform", "errno", "signal", "traceback", "inspect",
        }


_THIRD_PARTY_HINTS = {
    "numpy", "pandas", "torch", "transformers", "huggingface_hub",
    "fastapi", "uvicorn", "pydantic", "requests", "httpx", "aiohttp",
    "pytest", "click", "rich", "yaml", "toml", "tomli", "tomllib",
    "psutil", "tqdm", "scipy", "sklearn", "mlx", "mlx_lm", "llama_cpp",
    "anthropic", "openai", "azure", "boto3", "google", "kubernetes",
    "tree_sitter", "watchdog",
}


def _extract_imports_py(src: str) -> List[Dict[str, Any]]:
    """Return [{module, names, lineno}] for every Import / ImportFrom node."""
    out: List[Dict[str, Any]] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append({
                    "module": alias.name,
                    "names": [],
                    "lineno": node.lineno,
                })
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            out.append({
                "module": mod,
                "names": [a.name for a in node.names if a.name != "*"],
                "lineno": node.lineno,
            })
    return out


_TS_IMPORT_RE = re.compile(
    r'''import\s+(?:(?:\{([^}]*)\}|\*\s+as\s+\w+|\w+)\s+from\s+)?["']([^"']+)["']'''
)
_TS_REQUIRE_RE = re.compile(r'''require\s*\(\s*["']([^"']+)["']\s*\)''')


def _extract_imports_ts(src: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in _TS_IMPORT_RE.finditer(src):
        names = []
        if m.group(1):
            names = [n.strip().split(" as ")[0] for n in m.group(1).split(",")
                     if n.strip()]
        out.append({"module": m.group(2), "names": names, "lineno": 0})
    for m in _TS_REQUIRE_RE.finditer(src):
        out.append({"module": m.group(1), "names": [], "lineno": 0})
    return out


def _nearest_symbols(name: str, symbol_index: Dict[str, Any], k: int = 3) -> List[str]:
    if not name or not symbol_index:
        return []
    return difflib.get_close_matches(name, symbol_index.keys(), n=k, cutoff=0.6)


def _is_repo_module(module: str, import_index: Dict[str, Any]) -> bool:
    """True if any indexed file imports this module — i.e. it's known."""
    if not module:
        return False
    top = module.split(".")[0]
    for imports in import_index.values():
        if top in imports:
            return True
    # Heuristic: a repo-local module like "src.foo" or "src.foo.bar" matches
    # a file path src/foo.py / src/foo/bar.py via the import_index keys.
    path_guess = module.replace(".", "/")
    return any(rel.startswith(path_guess) for rel in import_index.keys())


def validate_imports(
    source: str,
    language: str = "python",
    session_id: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve imports against the project index.

    Args:
      source: file contents (Python or TS/JS).
      language: "python" | "typescript" | "javascript".
      session_id: optional ProjectIndex session key. Defaults to
        ``RC_SESSION_ID`` env. If both are unset the function returns
        ``{"ok": True, "notes": ["no_project_index"]}`` rather than fail
        closed — the gate is symbolic-augmentation, not authority.
      repo_root: forwarded to ``get_or_build_index``.

    Returns: dict described in module docstring.
    """
    if not isinstance(source, str) or not source.strip():
        return {"ok": True, "unresolved": [], "resolved_count": 0,
                "notes": ["empty_source"]}

    session_id = session_id or os.environ.get("RC_SESSION_ID") or ""
    if not session_id:
        return {"ok": True, "unresolved": [], "resolved_count": 0,
                "notes": ["no_session_id"]}

    try:
        from src.project_index import get_or_build_index  # type: ignore
    except ImportError:
        try:
            from project_index import get_or_build_index  # type: ignore
        except ImportError:
            return {"ok": True, "unresolved": [], "resolved_count": 0,
                    "notes": ["project_index_unavailable"]}

    index = get_or_build_index(
        session_id, repo_root or os.environ.get("CLAUDE_PROJECT_DIR")
    )
    if index is None:
        return {"ok": True, "unresolved": [], "resolved_count": 0,
                "notes": ["index_building_or_missing"]}

    lang = language.lower()
    if lang == "python":
        imports = _extract_imports_py(source)
    elif lang in ("typescript", "javascript", "ts", "js"):
        imports = _extract_imports_ts(source)
    else:
        return {"ok": True, "unresolved": [], "resolved_count": 0,
                "notes": [f"unsupported_language:{language}"]}

    stdlib = _stdlib_top_levels() if lang == "python" else set()
    unresolved: List[Dict[str, Any]] = []
    resolved = 0

    for imp in imports:
        mod = imp["module"]
        if not mod:
            continue
        top = mod.split(".")[0]

        if lang == "python" and (top in stdlib or top in _THIRD_PARTY_HINTS):
            resolved += 1
            continue
        if lang in ("typescript", "javascript", "ts", "js"):
            # Relative imports always treated as repo-local; checked below.
            if not mod.startswith(".") and "/" not in mod:
                # Bare specifier — assume third-party package (npm).
                resolved += 1
                continue

        if _is_repo_module(mod, index.import_index):
            resolved += 1
            # Verify named imports against symbol_index.
            for name in imp.get("names", []):
                if name and name not in index.symbol_index:
                    unresolved.append({
                        "module": mod,
                        "name": name,
                        "kind": "unknown_symbol",
                        "lineno": imp["lineno"],
                        "suggestion": _nearest_symbols(name, index.symbol_index),
                    })
            continue

        # Module not seen anywhere in the repo and not stdlib/third-party.
        unresolved.append({
            "module": mod,
            "kind": "unknown_module",
            "lineno": imp["lineno"],
            "suggestion": _nearest_symbols(top, index.symbol_index),
        })

    return {
        "ok": not unresolved,
        "unresolved": unresolved,
        "resolved_count": resolved,
        "notes": [],
    }
