# dup oracle persisted index

- Date: 2026-08-21 16:08
- Branch: dup-oracle-re-embeds-the-whole-repo-on-every-edit-no-persisted-index

## Tickets

- Resolves: https://app.notion.com/p/dup-oracle-re-embeds-the-whole-repo-on-every-edit-no-persisted-index-3c215f963d1381afa7b8d9ae82619a62
- Refs:

## Problem / Context

TB-94. The dup-oracle advisory hook (`RC_DUP_ORACLE=1`) rebuilds the entire
function+embedding index on **every** edit. The hook runs as a fresh process, so
its in-process cache (`_INDEX_CACHE` in `pre_edit_dup_advisory.py`) is always
empty, and `build_dup_index` re-embeds every function in the repo each time
(~19s/edit on RoutesWallet, 452 fns). That tax makes the oracle impractical to
leave on — which defeats a background advisory. `docs/dup-oracle.md` names index
persistence as the top follow-up before default-enable.

**Codebase facts that shape the fix:**
- `build_dup_index(repo_root, *, embed_fn, max_funcs)` (`src/dup_repo_index.py`)
  walks `_iter_repo_files(repo_root, _DUP_SRC_EXTS)`, extracts functions
  (tree-sitter, cheap), computes `logic_tokens` (cheap), and calls
  `embed_fn(fsrc)` per function — the **only** expensive step (torch encoder).
  `token_df` is derived cheaply from records.
- `embed_fn` is **injectable**; offline tests pass a deterministic stub. The real
  embedder is `unixcoder-base` (`RC_EMBEDDER`, set in `src/dup_embed.py`).
- Repo cache convention already in use: `RC_CACHE_DIR` override else
  `~/.cache/reasoning-core/` (`_patch_tracker._cache_dir`, `_prm_promotion`),
  repo keyed by `sha256(repo_root)[:16]` (`_mock_detector`).
- Offline test gate: `pytest -m "not live and not slow"`. Vectors are numpy
  float32, L2-normalised, shape `(n, d)`.

**The floor (out of scope, per the ticket).** Even with a persisted index, the
hook still embeds the **one new function** being written (the query, `embed_fn`
in `advise()`) — that's ~model-load time and needs a persistent process (sidecar
`/dup-check`), a heavier separate follow-up. This ticket removes the dominant
cost: re-embedding *all* repo functions on every edit.

**Dependency / branch note.** This branch is based on `feat/dup-oracle`, which is
**open PR #13** ("Advisory near-duplicate oracle (opt-in)", base `main`), not yet
merged. This persistence work is #13's named follow-up. PR-base decision is
flagged in Notes below (to agree before pushing).

## Plan

Add a self-contained, per-repo **disk cache** of the function index, keyed per
file by content hash. Reuse cached vectors for unchanged files; re-embed only
changed/new files. Discard the cache when the embedding contract changes; fall
back to a full build on any corrupt/unreadable cache, never crashing the edit.

**Shape (keeps existing API + tests green):**
1. Refactor the per-file work out of `build_dup_index` into a shared
   `_index_file(repo_root, rel, embed_fn) -> (records, vecs)` helper — one
   extraction code path (so the "systemic grammar error surfaces" contract is
   preserved). `build_dup_index` keeps its current no-persistence behaviour and
   tests, now built on the helper.
2. New `load_or_build_dup_index(repo_root, *, embed_fn=None, max_funcs=...,
   cache_dir=None, embedder_id=None)`: resolve cache path → load+validate cache →
   walk **all** files, reuse cached (records+vecs) on per-file hash match else
   `_index_file` → assemble the **full** in-memory index → **cap at assembly** →
   save cache atomically **only on success** → return. Prunes cache entries only
   for files **deleted from disk**.
3. **max_funcs is a bounded embedding ceiling over the stable sorted walk.**
   The walk is over the **sorted** `_iter_repo_files` order (stable); functions
   are embedded (or reused) in that order until `max_funcs` is reached, then the
   walk stops. So the **first build is always bounded** — a huge repo indexes its
   first N functions (partial coverage) instead of timing out and silently doing
   nothing. The cache stores exactly what it embeds. Over-cap entries are **not
   deleted**: cache entries are pruned only for files **deleted from disk**; a
   file pushed outside the cap this run keeps its cached vectors (carried forward)
   so a later boundary shift reuses them instead of re-embedding — deterministic,
   no churn. When the cap is hit, emit **one** stderr line
   (`dup-oracle: repo exceeds N functions, indexing first N — coverage partial`)
   so partial coverage is visible, not guessed.
