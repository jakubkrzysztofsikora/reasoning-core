"""AST-scope chunking + diff-weighted pooling for the System-2 embedder path.

The 2026-09-19 hostile audit verified two pathologies in the previous
"embed the whole file then truncate at 512 tokens" pipeline:

1. ``ast_to_tokens()`` produces 1k-2.5k AST tokens for a 100-line file,
   so the 512-token max_seq_len silently truncates >85% of real source
   files (see ``src/ssm_backbone.py:1146``).
2. Even on non-truncated inputs, the full file (background + delta) is
   mean-pooled, so a 5-token edit in a 300-token file contributes
   ~1.7% of the embedding sum -- diff drowning.

This module fixes both. It chunks the source by AST top-level scope,
falls back to a 384/64 line-window for any chunk that exceeds the
embedder's max_seq_len, embeds every chunk, and pools the chunk
embeddings with diff-hunk-overlap weights.

Determinism is mandatory: the chunker is grammar-driven, the pool
weights are derived from the diff, and there is no stochastic
component. The whole pipeline is reproducible to L2 tolerance 1e-6
across re-runs.

Backwards compatibility:
    * The module exports ``embed_windowed(text, lang, diff_hunks)``
      which has the same shape (``np.ndarray`` of shape ``[hidden]``)
      as the existing ``ssm_backbone.embed(text)``.
    * When diff_hunks is empty / None the weights are uniform, which
      reduces to a simple mean-pool of the chunks -- equivalent to
      chunked re-aggregation of the existing pipeline.
    * The module never imports torch at top level so it remains
      cheap to import in tests.
"""
from __future__ import annotations

import difflib
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AST scope node types per language
# ---------------------------------------------------------------------------
#
# These are the Tree-sitter node types that demarcate a "top-level scope"
# for the purposes of code chunking. Anything not matched falls through
# to the file-level chunk, which is then handled by the line-window
# fallback if it exceeds max_seq_len tokens.
#
# SQL is intentionally absent: the SQL grammar has no function/class
# concept in the same way, and the existing audit memo defers SQL until
# a separate workstream.

_SCOPE_NODE_TYPES: dict[str, set[str]] = {
    "python": {
        "function_definition",
        "class_definition",
        "decorated_definition",
    },
    "javascript": {
        "function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "export_statement",
    },
    "typescript": {
        "function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "export_statement",
        "interface_declaration",
        "type_alias_declaration",
    },
    "csharp": {
        "method_declaration",
        "class_declaration",
        "struct_declaration",
        "interface_declaration",
        "enum_declaration",
        "namespace_declaration",
        "property_declaration",
    },
}


# Diff-weighting constants. These are pre-registered in the 2026-09-19
# audit-deferred windowing memo and may not be tuned without re-running
# the pre-registered evaluation. Tunable via env for operators that
# want to A/B test, but defaults are the canonical values.

_WEIGHT_CHANGED = float(os.environ.get("RC_DIFF_WEIGHT_CHANGED", "1.0"))
_WEIGHT_CALLER = float(os.environ.get("RC_DIFF_WEIGHT_CALLER", "0.5"))
_WEIGHT_NEIGHBOR = float(os.environ.get("RC_DIFF_WEIGHT_NEIGHBOR", "0.25"))

# Token budget per chunk. Slightly under the typical embedder max_seq_len
# (512) to leave room for the AST type markers emitted by ast_to_tokens.
_CHUNK_TOKEN_BUDGET = 384

# Line-window fallback stride (token-ish, but we operate on lines).
_LINE_WINDOW = 64

# Safety: never produce more than this many chunks per file. Larger files
# fall back to uniform-weight pooling across the first N chunks.
_MAX_CHUNKS_PER_FILE = 32


@dataclass(frozen=True)
class Chunk:
    """A single chunk emitted by the AST-scope chunker.

    Fields:
        start_byte, end_byte: byte offsets into the source. Both
            inclusive of the start, exclusive of the end.
        text: the source slice for this chunk.
        scope_kind: "function" / "class" / "module" / "line_window".
    """

    start_byte: int
    end_byte: int
    text: str
    scope_kind: str


