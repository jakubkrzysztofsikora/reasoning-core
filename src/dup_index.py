"""Language-agnostic function normalisation for near-duplicate detection.

Reduces a function to its *logic* -- the tokens that carry meaning (operators,
callee / property names, literal values) with local variable names canonicalised
and language scaffolding (type annotations, the function's own name) stripped --
so a renamed or rewritten duplicate collapses to the same token stream while a
same-shape sibling (``min`` / ``max``, ``addWeeks`` / ``subWeeks``) does not.

Pure: tree-sitter only, no model. Parsing goes through :mod:`src.grammars`, so
the normaliser supports every language the grammar layer does. Used by the
near-duplicate oracle as the precision (Stage 2) step on top of an embedding
shortlist -- see ``docs/dup-oracle.md``.
"""
from __future__ import annotations

import difflib
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
# Type-annotation subtrees -- pruned whole, so `min<T>(...)` reads like `min(...)`
# and TS generics don't drown the one operator that distinguishes siblings.
_TYPE_PRUNE = frozenset({
    "type_annotation", "type_arguments", "type_parameters", "predefined_type",
    "generic_type", "union_type", "object_type", "type_identifier",
    "type_alias_declaration", "index_type_query", "type",
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
    """Return the outermost function-like node, or ``root`` if none."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in _FUNC_TYPES:
            return node
        stack.extend(node.children)
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
        stack.extend(node.children)
    return names


def normalize(path: str, src: str) -> list[str]:
    """Return the normalised token stream for the first function in ``src``.

    Local variables -> ``V``; the function's own name is dropped; type
    annotations are pruned; free names (callees, properties) are kept as
    ``N:<name>``; numeric literals as ``NUM:<value>``; strings as ``STR``;
    operators / keywords as ``K:<sym>``.
    """
    src_bytes = src.encode("utf-8", errors="replace")
    fn = _first_function(_parse(path, src).root_node)
    bound = _bound_names(fn, src_bytes)
    own = fn.child_by_field_name("name")
    own_span = (own.start_byte, own.end_byte) if own is not None else None

    tokens: list[str] = []
    for leaf in _leaves(fn, _TYPE_PRUNE):
        if own_span is not None and (leaf.start_byte, leaf.end_byte) == own_span:
            continue  # drop the function's own name -- a rename is not a logic change
        ttype = leaf.type
        text = _text(leaf, src_bytes)
        if ttype in _IDENT_TYPES:
            tokens.append("V" if text in bound else "N:" + text)
        elif ttype in ("string", "template_string"):
            tokens.append("STR")
        elif ttype == "number":
            tokens.append("NUM:" + text)
        elif text in _PUNCT:
            continue
        else:
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


def logic_ratio(a_path: str, a_src: str, b_path: str, b_src: str) -> float:
    """Similarity in [0, 1] of two functions' logic-token streams.

    ~1.0 for a true renamed/rewritten duplicate; clearly lower for a same-shape
    sibling that differs in a real operator or callee.
    """
    a = logic_tokens(a_path, a_src)
    b = logic_tokens(b_path, b_src)
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()
