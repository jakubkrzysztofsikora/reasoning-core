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

Function embeddings are held in a `DupOracleIndex` (`src/dup_repo_index.py`).
**Current limitation:** the advisory hook builds that index on demand and caches
it in-process, but the hook runs as a fresh process per edit — so a large repo
pays a full rebuild each time. Wiring it onto `project_index`'s background build
+ touched-file refresh (or the sidecar) — so the per-edit cost is only "embed
the new function + a cosine query" — is the main follow-up before it's usable at
scale / enabled by default.

## Acceptance test (the anchor)

A pinned, vendored **date-fns** snapshot (`tests/fixtures/dup_oracle/`): over its
functions the pipeline must spotlight exactly the real duplicate —
`cleanEscapedString`, copy-pasted across `format` / `lightFormat` / `parse` —
and must **not** confirm sibling families (`min`/`max`, `addWeeks`/`subWeeks`,
`addDays`/`addHours`). This freezes the behaviour as a regression guard,
independent of upstream date-fns. All feature tests run in the offline
`-m "not live"` gate (frozen `.npy` vectors); the model only runs in the
fixture generator.

## Evaluation

`eval/validate_dup_embedder.py` is an offline embedder-fitness check (mirrors
`eval/validate_embedder.py`): over the frozen fixture the pinned embedder places
the real duplicate ~10σ closer than unrelated functions — the premise Stage 1
relies on.

A formal `oracle_eval`-style corpus (labelled duplicate / sibling / unrelated
pairs with precision/recall + a threshold sweep, to calibrate RECALL/CONFIRM
beyond a single fixture) was **considered and deliberately deferred** for this
opt-in/experimental PR — happy to add it following the repo's eval conventions
if that's wanted before merge.

## Modules

- `src/dup_index.py` — normaliser, logic-token diff, distinctiveness ranking,
  function extraction (pure; tree-sitter only).
- `src/dup_oracle.py` — the two-stage query (`find_near_duplicates`).
- `src/dup_repo_index.py` — builds the repo function+embedding index.
- `src/dup_embed.py` — L2-normalising wrapper over `ssm_backbone.embed`
  (the only torch-touching module).
- `src/hooks/pre_edit_dup_advisory.py` — the PreToolUse advisory.

## Enabling

Off by default. To turn it on, set `RC_DUP_ORACLE=1` and wire the hook as a
`PreToolUse` matcher for `Edit|Write|MultiEdit` in `.claude/settings.json`:

    python3 src/hooks/pre_edit_dup_advisory.py

It reads the tool payload on stdin and, when a duplicate is found, prints a
`hookSpecificOutput.additionalContext` blurb (exit 0, never blocks).
