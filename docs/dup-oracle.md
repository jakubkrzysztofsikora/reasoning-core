# Near-duplicate oracle (advisory)

## What it adds

Today the scorer compares an edit against the **same file** (`novelty` =
before/after drift). It has no cross-file awareness, so an agent can happily
re-implement a helper that already exists elsewhere under a different name.

This oracle adds that awareness: when the agent is about to write a function,
it checks the repo's other functions for a behaviourally-similar one and, if
found, emits an advisory naming it — so the agent reuses or extends instead of
re-inventing. **Advisory only: it never blocks** (always exits 0). Opt-in via
`RC_DUP_ORACLE=1`.

**Language scope.** The oracle indexes **Python, JavaScript and TypeScript**
(`.py`, `.js`, `.mjs`, `.cjs`, `.ts`, `.tsx`). Other tree-sitter-parseable
languages (e.g. **C#**, **SQL**) are deliberately **out of scope**: making them
work is not just a matter of walking their files — the function extractor
(`dup_index._FUNC_TYPES`) and the normaliser (its param/local handling) would
each need language-specific entries, or renamed duplicates would silently fail
to collapse. Tracked as a follow-up; see the dup-oracle language-support issue.

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

**Index persistence (disk cache).** The hook runs as a fresh process per edit, so
the index is **persisted to disk** per repo (`load_or_build_dup_index` in
`src/dup_repo_index.py`): a single atomic `.npz` (binary vectors + a small JSON
manifest) keyed per file by content hash. On each edit, unchanged files reuse
their cached vectors and only changed/new files are embedded — so the advisory is
a cached lookup (seconds) rather than a whole-repo re-embed, which is what makes
it practical to leave enabled.

- **Bounded first build.** `max_funcs` (`RC_DUP_ORACLE_MAX_FUNCS`, default 5000)
  is an embedding ceiling over a stable sorted walk, so even a huge repo
  terminates — it indexes the first N functions and prints one
  `coverage partial` line to stderr. Over-cap entries are carried forward, never
  deleted; only files deleted from disk are pruned.
- **Invalidation.** The manifest records `{format_version, embedder_id, dim}`; a
  mismatch on any discards the whole cache and rebuilds. **Bump
  `CACHE_FORMAT_VERSION` on ANY change to the embedding pipeline** — the model
  id, the model **weights**, or `dup_embed`'s normalisation — because weights
  can't be fingerprinted offline; this manual bump is the contract that
  guarantees incompatible vectors are never reused.
- **Fail-open.** A corrupt/unreadable cache falls back to a full build and never
  crashes the edit; a mid-build systemic error (e.g. a tree-sitter ABI mismatch)
  propagates *without* writing a truncated cache, leaving the previous one
  intact. When an existing cache is discarded, one stderr breadcrumb is emitted.
- **Flags.** `RC_DUP_ORACLE_CACHE=0` disables persistence. A strict mode that
  *raises* instead of silently rebuilding on cache trouble
  (`RC_DUP_ORACLE_STRICT`, mirroring `RC_ADAPTER_REQUIRED`) is a planned
  follow-up, for policing the cache in CI once it's trusted.

**Remaining floor + the sidecar follow-up.** The disk cache removes the re-embed
cost but not the per-edit model load, and the hook still embeds the one **new**
function being written. Removing both needs a persistent process holding the
model in memory: the **sidecar** already does (it keeps embedding state across
requests via `/baseline`), so a `/dup-check` endpoint alongside it would drop the
per-edit cost to just "embed the new function + a cosine query". That is the next
follow-up before default-enable.

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
- `src/dup_repo_index.py` — builds the repo function+embedding index and
  persists it to disk (`load_or_build_dup_index`; per-file content-hash cache).
- `src/dup_embed.py` — L2-normalising wrapper over `ssm_backbone.embed`
  (the only torch-touching module).
- `src/hooks/pre_edit_dup_advisory.py` — the PreToolUse advisory.

## Enabling

Off by default. To turn it on, set `RC_DUP_ORACLE=1` and wire the hook as a
`PreToolUse` matcher for `Edit|Write|MultiEdit` in `.claude/settings.json`:

    python3 src/hooks/pre_edit_dup_advisory.py

It reads the tool payload on stdin and, when a duplicate is found, prints a
`hookSpecificOutput.additionalContext` blurb (exit 0, never blocks).

The index is cached to disk automatically (see **Index persistence** above).
Relevant env: `RC_DUP_ORACLE_CACHE=0` disables the cache, `RC_DUP_ORACLE_MAX_FUNCS`
sets the embedding ceiling, `RC_CACHE_DIR` relocates the cache
(default `~/.cache/reasoning-core/dup-index.<repo-hash>.npz`).
