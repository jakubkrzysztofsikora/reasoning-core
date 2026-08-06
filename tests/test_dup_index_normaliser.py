"""Unit tests for the near-duplicate logic-token normaliser (src/dup_index.py).

Pure / fast: tree-sitter only, no model. These pin the Stage-2 (precision)
behaviour -- a renamed or rewritten duplicate collapses to the same logic
stream, while a same-shape sibling that differs by one real operator/callee
does not.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.dup_index import logic_ratio, logic_tokens, normalize  # noqa: E402

# Confirm bar the oracle uses; kept in sync with the pipeline default.
CONFIRM = 0.97

# --- Python renamed copy (same behaviour, different name + var) --------------
TO_SLUG_PY = 'def to_slug(s):\n    import re\n    return re.sub(r"[^a-z0-9]+","-",s.lower()).strip("-")'
SLUGIFY_PY = 'def slugify(value):\n    import re\n    return re.sub(r"[^a-z0-9]+","-",value.lower()).strip("-")'

# --- TS renamed copy, one with type annotations --------------------------------
TO_SLUG_TS = 'function toSlug(s: string): string {\n  return s.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-+|-+$/g,"");\n}'
SLUGIFY_TS = 'function slugify(value) {\n  return value.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-+|-+$/g,"");\n}'

# --- Same-shape siblings that differ by ONE operator / callee -----------------
MIN_TS = 'function min(xs) {\n  let r;\n  for (const d of xs) { if (r === undefined || d < r) r = d; }\n  return r;\n}'
MAX_TS = 'function max(xs) {\n  let r;\n  for (const d of xs) { if (r === undefined || d > r) r = d; }\n  return r;\n}'
ADD_WEEKS_TS = 'function addWeeks(date, amount) {\n  return addDays(date, amount * 7);\n}'
SUB_WEEKS_TS = 'function subWeeks(date, amount) {\n  return addWeeks(date, -amount);\n}'


def test_renamed_python_copy_is_a_duplicate():
    assert logic_ratio("a.py", TO_SLUG_PY, "b.py", SLUGIFY_PY) >= CONFIRM


def test_renamed_ts_copy_is_a_duplicate_despite_type_annotations():
    # toSlug is annotated (: string), slugify is not -- stripping annotations +
    # the own-name must make them identical logic.
    assert logic_ratio("a.ts", TO_SLUG_TS, "b.ts", SLUGIFY_TS) >= CONFIRM


def test_min_vs_max_is_not_a_duplicate():
    # Differ only by `<` vs `>` -- the classic case raw similarity misses.
    assert logic_ratio("a.ts", MIN_TS, "b.ts", MAX_TS) < CONFIRM


def test_addweeks_vs_subweeks_is_not_a_duplicate():
    assert logic_ratio("a.ts", ADD_WEEKS_TS, "b.ts", SUB_WEEKS_TS) < CONFIRM


def test_own_name_is_dropped():
    # Identical bodies, different names -> the name must not appear in tokens.
    a = normalize("a.ts", TO_SLUG_TS)
    assert "N:toSlug" not in a and "N:slugify" not in a


def test_local_variables_are_canonicalised():
    # `s` vs `value` collapse to the bound-var placeholder V, not distinct names.
    toks = normalize("a.py", SLUGIFY_PY)
    assert "N:value" not in toks and "V" in toks


def test_type_annotations_are_pruned():
    # The `string` type name must not survive into the logic tokens.
    assert not any("string" in t for t in logic_tokens("a.ts", TO_SLUG_TS))


def test_operators_survive_as_logic_tokens():
    # min keeps its comparison operator -- that's the discriminating signal.
    assert "K:<" in logic_tokens("a.ts", MIN_TS)