4. Cache file: a **single atomic `.npz`** at
   `<cache_dir>/dup-index.<repo_hash>.npz` (`np.savez`, `allow_pickle=False`):
   `vectors` = `(M,d)` float32 matrix (binary — no base64), `manifest` = uint8
   JSON bytes holding **metadata only**: `{ meta: {format_version, embedder_id,
   dim}, files: { rel: {hash, functions:[{name,line,logic_tokens, row_start,
   n}]} } }`. One file ⇒ atomic `os.replace` with no vector/manifest skew;
   `np.load` is fast (~15 MB at 5000×768) vs a ~20 MB JSON parse+base64 decode.
   mkdir 0700.
5. **Save only on the success path.** The whole index is assembled in memory
   first; the single cache write happens last. If `_index_file` raises mid-build
   (systemic tree-sitter ABI error), the save is never reached → the previous
   `.npz` stays intact and the error **propagates** (mirrors `build_dup_index`;
   the hook's outer try fails open). A write-time `OSError` is swallowed (advisory
   must never block), returning the valid in-memory index.
6. **Invalidation rule (committed).** Any change to the embedding pipeline —
   model id, model weights, or `dup_embed` normalisation — MUST bump
   `CACHE_FORMAT_VERSION`. `meta` also carries `embedder_id`
   (default `RC_EMBEDDER`/`unixcoder-base`) + `dim` as automatic guards; a
   mismatch on **any** of the three discards the whole cache (full rebuild, fresh
   meta). Weights can't be auto-detected, so the mandatory version bump is the
   enforced contract — stated here, in `docs/dup-oracle.md`, and in a comment on
   the constant.
7. Hook: `_get_index` calls `load_or_build_dup_index` instead of
   `build_dup_index`. Kill-switch `RC_DUP_ORACLE_CACHE=0` forces no-persistence
   (mirrors `RC_PLAN_NOVELTY_CACHE`). Update `docs/dup-oracle.md` (limitation →
   implemented disk cache; sidecar still the future floor-remover).

## Behaviours (Given / When / Then)

- **B1 (AC1 — reuse unchanged).** *Given* a repo already indexed once, *When* the
  index is built again with no source file changed, *Then* zero repo functions
  are re-embedded (the cached vectors are reused). The one new query function the
  hook embeds is the acknowledged floor, not a repo re-embed.
- **B2 (AC2 — selective recompute).** *Given* exactly one source file changed
  since the last index, *When* the index is rebuilt, *Then* only that file's
  functions are re-embedded, the rest are reused, and the assembled index
  reflects the change (new/edited function present, deleted file's entries gone).
- **B3 (AC3 — contract-change invalidation).** *Given* the embedding contract
  changes (embedder id, `dim`, **or** a bumped `CACHE_FORMAT_VERSION` — the
  latter being the mandatory signal for any weights/normalisation change),
  *When* the index is rebuilt, *Then* the stale cache is discarded and everything
  re-embedded — no incompatible vectors reused.
- **B4 (AC4 — cache never crashes the edit + integrity).** Three cases:
  *(read)* a corrupt or unreadable `.npz` → full build, valid index, never
  raises. *(write I/O)* an `OSError` while saving the cache → swallowed, the
  valid in-memory index is still returned. *(mid-build raise)* `_index_file`
  raises partway (systemic ABI error) → **no truncated cache is written**, the
  previous `.npz` is left intact, and the error propagates for the hook to fail
  open on.

## Increments (test-first)

1. **Seam: `_index_file` helper.** test: `_index_file` returns
   `(list[FunctionRecord], list[vec])` for a source file, vectors row-aligned to
   records; existing `test_dup_repo_index.py` stays green (build_dup_index now
   delegates). → impl: extract the per-file extract+tokens+embed loop; rewire
   `build_dup_index` onto it. No behaviour change (guarded by existing suite).
2. **B1 — persist + reuse unchanged.** test (red): with a stub embedder that
   counts calls and a tmp `cache_dir`, `load_or_build_dup_index` twice over the
   same repo → 1st build embeds all N; 2nd build embeds **0**; both indexes equal
   (same records/embeddings/token_df). → impl: `.npz` load/save (binary vectors +
   uint8-JSON manifest, atomic `os.replace`), per-file hash reuse, assemble +
   persist on success.
3. **B2 — selective recompute + prune + bounded cap.** test (red): after a
   cached build, edit one file (add/rename a function) and delete another →
   rebuild embeds only the changed file's functions (counter == that file's fn
   count), the changed function appears in records, the deleted file's records
   are gone. Plus cap tests: with `max_funcs` below the repo total, first build
   embeds exactly `max_funcs` functions (bounded, not the total) and emits one
   `coverage partial` stderr line; a file pushed outside the cap keeps its cached
   vectors (no re-embed, not deleted). → impl: per-file hash invalidation, prune
   only disk-deleted files, bounded embedding over sorted walk, carry-forward of
   over-cap entries, stderr warning on cap-hit.
