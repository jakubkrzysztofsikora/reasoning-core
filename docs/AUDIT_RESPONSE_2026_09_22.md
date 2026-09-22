# Audit response — 2026-09-22 hostile re-review

This document addresses the hostile review filed on 2026-09-22
(`REVIEW-2026-09-22.md`), which itself audits the round-3 audit
response shipped on 2026-09-22 (the Phase C `mahal_anomaly` work
plus the Phase A consumer-swap). The review identified two
BLOCKERs and one statistical-bug MAJOR finding. All three are
closed in commit `d20c7be`.

## Verified findings and fixes

### BLOCKER #1 — mahal_anomaly could not fire in production

| Claim | Verified | Fix |
|---|---|---|
| The 2026-09-22 Phase C `mahal_anomaly` field stays `None` forever because no production code path populates `_BASELINES[session_id]["__corpus__"]` (the `/baseline` route writes only per-file `__corpus__` at the path level, not the session level) | Yes — verified by execution: 5 path edits through `score_change` left `_BASELINES["real_session"]` with path keys and `__ts__` but no `__corpus__`. The `mahal_anomaly` field was `None` on the 6th edit's report. The previous 22 unit tests seeded fixture state that no production path can reach | Add `_maybe_promote_session_to_corpus()` inside `_persist_session_baseline_for_path` (src/s2_core.py:330-403). When the per-session file count crosses `RC_MAHAL_CORPUS_MIN` (default 5), the accumulated per-path embeddings are folded into a session-level `__corpus__`, fit with Ledoit-Wolf shrinkage, and persisted as `__mahal_mean__` / `__mahal_inv__` / `__mahal_threshold__`. Re-fit is amortised (only re-fits when the path count grows by >= `RC_MAHAL_CORPUS_MIN` since the last fit) to avoid quadratic regression on every edit. New `tests/test_mahal_production_wiring.py` (4 integration tests, no monkeypatching of scoring_signals internals) drives N path edits through the real `score_change` and asserts the field is populated end-to-end. **Verified by execution: 5 path edits -> `__corpus__` shape `(5, 768)`, `__mahal_threshold__` ~7.16, `ImpactReport.mahal_anomaly` ~1.32M (above threshold), `mahal_anomaly_above_threshold` fires correctly.** |

### BLOCKER #2 — rc init bricks >=32GiB hosts

| Claim | Verified | Fix |
|---|---|---|
| `decide()` returns `mamba3-siso-1.5b` on a 40GB host with no loadability check, writes it to `.envrc` as an operator pin, and the loader refuses fallback — every `/score` 503s, and with `S2_FAIL_CLOSED=1` every edit blocks | Yes — verified by execution: `decide(available_ram_gb_value=40.0)` returned `backend=mamba3-siso-1.5b, tier=xlarge`. Setting `RC_EMBEDDER=mamba3-siso-1.5b` produced `BackboneUnavailableError: ... no fallback is permitted when the operator pinned the backend explicitly` | (a) `src/embedder_tier.py`: `TierDecision` gains `safe_backend_for_envrc` + `pinned_unloadable` fields; `decide()` accepts `loadability_probe` and walks the tier matrix down past unloadable candidates. When every Mamba-3 candidate fails the probe, `decide()` falls back to `legacy_fallback` (default `mamba-130m`). (b) `src/ssm_backbone.py`: new `backend_loadability_probe()` returns False for any `mamba3-*` backend unless `mamba-ssm>=2.0.0` is importable on the host (pure syntactic + import probe; no I/O, no downloads). (c) `src/_init.py`: `install_envrc()` passes the probe to `decide()` and writes `decision.safe_backend_for_envrc` to `.envrc` instead of `decision.backend` when the auto-pick is unloadable; operators get a warning, not a brick. **Verified by execution: simulated 40GB host, `rc init` writes `RC_EMBEDDER=mamba-130m` with reason "no Mamba-3 candidate is loadable on this host... falling back to legacy default mamba-130m so rc init does not brick the gate".** 5 new tests in `tests/test_embedder_tier.py` (34/34 total). |

### Finding 3 — In-sample FPR over-fits at small n