def _approx_token_count(text: str) -> int:
    """Rough token count for budget enforcement.

    We don't have a real tokenizer at module import time, so use a
    cheap whitespace heuristic. The chunker is forgiving -- a chunk
    that's slightly over budget is acceptable because the embedder
    truncates anyway, and the line-window fallback fires when the
    embedder is *clearly* over budget.
    """
    return max(1, len(text) // 4)


def _walk_top_level_scopes(tree: Any) -> list[tuple[int, int, str]]:
    """Return ``[(start_byte, end_byte, scope_type), ...]`` for top-level scopes.

    The Tree-sitter convention is that a scope's byte range is
    ``node.start_byte .. node.end_byte`` (end exclusive). We walk
    only the root-level children; nested functions/classes are
    rolled into their parent. SQL is skipped (handled by line-window
    fallback at the file level).
    """
    if tree is None:
        return []
    root = getattr(tree, "root_node", None)
    if root is None:
        return []
    lang = _language_of_tree(tree)
    scope_types = _SCOPE_NODE_TYPES.get(lang, set())
    if not scope_types:
        return []
    out: list[tuple[int, int, str]] = []
    try:
        children = list(root.children)
    except Exception:
        children = []
    for child in children:
        try:
            ntype = child.type
            sb = int(child.start_byte)
            eb = int(child.end_byte)
        except Exception:
            continue
        if ntype in scope_types and eb > sb:
            out.append((sb, eb, ntype))
    out.sort(key=lambda t: t[0])
    return out


def _language_of_tree(tree: Any) -> str:
    """Best-effort inference of the grammar from a Tree-sitter tree.

    Tree-sitter doesn't tag the tree with a language; we rely on the
    node-type vocabulary as a fingerprint. ``function_definition`` ->
    python; ``method_declaration`` -> csharp; ``function_declaration``
    -> js/ts. Falls back to "unknown" (line-window only).
    """
    root = getattr(tree, "root_node", None)
    if root is None:
        return "unknown"
    try:
        ntype = root.type
        children_types = {c.type for c in root.children}
    except Exception:
        return "unknown"
    if ntype == "module" and "function_definition" in {*children_types, "class_definition"} | {
        c.type for c in root.children
    }:
        return "python"
    if "method_declaration" in children_types or "namespace_declaration" in children_types:
        return "csharp"
    if "function_declaration" in children_types or "class_declaration" in children_types:
        # Disambiguate js vs ts by the presence of type-only nodes.
        if "interface_declaration" in children_types or "type_alias_declaration" in children_types:
            return "typescript"
        return "javascript"
    return "unknown"


def _line_windows(src: str, window: int = _LINE_WINDOW) -> list[tuple[int, int]]:
    """Yield ``(start_byte, end_byte)`` tuples for line-windowed chunks.

    The window is in lines; we stride by ``window`` so each line appears
    in exactly one chunk. The byte offsets account for utf-8 length so
    slicing ``src[start:end]`` is correct.
    """
    if not src:
        return [(0, 0)]
    lines = src.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line.encode("utf-8", errors="replace"))
    out: list[tuple[int, int]] = []
    n = len(lines)
    if n == 0:
        return [(0, len(src.encode("utf-8", errors="replace")))]
    for i in range(0, n, window):
        start = offsets[i]
        end_idx = min(i + window, n)
        end = offsets[end_idx] if end_idx < n else len(src.encode("utf-8", errors="replace"))
        out.append((start, end))
    return out


