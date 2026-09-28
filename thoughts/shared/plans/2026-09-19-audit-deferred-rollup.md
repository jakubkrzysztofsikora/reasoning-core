---
date: 2026-09-21 (updated)
branch: audit-hostile/2026-09-19-reaudit-fixes
status: revised -- Mamba-3 candidates added; auto-sizing on rc init added
         as Phase A.5; uniXcoder demoted from primary recommendation
tags: [audit-deferred, rollup, embedder, windowing, scoring-v3,
      plan, pre-registered-evaluation]
related:
  - thoughts/shared/research/2026-09-19-audit-deferred-charter.md
  - thoughts/shared/research/2026-09-19-audit-deferred-embedder.md
  - thoughts/shared/research/2026-09-19-audit-deferred-windowing.md
  - thoughts/shared/research/2026-09-19-audit-deferred-scoring-v3.md
  - docs/AUDIT_RESPONSE_2026_09_19.md
  - AGENTS.md (baseline-capture + evaluation rules)
---

# Plan: Rollup of audit-deferred workstreams (2026-09-19)

## Purpose

Three research memos landed on 2026-09-19 covering the audit-deferred
items from the 2026-09-19 hostile review. This plan synthesises the
recommended approaches into a single implementation sequence,
captures the pre-reg baselines, and stages the work so each phase
has its own decision gate.

## Memos referenced

| Memo                                            | Workstream         | Recommendation (revised 2026-09-21)        |
|-------------------------------------------------|--------------------|---------------------------------------------|
| `2026-09-19-audit-deferred-embedder.md`         | Embedder swap      | **`mamba3-siso-893m` zero-shot if pre-reg passes; fall back to `unixcoder-base`. Mamba-3 collapses the embedder + windowing workstreams via its 16K context window.** |
| `2026-09-19-audit-deferred-windowing.md`        | Windowed embeddings | Still required for `dup_embed` per-file comparisons if Mamba-3 ships; collapsed to optional for the main scoring path |
| `2026-09-19-audit-deferred-scoring-v3.md`       | Scoring dedup      | Keep `coherence_delta`, add `mahal_anomaly` (Ledoit-Wolf shrunk), keep `ais` / `novelty` as deprecated shims |
| **NEW** `2026-09-21-audit-deferred-auto-size.md`| `rc init` auto-sizing | Pick the largest mamba3 variant that fits available RAM/disk without exceeding 80% working-set |

## Baselines captured (per AGENTS.md)

| Baseline ID                                       | Purpose                                    |
|---------------------------------------------------|--------------------------------------------|
| `baseline-2026-09-19-embedder-ablation-pre`       | Embedder swap pre-fix reference            |
| `baseline-2026-09-19-windowing-pre`               | Windowing pre-fix reference                |
| `baseline-2026-09-19-scoring-v3-pre`              | Scoring-v3 pre-fix reference               |
| `baseline-2026-09-19-post-audit-fixes` (existing) | The P1+P2 fixes from the previous branch   |

Verify with:

```
rc baseline show baseline-2026-09-19-embedder-ablation-pre
rc baseline show baseline-2026-09-19-windowing-pre
rc baseline show baseline-2026-09-19-scoring-v3-pre
```

## Implementation phases

The three workstreams are mutually compatible (the new embedder
+ the new windowing + the new scoring signal stack additively).
We ship them in three phases to keep each decision gate small.

### Phase A: Windowing (lowest risk, biggest audit-claim coverage)

**Why first:** the 512-token truncation blindness affects *both*
the current Mamba-130M and the proposed uniXcoder (both have 512
max_seq_len). Shipping windowing unblocks the embedder ablation
in Phase B.

**Work:**

1. New module `src/diff_windowing.py`:
   - `_chunk_by_ast_scope(tree, src) -> list[Chunk]`
   - `_fallback_line_window(text, max_tokens=512, stride=64) -> list[str]`
   - `_diff_weights(chunks_before, chunks_after, diff_hunks) -> list[float]`
   - `embed_windowed(text, embedder, lang, diff_hunks) -> np.ndarray`
2. New helper in `src/ssm_backbone.py`:
   - `embed_windowed(text: str, lang: str, diff_hunks: Optional[str]) -> np.ndarray`