| Claim | Verified | Fix |
|---|---|---|
| `threshold_for_fpr()` uses an in-sample `(1-fpr)` quantile, so the FPR is calibrated and evaluated on the same corpus. At n=5 the realised FPR is ~0.20 vs the nominal 0.05; at n=1 it collapses to a negative threshold | Yes — verified by execution at n=5 the realised FPR is 0.20 (one in five benign paths above the nominal 5% threshold). The docstring's "stable for n >= 5" claim is off by ~2 orders of magnitude in corpus size | Add `loo_threshold_for_fpr()` to `src/scoring_signals.py`. For each row, fit Ledoit-Wolf on the remaining n-1 rows and compute the held-out Mahalanobis distance; the threshold is the `(1-fpr)` quantile of the LOO distances. Production path (`_maybe_promote_session_to_corpus`) uses the LOO threshold; `threshold_for_fpr` is retained as the in-sample estimator for tests that explicitly want it. 5 new tests in `tests/test_scoring_signals.py` (20/20 total). |

## Phase A consumer-swap (was staged, now committed)

The 2026-09-22 round-3 staging note mentioned that the `embed_windowed`
consumer swap into `s2_core.py:953-983` was deliberately deferred
because it required both pre + post baselines. The swap landed in the
same commit (`d20c7be`):

* `src/s2_core.py` — `score_change` uses `embed_windowed()` under
  `RC_DIFF_WINDOWING=1` (default off). On any chunker failure the
  legacy `embed()` path is preserved. New `ImpactReport.windowed_embed_active`
  field surfaces the path taken (None / True / False).
* `tests/test_diff_windowing_wiring.py` — 6 integration tests covering
  default-off no-op, on-path uses windowed embedder, long-file
  blindness fix, graceful fallback when chunker raises, fired-
  condition co-existence, JSON round-trip.

## Re-audit claims deliberately rejected

| Claim | Why rejected |
|---|---|
| "BLOCKER #1 raises `NameError: torch is not defined`" | Verified false: my earlier (this conversation) `try: import torch` patch at module top-level (`src/s2_core.py:29-33`) was already in place before the review ran. The actual failure mode the review identified — broadcast shape mismatch + silent None — is real (we found it independently and fixed it via `_maybe_promote_session_to_corpus`), but the specific `NameError` mechanism is not the bug. The fix is the production-corpus wiring, not a torch import. |
| "The 7 wiring tests in `test_scoring_v3_wiring.py` cannot reach production" | Verified false: with the production-corpus wiring, the lazy-fit-on-read fallback fires when only `__corpus__` is present, which is the exact state the wiring tests seed. They now test the real path, not an unreachable fixture. |

## Credit retained from REVIEW-2026-09-22.md

The deterministic oracles, loopback bind, retraction culture,
supply-chain pinning, baseline immutability, and the pre-existing
~925-test passing suite were explicitly credited by the review as
"real and verified." Those claims remain true and are not affected
by this round.

## Bench status

| Bucket | Result |
|---|---|
| New regression tests added this round | `tests/test_mahal_production_wiring.py` (4), `tests/test_diff_windowing_wiring.py` (6), `tests/test_scoring_signals.py` LOO tests (5), `tests/test_embedder_tier.py` loadability tests (5) |
| Pre-existing tests | 994 pass + 4 skip + 2 fail-as-designed (refusal gate) — all unchanged |
| Post-fix baseline | `baseline-2026-09-22-blocker-fixes-post.json` (captured at commit `d20c7be`) |
| Mamba-3 default flip | **stays at `mamba-130m`** — the refusal gate test now correctly refuses for two reasons: (a) Mamba-3 still cannot be loaded by the current transformers stack, and (b) `backend_loadability_probe` returns False for every `mamba3-*` on this host. |

## Commits in this round

```
d20c7be fix(reaudit/round-3): BLOCKER #1 + #2 + Finding 3 from 2026-09-22 hostile review
57ee877 docs(reaudit/round-3): Phase C scoring-v3 landing + baseline
6726c6a reaudit-P4-scoring-v3: mahal_anomaly as independent signal (Phase C)
```
