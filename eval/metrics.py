"""Code-quality drift metrics consumed by ``eval/aggregate.py``.

Public functions:

    ast_edit_distance(before_src, after_src, language="python") -> int
    cyclomatic_delta(before_src, after_src, language="python")  -> float
    fan_in_delta(before_src, after_src, language="python")      -> int
    fan_out_delta(before_src, after_src, language="python")     -> int

Implementation notes:

* AST edit distance uses ``ast.dump`` line-tokenisation + difflib for
  Python (Zhang-Shasha-flavoured upper bound). For non-Python or when
  ``ast.parse`` fails (partial files, syntax errors), it falls back to a
  symmetric line-set distance over the raw source -- still meaningful and
  always finite.
* Cyclomatic complexity reuses the branch-node count from
  ``src.s2_core._BRANCH_NODE_TYPES`` plus the +1 base per function. This
  is cheaper than depending on ``radon`` and matches what the sidecar
  already reports in the risk vector.
* Fan-in / fan-out reuse ``src.s2_core.build_call_graph``. Delta is signed
  (after - before).

All four functions are total: degenerate inputs return 0 / 0.0 instead of
raising. The aggregator depends on this so it can compute means without
guarding every call.
"""

from __future__ import annotations

import ast
import difflib
import logging
import os
import sys
from typing import Iterable

# Import-shape: the eval package lives outside the src package so we add
# the repo root to sys.path on demand. No-op in installed deployments where
# ``src`` is already importable.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AST edit distance
# ---------------------------------------------------------------------------

def _ast_dump_lines(src: str) -> list[str]:
    """Return the indented lines of ``ast.dump(parse(src), indent=2)``.

    Empty list on parse failure.
    """
    try:
        tree = ast.parse(src or "")
    except SyntaxError:
        return []
    try:
        dumped = ast.dump(tree, indent=2, annotate_fields=False)
    except TypeError:
        # Older Python: no indent kw. Fall back to default.
        dumped = ast.dump(tree, annotate_fields=False)
        # Inject newlines after parens for a coarse line view.
        dumped = dumped.replace("), ", "),\n").replace("[", "[\n").replace("]", "\n]")
    return [ln for ln in dumped.splitlines() if ln.strip()]


def _line_set_distance(before: str, after: str) -> int:
    """Symmetric difference over the line sets. Always finite."""
    set_b = set((before or "").splitlines())
    set_a = set((after or "").splitlines())
    return len(set_b.symmetric_difference(set_a))


def ast_edit_distance(before_src: str, after_src: str, language: str = "python") -> int:
    """Approximate AST edit distance.

    Returns the count of inserted + deleted nodes (lines of ``ast.dump``)
    between the two sources. Falls back to a line-set symmetric difference
    when ``ast.parse`` cannot handle the input (non-Python, partial file,
    syntax error). The fallback satisfies the EVAL_DESIGN.md "fall back to
    line-diff distance if AST parse fails" clause.
    """
    if language == "python":
        before_lines = _ast_dump_lines(before_src or "")
        after_lines = _ast_dump_lines(after_src or "")
        if before_lines or after_lines:
            sm = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
            inserts = 0
            deletes = 0
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "insert":
                    inserts += (j2 - j1)
                elif tag == "delete":
                    deletes += (i2 - i1)
                elif tag == "replace":
                    deletes += (i2 - i1)
                    inserts += (j2 - j1)
            return int(inserts + deletes)
    return int(_line_set_distance(before_src or "", after_src or ""))


# ---------------------------------------------------------------------------
# Cyclomatic complexity delta
# ---------------------------------------------------------------------------

def _branch_count_via_tree_sitter(src: str, language: str) -> int | None:
    """Use the s2_core branch counter when the grammar is available."""
    try:
        from src import s2_core  # type: ignore
    except Exception:
        return None
    try:
        parsed = s2_core.parse_source("a." + _ext_for(language), src or "")
    except Exception:
        return None
    if parsed is None or parsed.tree is None:
        return None
    try:
        return int(s2_core._count_branches(parsed.tree.root_node))
    except Exception:
        return None


