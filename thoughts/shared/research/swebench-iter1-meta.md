# swebench-iter1 — Meta + Cross-References

**Created**: 2026-05-08
**Author**: Jakub Sikora
**Status**: active research line

## What is "swebench-iter1"?

A new evaluation track at Circit, parallel to (not continuation of) the `claude-iter-N` line.

- `claude-iter-{1,2,3}`: Internal Circit-task A/B comparison using Claude Code. Iter-3 shipped 2026-05-08 (whitepaper at `thoughts/shared/research/2026-05-08-iter3-eval-whitepaper.md`). Setup B (reasoning-core sidecar) won.
- `swebench-iter1`: External-validity supplement using **gemini-2.5-pro via google-genai SDK** on **SWE-bench Verified Frontier+MultiFile+Holdout subset (165 instances)**. Different model family, different task domain, same A vs B research question.

## Why a separate numbering line?

- `claude-iter-4` is a different design (continued internal-task evolution; iter-3 paper §7 lays out the candidate scope).
- swebench-iter1 is **not** a successor to claude-iter-3 — it's a parallel-track external-validity check.
- Numbering them separately preserves trajectory clarity.

## Two deliverables

| | D1 | D2 |
|---|---|---|
| Scope | Setup A (vanilla SDK) baseline | Setup A vs Setup B (RC-gemini) replication |
| Cells | 495 | 990 |
| Frozen | Phase 0 (pre-Phase-1) | Phase 8 (gated on RC-gemini shipping) |
| Prereg | `swebench-gemini-prereg-D1.json` | `swebench-gemini-prereg-D2.json` |
| Verdict shape | descriptive (single arm; no winner) | inferential (paired bootstrap; lex order) |
| Standalone publishable | YES (gemini SWE-bench leaderboard-style baseline) | YES (cross-family A vs B) |

## Why decoupled?

If RC-gemini integration never ships, D1 still ships as a useful single-arm result. D2 stays draft. This avoids the iter-3 v1 plan's Texas-sharpshooter framing (presenting Setup-A-only data as A-vs-B supplement).

## Cross-references

- Plan: `thoughts/shared/plans/2026-05-08-swebench-gemini-eval-toolkit.md` (v2)
- D1 prereg: `thoughts/shared/research/swebench-gemini-prereg-D1.json`
- D2 prereg: `thoughts/shared/research/swebench-gemini-prereg-D2.json`
- iter-3 whitepaper: `thoughts/shared/research/2026-05-08-iter3-eval-whitepaper.md`
- iter-3 prereg: `thoughts/shared/research/iter3-prereg.json` + `iter3-prereg-v2.md`
- Toolkit code: `/Users/jakubsikora/research-gemini-swebench-eval-scripts/`
- Setups: `~/eval-setups-gemini/`

## Methodology inheritance

- 5 BARS rubric dims — same as iter-3 (`repo_fit`, `cleanliness`, `correctness_determinism`, `plan_signal`, `diff_discipline`).
- Anchors — copied from claude toolkit's `eval/rubric.py`.
- Judge α gate (Krippendorff α ≥ 0.6 per dim) — same as iter-3.
- Amendment protocol — same shape as iter3 `prereg_freeze_commitment`.
- L1-L5 honest meta-lessons inherited.

## Methodology deltas (vs iter-3)

1. **Gemini judge dropped**, **Claude judge added**. Self-judging conflict (gemini judging gemini). Cross-family substitute.
2. **No ≥0.90 pass-rate gate** at decision tier. SWE-bench resolved% is in 30-70% range; 0.90 would null both arms. Decision is paired-bootstrap CI on Δ.
3. **Two preregs, frozen at different times**. D1 frozen Phase 0; D2 frozen Phase 8 after RC-gemini smoke passes.
4. **30-instance random Verified holdout**. iter-3 used 8 internal tasks selected by operator; SWE-bench subset is researcher-curated to be discriminative which inflates between-setup variance. Holdout is the generalizability sanity check.
5. **BH-FDR q ≤ 0.10** multiple-comparisons correction over 5 BARS dims. iter-3 didn't need this (decision was lex on saturated arms); here we have inferential on each dim.
6. **External validity claim is bounded**: directional concordance only, magnitude not poolable, 4 confounds explicitly cited.
7. **agent runner is google-genai Python SDK, NOT gemini CLI**. Token + cache parity with iter-3 is load-bearing (B used 8% fewer tokens in iter-3); CLI doesn't expose `cached_content_token_count` per turn. This makes Setup A's framing slightly weaker ("vanilla SDK loop" not "vanilla CLI"). Honest tradeoff.

## Status

- Phase 0: in progress (prereg files written; freeze commit pending)
- Phase 1-9: not started
- Linux x86 cloud VM: not provisioned (blocks Phase 5+)
- RC-gemini integration: not shipping yet (blocks Phase 8+)

## Authorization

Operator (Jakub Sikora) authorized the swebench-iter1 supplement-track-A pre-Phase-1 on 2026-05-08.
