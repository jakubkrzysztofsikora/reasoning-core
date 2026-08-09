"""Build the repo-wide function + embedding index the near-duplicate oracle
queries.

Walks the repo's source files, extracts named functions, computes their logic
tokens + a repo token-df, and embeds each. The embedder is **injectable**
(default: ``dup_embed.embed_function``) so the build / cap logic is unit-testable
offline with a stub -- the real model is only reached when no ``embed_fn`` is
supplied.

File discovery is the dup oracle's own (``_iter_source_files``) rather than
``project_index._iter_repo_files``: the latter walks only ``.py/.js/.ts/.tsx``
for its call-graph feature, which would silently miss duplicates in other
grammar-supported code sources (``.mjs/.cjs/.cs``).
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .dup_index import build_token_df, extract_functions, logic_tokens
from .dup_oracle import FunctionRecord
from .grammars import EXTENSION_MAP, UnsupportedLanguageError
from .project_index import _is_skip_dir

# The oracle dedups *functions*, so index only the function-bearing code
# languages the grammars support -- not data/markup (json, yaml, css, html,
# markdown, dockerfile, sql) which have nothing to compare. Derived from the
# grammar extension map so a newly-supported code language is covered here
# automatically. Yields e.g. {.py, .js, .mjs, .cjs, .ts, .tsx, .cs}.
_CODE_LANGUAGES = frozenset({"python", "javascript", "typescript", "tsx", "csharp"})
_DUP_SRC_EXTS = frozenset(
    ext for ext, lang in EXTENSION_MAP.items() if lang in _CODE_LANGUAGES
)


def _iter_source_files(repo_root: str) -> list[str]:
    """Return sorted relative paths of the repo's function-bearing code files.

    Mirrors ``project_index``'s skip-dir pruning (dotdirs, vendored trees) but
    covers every code extension the dup oracle can extract functions from.
    """
    found: list[str] = []
    root = Path(repo_root)
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames if not _is_skip_dir(d)]
        reldir = Path(dirpath).relative_to(root)
        for fn in filenames:
            if any(fn.endswith(ext) for ext in _DUP_SRC_EXTS):
                found.append(str(reldir / fn))
    found.sort()
    return found


def _max_funcs_from_env() -> int:
    """Cap embedding cost / memory on large monorepos (768-dim f32 ~= 3 KB/fn).

    A malformed env value falls back to the default rather than raising at import
    time (which would break the hook's fail-open contract).
    """
    try:
        return int(os.environ.get("RC_DUP_ORACLE_MAX_FUNCS", "5000"))
    except ValueError:
        return 5000


DEFAULT_MAX_FUNCS = _max_funcs_from_env()


@dataclass
class DupOracleIndex:
    """The repo functions the oracle checks a new function against."""

    records: list[FunctionRecord]
    embeddings: np.ndarray  # (n, d) float32, L2-normalised, row-aligned to records
    token_df: Counter

    def __len__(self) -> int:
        return len(self.records)


def _default_embed() -> Callable[[str], "np.ndarray"]:
    # Imported lazily so this module (and its offline tests) never pull torch
    # unless the real embedder is actually used.
    from .dup_embed import embed_function

    return embed_function


def build_dup_index(
    repo_root: str,
    *,
    embed_fn: Optional[Callable[[str], "np.ndarray"]] = None,
    max_funcs: int = DEFAULT_MAX_FUNCS,
) -> DupOracleIndex:
    """Build the function + embedding index for ``repo_root``.

    ``embed_fn`` maps a function's source to an L2-normalised vector; when
    omitted the real code embedder is used. ``max_funcs`` bounds how many
    functions are embedded, so a monorepo can't blow the budget.
    """
    if embed_fn is None:
        embed_fn = _default_embed()

    collected: list[tuple[str, str, int, str]] = []  # (rel, name, line, source)
    for rel in _iter_source_files(repo_root):
        if len(collected) >= max_funcs:
            break
        try:
            src = open(os.path.join(repo_root, rel), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        try:
            funcs = extract_functions(rel, src)
        except (UnsupportedLanguageError, RuntimeError):
            # UnsupportedLanguageError: extension isn't a code grammar.
            # RuntimeError: the grammar wheel isn't installed (e.g. C# absent).
            # Either way, skip this file rather than failing the whole build --
            # the oracle must degrade gracefully, never disable itself on one
            # unindexable file.
            continue
        for name, line, fsrc in funcs:
            collected.append((rel, name, line, fsrc))
            if len(collected) >= max_funcs:
                break

    records = [
        FunctionRecord(rel, name, line, logic_tokens(rel, fsrc))
        for (rel, name, line, fsrc) in collected
    ]
    if records:
        embeddings = np.stack([embed_fn(fsrc) for (_r, _n, _l, fsrc) in collected]).astype(np.float32)
    else:
        embeddings = np.empty((0, 0), dtype=np.float32)
    token_df, _ = build_token_df([r.logic_tokens for r in records])
    return DupOracleIndex(records=records, embeddings=embeddings, token_df=token_df)