4. **B3 — contract-change invalidation.** test (red): build with
   `embedder_id="A"`, rebuild with `embedder_id="B"` → all N re-embedded, meta
   updated; same for a bumped `CACHE_FORMAT_VERSION` and a stored `dim` mismatch.
   → impl: meta compatibility gate that discards the whole cache on any mismatch;
   comment on the constant stating the mandatory-bump rule.
5. **B4 — never crash + cache integrity.** test (red): (a) cache file is garbage
   bytes → valid full build, no raise; (b) valid `.npz` with a manifest/shape
   mismatch → valid full build; (c) save raises `OSError` → swallowed, valid
   index still returned; (d) `_index_file` raises mid-build with a pre-existing
   cache present → error propagates AND the pre-existing `.npz` is byte-for-byte
   unchanged (no truncated write). → impl: guard load in try/except → empty
   cache; assemble fully before the single end-of-build atomic save; swallow
   write `OSError`; let build/embed errors propagate before any write.
6. **Wire hook + docs.** test: hook path (`_get_index` → `load_or_build_dup_index`
   with a tmp `cache_dir`) reuses the cache across two simulated hook runs; a
   `RC_DUP_ORACLE_CACHE=0` run does not persist; docs presence check. → impl:
   switch `_get_index`, add `RC_DUP_ORACLE_CACHE` kill-switch, update
   `docs/dup-oracle.md`.

## Notes / Out of scope / Risks

**PR-base decision to agree before pushing (why no draft PR yet):**
`feat/dup-oracle` is unmerged open PR #13. Options for this branch's PR:
- **(Recommended) Stacked PR** with base `feat/dup-oracle` → the PR diff shows
  only the persistence changes; GitHub auto-retargets to `main` when #13 merges.
- **Fold into PR #13** — commit onto `feat/dup-oracle` directly; one PR, but
  loses this ticket's own PR/branch identity.
- **Wait for #13 to merge**, then rebase this branch onto `main`.
`/ship` opens the PR once we pick; I'll hold the draft PR + Notion PR-link step
until then rather than push a misleading 25-commit diff against `main`.

**Out of scope:** the sidecar `/dup-check` persistent-process approach (removes
the model-load floor + the one-new-function query embed) — heavier separate
follow-up, already scoped in `docs/dup-oracle.md`. Detection quality (TB-88) and
the statusline list (TB-90) are siblings, untouched here.

**Risks:**
- `max_funcs` is a bounded embedding ceiling over sorted `_iter_repo_files`
  order (first build always terminates); over-cap entries are carried forward,
  never deleted, so cap-boundary shifts never expire cached vectors or re-embed
  (point 3 above). Partial coverage is surfaced on stderr.
- Concurrent hook processes racing on one cache file → atomic `os.replace` keeps
  writes safe; a lost update just costs a little recompute next edit (acceptable
  for an advisory). Noted, not locked.
- Home-dir writes during tests avoided by always passing a tmp `cache_dir` in
  unit tests; `build_dup_index` stays persistence-free so its tests don't touch
  disk.
- Embedder swap with an unchanged id but changed `dim` is caught by the `dim`
  field in meta; a weights/normalisation change with unchanged id **and** dim is
  caught only by the mandatory `CACHE_FORMAT_VERSION` bump (committed rule).

**Acknowledged v1 tradeoffs (not blocking):**
- **File-level hashing** re-embeds a whole file's functions when any one changes
  (function-level keying would need stable per-function identity across line
  shifts) — accepted for v1; a file rarely holds many in-scope functions.
- **No cache eviction** → stale per-repo `.npz` files accumulate in
  `~/.cache/reasoning-core`, and carry-forward lets one repo's cache grow past
  `max_funcs` over many boundary-shifting edits. Follow-up (LRU/size cap). The
  first-build *embedding* cost is already bounded by `max_funcs` (point 3).

**Observability + fail-fast (agreed):** the library fails **open** on cache
trouble (corrupt/incompatible → silent full rebuild), which is correct for an
advisory. Added a one-line stderr **breadcrumb** when an *existing* cache is
discarded (never on a clean first build) so a surprise re-embed isn't invisible.
The hook boundary stays fail-open **permanently** (a raising PreToolUse hook
would block every edit — contract, tested). **Follow-up ticket:**
`RC_DUP_ORACLE_STRICT=1` — opt-in strict mode that **raises** on cache
discard/corruption instead of rebuilding (mirrors `RC_ADAPTER_REQUIRED`), to be
flipped on in CI/dev once the cache is trusted.

**Feature-flag convention:** the repo uses **env-var flags** (`RC_*`,
default-off), no flag service. This feature: `RC_DUP_ORACLE=1` gates the whole
oracle (the "agreed working" switch); `RC_DUP_ORACLE_CACHE=0` disables the disk
cache; `RC_DUP_ORACLE_MAX_FUNCS` the ceiling; `RC_DUP_ORACLE_STRICT` (follow-up)
the fail-fast lever.
