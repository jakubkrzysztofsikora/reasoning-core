"""Language-agnostic function normalisation for near-duplicate detection.

Reduces a function to its *logic* -- the tokens that carry meaning (operators,
callee / property names, literal values) with local variable names canonicalised
and language scaffolding (type annotations, the function's own name) stripped --
so a renamed (or lightly-edited) duplicate collapses to the same token stream
while a same-shape sibling (``min`` / ``max``, ``addWeeks`` / ``subWeeks``) does
not. A wholesale structural rewrite is *not* expected to match here -- that is
what the embedding shortlist (Stage 1) is for.

Pure: tree-sitter only, no model. Parsing goes through :mod:`src.grammars`, so
the normaliser supports every language the grammar layer does. Used by the
near-duplicate oracle as the precision (Stage 2) step on top of an embedding
shortlist -- see ``docs/dup-oracle.md``.
"""
from __future__ import annotations

import difflib
from collections import Counter
from collections.abc import Iterable
from typing import Any

from .grammars import get_parser, select_grammar

# Identifier-like leaf node types across the supported grammars.
_IDENT_TYPES = frozenset({
    "identifier", "property_identifier", "shorthand_property_identifier",
    "private_property_identifier", "shorthand_property_identifier_pattern",
})
# Nodes that introduce local bindings whose *names* are noise (renaming them
# must not change the logic signature).
_PARAM_CONTAINERS = frozenset({"parameters", "formal_parameters"})
# Subtrees pruned whole before tokenising, so their contents never reach the
# logic stream: type annotations (`min<T>(...)` reads like `min(...)`, so TS
# generics don't drown the operator that distinguishes siblings) and string
# literals (a string like "<" must not leak in as an operator token).
_PRUNED_SUBTREES = frozenset({
    "type_annotation", "type_arguments", "type_parameters", "predefined_type",
    "generic_type", "union_type", "object_type", "type_identifier",
    "type_alias_declaration", "index_type_query", "type",
    "string", "template_string",
})
_FUNC_TYPES = frozenset({
    "function_declaration", "method_definition", "arrow_function",
    "function_expression", "function_definition",
})
# Structural punctuation carries no logic signal -- dropped.
_PUNCT = frozenset({"(", ")", "{", "}", "[", "]", ",", ";", ":", ".", "$", "=>", "?."})
# Operators ARE logic: `<` vs `>` is the whole difference between min and max.
_OPERATORS = frozenset({
    "+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "===", "!=", "!==",
    "&&", "||", "!", "**", "&", "|", "^", "~", "<<", ">>", "?",
})


def _parse(path: str, src: str) -> Any:
    """Parse ``src`` with the grammar chosen by ``path``'s extension.

    Raises ``UnsupportedLanguageError`` (from :mod:`src.grammars`) for unknown
    extensions; the caller decides what to do.
    """
    lang_id, _ = select_grammar(path)
    parser = get_parser(lang_id)
    return parser.parse(src.encode("utf-8", errors="replace"))


def _text(node: Any, src_bytes: bytes) -> str:
    return src_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _leaves(node: Any, prune: frozenset[str]):
    """Yield leaf nodes, skipping whole subtrees whose type is in ``prune``."""
    if node.type in prune:
        return
    if not node.children:
        yield node
        return
    for child in node.children:
        yield from _leaves(child, prune)


