"""Tree-sitter grammar loader for the System 2 sidecar.

Supports exactly five languages (per RC-003 / PLAN.md D2):

    Python, JavaScript, TypeScript (+TSX), C#, SQL.

Loader strategy:
    1. Prefer the ``tree_sitter_languages`` aggregate wheel which ships
       compiled grammars for ~40 languages. This is the path of least
       friction and avoids per-grammar build steps on macOS arm64.
    2. Fall back to the per-language wheels declared in requirements.txt:
       ``tree_sitter_python``, ``tree_sitter_javascript``,
       ``tree_sitter_typescript`` (exposes both ``language_typescript`` and
       ``language_tsx``), ``tree_sitter_c_sharp``, ``tree_sitter_sql``.

The two contracts other engineers rely on:

* ``select_grammar(path)`` returns a ``(language_name, Language)`` tuple where
  ``language_name`` is one of ``"python"``, ``"javascript"``, ``"typescript"``,
  ``"tsx"``, ``"csharp"``, ``"sql"`` (the names in the public board API map
  ``"tsx"`` as a sub-language of TypeScript, but for parsing we keep them
  distinct so the right grammar is used).
* ``UnsupportedLanguageError`` is raised -- never silently fall back -- when
  the extension is not in the supported set.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Public language identifiers. The HTTP /health response advertises these.
SUPPORTED_LANGUAGES: tuple[str, ...] = (
    "python",
    "javascript",
    "typescript",
    "csharp",
    "sql",
)

# Extension -> internal language id used for grammar selection. Note that
# ``.tsx`` is mapped to a separate parser even though the public surface area
# advertises only "typescript" -- this keeps the JSX-aware grammar in play
# without leaking implementation detail.
EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".cs": "csharp",
    ".sql": "sql",
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
    }[lang_id]
    try:
        return get_language(aggregate_name)
    except Exception as exc:
        logger.debug("aggregate grammar miss for %s: %s", lang_id, exc)
        return None


def _load_via_per_lang(lang_id: str) -> Optional[Any]:
    """Try the per-language wheel."""
    from tree_sitter import Language  # type: ignore

    try:
        if lang_id == "python":
            import tree_sitter_python as ts_python  # type: ignore

            return Language(ts_python.language())
        if lang_id == "javascript":
            import tree_sitter_javascript as ts_js  # type: ignore

            return Language(ts_js.language())
        if lang_id == "typescript":
            import tree_sitter_typescript as ts_ts  # type: ignore

            return Language(ts_ts.language_typescript())
        if lang_id == "tsx":
            import tree_sitter_typescript as ts_ts  # type: ignore

            return Language(ts_ts.language_tsx())
        if lang_id == "csharp":
            try:
                import tree_sitter_c_sharp as ts_cs  # type: ignore
            except ImportError:
                import tree_sitter_csharp as ts_cs  # type: ignore
            return Language(ts_cs.language())
        if lang_id == "sql":
            import tree_sitter_sql as ts_sql  # type: ignore

            return Language(ts_sql.language())
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
    ``EXTENSION_MAP``.
    """
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
    "EXTENSION_MAP",
    "PUBLIC_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "UnsupportedLanguageError",
    "get_parser",
    "select_grammar",
]