3. Wire into `src/s2_core.py:953-983`:
   - Replace `embed(before_tokens)` / `embed(after_tokens)` with
     `embed_windowed(before_src, lang, diff_hunks)` /
     `embed_windowed(after_src, lang, diff_hunks)`.
   - Diff hunks come from `difflib.unified_diff` (no new parser).
4. Per-language scope tables (Python, JS, TS, C#). SQL stays on the
   existing path with an explicit docs note (out of scope per the
   memo).
5. Tests:
   - `tests/test_diff_windowing.py` -- unit tests for chunker
   - `tests/test_windowed_long_file.py` -- long-file blindness fix
   - `tests/test_windowed_diffs_drowning.py` -- 5-token-in-300 diff drowning fix
   - Update `tests/test_s2_core.py` to verify back-compat
6. Baseline: capture `baseline-2026-09-19-windowing-post`.

**Pre-reg gate from the windowing memo:**

- Long-file blindness rate >= 80% on 100-sample corpus
- Diff-drowning >= 50% on 5-token-in-300-token set
- ROC-AUC >= 0.70 on `grounding_pairs_v3.jsonl`
- P95 trip rate parity on benign edits
- Per-edit latency p95 <= 5.0s
- Determinism L2 <= 1e-6 across 10 re-runs

### Phase A.5: rc init auto-sizing (2026-09-21 addition)

**Why between A and B:** the auto-sizer needs to know what the candidate
set looks like *before* the swap ships, so that operators get a sane
default regardless of which Phase B outcome wins.

**Work:**

1. New module `src/embedder_tier.py`:
   - `detect_tier(available_ram_gb: float, available_disk_gb: float) -> str`
   - `pick_backend(tier: str) -> str` -- maps tier -> RC_EMBEDDER value
   - `estimate_working_set_gb(backend: str) -> float` -- reads the
     model card for the chosen backend and applies the SSM working-set
     multiplier (2.5x checkpoint size on disk).
   - `fits(backend: str, available_ram_gb: float) -> bool` -- checks
     working-set <= 0.8 * available RAM.
2. Wire into `src/rc_cli.py:init`:
   - At `rc init` time, run `detect_tier()` + `pick_backend()` and
     emit `RC_EMBEDDER=<chosen>` to `.envrc` if the operator didn't
     pin a backend explicitly.
   - Surface a `S2_BACKBONE_LOAD` warning if the chosen backend's
     estimated working-set exceeds 80% of available RAM (refuse to
     start in that case unless operator confirms via
     `RC_ALLOW_OVERSIZED_BACKBONE=1`).
3. Persist the chosen backend to `.reasoning-core/install.manifest`
   so `rc upgrade` and `rc doctor` re-verify on subsequent runs.
4. Add a tier table to the immutable baseline manifest as
   `embedder_tier` and `embedder_backend`.
5. New tests:
   - `tests/test_embedder_tier.py` -- unit tests for
     detect/pick/estimate/fits across a grid of (ram, disk, backend).
   - `tests/test_rc_init_embedder.py` -- end-to-end test of
     `rc init` picking the right backend based on platform
     detection.
6. Capture `baseline-2026-09-21-auto-size-pre` and (after Phase B)
   `baseline-2026-09-21-auto-size-post`.

**Pre-reg gate:**

1. Picks the same backend as a hand-tuned operator in >=9/10 hosts
   (calibration against a fleet of 20 known hosts).
2. Refuses to load a variant whose working-set estimate exceeds
   80% of available RAM.
3. Tier detection is portable across Linux (`/proc/meminfo`),
   macOS (`vm_stat` + `sysctl hw.memsize`), and Windows
   (`psutil.virtual_memory().available`).

### Phase B: Embedder swap

**Why second:** depends on Phase A (windowing) + Phase A.5
(auto-sizing) being in place.

**Work:**

1. **Model-card verification (pre-flight).** Before any code change,
   run `eval/pin_model_cards.py` to record the model card metadata
   (hidden_size, vocab_size, exact max_seq_len, license file) for
   each candidate into a pinned manifest. Refuses to proceed if any
   card differs materially from the paper claims (e.g. claimed 16K
   context but actual card says 8K).
2. Run the pre-reg eval from the embedder memo across all
   candidates (`mamba-130m`, `unixcoder-base`, `bge-code`,
   `mamba3-siso-893m`, `mamba3-siso-1.5b` if host has RAM).
3. Update `src/ssm_backbone.py:_DEFAULT_BACKEND_NAME` from
   `"mamba-130m"` to the pre-reg winner.
2. Pin unixcoder-base in `_PINNED_REVISIONS` (already done; verify
   the SHA at `src/ssm_backbone.py:302-310`).
3. Update `eval/baselines/baseline-2026-09-19-post-audit-fixes.json`
   `embedder.backend` to `"unixcoder-base"` and capture a new
   `baseline-2026-09-19-embedder-ablation-post`.
4. New tests:
   - `tests/test_embedder_swap_default.py` -- verifies default
     backend
   - `tests/test_embedder_anisotropy.py` -- intra-code anisotropy
     reduction
   - `tests/test_embedder_regression_rocauc.py` -- ROC-AUC on the
     calibration corpus
5. Run the pre-reg eval suite from the embedder memo. If
   `unixcoder-base` zero-shot passes, ship it. If the fine-tune
   (`unixcoder-base-ft`) passes, commit it as
   `src/ssm_backbone_unixcoder_ft.py` with a default-on toggle
   `RC_EMBEDDER_FT=1`. Document the training recipe in
   `docs/EMBEDDER_FT_RECIPE.md`.
6. Update `README.md` "Audit & hardening" section, whitepaper, and
   `docs/AUDIT_RESPONSE_2026_09_19.md` to reflect the new default.

**Pre-reg gate from the embedder memo:**

- ROC-AUC >= 0.70 on test split; delta >= 0.05 vs mamba-130m
- Anisotropy reduction >= 0.04 absolute
- P95 trip-rate parity on benign corpus
- Per-edit latency median delta <= +25%
- Falsifiability control (`random-mamba`) AUC <= 0.55

### Phase C: Scoring-v3 (mahal_anomaly)

**Why third:** independent of A and B but adds new signal paths.
Wait until A+B are stable so we can measure attribution cleanly.

**Work:**

1. New module `src/scoring_signals.py`:
   - `mahalanobis_anomaly(emb_after, mu, sigma_inv, scale) -> float`
   - `knn_density(emb_after, benign_bank, k) -> float`
   - `regression_head_score(emb_before, emb_after, risk_vector, lr) -> float`
2. Calibration step `eval/recalibrate_v3.py`:
   - Loads `eval/calibrated/labels.jsonl` + mined corpus.
   - Computes per-kind benign mu / Sigma with Ledoit-Wolf shrinkage.
   - Sweeps thresholds per file_kind.
   - Emits `_KIND_THRESHOLDS_V3.json` consumed by `s2_core.py`.
3. Wire into `src/s2_core.py`:
   - Add `mahal_anomaly`, `knn_density`, `regression_head_prob` to
     `ImpactReport`.
   - Update `_KIND_THRESHOLDS` to read from the v3 manifest.
   - Keep `ais` / `novelty` as deprecated shims (no consumers
     break).
   - Add a new fired-condition: `mahal_anomaly_above_threshold`.
4. New tests:
   - `tests/test_mahalanobis_signal.py` -- unit tests for the
     Ledoit-Wolf fit and the distance computation.
   - `tests/test_scoring_v3_rocauc.py` -- ROC-AUC vs CD baseline.
   - `tests/test_scoring_v3_determinism.py` -- determinism across
     re-runs.
5. Capture `baseline-2026-09-19-scoring-v3-post`.

**Pre-reg gate from the scoring-v3 memo:**

- ROC-AUC >= 0.70; delta >= 0.05 vs CD baseline
- Mean Youden's J across file_kinds >= 0.45
- Calibration slope in [0.85, 1.15]
- P95 trip-rate parity
- `random-mamba` AUC <= 0.55 on the new signal
- Determinism L2 <= 1e-6

### Phase D: Docs and baselines consolidation

**After all three phases pass their pre-reg gates.**

1. Update `docs/AUDIT_RESPONSE_2026_09_19.md` with:
   - New section "Resolved deferred items (Phases A-C)"
   - Updated baseline manifest table with the three new
     `*-post` baselines
2. Update `README.md`:
   - "Audit & hardening" section gains the three new resolutions
   - "Embedder" section updates default to `unixcoder-base`
   - "Scoring" section adds `mahal_anomaly`
3. Update `docs/whitepaper/sections/scoring.tex`:
   - Section 4.4 keeps the algebraic-redundancy note
   - New section 4.6: "Independent anomaly signal (mahal_anomaly)"
4. Update `docs/whitepaper/sections/embedder.tex`:
   - Note the embedder swap and windowing strategy
5. Update `pyproject.toml` version bump if applicable (audit
   fixes shipped v0.2.0; this could be v0.3.0).

## Decision-gate summary

Each phase ends with:

1. Run the pre-reg evaluation.
2. Capture the `*-post` baseline.
3. Run the full pytest suite (107+ tests, 1 unrelated skip).
4. If the gate fails, revert that phase and re-open the memo.

```
rc baseline capture --id <id>
rc baseline compare baseline-2026-09-19-<phase>-pre \\
                     baseline-2026-09-19-<phase>-post
python -m pytest tests/test_<phase>.py
```

## Failure rollback

If any pre-reg gate fails:

1. Revert that phase's commits.
2. Keep the memo as the "considered but rejected" record.
3. Add a `STATUS: deferred` note in
   `docs/AUDIT_RESPONSE_2026_09_19.md`.
4. Re-open the workstream with a new memo.

## Risks

1. **Embedder fine-tune contamination.** If the calibration corpus
   leaks into training, the ROC-AUC is meaningless. Mitigation:
   split by `git_sha`, not by row.
2. **Windowing per-language scope drift.** Each grammar has its own
   scope detector. We ship four major languages; SQL stays on the
   existing path with a docs note.
3. **Mahalanobis singularity.** 768-dim covariance with ~131 samples
   is rank-deficient. Ledoit-Wolf shrinkage handles this.
4. **Calibration corpus size.** 30-50 pairs per kind is the
   pre-existing floor; we don't increase this.

## Out of scope

- Mamba-130M -> a 7B code model (codestral-mamba) -- separate
  workstream, requires GPU.
- AST-based deterministic fingerprints as a full replacement for
  embeddings -- separate workstream, not yet validated.
- Long-context 8K windowing -- now subsumed by the Mamba-3 16K
  context if Phase B picks Mamba-3; otherwise tracked under the
  windowing memo as before.
- **Mamba-3 bidirectional-scan kernel availability on macOS ARM.**
  The paper claims CUDA/Triton kernel support; macOS CPU may fall
  back to slow Python. If `mamba-ssm>=2.0.0` doesn't ship a
  CPU fast-path on macOS, we ship Mamba-3 as Linux/GPU-only and
  keep unixcoder-base as the macOS default until the upstream
  kernels land.
- **Auto-sizing on hosts without `psutil`.** Phase A.5 assumes
  `psutil.virtual_memory().available` is callable. If a host lacks
  `psutil`, fall back to `/proc/meminfo` / `vm_stat` / `sysctl`;
  the unit-test grid covers all three paths.


## Open questions for the user

1. **GPU budget for the fine-tune.** The embedder memo notes this is
   needed for ~30 minutes on a single A100. If unavailable, the
   fine-tune is dropped (zero-shot still ships).
2. **Calibration corpus refresh.** Should we re-mine
   `eval/calibrated/labels.jsonl` before Phase C? (Default: yes;
   re-run on this branch's history.)
3. **Docs scope.** Should we add a new whitepaper section
   (`4.7 Windowed embeddings`) or fold it into `4.2 Risk vector`?
   (Default: separate section.)
4. **Mamba-3 fast-path availability.** Has the user verified
   `mamba-ssm>=2.0.0` kernels compile and load on macOS ARM? If
   not, Phase A.5 needs to add a "macOS ARM CPU tier" branch that
   refuses to auto-pick Mamba-3 and falls back to `unixcoder-base`.
   (Default assumption: untested; will surface a clear boot warning
   and refuse to start if the fast path is unavailable.)
5. **Auto-sizer calibration fleet.** Do we have access to 20 hosts
   with known available-RAM values at install time for the
   pre-reg acceptance test? (Default: no; we'll synthesise a
   test grid of (ram, disk) tuples that covers the tier boundaries
   instead.)
6. **rc init UX.** Should the auto-sizer surface its pick
   interactively (`rc init` asks "I detected 16 GB free RAM;
   recommend mamba3-siso-893m. Use this? [Y/n]") or silently
   write to `.envrc`? (Default: interactive, with a `--yes` flag
   for scripted installs.)

