# Near-duplicate oracle (advisory)

## What it adds

Today the scorer compares an edit against the **same file** (`novelty` =
before/after drift). It has no cross-file awareness, so an agent can happily
re-implement a helper that already exists elsewhere under a different name.

This oracle adds that awareness: when the agent is about to write a function,
it checks the **whole repo** for a behaviourally-similar one and, if found,
emits an advisory naming it — so the agent reuses or extends instead of
re-inventing. **Advisory only: it never blocks** (always exits 0). Opt-in via
`RC_DUP_ORACLE=1`.

## How it works

Two stages, so it stays fast and low-noise:

1. **Recall — cosine shortlist.** Embed each function (code-pretrained encoder)
   and shortlist repo functions with cosine ≥ 0.80. Cheap; catches everything;
   imprecise on its own (util code clusters near ~0.99).
2. **Precision — logic-token diff.** Normalise each candidate (canonicalise
   local variable names, strip language scaffolding such as type annotations,
   drop the function's own name), keep only meaning-bearing tokens (operators,
   callee/property names, literals), and compare. This separates a true
   renamed duplicate (~1.0) from a same-shape sibling like `min`/`max` or
   `addWeeks`/`subWeeks`, which raw cosine cannot.
3. **Distinctiveness ranking.** Rank hits by how *rare* their shared tokens are
   across the repo, so boilerplate (identical constructors, thin wrappers)
   sinks below genuine duplication. A ranking, not a hard filter.

Function embeddings live alongside the existing project index
(`src/project_index.py`), built in the background and refreshed on touched
files, so the per-edit cost is only: embed the new function + a cosine query.

## Acceptance test (the anchor)

A pinned, vendored **date-fns** snapshot (`tests/fixtures/dup_oracle/`): over its
functions the pipeline must spotlight exactly the real duplicate —
`cleanEscapedString`, copy-pasted across `format` / `lightFormat` / `parse` —
and must **not** confirm sibling families (`min`/`max`, `addWeeks`/`subWeeks`,
`addDays`/`addHours`). This freezes the behaviour as a regression guard,
independent of upstream date-fns.
