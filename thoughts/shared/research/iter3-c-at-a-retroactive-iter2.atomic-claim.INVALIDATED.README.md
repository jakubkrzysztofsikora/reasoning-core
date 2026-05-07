# `iter3-c-at-a-retroactive-iter2.atomic-claim.INVALIDATED.jsonl` — Archive note

## What this file is

Partial output (2 of 48 cells) of a Phase 0.4 c_at_a_min retroactive run on iter-2 frozen v3, using the **atomic-claim decomposer rubric** that was committed at SHA 73eb55a in `iter3-prereg-decomposer-prompt.md`.

Two cells completed before the run was killed:

| cell | n_plan_claims | n_divergent | coverage | accuracy | c_at_a_min | duration_s |
|---|---|---|---|---|---|---|
| A/E1/run-01 | 16 | 22 | 0.000 | 1.0 (locked_pass_rate proxy) | 0.000 | 89.5 |
| A/P0/run-01 | 33 | 32 | 0.030 | 1.0 | 0.030 | 140.5 |

## Why it's invalidated

Phase 0.5 decomposer reliability test (committed at SHA 67005aa) FAILED both pre-registered thresholds:

- Mean Jaccard (Gemini-Vibe pairwise): 0.205 vs 0.6 floor
- Pearson on claim-count: 0.384 vs 0.7 floor

Per the pre-registered abort condition in `iter3-prereg-decomposer-prompt.md`:

> If Pearson r on claim-count < 0.7 across the 10-doc fixture, the C@Acc denominator is too parser-noisy to use. Switch to a coarser binary rubric: 'spec-claim attempted vs declared divergent' (one bit per claim, no count-of-claims dependence).

The atomic-claim values in this file (n_plan_claims 16 vs 33; n_divergent 22, 32) reflect the parser noise — they are not load-bearing measurements.

## Why preserved (not deleted)

Per round-5 reviewer consensus (engineer + tech reviewer):

- **Adversarial read on deletion**: "author deleted intermediate data that didn't fit the narrative"
- **Defender read on archival**: "author preserved invalidated intermediate data for audit"

The latter wins. Archival is cheap insurance.

## What this data CAN still be used for

1. **Timing benchmark** — the two `duration_s` values (89.5s, 140.5s) are real per-cell wall-clock for the atomic-claim decomposer at iter-2 PLAN.md sizes (16-33 claims). The iter-3 paper §10 (cost section) can cite this as the per-cell cost the binary fallback rubric is replacing.
2. **Parser-noise example** — the n_plan_claims=16 (E1) vs 33 (P0) divergence between cells is consistent with the Phase 0.5 finding that decomposer claim-counts vary substantially even within the same agent's output style.
3. **Audit trail** — proves the atomic-claim path was actually executed before fallback, not just theorized.

## What this data CANNOT be used for

- Computing iter-2 retroactive c_at_a_min (atomic version is invalidated)
- Validating the iter-3 inferential scope (lmer-MDE expansion claim depended on a reliable parser)
- Calibrating the iter-3 honesty threshold (separate calibration in `iter3-prereg-calibration-report.json` is the canonical source)

## Recovery / resumption protocol

The next session's Phase 0.4 retroactive re-run uses the **binary rubric** (per fallback commitment). It will produce a new file `iter3-c-at-a-retroactive-iter2.binary-rubric.jsonl` against all 48 cells. Both rubrics' values can be compared (Spearman ρ on the 2 overlap cells) as a sanity check, but the binary version is the canonical inferential metric.

## SHAs

- This README + JSONL committed in: TBD (this commit)
- Atomic-claim decomposer prompt SHA: `54d08110d7c99f1ee189f3a7927f4bb8e4275c8976290fc43cf990cf2e7412c6` (per iter3-prereg.json self_integrity)
- Phase 0.5 reliability report SHA: `fcea3d198822aedb6c2f18267a0ab991448f219df90ef5d972ba70b545de8a54`

## Related artifacts

- `iter3-decomposer-reliability-report.json` — Phase 0.5 raw report (Jaccard 0.205, Pearson 0.384, 10 fixtures)
- `iter3-prereg.json` — `phase_0_5_decomposer_reliability_FAILED` block + fallback commitment
