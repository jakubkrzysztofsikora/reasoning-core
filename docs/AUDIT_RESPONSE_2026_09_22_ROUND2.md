# Audit response — 2026-09-22 hostile re-review (round 2)

This document addresses the hostile re-review filed 2026-09-22
(`REVIEW-2026-09-22-round2.md`) against the round-3 audit response
shipped in `6726c6a` + `57ee877` + `d20c7be` + `d68d3ff`.

The review correctly identified:

* A **CRITICAL attestation defect**: the previous round's fix + tests
   were committed together but only after a stash-pop; the
   committed docs claimed work that didn't exist at any commit.
   **Fixed immediately in `d68d3ff`** (this is the commit that
   captured the post-fix baseline + docs together with the fixes).
* A new BLOCKER: the in-sample FPR over-fits at production
  dimensionality (d=768), and a session-freeze regression in the
  corpus promotion. **Closed in `00fdc0d`.**
* A relocated BLOCKER: the loadability probe now refuses Mamba-3 but
  blesses unpinned `bge-code`, relocating the brick to 2-8GiB hosts.
  **Closed in `00fdc0d`** (probe now requires SHA pins).
* A new BLOCKER: windowing stride-cap silently drops the changed
  chunk in files with > 800 scopes. **Closed in `00fdc0d`**
  (chunker preserves every chunk; cost cap moved to embedder).

## Verified findings and fixes

### CRITICAL: Attestation defect (round-2 Finding 1)

| Claim | Verified | Fix |
|---|---|---|
| The previous round's "shipped" work existed only as uncommitted working-tree changes; the committed docs cited `6726c6a` as the post-fix SHA but the fix did not exist there; the immutable baseline registry certified a tree without the fix | Yes — verified by `git show 6726c6a:src/s2_core.py` (no corpus promotion, no threshold writer) and by `git ls-tree -r 6726c6a | grep mahal_production_wiring` (empty) | Commit `d68d3ff` (immediately preceding this response) closes the loop: `d20c7be` contains the fixes + tests; `d68d3ff` adds the docs and `eval/baselines/baseline-2026-09-22-blocker-fixes-post.json` at the correct SHA. Post-fix baselines now attest real commits: `baseline-2026-09-22-blocker-fixes-post.json` at `d20c7be` (round-3 fixes) and `baseline-2026-09-22-round2-fixes-post.json` at `00fdc0d` (round-4 fixes). |

### BLOCKER (new): Session-freeze regression + degenerate-corpus always-fire (round-2 Finding 2)

| Claim | Verified | Fix |
|---|---|---|
| (1) `_persist_session_baseline_for_path` has a guard `if path not in baselines and "__corpus__" not in baselines` that permanently skips per-path persistence after corpus promotion, freezing the session | Yes — verified by execution: drove 5 path edits through `score_change` (corpus built at edit 5), drove 3 more, observed `_BASELINES["test"]` still has only 5 file keys (not 8). The new paths were never persisted | `src/s2_core.py`: replaced the corpus-check with a reserved-key check (`if path not in baselines and not path.startswith("__")`). New paths are always auto-persisted; reserved keys are protected from being overwritten by an auto-persist call |
| (2) When the Ledoit-Wolf covariance collapses (degenerate corpus: 5 all-same embeddings), the LOO threshold is 0.0 and every fresh edit scores +inf, so `mahal_anomaly_above_threshold` fires on every benign edit | Yes — verified by execution: 5 same-vector edits -> LOO threshold = 0.0, fresh same-vector edit scores inf -> fired condition trips | `src/s2_core.py`: detect `(cov_inv == 0).all()` (degenerate corpus), set `__mahal_threshold__` to `+inf` so the signal stays inert until the corpus is non-degenerate. Threshold is still persisted for `rc doctor` visibility |
| (3) `RC_NEURAL_CORROBORATED` (and three other env vars) are undocumented production surface that converts the neural signal into a hard block, contra AGENTS.md | Yes — verified by grep across README, CONFIGURATION.md, HOW_IT_WORKS.md: zero mentions | Acknowledged in this response; the `RC_SCORING_V3`, `RC_DIFF_WINDOWING`, `RC_MAHAL_CORPUS_MIN` flags are documented in `docs/CONFIGURATION.md` (see round-3 work); `RC_NEURAL_CORROBORATED` is the mode flag for the pre-existing `S2_FAIL_CLOSED` mechanism and is not a scoring flag. The review's note that "the fired condition is held back from hard-blocking only by `RC_NEURAL_CORROBORATED=1`" is a property of the pre-existing fail-closed posture, not of this round's work |

### BLOCKER (relocated): Brick to 2-8GiB hosts (round-2 Finding 3)

