---
date: 2026-05-06
commit: 7bf2d77
branch: main
ticket: iter2-eval
status: draft
---
# Plan: Iteration-2 Eval — Setup B wins 8/8

## Summary

Iteration 1 of the Setup A vs Setup B eval — Setup B (reasoning-core active) won 6/8.
T1 (mock-instead-of-integrate) and T9 (generic plan) were real losses: reasoning-core
ran during the eval but the shipped code has no mock-detector and no plan-specificity
scorer, so it could not catch either failure mode. E1 was won by Setup B at the
correctness gate; structural quality is currently flat there because no
language-convention enforcement exists yet.

This plan ships in 7 phases targeting the three observed gaps. After P1–P3, expected
verdict: Setup B wins 8/8 with measurable margin.

## Research References

- thoughts/shared/research/2026-05-05-coherence-delta-calibration.md
- thoughts/shared/research/2026-05-05-risk-vector-delta-refactor.md
- thoughts/shared/research/2026-05-05-impl-state-vs-plans.md
- Iter-2 deep-research synthesis (4 streams): mock-detection, plan-specificity,
  endurance/pivot-detection, codebase audit.

## Critical context from Iter-1 audit

| Failure | Root cause |
|---|---|
| T1 lost (5/5/5/5/5 vs 1/3/3/1/5) | Reasoning-core ran but shipped code has no mock-detector. B wrote `cy.intercept('/**', { fixture })`. `build_call_graph` is symbol-name-only — can't tell real call from mock. |
| T9 lost (3 plan vs 1 plan) | Reasoning-core ran but `pre_plan_guard.py` has zero specificity scoring. B's plan was generic ("Read full diff once") and passed all 4 existing checks (LOC budget, phase ratio, boundary prose, novelty drift) with no warning. |
| E1 won at gate, structural quality flat | Setup B passed correctness 10/10. No language-fingerprint hook exists to detect that an agent might substitute Python for required .NET work in a long session. |

---

## Phase 0: Formalize Setup B for iter-2 (build out post-iter-1 scaffolding)

### Context

Reasoning-core was active during iter-1 — hooks fired and sidecar served `/score` — but
through ad-hoc / pre-existing wiring (repo-level `.claude/settings.json` resolved via
`${CLAUDE_PROJECT_DIR}`, user-level `~/.claude/settings.json`, or direnv-loaded env).
The `/Users/jakubsikora/eval-setups/B/{.envrc, settings.local.json}` files were created
**after** iter-1 to formalize Setup B as a reproducible eval target. They are currently
stubs.

Goal of P0: build out those stubs into a real, version-controlled Setup B definition that
(a) reproduces the iter-1 wiring deterministically, (b) adds the iter-2 feature flags so
P1/P2/P3 hook additions become active, and (c) gives the eval spawner a single source of
truth instead of relying on inherited settings.

### Changes

**File `/Users/jakubsikora/eval-setups/B/.envrc`** — Verify the file activates reasoning-core
on session start. Add iter-2 feature flags so P1/P2/P3 hooks become active under Setup B:

```bash
# Iter-2 additions (defaults to shadow-mode; P4 promotes to enforcement)
export RC_MOCK_DETECTOR=1
export RC_PLAN_QUALITY=1
export RC_LANG_LOCK=1
export RC_SHADOW_MODE=1   # log decisions, do not enforce, until P4 calibrates
```

**File `/Users/jakubsikora/eval-setups/B/settings.local.json`** — Confirm `hooks` block
references `${CLAUDE_PROJECT_DIR}/src/hooks/...` paths so iter-2 hook additions land without
edits to this file. If the existing block hardcodes paths to specific hook scripts, extend
the matchers with: `pre_test_guard.py` (P1), `session_start_manifest.py` (P3),
`pre_compact_guard.py` (P3), `post_batch_lang_audit.py` (P3).

**File `eval/spawner.py`** — Audit the pre-flight diff check. Add a smoke probe: spawn a
disposable worktree, execute a synthetic Edit, assert that a row lands in
`/tmp/rc-events/$(date +%F)/<session>.jsonl` with the expected `decision` field. Refuse to
run iter-2 evals if the smoke probe fails.