def _first_function(root: Any) -> Any:
    """Return the first function-like node in document order, or ``root`` if none.

    Callers feed a single extracted function, so sibling order is not relied
    upon; the reversed push keeps a true left-to-right traversal regardless.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in _FUNC_TYPES:
            return node
        stack.extend(reversed(node.children))
    return root


def _bound_names(fn: Any, src_bytes: bytes) -> set[str]:
    """Names local to the function (params, declared vars, assignment targets).

    Their identities are noise -- two functions that differ only in local
    variable names are the same logic.
    """
    names: set[str] = set()
    stack = [fn]
    while stack:
        node = stack.pop()
        if node.type in _PARAM_CONTAINERS:
            for leaf in _leaves(node, frozenset()):
                if leaf.type == "identifier":
                    names.add(_text(leaf, src_bytes))
        elif node.type == "variable_declarator":
            name = node.child_by_field_name("name")
            if name is not None and name.type == "identifier":
                names.add(_text(name, src_bytes))
        elif node.type in ("assignment", "augmented_assignment"):
            lhs = node.child_by_field_name("left")
            if lhs is not None:
                for leaf in _leaves(lhs, frozenset()):
                    if leaf.type == "identifier":
                        names.add(_text(leaf, src_bytes))
        elif node.type in ("for_in_statement", "for_of_statement", "for_statement"):
            # Loop binding, e.g. `for (const d of xs)` / `for x in xs` -- the
            # variable sits under the ``left`` field for both TS/JS and Python.
            left = node.child_by_field_name("left")
            if left is not None:
                for leaf in _leaves(left, frozenset()):
                    if leaf.type == "identifier":
                        names.add(_text(leaf, src_bytes))
        stack.extend(node.children)
    return names


def normalize(path: str, src: str) -> list[str]:
    """Return the normalised token stream for the first function in ``src``.

    Local variables -> ``V``; the function's own name is dropped; type
    annotations and string literals are pruned entirely; free names (callees,
    properties) are kept as ``N:<name>``; numeric literals as ``NUM:<value>``;
    operators / keywords as ``K:<sym>``.
    """
    src_bytes = src.encode("utf-8", errors="replace")
    fn = _first_function(_parse(path, src).root_node)
    bound = _bound_names(fn, src_bytes)
    own = fn.child_by_field_name("name")
    own_span = (own.start_byte, own.end_byte) if own is not None else None

    tokens: list[str] = []
    for leaf in _leaves(fn, _PRUNED_SUBTREES):
        if own_span is not None and (leaf.start_byte, leaf.end_byte) == own_span:
            continue  # drop the function's own name -- a rename is not a logic change
        ttype = leaf.type
        text = _text(leaf, src_bytes)
        if ttype in _IDENT_TYPES:
            tokens.append("V" if text in bound else "N:" + text)
        elif ttype == "number":
            tokens.append("NUM:" + text)
        elif text in _PUNCT:
            continue
        elif text:
            tokens.append("K:" + (text if len(text) <= 4 else ttype))
    return tokens


def logic_tokens(path: str, src: str) -> list[str]:
    """The meaning-bearing subset of :func:`normalize`: operators, free names,
    literal values. This is what a duplicate must match on -- the shared
    skeleton (keywords, bound vars, punctuation) is dropped so a single
    differing operator or callee is not diluted by boilerplate.
    """
    return [
        t for t in normalize(path, src)
        if t.startswith("N:") or t.startswith("NUM:")
        or (t.startswith("K:") and t[2:] in _OPERATORS)
    ]


def logic_ratio_tokens(a_tokens: list[str], b_tokens: list[str]) -> float:
    """Similarity in [0, 1] of two already-extracted logic-token streams.

    The query path uses this directly on stored tokens, so the oracle never
    re-parses index functions at query time.
    """
    if not a_tokens and not b_tokens:
        return 1.0
    return difflib.SequenceMatcher(None, a_tokens, b_tokens).ratio()


def logic_ratio(a_path: str, a_src: str, b_path: str, b_src: str) -> float:
    """Similarity in [0, 1] of two functions' logic-token streams.

    ~1.0 for a renamed or lightly-edited duplicate; clearly lower for a
    same-shape sibling that differs in a real operator/callee, and low for a
    wholesale structural rewrite (which Stage 1's embedding shortlist catches).
    """
    return logic_ratio_tokens(logic_tokens(a_path, a_src), logic_tokens(b_path, b_src))


def extract_functions(path: str, src: str) -> list[tuple[str, int, str]]:
    """Extract named function definitions from a source file.

    Yields ``(name, line, source)`` for each ``function_declaration``,
    ``method_definition``, and *named* (variable-bound) arrow / function
    expression whose source is 40-3000 chars. Unnamed inline callbacks are
    skipped so the index holds reusable, named units. ``line`` is 1-based.
    """
    src_bytes = src.encode("utf-8", errors="replace")
    root = _parse(path, src).root_node
    out: list[tuple[str, int, str]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in _FUNC_TYPES:
            parent = node.parent
            if node.type in ("arrow_function", "function_expression"):
                if parent is None or parent.type != "variable_declarator":
                    stack.extend(node.children)
                    continue
            text = _text(node, src_bytes)
            if 40 <= len(text) <= 3000:
                name_node = node.child_by_field_name("name")
                if name_node is None and parent is not None and parent.type == "variable_declarator":
                    name_node = parent.child_by_field_name("name")
                name = _text(name_node, src_bytes) if name_node is not None else "anonymous"
                out.append((name, node.start_point[0] + 1, text))
        stack.extend(node.children)
    return out


# ---------------------------------------------------------------------------
# Distinctiveness ranking
# ---------------------------------------------------------------------------
#
# Two confirmed duplicates that share only *ubiquitous* tokens (``super``,
# ``this``, a common assignment) are weak evidence -- typically boilerplate like
# identical constructors. Two that share repo-*rare* tokens (a specific callee)
# are strong. We rank hits by how many distinctive (rare) tokens they share, so
# boilerplate sinks below genuine duplication. This is a RANKING signal, not a
# hard filter: gating on it wrongly dropped a real duplicate in testing.


def build_token_df(functions_tokens: Iterable[list[str]]) -> tuple[Counter, int]:
    """Document frequency of each logic token across a corpus of functions.

    Returns ``(df, n)`` where ``df[token]`` is the number of functions whose
    logic-token *set* contains ``token`` and ``n`` is the function count.
    """
    df: Counter = Counter()
    n = 0
    for tokens in functions_tokens:
        n += 1
        for token in set(tokens):
            df[token] += 1
    return df, n


def rare_cutoff(n_functions: int, frac: float = 0.03, floor: int = 3) -> int:
    """Df threshold at/below which a token counts as *distinctive*.

    Scales with repo size (``frac`` of functions) over a small absolute floor,
    so tiny repos still get a meaningful cutoff.
    """
    return max(floor, int(frac * n_functions))


def distinctive_shared_tokens(
    a_tokens: list[str], b_tokens: list[str], token_df: Counter, cutoff: int
) -> list[str]:
    """Tokens shared by both functions that are rare across the repo
    (``df <= cutoff``) -- the evidence a match is genuine, not boilerplate. Also
    what the advisory shows the agent as the "why we think so".
    """
    shared = set(a_tokens) & set(b_tokens)
    return sorted(t for t in shared if token_df.get(t, 0) <= cutoff)
