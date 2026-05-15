"""Project-wide symbol and import index for structural risk dimensions.

Builds two indices:
  - symbol_index:  {symbol_name: [(rel_path, line)]} — definition sites
  - import_index:  {rel_path: set(imported_module)} — cross-file edges

Used by the scorer to compute project_fan_in and project_coupling dimensions
when RC_PROJECT_INDEX=1. Index build is O(repo_files) once per session;
refresh is O(touched_files).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ProjectIndex:
    """Immutable snapshot of a project's structural indices."""

    session_id: str
    repo_root: str
    symbol_index: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    import_index: dict[str, set[str]] = field(default_factory=dict)

    def symbol_locations(self, name: str) -> list[tuple[str, int]]:
        return self.symbol_index.get(name, [])

    def file_imports(self, rel_path: str) -> set[str]:
        return self.import_index.get(rel_path, set())

    def files_importing(self, module: str) -> list[str]:
        return [
            rel_path
            for rel_path, imports in self.import_index.items()
            if module in imports
        ]


# ---------------------------------------------------------------------------
# File discovery — same filtering as _mock_detector._iter_repoFiles
# ---------------------------------------------------------------------------


_SKIP_DIRS = frozenset({
    ".git", ".hg", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".venv", "venv", "node_modules", ".tox", "build", "dist",
    ".eggs", "*.egg-info",
})

_SRC_EXTS = frozenset({".py", ".js", ".ts", ".tsx"})


def _iter_repo_files(repo_root: str) -> list[str]:
    """Return sorted list of relative source-file paths."""
    found: list[str] = []
    root = Path(repo_root)
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(str(root)):
        # Prune skip-dirs in-place (like _mock_detector)
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        reldir = Path(dirpath).relative_to(root)
        for fn in filenames:
            if any(fn.endswith(ext) for ext in _SRC_EXTS):
                found.append(str(reldir / fn))
    found.sort()
    return found


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _extract_python_symbols(src: str) -> dict[str, int]:
    """Extract top-level symbol definitions from Python source.
    Returns {name: line_number}.
    """
    symbols: dict[str, int] = {}
    try:
        import ast
        tree = ast.parse(src)
    except Exception:
        return symbols
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols[node.name] = node.lineno
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    symbols[tgt.id] = node.lineno
    return symbols


def _extract_python_imports(src: str) -> set[str]:
    """Extract imported module names from Python source."""
    imports: set[str] = set()
    try:
        import ast
        tree = ast.parse(src)
    except Exception:
        return imports
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imports.add(mod.split(".")[0])
            for alias in node.names:
                imports.add(alias.name)
    return imports


def _extract_ts_js_imports(src: str) -> set[str]:
    """Extract imported module names from JS/TS source via regex.
    Tree-sitter is too heavy for a simple import scan; regex is sufficient
    for the coupling heuristic.
    """
    imports: set[str] = set()
    # import { x } from "module"
    # import * as x from "module"
    # import x from "module"
    # const x = require("module")
    patterns = [
        re.compile(r'''import\s+(?:(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+)?["']([^"']+)["']'''),
        re.compile(r'''require\s*\(\s*["']([^"']+)["']\s*\)'''),
    ]
    for pat in patterns:
        for m in pat.finditer(src):
            mod = m.group(1)
            if mod.startswith("."):
                # Relative import — extract the base name
                mod = os.path.basename(mod).replace(os.path.splitext(mod)[1], "")
            else:
                mod = mod.split("/")[0]
            if mod:
                imports.add(mod)
    return imports


# ---------------------------------------------------------------------------
# Singleflight build
# ---------------------------------------------------------------------------

_PROJECT_INDEX_FUTURES: dict[str, Future] = {}
_PROJECT_INDEX_LOCK = threading.Lock()


def _build_index(repo_root: str, session_id: str) -> ProjectIndex:
    """Build the project index from scratch."""
    files = _iter_repo_files(repo_root)
    symbol_index: dict[str, list[tuple[str, int]]] = {}
    import_index: dict[str, set[str]] = {}

    for rel_path in files:
        full = Path(repo_root) / rel_path
        try:
            src = full.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if rel_path.endswith(".py"):
            for name, line in _extract_python_symbols(src).items():
                symbol_index.setdefault(name, []).append((rel_path, line))
            import_index[rel_path] = _extract_python_imports(src)
        elif any(rel_path.endswith(ext) for ext in (".js", ".ts", ".tsx")):
            import_index[rel_path] = _extract_ts_js_imports(src)

    logger.info(
        "Project index built: session=%s files=%d symbols=%d",
        session_id, len(files), len(symbol_index),
    )
    return ProjectIndex(
        session_id=session_id,
        repo_root=repo_root,
        symbol_index=symbol_index,
        import_index=import_index,
    )


def get_or_build_index(session_id: str, repo_root: str | None = None) -> ProjectIndex | None:
    """Lazy-build the project index with singleflight deduplication.

    If another thread is already building for this session, wait for it.
    Returns None if repo_root is not provided and no cached index exists.
    """
    global _PROJECT_INDEX_FUTURES
    # Fast path: check if already built
    with _PROJECT_INDEX_LOCK:
        fut = _PROJECT_INDEX_FUTURES.get(session_id)
        if fut is not None:
            if fut.done():
                try:
                    return fut.result()
                except Exception:
                    return None
            # Build in progress — return None (caller uses intra-file fallback)
            return None
        if repo_root is None:
            return None
        # Start build
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="projidx")
        fut = executor.submit(_build_index, repo_root, session_id)
        _PROJECT_INDEX_FUTURES[session_id] = fut
        executor.shutdown(wait=False)

    # Wait for our own build
    try:
        return fut.result(timeout=300)
    except Exception as exc:
        logger.warning("Project index build failed: %s", exc)
        return None


def clear_index(session_id: str) -> None:
    """Test helper — drop cached index."""
    with _PROJECT_INDEX_LOCK:
        _PROJECT_INDEX_FUTURES.pop(session_id, None)


def clear_all_indices() -> None:
    """Test helper — drop all cached indices."""
    with _PROJECT_INDEX_LOCK:
        _PROJECT_INDEX_FUTURES.clear()


__all__ = [
    "ProjectIndex",
    "clear_all_indices",
    "clear_index",
    "get_or_build_index",
]