### Success Criteria

#### Automated

- [ ] Smoke probe writes a Cypress test file with `cy.intercept('/**', {fixture})` → P1 mock-detector audit row appears with `mock_score >= 0.5`
- [ ] Smoke probe writes a plan file with "Read the diff. Verify it works." → P2 plan-quality audit row appears with `cgs <= 0.2`
- [ ] Smoke probe in a C#-declared session attempts to Write a `.py` file → P3 Invariant 1 audit row appears
- [ ] All three smoke decisions are logged but not enforced under `RC_SHADOW_MODE=1`

#### Manual

- [ ] Run a full iter-1 task replay (T1, T9, E1) under updated Setup B, confirm transcripts contain audit references to all three new layers

### Dependencies

Requires P1, P2, P3 hook code to exist (else flags are no-ops). Blocks P6 (eval spawn must
verify wiring before committing to n=3 budget).

---

## Phase 1: T1 fix — Mock-detector layer

### Changes

**File `src/hooks/_mock_detector.py` (new)** — Pure-AST/regex helpers:

- `wildcard_intercept_ratio(file_content)` — Cypress AST walk; `cy.intercept('/**' or wildcard, { fixture | object })` ÷ total intercepts. Threshold 0.5.
- `mock_to_real_client_ratio(file_content)` — count imports against two lexicons:
  - Mock: `jest.mock`, `sinon`, `unittest.mock`, `Moq`, `NSubstitute`, `msw`, `nock`, `WireMock`
  - Real: `axios`, `fetch`, `HttpClient`, `pg`, `amqplib`, `@azure/service-bus`, `requests`
- `mystery_guest_score(file_path, repo_root)` — fixture IDs in test file ∩ seed-file IDs; 1.0 = no overlap (fabricated).

**File `src/hooks/pre_edit_guard.py`** — Add a post-SSM-score check for `_file_kind == "test_code"`. If any of the 3 mock signals exceeds threshold, override `regression_detected=True`.

**File `src/s2_core.py`** — Add a 9th risk dim `integration_authenticity` = `1 − max(wildcard_intercept_ratio, mock_to_real_client_ratio, mystery_guest_score)` on `ImpactReport`.

### Success Criteria

#### Automated

- [ ] `tests/test_mock_detector.py`:
  - `cy.intercept('/**', {fixture: 'a.json'})` → `wildcard_intercept_ratio = 1.0` → block
  - `cy.intercept('/api/auth', (req) => req.continue())` → 0.0 → allow
  - replay-attack T1's actual artifact from iter-1 → blocks

#### Manual