def _branch_count_via_ast(src: str) -> int:
    """Count McCabe-style branches in a Python source via stdlib ``ast``."""
    try:
        tree = ast.parse(src or "")
    except SyntaxError:
        return 0
    fn_count = 0
    branches = 0
    branch_types: tuple[type, ...] = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.Try,
        ast.ExceptHandler,
        ast.With,
        ast.AsyncWith,
        ast.IfExp,
        ast.BoolOp,
        ast.Match,
    )
    fn_types = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if isinstance(node, fn_types):
            fn_count += 1
        elif isinstance(node, branch_types):
            branches += 1
    # McCabe: 1 base + branches per function.
    return fn_count + branches


def _ext_for(language: str) -> str:
    return {
        "python": "py",
        "javascript": "js",
        "typescript": "ts",
        "tsx": "tsx",
        "csharp": "cs",
        "sql": "sql",
    }.get(language, "py")


def cyclomatic_delta(before_src: str, after_src: str, language: str = "python") -> float:
    """Signed cyclomatic-complexity delta (``after - before``)."""
    if language == "python":
        c_before = _branch_count_via_ast(before_src or "")
        c_after = _branch_count_via_ast(after_src or "")
        return float(c_after - c_before)

    b = _branch_count_via_tree_sitter(before_src or "", language)
    a = _branch_count_via_tree_sitter(after_src or "", language)
    if b is None or a is None:
        return 0.0
    return float(a - b)


# ---------------------------------------------------------------------------
# Fan-in / fan-out delta
# ---------------------------------------------------------------------------

def _call_graph(src: str, language: str) -> dict[str, set[str]]:
    try:
        from src import s2_core  # type: ignore
    except Exception:
        return {}
    try:
        parsed = s2_core.parse_source("a." + _ext_for(language), src or "")
    except Exception:
        return {}
    if parsed is None or parsed.tree is None:
        return {}
    try:
        return s2_core.build_call_graph(parsed.tree, src or "", parsed.language)
    except Exception:
        return {}


def _max_fan_in(graph: dict[str, set[str]]) -> int:
    in_counts: dict[str, int] = {n: 0 for n in graph}
    for callers in graph.values():
        for callee in callers:
            if callee in in_counts:
                in_counts[callee] = in_counts.get(callee, 0) + 1
    return max(in_counts.values(), default=0)


def _max_fan_out(graph: dict[str, set[str]]) -> int:
    return max((len(v) for v in graph.values()), default=0)


def fan_in_delta(before_src: str, after_src: str, language: str = "python") -> int:
    g_before = _call_graph(before_src or "", language)
    g_after = _call_graph(after_src or "", language)
    return int(_max_fan_in(g_after) - _max_fan_in(g_before))


def fan_out_delta(before_src: str, after_src: str, language: str = "python") -> int:
    g_before = _call_graph(before_src or "", language)
    g_after = _call_graph(after_src or "", language)
    return int(_max_fan_out(g_after) - _max_fan_out(g_before))


__all__ = [
    "ast_edit_distance",
    "cyclomatic_delta",
    "fan_in_delta",
    "fan_out_delta",
]


# ---------------------------------------------------------------------------
# CLI entrypoint -- supports the legacy `eval/scripts/ast_edit_distance.py`
# call shape from RC-210 acceptance criteria.
# ---------------------------------------------------------------------------

def _cli_ast_edit_distance(argv: Iterable[str]) -> int:
    import json
    args = list(argv)
    if len(args) != 2:
        print("usage: ast_edit_distance.py <before_path> <after_path>", file=sys.stderr)
        return 2
    before = open(args[0], encoding="utf-8").read() if args[0] != "-" else sys.stdin.read()
    after = open(args[1], encoding="utf-8").read()
    print(json.dumps({"ast_edit_distance": ast_edit_distance(before, after)}))
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised as helper script
    sys.exit(_cli_ast_edit_distance(sys.argv[1:]))
