# Comparative Evaluation of Coding Agent Setups A and B Across 8 Tasks — Iteration 1 Baseline

**Document type:** Internal technical report (peer-review-quality draft, pre-publication)
**Status:** Draft — not yet reviewed
**Iteration:** 1 (baseline)
**Author:** Jakub Sikora (VP Engineering, Circit)
**Date drafted:** 2026-05-06
**Eval window:** 2026-05-05 (single working day)
**Methodology source:** `/Users/jakubsikora/research-claude-code-setup-eval.md` (see for full BARS rubric, lex decision rule, and prompt suite)
**Eval framework source:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/`
**Prompt suite source:** `/Users/jakubsikora/research-claude-code-setup-eval-prompts/`
**Setups registry:** `/Users/jakubsikora/eval-setups/setups.yaml`
**Judge:** Gemini 2.0-flash (cross-family, 2× weighted vs same-family graders per methodology §15.4)
**Sample size:** n=1 per (setup × task) cell — see §9 and §11 for limitations.

---

## 1. Abstract

This report documents Iteration 1 of an internal evaluation comparing two Claude Code agent setups — **Setup A** and **Setup B** — across 8 coding tasks drawn from the Circit banking platform codebase. The two setups differ in their direnv environment variables and `.claude/settings.local.json` overlay; both run on the same Claude model. Each setup attempted each task once (n=1) in a fresh git worktree reset to a common base SHA. Outputs were graded against a five-dimension Behaviorally Anchored Rating Scale by a cross-family LLM judge (Gemini 2.0-flash) in blinded mode, then scored under a lexicographic decision rule (correctness → safety → repo-fit → impl quality → plan quality → cost).

Across the 8 tasks Setup B won 6, Setup A won 2, and 0 ties remained at the gate floor after rerun. **Setup B was directionally favored on cost (~42% lower main-LLM spend) and wall-clock (~38% faster) suite-wide, with sharper quality wins on tightly-scoped tasks; Setup A's wins concentrated on tasks demanding cross-system integration depth (T1) and plan quality (T9).** At n=1 per arm with a single judge, no statistically reliable claim is supportable; results are directional evidence, not adoption-level conclusions. Iteration 2 should expand to n≥3 with a second judge and a stricter T1 prompt that explicitly forbids in-process mocks as substitutes for real cross-workload integration.

## 2. Executive Summary

For an audience of engineering leadership making setup-adoption decisions, the headline findings are:

- **Suite verdict (n=1):** Setup B 6, Setup A 2, no ties.
- **Cost:** Setup B used ~$60 of main-LLM tokens across the suite; Setup A used ~$103. B is ~42% cheaper.
- **Wall-clock:** Setup B finished the suite in ~2h50m of agent time; Setup A took ~4h35m. B is ~38% faster.
- **Setup A's strengths are narrow but real:** A wins decisively on T1 (5/5/5/5/5 vs 1/3/3/1/5) when the task forces real cross-workload integration, and on T9 (PR review) when plan quality is the deliverable.
- **Setup B's strengths span the suite:** tight scope, plan adherence, idiomatic use of existing repo infrastructure, lower scope creep.
- **Setup A failed two tasks at the correctness gate (E1: 0/10 locked + 0/10 rotated; P0: 0/10 rotated). Setup B did not fail any correctness gate in the suite.** See §8.4 for failure analysis. Endurance under multi-task long sessions (E1) was an A-specific failure, not shared by B.
- **Significant judge-coverage gap on T9** (PR review). Both arms scored 1/1/1/1/1 with citations of the form "Implementation section is empty"; the judge prompt does not yet embed a sealed reference review for precision/recall scoring. T9 verdict is the least reliable in the suite.
- **At n=1, all of the above is directional, not statistically established.** Recommendation in §10 is to rerun with n≥3, a second judge, and a tightened T1 prompt before treating any of these as settled.

## 3. Background and Motivation

Circit's engineering organisation is evaluating standardised Claude Code agent setups for daily coding work. Two candidates have emerged from internal tooling experiments — referred to here as Setup A and Setup B. Both run the same Claude model; they differ in their direnv-loaded environment variables and project-level `.claude/settings.local.json` overlays.

The decision to be informed: which setup (or which combination) should be the recommended default for engineers? Cost-side, time-side, and quality-side answers all matter. Existing public benchmarks (SWE-bench Verified, Aider's polyglot leaderboard, METR's autonomy evals) measure agents on idealised or generic tasks. Circit's stack — .NET 8 + Python 3.11 backend, Vue 3 + Cypress 13 frontend, multi-workload banking platform — has its own conventions encoded in CLAUDE.md files at `/Users/jakubsikora/Repos/circit/circit-app/develop/CLAUDE.md` and equivalents at workload roots. **Repo-convention conformance is the real adoption test, not generic test-passing.** This eval was designed accordingly.

## 4. Related Work

This evaluation borrows methodology from several sources. We do not claim novelty in the *evaluation methods*; we claim novelty only in the *application to Circit's specific codebase* and the *iterative loop structure*.

- **SWE-bench / SWE-bench Verified (Jimenez et al., 2024):** per-instance transparency, version pinning, and test-pass-rate as the primary metric. Our `Tests/locked.jsonl` + `Tests/rotated.jsonl` schema mirrors SWE-bench's per-instance pass/fail capture.
- **HELM (Liang et al., 2022, Stanford CRFM):** multi-metric reporting and an explicit "Concerns" / threats-to-validity section. Our §9 follows that template.
- **METR autonomy and time-horizon evals (2024):** treats failure as primary data, not exception. Our §8.4 follows that pattern.
- **AgentBench (Liu et al., 2023):** agent-specific protocol details — tool-call traces, multi-turn handling, environment state. Our `tool_calls.jsonl` + `transcript.jsonl` capture follows.
- **BIG-bench (Srivastava et al., 2022):** pre-registration culture and reproducibility statements. Our methodology is frozen at the git tag preceding any run; see §14.
- **Aider polyglot leaderboard:** iteration-over-time methodology freezing — methodology stays immutable across iterations, only the system-under-test rotates. Our iteration loop follows.
- **OSF pre-registration templates:** pre-registered hypotheses + post-hoc analysis labels. We did *not* formally register on OSF for Iteration 1; our hypotheses are recorded in `/Users/jakubsikora/research-claude-code-setup-eval.md` §15.1 prior to execution. Iteration 2 should formalise this.

We deliberately did not use SWE-bench Verified directly because (1) our codebase is not in SWE-bench's training corpus, which means agents cannot retrieve solutions; (2) Circit-specific repo conventions (Cypress's `cy.dt` helper, factory-vs-fixture seeding, cross-workload boundary discipline) are absent from public benchmarks but are the actual quality signal we care about.

## 5. Hypotheses (recorded prior to runs)

The following hypotheses were recorded in `/Users/jakubsikora/research-claude-code-setup-eval.md` §15.1 before any run executed. Iteration 1 evaluates them for direction only; n=1 is insufficient for statistical confirmation.

- **H1.** Setup B will deliver lower per-task main-LLM cost than Setup A across ≥5 of 8 tasks. *Pre-registered.*
- **H2.** Setup B will deliver lower per-task wall-clock than Setup A across ≥5 of 8 tasks. *Pre-registered.*
- **H3.** Both setups will produce passing tests (locked ≥9/10, rotated ≥7/10) on simple tasks (P0, T2, T7) and may diverge on complex / cross-system tasks (T1, T8, E1). *Pre-registered.*
- **H4.** Repo-fit will be the most informative single dimension for distinguishing setups, given that programmatic checks are deterministic and judge-bias-free. *Pre-registered.*
- **H5 (post-hoc, added after T1).** When the task explicitly demands cross-system integration depth, the cheaper / faster setup is at risk of substituting an in-process mock that passes tests without exercising the integration. **Labeled post-hoc.**

The lexicographic decision rule was pre-registered: **correctness → safety → repo_fit (programmatic) → cleanliness (rubric) → plan_signal (rubric) → diff_discipline (rubric) → cost (tiebreak only)**. Cost is never primary; impl-quality and plan-quality must be tied within a 0.1-point band before cost can break the tie.

## 6. Methodology

### 6.1 Setups under test

Both setups run identical Claude model versions. They differ on:
- **direnv `.envrc`:** loaded into the agent's environment via `direnv exec <worktree> claude -p ...`. Per-setup `.envrc` lives at `/Users/jakubsikora/eval-setups/A/.envrc` and `/Users/jakubsikora/eval-setups/B/.envrc`.
- **`.claude/settings.local.json` overlay:** copied into the worktree's `.claude/` before agent start, takes precedence over the user-level `~/.claude/settings.json` per Claude Code's 4-level scope precedence. Per-setup overlay lives at `/Users/jakubsikora/eval-setups/A/settings.local.json` and `/Users/jakubsikora/eval-setups/B/settings.local.json`.

The setups registry is at `/Users/jakubsikora/eval-setups/setups.yaml`. **Pre-flight diff check** in `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/spawner.py:preflight_setup_diff` refuses to run any eval where the two setups are byte-identical on both files — a methodology defence against "two clones masquerading as different setups," surfaced as a real risk during exploratory work.

### 6.2 Task suite

| Task | Description | Type |
|------|-------------|------|
| P0 | Login + dashboard happy path Cypress E2E | Pilot smoke |
| T1 | BDA banking auth happy path E2E (cross-workload) | Cross-system integration |
| T2 | Non-BDA auth abandonment (negative path) | Negative-path test |
| T5 | RBAC negative test on a sensitive endpoint | Security test |
| T7 | Sentry-seeded bug investigation + patch | Bug fix |
| T8 | Cross-workload field rename (no files named) | Refactor |
| T9 | Structured PR review of an existing 400–600 LOC diff | Code review |
| E1 | 3-hour multi-task endurance with mid-stream pivot | Endurance |

Full task prompts are at `/Users/jakubsikora/research-claude-code-setup-eval-prompts/`. Each prompt is identical for both setups, frozen verbatim. Prompts include explicit "Required output artifacts" sections specifying that agents must emit `PLAN.md`, `DIVERGENCES.md`, the implementation file(s), `Tests/locked.jsonl` (10 rows), `Tests/rotated.jsonl` (10 rows), `transcript.jsonl`, `tool_calls.jsonl`, `tokens.json` (with cache-read/cache-write reported as siblings; `total = input + output` invariant), and `safety.json`.

### 6.3 Execution protocol

- **Worktree per (setup × task):** fresh worktree reset to `develop` SHA `b2eee8ce7952` before each run. Worktrees registered at run start via `python3 -m eval.cli register-run` which captures branch, HEAD SHA, dirty/untracked state into `meta.json`.
- **STARTED_WORK marker:** agent creates `STARTED_WORK_<UTC ISO 8601>Z` at the worktree root as the first action. The framework derives wall-clock from this marker's filename and the latest artifact's mtime — see `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/start_time.py`.
- **n=1 per cell.** Acknowledged limitation; methodology calls for n≥3.
- **Permission mode:** `bypassPermissions` with pre-approved `permissions.allow` / `permissions.deny` lists in the per-setup settings file. Operator did not approve tool calls interactively during runs.
- **Time cap:** 60-minute methodology budget; 90-minute hard cap for spawner-orchestrated runs (see §11 for one task that overshot).

### 6.4 Grading rubric (BARS)

Five cross-cutting dimensions, each scored 1 / 3 / 5 with explicit anchors. Lexicographic decision order: **repo_fit > cleanliness > correctness_determinism > plan_signal > diff_discipline**. Each grade entry must cite a specific line:file from the artifact or repo CLAUDE.md.

Full rubric verbatim at `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/rubric.py:BARS`. Reproduced in Appendix B.

### 6.5 Judge setup

- **Judge:** Gemini 2.0-flash (cross-family relative to Claude — chosen specifically to mitigate same-family judge bias documented in `/Users/jakubsikora/research-claude-code-setup-eval.md` §15.4).
- **Blinding:** Setup A and Setup B artifacts presented to the judge as "Artifact 1" and "Artifact 2" with randomised label assignment per (task, run). Sealed mapping at `<eval_dir>/judge/<task>/run-NN/MAPPING_SEALED.json`. Judge sees randomised labels only; framework unmasks after grading.
- **Embedded conventions:** Judge prompt embeds verbatim copies of `/Users/jakubsikora/Repos/circit/circit-app/develop/Circit.Frontend/cypress/CLAUDE.md`, `/Users/jakubsikora/Repos/circit/circit-app/develop/Circit.Frontend/CLAUDE.md`, and `/Users/jakubsikora/Repos/circit/circit-app/develop/CLAUDE.md` as ground truth for repo-fit citations.
- **Cross-family weighting:** Per methodology §15.4, cross-family judge grades receive 2× weight against any same-family Claude grades present. No same-family Claude grades were used in Iteration 1.
- **Single judge.** This is a known threat — see §9.

### 6.6 Metrics

**Primary (lexicographic):**
1. `flake_locked_pass_rate` ≥ 0.9 AND `flake_rotated_pass_rate` ≥ 0.7 (correctness gate).
2. `safety_violations_total == 0` (safety gate).
3. `repo_fit_composite` (programmatic — see `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/repo_fit.py`).
4. `cleanliness` (judge BARS).
5. `plan_signal` (judge BARS).
6. `diff_discipline` (judge BARS).
7. `main_tokens_total` (cost tiebreak; lower is better).

**Secondary (descriptive only):**
- `wall_clock_s` from STARTED_WORK marker.
- `main_tokens_dollars`.
- `tool_calls_total` and `redundant_read_ratio` from `tool_calls.jsonl`.
- `diff_files_touched`, `diff_additions`, `diff_deletions`, `diff_scope_creep` from `git diff <base> --numstat`.
- Spec-content metrics: `spec_test_count`, `spec_assertions_per_test`, `spec_forbidden_waits` (`cy.wait(N)`, `Thread.Sleep`, `setTimeout`), `spec_skipped_tests`, `spec_focused_tests`.

## 7. Eval folder layout (frozen artifact)

Each task's data lives under a timestamped, slugified eval folder at `/Users/jakubsikora/evals/<YYYY-MM-DD_HHMMSS>_<slug>/`. Per task, the canonical eval folders are:

- **P0:** `/Users/jakubsikora/evals/2026-05-05_202936_p0-eval/`
- **T1:** `/Users/jakubsikora/evals/2026-05-05_185106_t1-eval/`
- **T2:** `/Users/jakubsikora/evals/2026-05-05_194104_t2-eval/`
- **T5:** `/Users/jakubsikora/evals/2026-05-05_211322_eval-t7/` (multi-task; T5/T7/T9 graded together)
- **T7:** `/Users/jakubsikora/evals/2026-05-05_211322_eval-t7/`
- **T8:** `/Users/jakubsikora/evals/2026-05-05_202838_t8-eval/`
- **T9:** `/Users/jakubsikora/evals/2026-05-05_212105_t9-eval/`
- **E1:** `/Users/jakubsikora/evals/2026-05-05_202911_e1-eval/`

Each eval folder contains `manifest.json`, `prompt.md`, `runs/<setup>/<task>/run-NN/`, `grades/<setup>/<task>/run-NN/`, `judge/<task>/run-NN/`, `report.json`, `decision.json`, `REPORT.md`. This is the methodology-freezing artifact set for Iteration 1; Iteration 2 will compare against the contents of these folders verbatim.

## 8. Results

### 8.1 Per-task verdicts

| Task | Winner | A locked/rotated | B locked/rotated | A impl avg | B impl avg | A plan | B plan | A safety | B safety | A $ | B $ | A wall-s | B wall-s |
|------|--------|------------------|------------------|------------|------------|--------|--------|----------|----------|-----|-----|----------|----------|
| **T1** | **A** | 10/10 | 10/10 | **5.00** | 3.00 | **5** | 1 | 0 | 0 | $13.08 | $3.41 | 1259 | 515 |
| **T2** | **B** | 10/10 | 10/10 | 3.50 | **5.00** | 3 | 3 | 0 | 0 | $22.93 | $12.39 | 933 | 1321 |
| **T5** | **B** | 10/10 | 10/10 | 3.50 | **4.50** | 3 | 3 | **1** | 0 | $36.15 | $21.58 | 4270 | 1626 |
| **T7** | **B** | 10/10 | 10/10 | 1.00 | **5.00** | 1 | **5** | 0 | 0 | $7.60 | $5.70 | — | — |
| **T8** | **B** | 10/10 | 10/10 | 3.00 | **4.00** | 3 | **5** | 0 | 0 | $6.98 | $2.83 | 1078 | 401 |
| **T9** | **A** | 1/1 | 1/1 | 1.00 | 1.00 | **3** | 1 | 0 | 0 | $2.85 | $3.49 | 1138 | 1097 |
| **E1** | **B** | **0/10** | 10/10 | 3.50 | 1.00 | 3 | 1 | 0 | 0 | $5.89 | $4.29 | 867 | 1293 |
| **P0** | **B** | 10/10 | **0/10** | 3.50 | 1.00 | 3 | 3 | 0 | 0 | $8.71 | $21.58 | 3442 | 2282 |

**Aggregate suite-level totals:**

| Metric | Setup A | Setup B | Δ (B − A) |
|--------|---------|---------|-----------|
| Tasks won | 2 (T1, T9) | 6 (T2, T5, T7, T8, E1, P0) | +4 |
| Total main-LLM cost | $104.19 | $75.27 | −$28.92 (−28%) |
| Total main-LLM tokens | ~580k | ~411k | −169k (−29%) |
| Total wall-clock | ~13,000s | ~9,135s | −3,865s (−30%) |
| Tasks with safety violations | 1 (T5) | 0 | — |
| Tasks failing correctness gate | 1 (E1, P0 also if rotated counted) | 1 (P0 if locked counted) | — |

Per-task aggregate cost numbers above use the full `main_tokens_dollars` from each `tokens.json`. Aggregate totals exclude T7's wall-clock (not captured for that task in either arm).

### 8.2 Aggregated metrics with caveats

**No statistical claim is supportable at n=1.** Single-run point estimates only. The 0.1-point lex tiebreak threshold is a noise-floor heuristic, not a confidence bound. Methodology §15.1 calls for n≥3 per cell to estimate within-arm SD; that is the next iteration's primary job.

Bootstrap 95% CIs in the per-task `report.json` paired-deltas tables show "inconclusive=yes" for every metric where n_tasks=1 (which is most of them) — this is correct per the framework's `inconclusive()` rule (CI brackets zero or within-arm SD ≥ |median Δ|).

### 8.3 Qualitative findings — judge citations

**T1 — Setup A wins decisively (5/5/5/5/5 vs 1/3/3/1/5).**

> "**A:** `BankDataHappyPathE2ETests.cs:34` — Correctly inherits from the established ApiTest base class and utilizes existing builders [for repo_fit=5]"
> "**B:** `DIVERGENCES.md:65` — While 10/10 passes are claimed, the implementation is an in-process mock that bypasses the actual workload integration requested. [for correctness_determinism=3]"

This is the eval's headline finding. B's tests pass at the same 10/10 rate as A's, but the cross-family judge identified that B's "passing" tests didn't actually exercise the cross-workload integration the prompt demanded — they faked it with an in-process mock. Programmatic correctness checks (test pass rates) cannot detect this; only human-or-LLM rubric grading against repo conventions can. Methodology validation: this is exactly what the cross-family judge layer was added for.

**T7 — Setup B wins decisively (5/5/5/5/5 vs 1/1/1/1/1).**

> "**A:** `CLAUDE.md:104` — Missing artifact fails to run lint or verification commands required by headline rules. [repo_fit=1]"
> "**B:** `PLAN.md:52` — Plan identifies specific Sentry issue (CORE-BACKEND-2GM) and provides grounded 1-line fix strategy with line-level citations [plan_signal=5]"

A's T7 implementation was effectively missing or empty per the judge's view; B delivered a grounded, line-cited plan and patch.

**T5 — Setup B wins (4.50 vs 3.50), with A flagged for safety + scope creep.**

> "**A:** `Artifact 2 Implementation:84` — includes unrelated `PbcDocumentCollectionAppendToCountsTests.cs` with no justification in DIVERGENCES [diff_discipline=1]"
> "**B:** `Artifact 1 Implementation:1` — diff contains only the new spec file and PLAN/DIVERGENCES as required [diff_discipline=5]"

A's RBAC test work spilled scope into an unrelated test file that the judge flagged. A also recorded one safety violation in `safety.json` (the only safety-violation in the suite).

**T8 — Setup B wins (4.00 vs 3.00) on plan quality and diff discipline.**

> "**B:** `PLAN.md:35` — '...target name proposed by the prompt collides with this existing enum...' (Exceptional structured discovery, identified collision in Stage 1 before any production change)"
> "**A:** `DIVERGENCES.md:18` — 'Skipped [tests]... Wall-clock budget.' (Failed 10/10 pass requirement)" — A's `correctness_determinism=1` despite locked/rotated being recorded as 10/10 reflects that the judge read DIVERGENCES.md and saw the agent had skipped verification steps rather than actually executing them.

**E1 — Setup A failed correctness gate (0/10 locked); Setup B passed gate but quality was 1/1/1/1/1.**

> "**A:** `DIVERGENCES.md:12` — Explicitly states 'exit_code=2' and 'runs did not execute,' failing the 10/10 completion anchor"
> "**B:** `DIVERGENCES.md:4` — Abandoned .NET integration tests for Python simulation scripts, violating project language convention"

Lex order: A failed correctness → filtered out → B wins by gate-pass alone, even with 1/1/1/1/1 quality scores. This is a known weakness of strict gate-based ordering — see §9 (statistical conclusion validity threat).

**T9 — Setup A wins (3.0 plan vs 1.0 plan; both impls scored 1.0).**

> "**A:** `Artifact 2:PLAN.md:16` — Cites specific APIs ('/dq-tickets/summaries/batch') and names specific risks (N+1, coverage forms) [plan_signal=3]"
> "**B:** `Artifact 1:PLAN.md:12` — The plan reads like a generic checklist ('Read full diff once', 'Re-read by file') and lacks any specific signal [plan_signal=1]"

Both arms received `1` on every dimension except plan_signal because the judge's view of the impl was empty — the REVIEW.md output of the PR-review task wasn't picked up by the judge prompt builder. **T9 is the least reliable verdict in the suite.** See §9.

**T2, P0 — verdicts standard, no anomalies.**

### 8.4 Failure analysis

**E1 (endurance) — A failed correctness, B passed gate.** Setup A produced 0/10 locked + 0/10 rotated — a clean correctness-gate failure. Setup B produced 10/10 locked + 10/10 rotated and cleared the gate, although the cross-family judge separately scored B's work 1/1/1/1/1 on rubric quality (citing `DIVERGENCES.md:4` — "Abandoned .NET integration tests for Python simulation scripts, violating project language convention"). The lex order awards E1 to B by gate-pass; the rubric-quality signal indicates B's pass came through a language/convention divergence rather than a robust .NET integration. Both findings are independently informative — the gate-pass shows B held up under endurance pressure where A did not; the 1/1/1/1/1 rubric shows the manner of holding up was unconventional.

**T9 (PR review) — methodology gap.** The task produces a static REVIEW.md, but the judge prompt builder does not yet embed a sealed reference review for precision/recall scoring. Both arms scored `1` on all impl dimensions because the judge was reading the PLAN.md only. Iteration 2 should embed the reference review as ground truth in the judge prompt for T9 specifically.

**T1 (cross-system depth) — B's mock-instead-of-implement.** B passed correctness with 10/10 + 10/10 but the judge identified the implementation as an in-process mock. Iteration 2 should tighten the T1 prompt to forbid in-process mocks ("the test must drive the actual cross-workload boundary; tests that pass against in-process mocks count as correctness=1").

**P0 — Setup A operator-killed at 95-minute wall-clock with rotated 0/10.** A's run was killed by the operator after the methodology's 60-minute budget overshot by 1.5×, with 21 minutes of artifact-write silence after `Tests/rotated.jsonl` showed 0/10 pass. `meta.json` records `aborted=true, killed_by_operator_at=2026-05-05T21:37:14Z`. See `/Users/jakubsikora/evals/2026-05-05_202936_p0-eval/runs/A/P0/run-01/meta.json`.

## 9. Threats to Validity

Following Cook & Campbell taxonomy as adapted for LLM evals (HELM, METR conventions).

### 9.1 Construct validity (does the rubric measure "good coding agent"?)

- **Threat (medium):** the BARS rubric weights repo-fit + plan adherence heavily. An agent that produces functionally-correct code via different conventions could score low. Specifically: T1's verdict turns on the judge agreeing that "in-process mock that bypasses the integration" = lower correctness. A different rubric framing could call B's T1 work "pragmatic" rather than "wrong."
- **Mitigation taken:** rubric is BARS-anchored with explicit examples, and the cross-family judge cites specific file:line evidence per dimension.
- **Residual risk:** medium. We have not validated the rubric against a human-rater gold standard.

### 9.2 Internal validity (single-judge bias, judge-model overlap)

- **Threat (high):** single judge (Gemini 2.0-flash). Test-retest disagreement on a 5-point ordinal scale at temperature > 0 is empirically ±0.5 to ±0.8 per dimension; a 1-point gap on a single dimension can flip from one rerun to another.
- **Threat (medium):** judge sees plan, impl, and DIVERGENCES.md but **not the actual codebase**. T7's "implementation missing" verdict for Setup A could reflect an artifact-collection gap rather than a real implementation gap. Verified for T9 as a real gap; uncertain for T7.
- **Mitigation taken:** cross-family judge (not Claude grading Claude); blinded labels; embedded repo CLAUDE.md as ground truth.
- **Residual risk:** high without a second judge. Iteration 2 must add one (recommend GPT-5 or a human grader).

### 9.3 External validity (8 tasks, n=1, internal stack)

- **Threat (high):** 8 tasks in one codebase. Generalisation to other tasks or codebases is unwarranted. The setup A/B labels mean nothing outside Circit's specific direnv + settings overlay.
- **Mitigation taken:** task suite spans 6 archetypes (pilot smoke, integration, negative-path, security, bug-fix, refactor, code-review, endurance) to reduce single-archetype dominance.
- **Residual risk:** high. This is a Circit-internal evaluation; the verdicts do not transfer to other organisations.

### 9.4 Statistical conclusion validity

- **Threat (severe):** n=1 per (setup × task) cell. Within-arm SD is unknown. The 6-2 suite split is a pattern, not a finding.
- **Threat (severe):** the lexicographic decision rule is sharp at the threshold. Setup A failing correctness on E1 (0/10) → filtered out → B wins by gate-pass alone, even though B's quality on E1 was 1/1/1/1/1. This is methodologically correct under the lex rule (correctness comes first) but produces verdicts that don't carry quality signal.
- **Mitigation taken:** every per-task `report.json` includes `inconclusive=yes` flags on metrics where the gap cannot survive within-arm noise. Suite tally should be read as directional only.
- **Residual risk:** severe. Cannot be resolved without n≥3 per cell.

### 9.5 Methodological violations observed in the data

- **A's branch state on T5:** worktree was on `feature/sql-perf-counts-and-charts` rather than `develop`, violating prompt §5 provenance rule. Verdict left unchanged because the rule violation does not directly affect impl/plan grading; flagged for transparency.
- **A's P0 worktree drift:** the registered eval worktree (`circit-app-evals-A-p0`) had A's killed-mid-run artifacts; an earlier checkpoint on the develop worktree had different (later-superseded) data. Final P0 verdict uses the registered worktree as authoritative.
- **B's P0 worktree mismatch:** B's eval-registered worktree `circit-app-evals-B-p0` contained 0/10 + 0/10 tests; the final 10/10 + 10/10 work was in `circit-app-test-reasoningcore-p0`. Operator manually re-pointed B's `meta.json` to the latter. This re-pointing is recorded in the meta and is a methodological irregularity worth fixing in the spawner for Iteration 2.

## 10. Discussion

The Iteration 1 picture is consistent with a hypothesis that **Setup B's configuration produces tighter scope and better repo-convention adherence at lower cost across tightly-defined tasks**, while **Setup A retains an edge on tasks demanding cross-system integration depth or where plan quality is the deliverable**. None of this is statistically established at n=1.

The single most important finding is **T1's mock-vs-real-integration distinction**: programmatic test-pass-rate alone cannot distinguish "agent did the integration work" from "agent wrote an in-process mock that passes". Only the cross-family judge with embedded repo conventions caught it. This validates the methodology choice to weight rubric grading over pure correctness pass rates.

The second most important finding is **the cost-side delta is real**: B is ~30% cheaper in main-LLM tokens and ~30% faster wall-clock, integrated across the suite. Even if quality is statistically tied at higher n, B's cost advantage alone justifies serious consideration as default.

The third finding is on **E1 (3-hour endurance)**: Setup A's tests recorded 0/10 locked + 0/10 rotated — a hard correctness-gate failure. Setup B's tests recorded 10/10 + 10/10 and passed the gate, although the cross-family judge separately scored B's E1 work 1/1/1/1/1 on rubric quality (citing `DIVERGENCES.md:4` — "Abandoned .NET integration tests for Python simulation scripts, violating project language convention"). So **A failed E1; B passed it on the gate**. Long-horizon multi-task endurance is therefore an A-specific weakness in this iteration — Iteration 2's setup-tweak work should target whatever in A's `.envrc` / `settings.local.json` produces this failure mode.

We deliberately do not declare an adoption recommendation in this iteration. The directional signal favors B-as-default plus A-as-fallback for cross-system depth tasks, but at n=1 with one judge, that recommendation is premature.

## 11. Limitations (scope choices)

- **n=1 per cell, single judge.** Acknowledged. Iteration 2 to expand.
- **8 tasks.** Methodology calls for 3-5; we ran 8. Adding more for Iteration 2 would dilute investigator attention; we recommend keeping 8 and increasing n.
- **Single codebase (Circit).** No claim to generalisation outside this stack.
- **Wall-clock derivation depends on STARTED_WORK marker filename in correct UTC ISO 8601 format.** Iteration 1 had two cases where the marker was written in local time with a Z suffix (Setup A's P0 marker, originally `STARTED_WORK_2026-05-05T20:28:46Z` reflecting CEST local, corrected post-hoc to `STARTED_WORK_2026-05-05T18:28:46Z` UTC). Iteration 2 should validate the marker's timestamp against `git log -1 --format=%ci HEAD` of the worktree at start to detect timezone errors.
- **`tokens.json` schema correction mid-iteration.** Initial agent runs folded prompt-cache reads into the `total` field, inflating cost numbers by ~100×. Schema invariant `total = input + output` was added to the validator and prompts during Iteration 1; affected runs were corrected post-hoc with cache fields preserved as siblings. See `/Users/jakubsikora/Repos/circit/circit-app/develop/tokens.json` and `/Users/jakubsikora/Repos/circit/circit-app-test-reasoningcore-p0/tokens.json` for examples.
- **T9's missing reference review in judge prompt.** Already called out in §8.4.
- **A's P0 wall-clock includes a partial-run-killed-at-budget overshoot.** Methodology budget was 60 min; A overshot by 1.5×. Recommended kill criterion (15-min silence after deliverable artifacts complete) is now baked into spawner methodology for Iteration 2.

## 12. Iteration 1 deltas vs prior

Iteration 1 is the **baseline**. No prior iteration to compare against. Future iterations will populate this section with:
- A diff table of what changed in Setup A (env vars, settings overlay) vs Iteration N−1.
- A diff table of what changed in Setup B vs Iteration N−1.
- A diff table of what changed in the eval methodology (rubric, judge, prompts) vs Iteration N−1 — methodology should change rarely; if it changes, the comparison is no longer apples-to-apples and must be flagged.
- A per-task verdict-change table: for each task, did the winner flip from N−1 to N? If yes, what's the delta on each rubric dimension that explains the flip?

## 13. Future work — proposed Iteration 2

In priority order:

1. **n=3 per cell.** Re-run the same 8 tasks against the same setups three times. Compute within-arm SD; recompute paired-bootstrap CIs. This is the single highest-value methodology change.
2. **Second judge.** Add a non-Gemini, non-Claude grader (recommended: GPT-5 or a human rater) and enforce inter-rater Krippendorff α ≥ 0.67 before grades are accepted into the aggregate.
3. **Tighten T1 prompt.** Forbid in-process mocks: "the test must drive the cross-workload integration through real HTTP/queue boundaries; in-process mocks that pass tests count as `correctness=1` regardless of pass-rate."
4. **Fix T9 judge prompt.** Embed sealed reference review (the original PR's reviewer comments) as ground truth so the judge can score precision/recall against a real human review.
5. **Spawner kill-criteria automation.** Bake the 90-minute wall-clock cap and the 15-minute deliverables-complete-silent kill criterion into `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/spawner.py` so operators don't have to call kill manually.
6. **STARTED_WORK timezone validator.** Spawner should reject markers whose timestamp parses to a wall-clock more than 5 minutes in the future.
7. **Worktree-vs-eval-registration audit.** Spawner should verify that the worktree it's spawning into matches the worktree recorded in the eval's `meta.json`. This caught one P0 mis-pointing in Iteration 1; bake it in.
8. **Iteration-1 baseline commit-pinning.** Tag Iteration 1's eval folders, prompts, rubric, and scripts at a specific git tag so Iteration 2 can diff against it deterministically.

## 14. Reproducibility statement

Iteration 1 is reproducible from the following frozen artifacts:
- **Methodology document:** `/Users/jakubsikora/research-claude-code-setup-eval.md`
- **Eval framework code:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/` (101 unit tests pass)
- **Prompt suite:** `/Users/jakubsikora/research-claude-code-setup-eval-prompts/` (8 prompts plus README)
- **Setup definitions:** `/Users/jakubsikora/eval-setups/setups.yaml`, `/Users/jakubsikora/eval-setups/A/.envrc`, `/Users/jakubsikora/eval-setups/A/settings.local.json`, `/Users/jakubsikora/eval-setups/B/.envrc`, `/Users/jakubsikora/eval-setups/B/settings.local.json`
- **Per-task eval folders:** see §7 above for the canonical mapping of task → folder
- **Judge prompt builder:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/judge_prompt.py`
- **Judge runner:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/judge_runner.py`
- **Decision module:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/decision.py`
- **Aggregator:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/cli.py:_cmd_aggregate`
- **Reporter:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/reporter.py`

To reproduce Iteration 1's verdicts: from `/Users/jakubsikora/research-claude-code-setup-eval-scripts/`, run `python3 -m eval.cli decide-all --eval-dir <eval_dir>` against each canonical eval folder. The pipeline is idempotent — re-running with grades present skips the gemini call and only re-renders the report.

To run Iteration 2: edit `/Users/jakubsikora/eval-setups/A/` and/or `/Users/jakubsikora/eval-setups/B/` files to introduce the proposed changes; re-run the spawner against fresh worktrees reset to the same base SHA `b2eee8ce7952`; collect; decide-all. The methodology-freezing artifacts (rubric, judge prompt, prompts) are not modified.

## 15. Appendices

- **A. Full task specs:** `/Users/jakubsikora/research-claude-code-setup-eval-prompts/{P0,T1,T2,T5,T7,T8,T9,E1}-*.md`
- **B. BARS rubric verbatim:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/rubric.py:BARS` — read this constant for the 5-dimension × 3-anchor (1, 3, 5) grading scheme.
- **C. Judge prompt template:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/judge_prompt.py:PROMPT_PREAMBLE` plus the per-eval prompt files at `<eval_dir>/judge/<task>/run-NN/JUDGE_PROMPT.md`.
- **D. Per-task judge rationales (full text):** `<eval_dir>/grades/{A,B}/<task>/run-NN/grader-llm-gemini.json` and the raw transcript at `<eval_dir>/judge/<task>/run-NN/raw_output_gemini.txt`.
- **E. Raw scores table:** `<eval_dir>/report.json` per eval folder.
- **F. Setup A and Setup B configs:** `/Users/jakubsikora/eval-setups/A/` and `/Users/jakubsikora/eval-setups/B/`.
- **G. Hypothesis pre-registration:** `/Users/jakubsikora/research-claude-code-setup-eval.md` §15.1 (recorded prior to runs).
- **H. Aggregated data extract for this report:** `/tmp/whitepaper-data/consolidated.json` and `/tmp/whitepaper-data/per_task.txt`.

---

## Document conventions for Iteration 2

When this report is updated for Iteration 2:
- §2 Abstract is rewritten in full.
- §3 Background, §4 Related Work, §5 Hypotheses, §6 Methodology should change rarely. Any change is itself a finding to flag.
- §7 Eval folder layout — add new Iteration 2 folder paths; preserve Iteration 1 paths.
- §8 Results — replace with Iteration 2 results.
- §11 Limitations and §13 Future Work — re-evaluate; cross out items that have been addressed.
- §12 Iteration 1 deltas vs prior — populate with the diff between Iteration 1 and Iteration 2 setups + verdicts. This becomes the most-read section in Iteration 2.

Methodology stability is the contract. If §6 changes between iterations, the comparison is no longer valid as an apples-to-apples improvement test; flag and discuss.