- [ ] Re-run T1 prompt under iter-2 wiring; agent's first attempt at `cy.intercept('/**', ...)` produces a block stderr with the specific repair hint

### Dependencies

Requires P0.

---

## Phase 2: T9 fix — Plan-quality scoring (CGS composite gate)

### Changes

**File `src/hooks/_plan_quality.py` (new)** — 6 specificity signals + composite gate:

- `ard(plan)` — Artifact-Reference Density (file paths + endpoints + line refs ÷ sentences). Pass ≥ 0.4.
- `nrd(plan)` — Named-Risk Density (lexicon: N+1, race condition, TOCTOU, deadlock, SQLi, XSS, CSRF, dead code, unbounded pagination, missing auth check, off-by-one). Pass ≥ 0.2.
- `gpas(plan)` — Generic-Phrase Anti-Pattern Score (blocklist + cosine similarity to known-generic corpus ≥ 0.82). Pass < 0.15.
- `wwds(plan)` — What/Why Differentiation. Pass ≥ 0.5.
- `cdgs(plan, diff)` — Claim-to-Diff Grounding (FActScore-style; uses Qwen critic from P5 if available, falls back to BM25). Pass ≥ 0.6.
- `slr(plan)` — Specificity-to-Length Ratio. Pass ≥ 0.35.
- `composite_gate_score` returns CGS ∈ [0,1] with weights {ard:0.25, nrd:0.20, gpas:0.20, wwds:0.15, cdgs:0.15, slr:0.05}.

**File `src/hooks/pre_plan_guard.py`** — Add `_check_specificity` to `_gather_warnings()`. CGS ≥ 0.75 pass, 0.5–0.75 warn, < 0.5 hard reject (gated by `RC_PLAN_BLOCK=1`).

### Success Criteria

#### Automated

- [ ] `tests/test_plan_quality.py`:
  - Setup B's iter-1 plan → CGS = 0.0 → reject
  - Setup A's iter-1 plan → CGS ≥ 0.85 → accept
  - Generic-but-with-paths edge case → CGS in warn band

#### Manual

- [ ] Re-run T9 prompt; agent's first generic-checklist plan produces a block stderr listing failed signals

### Dependencies

Requires P0. Soft-requires P5 for CDGS+WWDS — degrades to BM25 + heuristic-only if Qwen unavailable.

---

## Phase 3: Long-horizon hardening — language fingerprint + cumulative-drift gates

### Changes

**File `src/hooks/session_start_manifest.py` (new)** — SessionStart hook. Snapshots file-extension distribution under task scope. Writes `thoughts/shared/session_state/<session_id>.json` with `{declared_language, framework, ext_distribution, task_spec_hash}`.

**File `src/hooks/pre_edit_guard.py`** — Add 2 invariant checks before SSM scoring:
- Invariant 1 — Language Fingerprint Lock: read manifest. If `Path(file_path).suffix` not in declared language family → deny.
- Invariant 2 — Cumulative Drift Gate: existing field already computed. Warn at 4.0, deny at 6.0 (override `RC_DRIFT_OVERRIDE=1`).

**File `src/hooks/pre_plan_guard.py`** — Invariant 5: Framework Pivot in Plan. Parse plan for technology declarations and compare against session manifest.

**File `src/hooks/pre_task_guard.py`** — Invariant 4: Subagent Language Pivot. Extend `screen_prompt()` regex.

**File `src/hooks/pre_compact_guard.py` (new)** — PreCompact hook. Serializes session state + re-injects "Task language: C#. Test framework: xUnit." as `systemMessage` on next turn.

**File `src/hooks/post_batch_lang_audit.py` (new)** — PostToolUse rolling extension audit. If non-declared-language % > 20%, injects warning.

### Success Criteria

#### Automated

- [ ] `tests/test_lang_invariants.py`:
  - Session declared C#, agent attempts `Write Tests/foo.py` → block (Invariant 1)
  - Same write with `RC_LANG_OVERRIDE=1` → allow + audit-log entry
  - cumulative_drift = 5.0 → warn (Invariant 2); = 6.5 → deny
  - Plan with "use pytest" against C# manifest → block (Invariant 5)
  - Subagent prompt with `pip install` against C# manifest → block (Invariant 4)
- [ ] PreCompact handoff produces `session_state/<id>.json` with manifest preserved

### Dependencies

Requires P0. Cumulative drift threshold needs P7 calibration eventually.

---

## Phase 4: Validation harness (P0 from prior co-reasoner research, promoted)

### Changes

**File `eval/validate_embedder.py` (new)** — 50 repo files vs 50 wiki paragraphs; mean intra-code cosine vs cross-modal cosine; require ≥ 3σ separation. If fails, fall back to `sentence-transformers/all-MiniLM-L6-v2`.

**File `eval/calibration_corpus.py` (new)** — Walk last 6mo git history. Label `merged-and-stable-7d` as negatives, `reverted-within-7d` as positives. Stratified by file_kind. Output: `eval/calibrated/labels.jsonl`.

**File `eval/shadow_mode.py` (new)** — All P1-P3 invariants ship with `RC_SHADOW_MODE=1` honored — log decisions, do not enforce. 4-week shadow window before flipping.

### Success Criteria

#### Automated

- [ ] `python -m eval.validate_embedder` exits 0 with `separation_sigma >= 3.0`
- [ ] `eval/calibrated/labels.jsonl` has ≥ 200 rows, ≥ 30 per kind
- [ ] All P1-P3 hooks accept `RC_SHADOW_MODE=1` and exit 0 with audit-only

### Dependencies

Requires nothing. Blocks P7.

---

## Phase 5: Generative critic head — Qwen2.5-Coder-1.5B

### Changes

**File `scripts/start-gen-sidecar.sh` (new)** — Boots `mlx_lm.server --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit --port 8766`. Cross-platform via `RC_REASONER_BACKEND={mlx, llama, remote}`.

**File `src/gen_client.py` (new)** — Async OpenAI-compatible client. Used by P2's CDGS + P3's Invariant 5 plan analyzer. Temperature pinned to 0 for gate paths.

**File `scripts/start-sidecar.sh`** — Optionally launch gen-sidecar alongside Mamba sidecar if `RC_REASONER_BACKEND` is set.

### Success Criteria

#### Automated

- [ ] `curl -fsS http://127.0.0.1:8766/v1/chat/completions` returns valid response
- [ ] `gen_client.score_plan_grounding(plan, diff)` returns deterministic scores at temp=0

### Dependencies

Requires P0. Optional for P2 (CDGS gracefully degrades) and P3 (Invariant 5 falls back to keyword-only).

---

## Phase 6: Eval framework operational changes (no methodology / criteria edits)

### Context

Methodology — rubric, prompts, judge prompt template — is frozen by the iter-1
methodology contract and we cannot influence the criteria. We do not edit T1/T9 prompts,
the BARS rubric, or `judge_prompt.py`. This phase covers only **operational** params
already pre-registered in iter-1 §13 as future work for the next iteration: expanding n,
adding a second judge, enforcing the inter-rater agreement gate.

### Changes

**File `eval/spawner.py`** — Bump `n=3` per (setup × task) cell (was 1 in iter-1). Add a
second judge slot (cross-family, non-Gemini) and enforce inter-rater Krippendorff
α ≥ 0.67 before grades aggregate. Methodology already pre-registers cross-family
weighting at 2× — no change to that rule. Second judge is an additional grader, not a
methodology change.

### Success Criteria

#### Automated

- [ ] `eval/cli decide-all` passes with α ≥ 0.67 across the two judges
- [ ] All n=3 runs per cell completed within 90-min cap
- [ ] Zero edits to `research-claude-code-setup-eval-prompts/`, `rubric.py`, or `judge_prompt.py` (methodology lockdown)

### Dependencies

Requires P0 (Setup B formalized). Independent of P1-P5.

---

## Phase 7: Calibration + decision rule (post-shadow-mode)

### Changes

**File `src/calibration.py` (new)** — Mahalanobis distance over 8-or-9-dim risk space. Hierarchical Bayes per-kind shrinkage; James-Stein. Bootstrap CI (B=1000) on each threshold.

**File `eval/recalibrate.py` (new)** — Monthly Page-Hinkley/CUSUM on rolling 90-day window. Force recalibration after >20% LOC churn.

### Success Criteria

#### Automated

- [ ] Mahalanobis threshold calibrated such that FPR ≤ 5% on labeled benign corpus
- [ ] Per-kind Bayesian shrinkage produces stable thresholds even with `n_kind = 5`

### Dependencies

Requires P4 (labeled corpus must exist). Runs after 4-week shadow-mode window.

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| P1 mock-detector false-positives on legitimate Cypress fixtures | Medium | Medium | Ship in shadow-mode first (P4); calibrate thresholds against fixture-heavy specs |
| P2 plan-quality false-rejects on legitimate brief plans | Medium | High | CGS warn-band (0.5–0.75) requires human approval; calibrate on existing `thoughts/shared/plans/` |
| P3 language-fingerprint blocks legitimate cross-language work | Medium | Medium | `RC_LANG_OVERRIDE=1` per-shell override |
| Qwen 1.5B HumanEval ~43% means hallucinated CDGS scores | High | Medium | Pin temp=0; ensemble with BM25; require BM25 + Qwen agreement |
| Setup B becomes too slow under all 6 hooks | Medium | High | All P1-P3 hooks accept `RC_SHADOW_MODE=1`; profile p95 before enabling enforcement |
| Iter-2 verdict still inconclusive at n=3 | Low | High | Methodology already proves at n=3 there's enough data for paired Wilcoxon |

## Rollback Strategy

- Each phase ships behind an env knob (`RC_MOCK_DETECTOR=1`, `RC_PLAN_QUALITY=1`, `RC_LANG_LOCK=1`). Default off until shadow-mode validates.
- Setup B's `.envrc` and `settings.local.json` are version-controlled. Revert via `git checkout`.
- New hook files can be removed from `settings.local.json` `hooks` block without touching the source.
- If P0's wiring breaks the eval entirely, the spawner's pre-flight check should refuse to run.

## File Ownership Summary

| File | Phase | Change Type |
|------|-------|-------------|
| `/Users/jakubsikora/eval-setups/B/.envrc` | P0 | Modify |
| `/Users/jakubsikora/eval-setups/B/settings.local.json` | P0 | Modify |
| `eval/spawner.py` | P0, P6 | Modify |
| `src/hooks/_mock_detector.py` | P1 | Create |
| `src/hooks/pre_edit_guard.py` | P1, P3 | Modify |
| `src/s2_core.py` | P1 | Modify |
| `tests/test_mock_detector.py` | P1 | Create |
| `src/hooks/_plan_quality.py` | P2 | Create |
| `src/hooks/pre_plan_guard.py` | P2, P3 | Modify |
| `tests/test_plan_quality.py` | P2 | Create |
| `src/hooks/session_start_manifest.py` | P3 | Create |
| `src/hooks/pre_task_guard.py` | P3 | Modify |
| `src/hooks/pre_compact_guard.py` | P3 | Create |
| `src/hooks/post_batch_lang_audit.py` | P3 | Create |
| `tests/test_lang_invariants.py` | P3 | Create |
| `eval/validate_embedder.py` | P4 | Create |
| `eval/calibration_corpus.py` | P4 | Create |
| `eval/shadow_mode.py` | P4 | Create |
| `scripts/start-gen-sidecar.sh` | P5 | Create |
| `src/gen_client.py` | P5 | Create |
| `scripts/start-sidecar.sh` | P5 | Modify |
| `src/calibration.py` | P7 | Create |
| `eval/recalibrate.py` | P7 | Create |

## Recommended Sequencing

```
Week 1:  P1 + P2 in parallel (2 dev streams, 3 days each)
Week 2:  P3 (4 days; touches 5 hook files)
Week 2:  P4 in parallel (validation harness, blocks P7 only)
Week 3:  P0 (verify wiring + add iter-2 flags) → smoke probe
Week 3:  P5 (Qwen sidecar) + P6 (eval methodology)
Week 4:  Shadow-mode collection in real Setup B sessions
Week 5:  Run iter-2 with n=3 + 2 judges
Week 6+: P7 calibration on shadow data; promote enforcement
```

P0 moved to Week 3 because it depends on P1/P2/P3 hook code existing (the iter-2 feature
flags are no-ops without the hooks). Setup B's existing wiring continues to function for
the original 5 hooks during P1-P3 development.

## Expected outcome

| Task | Iter-1 Setup B | Iter-2 Setup B target | Mechanism |
|---|---|---|---|
| T1 | LOST (3.0 impl) | WIN (≥4.5 impl) | P1 mock-detector blocks `cy.intercept('/**', {fixture})` at write-time |
| T2 | WIN (5.0 impl) | WIN (≥4.5) | maintain |
| T5 | WIN (4.5 impl) | WIN (≥4.5) | maintain; P3 catches scope creep earlier |
| T7 | WIN (5.0 impl) | WIN (≥4.5) | maintain |
| T8 | WIN (4.0 impl) | WIN (≥4.5) | P2 plan-quality keeps the win on plan_signal margin |
| T9 | LOST (1.0 plan) | WIN (≥3.5 plan) | P2 forces specific plan; CGS rejects "Read full diff once" at write time |
| E1 | WIN (gate-pass) | WIN (≥4.0 impl quality) | P3 Invariant 1 prevents language drift; quality dimension lifts |
| P0 | WIN (3.5 impl) | WIN (≥4.0) | maintain |

Target: Setup B wins 8/8 with measurable margin, n=3 per cell, Krippendorff α ≥ 0.67 between two judges.