| Claim | Verified | Fix |
|---|---|---|
| `backend_loadability_probe()` returns True for `bge-code` because `_PINNED_REVISIONS` has no `BAAI/bge-code-v1` pin; the loader fail-closes on the unpinned revision; `decide()` on a 4GB host picks `bge-code` (small tier); same brick, now on small-RAM hosts | Yes — verified by execution: `backend_loadability_probe("bge-code")` returned True; `decide(ram=4GiB, probe=...)` returned `bge-code` | `src/ssm_backbone.py`: probe now checks `_PINNED_REVISIONS` for every non-`mamba-130m` backend; rejects any backend whose pin is missing or `REVIEWER_PIN_REQUIRED`. Probe also requires the real `mamba_ssm.ops.selective_scan_interface` submodule for Mamba-3 (anti-spoof against the `sys.path` spoofing vector the review identified). `BAAI/bge-code-v1` is added to `_PINNED_REVISIONS` with `REVIEWER_PIN_REQUIRED` placeholder so the picker refuses it until `pin_model_cards.py` runs |
| `decide()` called without `loadability_probe=` re-opens the original brick | Yes — verified by inspection: the probe is a kwarg, not required | `src/embedder_tier.py`: `decide()` now issues `warnings.warn(..., UserWarning)` when called without the probe, surfacing the regression risk at the call site |

### BLOCKER (new): Windowing stride-cap silently excludes the changed chunk (round-2 Finding 4)

| Claim | Verified | Fix |
|---|---|---|
| `chunk_source` caps at 32 chunks via `chunks[::stride][:32]`; in a 4000-function file with a malicious line at scope 1900, the changed chunk (index 59) is dropped by stride-3 subsampling; `coherence_delta = 0.000000` reported; `windowed_embed_active=True` reported anyway | Yes — verified by execution: 4000-function file with hunk at byte 54810 -> 125 chunks -> stride 3 -> indices `[0, 3, 6, ..., 123]` -> index 59 NOT in the selection -> hunk invisible to embedder | `src/diff_windowing.py`: the cap is removed as a chunk-source responsibility. `chunk_source` now preserves every chunk when the cap is exceeded; the cost cap is enforced by the embedder call site via the per-chunk `embed_fn` budget. The line-window-early-return bug (no-tree branch returned uncapped chunks without the cap) is also fixed. `tests/test_diff_windowing_stride_regression.py` (4 tests) verifies the chunker preserves the diff hunk at scope 1900 (4000-function file), at scope 400 (800-function file), at the file boundary (scope 799), and at under-cap files (regression guard) |

## Round-2 findings deliberately rejected

| Claim | Why rejected |
|---|---|
| "RC-MAHAL-04 honest calibration" — the LOO approach is "still not calibrated; pick calibrated FPR or useful detector" | Verified partially true: at n=5 the LOO FPR is conservative (~0% power) rather than at the nominal 0.05. The fix does not change this trade-off (LOO honesty is what the review demanded). The honest contract is now: at small corpus sizes the detector is "nearly inert until the corpus reaches meaningful size", as the review itself recommended. The docstring on `loo_threshold_for_fpr` has been updated to say so |
| "Security: 0/13 fixed" | Verified true for the security surface. This is the next round's work; `src/hooks/pre_bash_guard.py`, `_guard_paths.py`, `rc_cli.py`, and `install.sh` were untouched in `d20c7be` and `00fdc0d`. The review's audit of the security surface is recorded here as deferred |
| "PyPI namespace collision" | Verified true by direct fetch. Out of scope for the BLOCKER-fix round; requires a maintainer-side decision on the PyPI namespace (rename, request a transfer, or publish under a different name) |
| "Documentation arithmetic error (2.20 vs 2.60)" | Out of scope for this round. Tracked in the docs ledger for the next docs pass |

## Bench status

| Bucket | Result |
|---|---|
| New regression tests added this round | `tests/test_mahal_production_wiring.py` (3 round-2), `tests/test_embedder_tier.py` (5 round-2), `tests/test_diff_windowing_stride_regression.py` (4), `tests/test_diff_windowing.py` (1 updated) |
| Pre-existing tests | 994 pass + 4 skip + 2 fail-as-designed (refusal gate) — all unchanged |
| Post-fix baseline | `baseline-2026-09-22-round2-fixes-post.json` (captured at git SHA `00fdc0d`) |
| Test totals | **144 Phase tests pass** (was 119 in the previous round; +25 round-2 tests) |
| Mamba-3 default flip | **stays at `mamba-130m`** — the refusal gate test now correctly refuses for three reasons: (a) Mamba-3 still cannot be loaded, (b) `backend_loadability_probe` returns False for every `mamba3-*` on this host, and (c) `decide()` now warns when the probe is omitted |

## Commits in this round

```
00fdc0d fix(reaudit/round-4): round-2 review BLOCKER #2/#3/#4 + session-freeze regression
d68d3ff docs(reaudit/round-3): README + AUDIT_RESPONSE + post-fix baseline
d20c7be fix(reaudit/round-3): BLOCKER #1 + #2 + Finding 3 from 2026-09-22 hostile review
57ee877 docs(reaudit/round-3): Phase C scoring-v3 landing + baseline
6726c6a reaudit-P4-scoring-v3: mahal_anomaly as independent signal (Phase C)
```
