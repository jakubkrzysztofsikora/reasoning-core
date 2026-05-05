"""Tree-sitter grammar loader for the System 2 sidecar.

Supports the following languages:

    Code:  Python, JavaScript, TypeScript (+TSX), C#, SQL.
    Data:  Markdown, JSON, YAML, CSS, SCSS, HTML, Dockerfile.

"Data" languages skip call-graph extraction (no call semantics) but still
flow through the embedding-based novelty scoring path.

Vue SFCs (``.vue``) are routed through the HTML grammar as a pragmatic
fallback: Vue SFC structure (``<template>``, ``<script>``, ``<style>``
blocks) is HTML-shaped, and no ``tree-sitter-vue`` wheel is published on
PyPI for Python 3.13. Embedding-based scoring tolerates an inexact AST,
so the HTML parser provides enough structural signal for novelty
detection.

Loader strategy:
    1. Prefer the ``tree_sitter_languages`` aggregate wheel which ships
       compiled grammars for ~40 languages. This is the path of least
       friction and avoids per-grammar build steps on macOS arm64.
    2. Fall back to the per-language wheels declared in requirements.txt.
       Each per-language ``import`` is wrapped in its own ``except
       ImportError`` so a missing wheel for one language never breaks the
       others.

The two contracts other engineers rely on:

* ``select_grammar(path)`` returns a ``(language_name, Language)`` tuple where
  ``language_name`` is one of the internal language ids (``"python"``,
  ``"javascript"``, ``"typescript"``, ``"tsx"``, ``"csharp"``, ``"sql"``,
  ``"markdown"``, ``"json"``, ``"yaml"``, ``"css"``, ``"scss"``, ``"html"``,
  ``"dockerfile"``). The public surface area collapses ``tsx`` to
  ``typescript``. ``.vue`` files resolve to the ``html`` language id.
* ``UnsupportedLanguageError`` is raised -- never silently fall back -- when
  the extension is not in the supported set.

Dockerfile note: ``EXTENSION_MAP`` is keyed on file extension, but Dockerfiles
canonically have *no* extension. ``select_grammar`` therefore special-cases
files whose basename is ``Dockerfile`` (case-insensitive) or whose name ends
in ``.dockerfile`` and routes them to the ``dockerfile`` grammar.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Public language identifiers. The HTTP /health response advertises these.
# Code languages get full call-graph extraction; structured-data / markup
# languages are parsed for embedding-based novelty scoring only — the call
# graph is a no-op for them.
SUPPORTED_LANGUAGES: tuple[str, ...] = (
    "python",
    "javascript",
    "typescript",
    "csharp",
    "sql",
    "markdown",
    "json",
    "yaml",
    "css",
    "scss",
    "html",
    "dockerfile",
)

# Languages where build_call_graph returns {} by design (structured data /
# markup / config — no call semantics). score_change still runs the Mamba
# embedding pass so semantic drift is detected.
DATA_LANGUAGES: frozenset[str] = frozenset(
    {
        "markdown",
        "json",
        "yaml",
        "css",
        "scss",
        "html",
        "dockerfile",
    }
)

# Extension -> internal language id used for grammar selection. Note that
# ``.tsx`` is mapped to a separate parser even though the public surface area
# advertises only "typescript" -- this keeps the JSX-aware grammar in play
# without leaking implementation detail.
#
# Dockerfile has no canonical extension; ``select_grammar`` handles the
# basename-based routing for plain ``Dockerfile`` files. ``.dockerfile`` is
# included here for IDE-style suffix conventions.
EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".cs": "csharp",
    ".sql": "sql",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
    ".json": "json",
    ".jsonc": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".css": "css",
    ".scss": "scss",
    ".sass": "scss",
    ".html": "html",
    ".htm": "html",
    # Vue SFCs route through the HTML grammar (no tree-sitter-vue wheel
    # for Py 3.13; HTML is a structural superset of SFC root layout).
    ".vue": "html",
    ".dockerfile": "dockerfile",
}

# Public-facing language label for a given internal id (collapses tsx ->
# typescript so /score responses stay aligned with the advertised set).
PUBLIC_LANGUAGE: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "typescript",
    "csharp": "csharp",
    "sql": "sql",
    "markdown": "markdown",
    "json": "json",
    "yaml": "yaml",
    "css": "css",
    "scss": "scss",
    "html": "html",
    "dockerfile": "dockerfile",
}


class UnsupportedLanguageError(Exception):
    """Raised when a file extension is not in the supported set."""

    def __init__(self, extension: str):
        self.extension = extension
        super().__init__(f"unsupported language extension: {extension!r}")


_LANG_CACHE: dict[str, Any] = {}
_LOAD_LOCK = threading.Lock()


def _ext_of(path: str) -> str:
    if not path:
        return ""
    _, ext = os.path.splitext(path)
    return ext.lower()


def _load_via_aggregate(lang_id: str) -> Optional[Any]:
    """Try the tree_sitter_languages aggregate wheel."""
    try:
        from tree_sitter_languages import get_language  # type: ignore
    except Exception:
        return None
    aggregate_name = {
        "python": "python",
        "javascript": "javascript",
        "typescript": "typescript",
        "tsx": "tsx",
        "csharp": "c_sharp",
        "sql": "sql",
        "markdown": "markdown",
        "json": "json",
        "yaml": "yaml",
        "css": "css",
        "scss": "scss",
        "html": "html",
        "dockerfile": "dockerfile",
    }.get(lang_id)
    if aggregate_name is None:
        return None
    try:
        return get_language(aggregate_name)
    except Exception as exc:
        logger.debug("aggregate grammar miss for %s: %s", lang_id, exc)
        return None


def _load_via_per_lang(lang_id: str) -> Optional[Any]:
    """Try the per-language wheel.

    Each language branch wraps its own ``import`` in its own ``except
    ImportError`` so a missing wheel for one language never breaks the
    others.
    """
    from tree_sitter import Language  # type: ignore

    try:
        if lang_id == "python":
            try:
                import tree_sitter_python as ts_python  # type: ignore
            except ImportError:
                return None
            return Language(ts_python.language())
        if lang_id == "javascript":
            try:
                import tree_sitter_javascript as ts_js  # type: ignore
            except ImportError:
                return None
            return Language(ts_js.language())
        if lang_id == "typescript":
            try:
                import tree_sitter_typescript as ts_ts  # type: ignore
            except ImportError:
                return None
            return Language(ts_ts.language_typescript())
        if lang_id == "tsx":
            try:
                import tree_sitter_typescript as ts_ts  # type: ignore
            except ImportError:
                return None
            return Language(ts_ts.language_tsx())
        if lang_id == "csharp":
            try:
                import tree_sitter_c_sharp as ts_cs  # type: ignore
            except ImportError:
                try:
                    import tree_sitter_csharp as ts_cs  # type: ignore
                except ImportError:
                    return None
            return Language(ts_cs.language())
        if lang_id == "sql":
            try:
                import tree_sitter_sql as ts_sql  # type: ignore
            except ImportError:
                return None
            return Language(ts_sql.language())
        if lang_id == "markdown":
            try:
                import tree_sitter_markdown as ts_md  # type: ignore
            except ImportError:
                return None
            # tree-sitter-markdown ships two grammars (block + inline);
            # we use the block grammar — sufficient for novelty scoring.
            try:
                return Language(ts_md.language())
            except AttributeError:
                return Language(ts_md.language_block())
        if lang_id == "json":
            try:
                import tree_sitter_json as ts_json  # type: ignore
            except ImportError:
                return None
            return Language(ts_json.language())
        if lang_id == "yaml":
            try:
                import tree_sitter_yaml as ts_yaml  # type: ignore
            except ImportError:
                return None
            return Language(ts_yaml.language())
        if lang_id == "css":
            try:
                import tree_sitter_css as ts_css  # type: ignore
            except ImportError:
                return None
            return Language(ts_css.language())
        if lang_id == "scss":
            try:
                import tree_sitter_scss as ts_scss  # type: ignore
            except ImportError:
                # SCSS sometimes ships as part of tree-sitter-css; fall back.
                try:
                    import tree_sitter_css as ts_css_fallback  # type: ignore
                except ImportError:
                    return None
                return Language(ts_css_fallback.language())
            return Language(ts_scss.language())
        if lang_id == "html":
            try:
                import tree_sitter_html as ts_html  # type: ignore
            except ImportError:
                return None
            return Language(ts_html.language())
        if lang_id == "dockerfile":
            try:
                import tree_sitter_dockerfile as ts_docker  # type: ignore
            except ImportError:
                return None
            return Language(ts_docker.language())
    except Exception as exc:
        logger.debug("per-language wheel miss for %s: %s", lang_id, exc)
        return None
    return None


def _load_language(lang_id: str) -> Any:
    if lang_id in _LANG_CACHE:
        return _LANG_CACHE[lang_id]
    with _LOAD_LOCK:
        if lang_id in _LANG_CACHE:
            return _LANG_CACHE[lang_id]
        lang = _load_via_aggregate(lang_id)
        if lang is None:
            lang = _load_via_per_lang(lang_id)
        if lang is None:
            raise RuntimeError(
                f"Could not load tree-sitter grammar for {lang_id!r}. "
                f"Install tree-sitter-languages or the per-language wheel."
            )
        _LANG_CACHE[lang_id] = lang
        return lang


def select_grammar(path: str) -> tuple[str, Any]:
    """Return ``(language_id, Language)`` for the given path.

    Raises ``UnsupportedLanguageError`` for any extension not in
    ``EXTENSION_MAP`` and not matched by the Dockerfile filename special-case.

    Dockerfile files have no canonical extension. This routes the bare
    filename ``Dockerfile`` (case-insensitive) and ``foo.dockerfile``-style
    suffixed names to the ``dockerfile`` grammar.
    """
    # Dockerfile basename detection — must run before the extension lookup
    # because os.path.splitext('Dockerfile') returns ('Dockerfile', '') and
    # an empty extension is not in EXTENSION_MAP.
    basename = os.path.basename(path or "").lower()
    if basename == "dockerfile" or basename.endswith(".dockerfile"):
        lang = _load_language("dockerfile")
        return "dockerfile", lang

    ext = _ext_of(path)
    lang_id = EXTENSION_MAP.get(ext)
    if lang_id is None:
        raise UnsupportedLanguageError(ext or "")
    lang = _load_language(lang_id)
    return lang_id, lang


def get_parser(lang_id: str) -> Any:
    """Build a Parser bound to the requested language.

    Tree-sitter 0.23+ accepts ``Parser(language)`` directly; older 0.21/0.22
    require ``parser.set_language(...)`` or attribute assignment. Try the
    constructor path first, then fall back. Raises a typed RuntimeError when
    none of the binding strategies work — usually an ABI version mismatch
    between the Python bindings and the per-language grammar wheel.
    """
    from tree_sitter import Parser  # type: ignore

    lang = _load_language(lang_id)
    # Path 1: modern bindings — Parser(language) constructor.
    try:
        return Parser(lang)  # type: ignore[call-arg]
    except TypeError:
        pass
    except ValueError as exc:
        # ABI version mismatch surfaces here as ValueError. Re-raise with hint.
        raise RuntimeError(
            f"tree-sitter ABI mismatch for {lang_id!r}: {exc}. "
            f"Upgrade `tree-sitter` core to 0.25+ (see requirements.txt)."
        ) from exc
    # Path 2: legacy bindings — empty constructor + assignment / set_language.
    parser = Parser()
    try:
        parser.language = lang  # type: ignore[attr-defined]
        return parser
    except (AttributeError, ValueError):
        pass
    try:
        parser.set_language(lang)  # type: ignore[attr-defined]
        return parser
    except Exception as exc:
        raise RuntimeError(
            f"could not bind tree-sitter language {lang_id!r}: {exc}. "
            f"This typically means a tree-sitter ABI mismatch — upgrade "
            f"`tree-sitter` to 0.25+ in requirements.txt."
        ) from exc


__all__ = [
    "DATA_LANGUAGES",
    "EXTENSION_MAP",
    "PUBLIC_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "UnsupportedLanguageError",
    "get_parser",
    "select_grammar",
]
