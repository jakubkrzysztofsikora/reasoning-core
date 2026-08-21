"""Build the repo-wide function + embedding index the near-duplicate oracle
queries.

Walks the repo's source files, extracts named functions, computes their logic
tokens + a repo token-df, and embeds each. The embedder is **injectable**
(default: ``dup_embed.embed_function``) so the build / cap logic is unit-testable
offline with a stub -- the real model is only reached when no ``embed_fn`` is
supplied.

File discovery reuses ``project_index._iter_repo_files`` with the dup oracle's
own extension set: that helper walks only ``.py/.js/.ts/.tsx`` by default (for
its call-graph feature), which would silently miss duplicates in ``.mjs/.cjs``
(both JavaScript).
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .dup_index import build_token_df, extract_functions, logic_tokens
from .dup_oracle import FunctionRecord
from .grammars import EXTENSION_MAP, UnsupportedLanguageError
from .project_index import _iter_repo_files

# The oracle dedups *functions*, so index only languages the extractor AND the
# normalizer fully handle -- currently Python / JavaScript / TypeScript. Being
# grammar-parseable is NOT sufficient: the real gates are dup_index._FUNC_TYPES
# (which function nodes are extracted) and normalize()'s bound-name handling
# (which params/locals are canonicalized so renamed duplicates collapse). C# and
# SQL parse but are deliberately OUT OF SCOPE -- enabling them needs their
# function node types in _FUNC_TYPES and their param containers in the
# normalizer, not just a wider walk. Explicit allowlist by design; yields
# {.py, .js, .mjs, .cjs, .ts, .tsx}.
_CODE_LANGUAGES = frozenset({"python", "javascript", "typescript", "tsx"})
_DUP_SRC_EXTS = frozenset(
    ext for ext, lang in EXTENSION_MAP.items() if lang in _CODE_LANGUAGES
)


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


def _index_file(
    repo_root: str,
    rel: str,
    embed_fn: Callable[[str], "np.ndarray"],
) -> tuple[list[FunctionRecord], list["np.ndarray"]]:
    """Extract, tokenise and embed every named function in one repo file.

    Returns ``(records, vecs)`` row-aligned (``vecs[i]`` is the embedding of
    ``records[i]``). The single per-file extract+embed code path shared by the
    plain builder and the persistent cache, so both honour the same contracts:

    * an unreadable file (``OSError``) or a non-code extension
      (``UnsupportedLanguageError``) yields ``([], [])`` -- skip it;
    * a grammar-load/ABI ``RuntimeError`` is deliberately **not** caught. Every
      in-scope language is a required dependency, so such a failure is systemic
      (a broken install), not a per-file quirk -- letting it surface is correct
      rather than masking it as a silently-empty index. This is also why callers
      must assemble fully *before* persisting: a mid-build raise here must never
      leave a truncated cache behind.
    """
    try:
        with open(os.path.join(repo_root, rel), encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError:
        return [], []
    try:
        funcs = extract_functions(rel, src)
    except UnsupportedLanguageError:
        return [], []
    records: list[FunctionRecord] = []
    vecs: list["np.ndarray"] = []
    for name, line, fsrc in funcs:
        records.append(FunctionRecord(rel, name, line, logic_tokens(rel, fsrc)))
        vecs.append(embed_fn(fsrc))
    return records, vecs


def _assemble_index(
    records: list[FunctionRecord], vecs: list["np.ndarray"]
) -> DupOracleIndex:
    """Pack row-aligned records + vectors into a ``DupOracleIndex`` (token_df is
    derived cheaply from the records)."""
    if records:
        embeddings = np.stack(vecs).astype(np.float32)
    else:
        embeddings = np.empty((0, 0), dtype=np.float32)
    token_df, _ = build_token_df([r.logic_tokens for r in records])
    return DupOracleIndex(records=records, embeddings=embeddings, token_df=token_df)


def build_dup_index(
    repo_root: str,
    *,
    embed_fn: Optional[Callable[[str], "np.ndarray"]] = None,
    max_funcs: int = DEFAULT_MAX_FUNCS,
) -> DupOracleIndex:
    """Build the function + embedding index for ``repo_root`` (no persistence).

    ``embed_fn`` maps a function's source to an L2-normalised vector; when
    omitted the real code embedder is used. ``max_funcs`` bounds how many
    functions land in the index, so a monorepo can't blow the budget. Stays
    persistence-free -- the disk cache lives in :func:`load_or_build_dup_index`.
    """
    if embed_fn is None:
        embed_fn = _default_embed()

    records: list[FunctionRecord] = []
    vecs: list["np.ndarray"] = []
    for rel in _iter_repo_files(repo_root, _DUP_SRC_EXTS):
        if len(records) >= max_funcs:
            break
        frecords, fvecs = _index_file(repo_root, rel, embed_fn)
        for rec, vec in zip(frecords, fvecs):
            records.append(rec)
            vecs.append(vec)
            if len(records) >= max_funcs:
                break
    return _assemble_index(records, vecs)