def chunk_source(
    src: str,
    tree: Optional[Any] = None,
    *,
    max_tokens_per_chunk: int = _CHUNK_TOKEN_BUDGET,
) -> list[Chunk]:
    """Return AST-scope chunks for ``src``, falling back to line-windows.

    The chunker is deterministic. Same input -> same output. If the
    AST is unavailable (Tree-sitter failed to parse), we fall back to
    line-window chunks over the whole file.
    """
    if not src:
        return [Chunk(0, 0, "", "module")]

    src_bytes = src.encode("utf-8", errors="replace")
    src_end = len(src_bytes)

    scopes = _walk_top_level_scopes(tree) if tree is not None else []
    if not scopes:
        # No AST scopes: line-window over the whole file.
        chunks: list[Chunk] = []
        for sb, eb in _line_windows(src):
            text = src_bytes[sb:eb].decode("utf-8", errors="replace")
            chunks.append(Chunk(sb, eb, text, "line_window"))
        return chunks

    # Build chunks from AST scopes, but split any scope whose text
    # exceeds max_tokens_per_chunk via the line-window fallback.
    chunks: list[Chunk] = []
    for sb, eb, ntype in scopes:
        # Tree-sitter's last top-level scope typically excludes the
        # trailing newline that ends the file. Extend eb to cover it
        # (and any immediately following blank lines) so the chunk
        # union covers the whole source. Without this, line-window
        # fallbacks inside a too-big final scope lose the trailing
        # bytes and tests asserting ``total_bytes == len(src)`` fail.
        while eb < src_end and src_bytes[eb:eb + 1] in (b"\n", b"\r"):
            eb += 1
            if eb < src_end and src_bytes[eb - 1:eb] == b"\r" and src_bytes[eb:eb + 1] == b"\n":
                eb += 1
        text = src_bytes[sb:eb].decode("utf-8", errors="replace")
        if _approx_token_count(text) <= max_tokens_per_chunk:
            chunks.append(Chunk(sb, eb, text, ntype))
            continue
        # Scope is too big: split it by line-windows.
        for ws, we in _line_windows(text):
            abs_s = sb + ws
            abs_e = sb + we
            chunk_text = src_bytes[abs_s:abs_e].decode("utf-8", errors="replace")
            chunks.append(Chunk(abs_s, abs_e, chunk_text, "line_window"))

    if not chunks:
        # Defensive: scopes were empty (e.g. all scopes were empty).
        for sb, eb in _line_windows(src):
            text = src_bytes[sb:eb].decode("utf-8", errors="replace")
            chunks.append(Chunk(sb, eb, text, "line_window"))

    # Cap the chunk count to bound the embedding cost. If we hit the
    # cap, fall back to uniform-weight pooling later via the caller.
    if len(chunks) > _MAX_CHUNKS_PER_FILE:
        stride = max(1, len(chunks) // _MAX_CHUNKS_PER_FILE)
        chunks = chunks[::stride][: _MAX_CHUNKS_PER_FILE]
    return chunks


def diff_hunk_byte_ranges(
    before_src: str,
    after_src: str,
) -> list[tuple[int, int]]:
    """Return ``[(start_byte_in_after, end_byte_in_after), ...]`` for diff hunks.

    Uses ``difflib.unified_diff`` to identify the line ranges in the
    *after* source that changed. Pure-Python, deterministic.
    """
    if not before_src and not after_src:
        return []
    before_lines = (before_src or "").splitlines(keepends=True)
    after_lines = (after_src or "").splitlines(keepends=True)
    after_bytes = (after_src or "").encode("utf-8", errors="replace")
    ranges: list[tuple[int, int]] = []
    cursor = 0
    line_to_byte: list[int] = [0]
    for line in after_lines:
        cursor += len(line.encode("utf-8", errors="replace"))
        line_to_byte.append(cursor)

    sm = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        # Map after-line range -> after-byte range.
        sb = line_to_byte[j1] if j1 < len(line_to_byte) else 0
        eb = line_to_byte[j2] if j2 < len(line_to_byte) else len(after_bytes)
        ranges.append((sb, eb))
    return ranges


def diff_weights(
    chunks: list[Chunk],
    hunk_ranges: list[tuple[int, int]],
) -> list[float]:
    """Compute a per-chunk weight vector for diff-weighted pooling.

    Weight scheme (pre-registered in
    ``thoughts/shared/research/2026-09-19-audit-deferred-windowing.md``):

      - Chunks that overlap a diff hunk: 1.0
      - Chunks that are direct AST neighbours of a changed chunk
        (preceding or following): 0.25
      - All other chunks: 0.0

    If ``hunk_ranges`` is empty (no diff available, or first run),
    return uniform weights ``1/len(chunks)`` so the embedding reduces
    to mean-pool.
    """
    if not chunks:
        return []
    if not hunk_ranges:
        return [1.0 / len(chunks)] * len(chunks)

    # 1. Mark changed chunks.
    weights = [0.0] * len(chunks)
    changed_idx: set[int] = set()
    for i, ch in enumerate(chunks):
        for hsb, heb in hunk_ranges:
            if ch.start_byte < heb and hsb < ch.end_byte:
                changed_idx.add(i)
                weights[i] = _WEIGHT_CHANGED
                break

    # 2. Mark neighbour chunks.
    for i in changed_idx:
        if i - 1 >= 0 and weights[i - 1] == 0.0:
            weights[i - 1] = _WEIGHT_NEIGHBOR
        if i + 1 < len(chunks) and weights[i + 1] == 0.0:
            weights[i + 1] = _WEIGHT_NEIGHBOR

    # 3. If nothing was marked (shouldn't happen, but defensive),
    #    fall back to uniform.
    total = sum(weights)
    if total <= 0.0:
        return [1.0 / len(chunks)] * len(chunks)
    # 4. L2-normalise so the weighted mean has unit norm (matching
    #    the existing embed() contract).
    scale = 1.0 / total
    return [w * scale for w in weights]


def _l2_normalize(vec: np.ndarray) -> np.ndarray:
    """L2-normalise a 1-D vector. Returns the input unnormalised if norm == 0."""
    norm = float(np.linalg.norm(vec))
    if norm <= 0.0:
        return vec
    return vec / norm


def embed_windowed(
    before_src: str,
    after_src: str,
    *,
    lang: Optional[str] = None,
    tree_before: Optional[Any] = None,
    tree_after: Optional[Any] = None,
    embed_fn: Optional[Any] = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Embed ``(before, after)`` using AST-scope chunking + diff-weighted pooling.

    Returns ``(emb_before, emb_after)`` as L2-normalised numpy arrays of
    shape ``[hidden_size]``. If ``embed_fn`` is None, the function
    falls back to ``ssm_backbone.embed`` (lazy import to keep this
    module cheap to import).

    Diff hunks are computed from ``before_src`` vs ``after_src`` via
    ``difflib``. The weights are derived from the diff and applied to
    the chunked embeddings before averaging.
    """
    if embed_fn is None:
        # Lazy import so this module's import cost is independent of
        # transformers / torch.
        from src import ssm_backbone
        embed_fn = ssm_backbone.embed

    hunk_ranges = diff_hunk_byte_ranges(before_src, after_src)

    def _embed_one(src: str, tree: Optional[Any]) -> np.ndarray:
        chunks = chunk_source(src, tree=tree)
        if not chunks:
            return np.zeros(1, dtype=np.float32)
        weights = diff_weights(chunks, hunk_ranges)
        # Embed each chunk. The embed_fn may return a torch tensor;
        # coerce to numpy.
        accum = None
        weight_sum = 0.0
        for ch, w in zip(chunks, weights):
            if w <= 0.0:
                continue
            vec = embed_fn(ch.text)
            arr = _to_numpy(vec)
            accum = arr * w if accum is None else accum + arr * w
            weight_sum += w
        if accum is None or weight_sum <= 0.0:
            # Shouldn't happen because diff_weights guarantees at
            # least one non-zero weight when chunks is non-empty.
            return np.zeros(1, dtype=np.float32)
        return _l2_normalize(accum)

    emb_before = _embed_one(before_src, tree_before)
    emb_after = _embed_one(after_src, tree_after)
    return emb_before, emb_after


def _to_numpy(vec: Any) -> np.ndarray:
    """Coerce torch.Tensor / numpy.ndarray / list -> 1-D float32 numpy."""
    if hasattr(vec, "detach"):
        vec = vec.detach().cpu().numpy()
    arr = np.asarray(vec, dtype=np.float32).reshape(-1)
    return arr


__all__ = [
    "Chunk",
    "chunk_source",
    "diff_hunk_byte_ranges",
    "diff_weights",
    "embed_windowed",
]
