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
    for rel in _iter_repo_files(repo_root, _DUP_SRC_EXTS):
        if len(collected) >= max_funcs:
            break
        try:
            with open(os.path.join(repo_root, rel), encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        try:
            funcs = extract_functions(rel, src)
        except UnsupportedLanguageError:
            continue  # extension isn't a code grammar
        # NB: a grammar-load/ABI RuntimeError is deliberately NOT caught here.
        # Every in-scope language is a required dependency, so such a failure is
        # systemic (a broken install), not a per-file quirk -- letting it surface
        # is correct rather than masking it as a silently-empty index.
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
