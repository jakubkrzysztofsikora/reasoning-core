# Near-duplicate oracle (advisory)

## What it adds

Today the scorer compares an edit against the **same file** (`novelty` =
before/after drift). It has no cross-file awareness, so an agent can happily
re-implement a helper that already exists elsewhere under a different name.

This oracle adds that awareness: after the agent edits a file, it checks the
functions in it against the repo's other functions for a behaviourally-similar
one, records each duplicate as a finding, and reviews the open findings when the
session ends — so the agent reuses or extends instead of re-inventing. The
per-edit check never blocks; the session-end check blocks only on a duplicate
worth extracting (see **Findings and the Stop review**). Opt-in via
`RC_DUP_ORACLE=1`.

**Language scope.** The oracle indexes **Python, JavaScript and TypeScript**
(`.py`, `.js`, `.mjs`, `.cjs`, `.ts`, `.tsx`). Other tree-sitter-parseable
languages (e.g. **C#**, **SQL**) are deliberately **out of scope**: making them
work is not just a matter of walking their files — the function extractor
(`index._FUNC_TYPES`) and the normaliser (its param/local handling) would
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

Function embeddings are held in a `DupOracleIndex` (`src/duplicate_detection/repo_index.py`).

**Index persistence (disk cache).** The hook runs as a fresh process per edit, so
the index is **persisted to disk** per repo (`load_or_build_dup_index` in
`src/duplicate_detection/repo_index.py`): a single atomic `.npz` (binary vectors + a small JSON
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
  id, the model **weights**, or `embed`'s normalisation — because weights
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

## Findings and the Stop review

Scoring decides *whether* two functions are duplicates. What happens next is
decided here.

**After each edit** (`post_edit_duplicate_check.py`, PostToolUse, never blocks)
each confirmed duplicate is routed by where the match is:

| Match is in | Treated as | Message to the agent now? |
|---|---|---|
| the edited file itself | in the agent's hands | yes, once per finding |
| another file in the working diff | likely **move** | no |
| only untouched files | tracked **follow-up** | no — reviewed at Stop |

If git can't say what changed, nothing is treated as a move. Each finding gets a
verdict: **EXTRACT** when it has ≥ 3 sites, ≥ 12 logic tokens and every site is
test-covered (a test file names the function or its file's stem — a heuristic);
otherwise **NOTE**. Every finding is recorded in a per-repo ledger outside the
repo (`~/.cache/reasoning-core/dup-findings.<repo-hash>.jsonl`; statuses
`open` / `deferred` / `resolved`).

**At session end** (`stop_duplicate_check.py`, Stop):

1. Each open finding is re-checked deterministically (tree-sitter + logic-token
   ratio, no model). If the duplicate is gone — a finished move or extraction —
   the finding is resolved.
2. If an **EXTRACT** finding is still open, the stop is **blocked** and the
   finding listed; open NOTE findings ride along in the same message but never
   block on their own.
3. The agent clears a block by extracting, or with
   `rc dup-defer <id> --reason "..."` (reason required; a deferred finding is
   not reopened).
4. Blocking continues while `stop_hook_active` is set, capped at 3 blocks per
   finding (`review.MAX_BLOCKS`). Past the cap the stop goes through and an
   `escalated` audit event is written.
5. It blocks whenever the feature is on, unlike `stop_reconcile.py` which
   waits for `RC_MODE=copilot`. An explicit `RC_MODE=advise` turns blocking
   off; findings then go to stderr and the audit log only.

Both hooks fail open: an error never blocks, and leaves a stderr line plus a
`fail-open` audit event.

**Audit events** (shared audit log, one per hook run):

- `dup_check`: `found` / `clean` / `skipped` / `fail-open`, with `latency_ms`.
- `dup_stop`: `blocked` / `approved` / `escalated` / `advise` / `fail-open`.
- `dup_deferred`: written by `rc dup-defer`, with the finding id and reason.

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

All engine code lives in the `src/duplicate_detection/` package:

- `index.py` — normaliser, logic-token diff, distinctiveness ranking,
  function extraction (pure; tree-sitter only).
- `oracle.py` — the two-stage query (`find_near_duplicates`).
- `repo_index.py` — builds the repo function+embedding index and
  persists it to disk (`load_or_build_dup_index`; per-file content-hash cache).
- `embed.py` — L2-normalising wrapper over `ssm_backbone.embed`
  (the only torch-touching module).
- `findings.py` — per-repo findings ledger, so findings outlive the edit.
- `check.py` — the per-edit check: routing, verdict, recording
  (`check_edit`).
- `review.py` — the session-end review (`stop_decision`).
- `defer.py` — deferring a finding with a reason (backs `rc dup-defer`).

The hooks live with the other hooks:

- `src/hooks/post_edit_duplicate_check.py` — the per-edit check.
- `src/hooks/stop_duplicate_check.py` — the session-end review.

## Enabling

Off by default. To turn it on, set `RC_DUP_ORACLE=1` and wire both hooks in
`.claude/settings.json`: the per-edit check as a `PostToolUse` matcher for
`Edit|Write|MultiEdit`, and the review as a `Stop` hook:

    python3 src/hooks/post_edit_duplicate_check.py
    python3 src/hooks/stop_duplicate_check.py

The per-edit check reads the tool payload on stdin and, for a duplicate inside
the edited file, prints a `hookSpecificOutput.additionalContext` blurb (exit 0,
never blocks). The Stop hook prints `{"decision": "block", "reason": ...}` when
it blocks (exit 0).

The index is cached to disk automatically (see **Index persistence** above).
Relevant env: `RC_DUP_ORACLE_CACHE=0` disables the cache, `RC_DUP_ORACLE_MAX_FUNCS`
sets the embedding ceiling, `RC_CACHE_DIR` relocates the cache
(default `~/.cache/reasoning-core/dup-index.<repo-hash>.npz`).
