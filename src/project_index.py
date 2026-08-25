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
import fnmatch
import os
from collections import OrderedDict
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


def _python_definitions(src: str) -> list[tuple[str, int, str]]:
    """Return top-level definitions with a stable body fingerprint."""
    try:
        import ast
        tree = ast.parse(src)
    except SyntaxError:
        return []
    definitions = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Names and locations differ for intentional copies; arguments and
            # body shape are a conservative high-confidence duplicate signal.
            if hasattr(node, "args"):
                fingerprint = ast.dump(node.args, include_attributes=False) + ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False)
            else:
                fingerprint = ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False)
            definitions.append((node.name, node.lineno, fingerprint))
    return definitions


def find_duplicate_definitions(repo_root: str, file_path: str, after_src: str) -> list[dict[str, Any]]:
    """Find exact-name and high-confidence same-body definitions in a repo.

    The caller supplies the proposed source, allowing this to run before the
    write reaches disk.  Same-file matches are excluded by line/name pairing.
    """
    proposed = _python_definitions(after_src) if file_path.endswith(".py") else []
    if not proposed:
        return []
    findings: list[dict[str, Any]] = []
    root = Path(repo_root)
    target = Path(file_path)
    try:
        target_rel = str(target.resolve().relative_to(root.resolve())) if target.is_absolute() else str(target)
    except ValueError:
        target_rel = str(target)
    for rel_path in _iter_repo_files(repo_root):
        if not rel_path.endswith(".py"):
            continue
        try:
            existing = _python_definitions((root / rel_path).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for new_name, new_line, new_body in proposed:
            for old_name, old_line, old_body in existing:
                if rel_path == target_rel and old_name == new_name and old_line == new_line:
                    continue
                kind = "exact_name" if new_name == old_name else "semantic_body"
                if kind == "exact_name" or new_body == old_body:
                    findings.append({"kind": kind, "symbol": new_name, "path": rel_path, "line": old_line})
    return findings


def _module_for_path(path: str) -> str:
    p = Path(path).with_suffix("")
    parts = list(p.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _local_imports(src: str, rel_path: str, known: set[str]) -> set[str]:
    """Resolve local Python imports into project module names."""
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    current = _module_for_path(rel_path).split(".")
    package = current[:-1]
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known:
                    edges.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = (node.module or "").split(".") if node.module else []
            if node.level:
                prefix = package[:max(0, len(package) - node.level + 1)]
                candidate = ".".join(prefix + base)
            else:
                candidate = ".".join(base)
            if candidate in known:
                edges.add(candidate)
            for alias in node.names:
                child = f"{candidate}.{alias.name}" if candidate else alias.name
                if child in known:
                    edges.add(child)
    return edges


def find_import_cycle(repo_root: str, file_path: str, after_src: str) -> list[str] | None:
    """Return one newly present local import cycle, including its closing node."""
    root = Path(repo_root)
    files = [path for path in _iter_repo_files(repo_root) if path.endswith(".py")]
    known = {_module_for_path(path) for path in files}
    graph: dict[str, set[str]] = {}
    try:
        target_rel = str(Path(file_path).resolve().relative_to(root.resolve())) if Path(file_path).is_absolute() else str(Path(file_path))
    except ValueError:
        target_rel = str(Path(file_path))
    for rel in files:
        source = after_src if rel == target_rel else (root / rel).read_text(encoding="utf-8", errors="replace")
        graph[_module_for_path(rel)] = _local_imports(source, rel, known)
    start = _module_for_path(target_rel)
    visiting: list[str] = []
    visited: set[str] = set()
    def visit(module: str) -> list[str] | None:
        if module in visiting:
            return visiting[visiting.index(module):] + [module]
        if module in visited:
            return None
        visiting.append(module)
        for child in graph.get(module, set()):
            cycle = visit(child)
            if cycle:
                return cycle
        visiting.pop()
        visited.add(module)
        return None
    return visit(start)


# ---------------------------------------------------------------------------
# File discovery — same filtering as _mock_detector._iter_repoFiles
# ---------------------------------------------------------------------------


# Exact directory names to prune. Hidden directories (``.``-prefixed) are
# pruned separately so we don't need to enumerate every dotfile cache here.
_SKIP_DIRS = frozenset({
    "__pycache__", ".pytest_cache", ".mypy_cache",
    ".venv", "venv", "node_modules", ".tox", "build", "dist",
    ".eggs",
})

# Glob patterns for directory pruning. Matched with ``fnmatch`` so wildcards
# behave as intended -- a flat ``in _SKIP_DIRS`` check would never match
# ``foo.egg-info`` against the pattern ``*.egg-info``.
_SKIP_DIR_PATTERNS = ("*.egg-info",)

_SRC_EXTS = frozenset({".py", ".js", ".ts", ".tsx"})


def _is_skip_dir(name: str) -> bool:
    if name.startswith("."):
        return True
    if name in _SKIP_DIRS:
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in _SKIP_DIR_PATTERNS)


def _iter_repo_files(repo_root: str, exts: "frozenset[str]" = _SRC_EXTS) -> list[str]:
    """Return sorted list of relative source-file paths.

    ``exts`` selects which file extensions to include (default: the call-graph
    source set). The dup oracle passes its own wider code-extension set so the
    walk + skip-dir pruning logic lives in one place.
    """
    found: list[str] = []
    root = Path(repo_root)
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(str(root)):
        # Prune skip-dirs in-place (exact names + glob patterns + dotdirs).
        dirnames[:] = [d for d in dirnames if not _is_skip_dir(d)]
        reldir = Path(dirpath).relative_to(root)
        for fn in filenames:
            if any(fn.endswith(ext) for ext in exts):
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
    # Record module/package names only, not imported symbols. The previous
    # version added ``alias.name`` from ``from x import y``, which inflated
    # the import graph: ``from typing import Any`` would record ``Any`` as a
    # dependency, skewing fan-in / coupling.
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod:
                imports.add(mod.split(".")[0])
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

_PROJECT_INDEX_FUTURES: "OrderedDict[str, Future]" = OrderedDict()
_PROJECT_INDEX_LOCK = threading.Lock()
# Hard cap on cached session indices. Hook-driven harnesses can create a new
# ``session_id`` per Claude session; without an upper bound the futures map
# would grow without limit. Eviction is LRU on insertion order.
_PROJECT_INDEX_MAX_SESSIONS: int = int(os.environ.get("RC_PROJECT_INDEX_MAX", "64"))


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

    Returns:
      - The ``ProjectIndex`` when a previous build for this ``session_id``
        has completed successfully.
      - ``None`` when no index exists and ``repo_root`` is missing, when a
        prior build raised, or when a build is currently in progress (the
        caller is expected to fall back to intra-file scoring until the
        future resolves on a later call).

    The build is *not* awaited inline: a real-world project index can take
    seconds to build (file walk + AST parse per file), and the hook caller
    runs on the critical path. The first call kicks off the executor and
    returns ``None``; subsequent calls see the cached future and either
    return the result or fall back, depending on whether the build has
    completed.
    """
    global _PROJECT_INDEX_FUTURES
    # Fast path: check if already built
    with _PROJECT_INDEX_LOCK:
        fut = _PROJECT_INDEX_FUTURES.get(session_id)
        if fut is not None:
            # LRU touch -- recently-used sessions move to the tail so cold
            # entries fall out first when we hit the cap.
            _PROJECT_INDEX_FUTURES.move_to_end(session_id)
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
        # Evict oldest entries when over budget.
        while len(_PROJECT_INDEX_FUTURES) > _PROJECT_INDEX_MAX_SESSIONS:
            _PROJECT_INDEX_FUTURES.popitem(last=False)
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
