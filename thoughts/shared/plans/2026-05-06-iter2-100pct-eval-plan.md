---
date: 2026-05-06
commit: a53aaa6
branch: main
ticket: iter2-eval
status: draft
supersedes: 2026-05-06-iter2-100pct-eval-plan.md (v1)
revision: v2 — incorporates 19 corrections from 3-reviewer adversarial pass
---
# Plan v2: Iteration-2 Eval — falsifiable goal + reviewer corrections

## Summary

Iteration 1: Setup B (reasoning-core active) won 6/8. T1 (mock-instead-of-integrate)
and T9 (generic plan) lost because the shipped scoring code lacks a mock-detector and
a plan-specificity scorer. E1 was won at the correctness gate but structural quality
was flat because no language-convention enforcement exists.

This v2 plan ships in 8 phases. Three reviewers (LLM scientist, agent-harness engineer,
senior dev) flagged 19 hard corrections in the v1 draft; v2 incorporates all. Major
corrections: PreCompact API claim was factually wrong (rewritten to use
`additionalContext` on session resume); Bash escape vector wide open (now extends
`pre_bash_guard` to consume the session manifest); n=3 paired Wilcoxon goal was
statistically unachievable (reframed to sign-test 8/8 → p=0.0039); Mamba-130M is
unfit for plan-vs-diff grounding (sentence-transformers MiniLM is the default,
Mamba is the fallback); P7 calibration runs **concurrent** with shadow not after;
new P-1 phase adds daily-use ergonomics (magic comments, one-shot bypass, `rc`
CLI) — without these the system is operationally hostile per the senior-dev review.

## Falsifiable goal (replaces v1's "8/8 with measurable margin")

Pre-registered acceptance criterion for iter-2:

- **Primary**: ≥7 of 8 task-mean wins for Setup B with ≥1.0 BARS gap; sign-test
  across 8 tasks 8/8 → p=0.0039 (binomial). 7/8 → p=0.035, also acceptable.
- **Secondary**: paired bootstrap 95% CI on suite-mean BARS impl-quality excludes 0.
- **Statistical floor**: n=3 paired Wilcoxon at α=0.05 is impossible (max one-tailed
  p=0.25); we abandon the per-task Wilcoxon goal and rely on sign-test + bootstrap.
- **Drop**: "measurable margin" language is survivorship optimism; replaced with the
  explicit BARS-gap and CI criteria above.

## Research References

- thoughts/shared/research/2026-05-05-coherence-delta-calibration.md
- thoughts/shared/research/2026-05-05-risk-vector-delta-refactor.md
- thoughts/shared/research/2026-05-05-impl-state-vs-plans.md
- v1 plan + 4 deep-research streams (mock-detection, plan-specificity, endurance,
  codebase audit) + 3 reviewer adversarial passes (in chat history).

## Critical context from Iter-1 audit

| Failure | Root cause |
|---|---|
| T1 lost (5/5/5/5/5 vs 1/3/3/1/5) | Reasoning-core ran but shipped code has no mock-detector. `build_call_graph` is symbol-name-only. |
| T9 lost (3 plan vs 1 plan) | `pre_plan_guard.py` has zero specificity scoring; novelty check measures plan-vs-plan distance, not generic-vs-specific. |
| E1 won at gate, quality flat | No language-fingerprint hook; `cumulative_drift` computed but never gated. |

## 19 reviewer corrections folded into v2

| # | Correction | Phase |
|---|---|---|
| 1 | PreCompact cannot inject systemMessage — rewrite to use `additionalContext` via SessionStart-on-resume / UserPromptSubmit | P3 |
| 2 | Bash escape vector — extend `pre_bash_guard` to consume session manifest, deny redirects to disallowed extensions | P3 |
| 3 | Reframe goal: drop "8/8 measurable margin", adopt sign-test 8/8 → p=0.0039 with ≥1.0 BARS gap | Goal |
| 4 | Default plan-quality embedder = sentence-transformers/all-MiniLM-L6-v2; Mamba = fallback (inverse of v1) | P2, P4 |
| 5 | P7 (Mahalanobis fit) runs concurrent with P4 shadow window, not after | Sequencing |
| 6 | Enforcement promotion gated by FPR ≤ 2% on labeled benign + zero blocks on 50-edit golden set, not by calendar | P4, P7 |
| 7 | New: `# rc:skip` / `# rc:skip-lang` magic comments — per-file/per-edit opt-out | P-1 |
| 8 | New: `RC_BYPASS_NEXT=1` one-shot bypass auto-clears after next hook call (mirrors `--no-verify`) | P-1 |
| 9 | New: `rc status` + `rc explain <decision-id>` CLI for env-knob discoverability | P-1 |
| 10 | New: `RC_LANG_ALLOW=py,sh` allowlist + path-prefix exemption (`scripts/`, `tools/`) | P3 |
| 11 | Mock-detector heuristics as cheap pre-filter; Stryker mutation score is the actual gate. Require r ≥ 0.6 correlation between heuristic and mutation score | P1 |
| 12 | Adversarial-robustness gate for CGS — red-team 20 cosmetically-padded plans, require CGS<0.5 on ≥80% | P2 |
| 13 | OOD plan detection (kNN density estimator) + judge-bias eval (per-dim systematic offset) | P4, P6 |
| 14 | Calibration corpus filter: exclude amend commits, force-pushes, reverts touching different files than original | P4 |
| 15 | `RC_GEN_BUDGET_MS=2500` per Qwen call with hard timeout → fail-open to BM25 | P5 |
| 16 | Single sidecar broker, not two independent servers (Mamba+Qwen Metal contention) | P5 |
| 17 | Move audit log to `~/.local/share/reasoning-core/events/`, daily gzip rotation, 5GB cap, drop plan/diff bodies (keep hashes+scores) | P-1 |
| 18 | Linux CI variant — `RC_REASONER_BACKEND=llama` GGUF path mandatory for off-Mac eval reproducibility | P5 |
| 19 | Drift thresholds 4.0/6.0 are placeholders; bootstrap from synthetic drift trajectories (CUSUM at 5% type-I) | P3, P7 |

---

## Phase -1: Day-zero ergonomics (NEW — gates daily-use viability)

### Context

Senior-dev review: "12+ env vars, no `rc-doctor` CLI, no per-file skip — new users
will rage-quit by day 2." Without per-edit override ergonomics, every block is a
shell-restart interruption. This phase ships first because P1-P3 introduce more
gates that compound the friction.

### Changes

#### File: `src/rc_cli.py` (new)

`rc status` (env knob snapshot, sidecar health, last 5 decisions), `rc explain
<decision-id>` (full audit row + repair hint trail), `rc bypass-next` (writes
single-shot kill switch consumed by next PreToolUse), `rc skip-file <path>` (adds
file path to per-session allow list).

#### File: `src/hooks/_kill_switches.py` (new)

Reads `~/.local/state/reasoning-core/kill_switches.json` at hook execution time.
Switches: `bypass_next` (consumed-on-read), `skip_files: [paths]`, `disable_until:
<iso-ts>`. Env vars are fallback only. **Switches read at hook-call time, not
session boot** — operator can flip without restarting Claude.

#### File: `src/hooks/_magic_comments.py` (new)

Parser for in-file directives. Supports:

- `# rc:skip` (Python/sh comment) or `// rc:skip` (JS/TS/C#) anywhere in first
  20 lines → bypass all reasoning-core checks for this file.
- `# rc:skip-lang` → bypass language fingerprint lock only.
- `# rc:skip-mock` → bypass mock-detector only.
- `# rc:skip-quality` → bypass plan-quality CGS only.
- `# rc:override <reason>` → allow but log the override + reason in audit.

#### File: `src/hooks/pre_edit_guard.py`

Read magic comments + kill switches before any other check. If matched, exit 0
with audit row `decision=allowed_via_override`.

#### File: `src/hooks/audit_log.py`

Move audit destination from `/tmp/rc-events/` to
`~/.local/share/reasoning-core/events/YYYY-MM-DD/`. Daily gzip rotation, retain
90 days, 5GB hard cap (oldest-first eviction). Drop plan/diff bodies — hash+score
only. Add `decision_id` UUID to each row so `rc explain` can resolve.

### Success Criteria

#### Automated

- [ ] `rc status` exits 0 in under 200 ms; lists all 12+ env knobs with current values
- [ ] `RC_BYPASS_NEXT=1 echo '{}' | python pre_edit_guard.py` allows the next call then auto-clears
- [ ] File containing `# rc:skip` on line 3 → audit row `decision=allowed_via_override` and SSM scoring is skipped
- [ ] After 90 days of audit data, gzip rotation kicks in and disk usage stays ≤ 5GB

#### Manual

- [ ] New user reads README "Run it locally" section, hits a block, runs `rc explain <id>` and resolves without checking source code

### Dependencies

Requires nothing. Blocks P1, P2, P3 (those add gates that compound friction without these escape hatches).

---

## Phase 0: Formalize Setup B for iter-2 (post-iter-1 scaffolding)

[Content unchanged from v1 except: file ownership table updated to include kill_switches.json setup; smoke probe checks magic-comment parser as well]

---

## Phase 1: T1 fix — Mock-detector layer (heuristic pre-filter + mutation gate)

### Reviewer corrections folded in

- Heuristics are cheap pre-filter only; **Stryker mutation score is the gate** (correction #11)
- Require r ≥ 0.6 correlation between heuristic-score and mutation-score on 30-file labeled corpus before promoting to enforcement

### Changes

**File `src/hooks/_mock_detector.py` (new)** — same 3 signals as v1:
- `wildcard_intercept_ratio` (Cypress AST)
- `mock_to_real_client_ratio` (lexicon imports)
- `mystery_guest_score` (fixture-vs-seed reconciliation)

Output: cheap heuristic score in [0, 1].

**File `eval/stryker_runner.py` (new)** — Run StrykerJS / Stryker.NET / mutmut
scoped to `**/adapters/**` and `**/clients/**`. If mutation-score on those files
< 0.5 after the agent's test suite runs, that's the actual gate signal. Heuristic
score is the cheap proxy that triggers the (slow) mutation run.

**File `src/hooks/pre_edit_guard.py`** — Cheap path: if heuristic score >= 0.5
and `_file_kind == "test_code"`, mark for mutation-test in CI (don't block at
edit time; mutation testing is too slow). Block only on second-edit if first
edit's mutation run came back vacuous.

**File `src/s2_core.py`** — 9th risk dim `integration_authenticity` = `1 − heuristic_score`.

### Success Criteria

#### Automated

- [ ] `tests/test_mock_detector.py`: synthetic Cypress test with `cy.intercept('/**', {fixture})` → heuristic score = 1.0; same with `req.continue()` → 0.0
- [ ] **Calibration test**: on a 30-file labeled corpus (mix of legit-fixture and mock-instead-of-integrate), Spearman r ≥ 0.6 between heuristic score and Stryker mutation score
- [ ] Replay-attack T1's iter-1 artifact → heuristic score >= 0.5 AND CI mutation run produces surviving mutants → blocks via second-pass gate

#### Manual

- [ ] Re-run T1 prompt under iter-2; agent's first attempt at `cy.intercept('/**', ...)` produces a heuristic warning at edit time + a mutation-test failure in CI

### Dependencies

Requires P-1 (override ergonomics), P0 (Setup B formalized).

---

## Phase 2: T9 fix — Plan-quality scoring with falsifiable embedder choice

### Reviewer corrections folded in

- Default plan-quality embedder = `sentence-transformers/all-MiniLM-L6-v2` (correction #4); Mamba is fallback only after embedder-fitness 3σ test passes for both
- CGS weights: uniform 1/6 + L2 prior until n ≥ 60 labeled plans (correction from LLM scientist re overfitting)
- Adversarial-robustness gate: red-team 20 cosmetically-padded plans, require CGS < 0.5 on ≥ 80% (correction #12)

### Changes

**File `src/hooks/_plan_quality.py` (new)** — Same 6 signals (ARD, NRD, GPAS, WWDS, CDGS, SLR) but:

- GPAS uses MiniLM embeddings by default (`sentence-transformers/all-MiniLM-L6-v2`); falls back to Mamba only if MiniLM unavailable AND Mamba passed embedder-fitness test
- CGS weights start uniform `{ard: 1/6, nrd: 1/6, gpas: 1/6, wwds: 1/6, cdgs: 1/6, slr: 1/6}` with L2 prior until n ≥ 60 labeled plans accumulate; weights then fit via leave-one-out logistic with bootstrap CI

**File `src/hooks/pre_plan_guard.py`** — `_check_specificity` returns CGS. Gate: ≥ 0.75 pass, 0.5–0.75 warn, < 0.5 hard reject (gated by `RC_PLAN_BLOCK=1`).

**File `tests/test_plan_quality_adversarial.py` (new)** — Red-team corpus: 20
cosmetically-padded plans (generic prose with file paths and risk-keywords sprinkled).
Required: CGS < 0.5 on ≥ 80% to ship enforcement.

### Success Criteria

#### Automated

- [ ] Setup B's iter-1 plan ("Read full diff once") → CGS = 0.0
- [ ] Setup A's iter-1 plan → CGS ≥ 0.85
- [ ] Adversarial corpus: 16/20 (≥80%) padded-generic plans score CGS < 0.5
- [ ] CGS weights converge via leave-one-out CV with bootstrap CI excluding 0 (after n ≥ 60 labeled plans)

### Dependencies

Requires P-1, P0. Soft-requires P5 for CDGS+WWDS — degrades to BM25 + heuristic only.

---

## Phase 3: Long-horizon hardening — corrected mechanism + Bash extension

### Reviewer corrections folded in

- PreCompact cannot inject systemMessage (correction #1) — rewritten to write disk + inject via `additionalContext` on next SessionStart/UserPromptSubmit
- pre_bash_guard must consume session manifest (correction #2) — without this, `Bash(cat > Tests/foo.py <<EOF)` bypasses Invariant 1 entirely
- Manifest keyed by `(cwd_hash, task_spec_hash)` not raw `session_id` — survives `--resume` (agent-harness reviewer)
- `RC_LANG_ALLOW` allowlist + path-prefix exemption (`scripts/`, `tools/`) for polyglot reality (correction #10)
- Drift thresholds: bootstrap from synthetic CUSUM injections (correction #19), placeholder values labeled as such

### Changes

**File `src/hooks/session_start_manifest.py` (new)** — Keyed by `sha256(cwd + task_spec_text)`. On SessionStart, if a manifest with matching key < 24h old exists, rehydrate it. Stores: declared language family, framework, ext distribution, allowlisted extensions (`RC_LANG_ALLOW`), allowlisted path prefixes.

**File `src/hooks/pre_edit_guard.py`** — Add Invariants 1+2 BEFORE reconstruction (cheap, fail-fast, agent-harness reviewer ordering):
- Invariant 1: language fingerprint lock with allowlist + path-prefix exemption
- Invariant 2: cumulative_drift gate (warn 4.0, deny 6.0 — placeholders, recalibrated in P7 from synthetic CUSUM)

**File `src/hooks/pre_bash_guard.py`** — Extend `screen_command()` to read session manifest and detect heredoc/`>` redirect / `tee` / `sed -i` / `python -c open(...,'w')` targeting paths in disallowed-language families. Without this extension, P3 Invariant 1 is theatre.

**File `src/hooks/pre_plan_guard.py`** — Invariant 5: Framework Pivot in Plan (regex for `pip install`, `requirements.txt`, `pytest`, `import unittest` against C# manifest).

**File `src/hooks/pre_task_guard.py`** — Invariant 4: Subagent Language Pivot.

**File `src/hooks/pre_compact_guard.py` (new)** — PreCompact hook serializes manifest + the original task spec text to `~/.local/state/reasoning-core/sessions/<key>.json`. **Does not** attempt to inject `systemMessage` (was wrong in v1).

**File `src/hooks/session_resume_inject.py` (new)** — UserPromptSubmit hook fires on first user turn after resume. Reads matching session state and emits `additionalContext` containing "Task language: C#. Framework: xUnit. Do not substitute Python." This is the supported Claude Code mechanism (per agent-harness reviewer).

**File `src/hooks/post_batch_lang_audit.py` (new)** — PostToolUse rolling extension audit. If non-declared-language % > 20%, injects warning via `additionalContext` on next turn.

### Success Criteria

#### Automated

- [ ] Session declared C# manifest, agent attempts `Write Tests/foo.py` → block (Invariant 1)
- [ ] Same agent attempts `Bash(cat > Tests/foo.py <<EOF\npass\nEOF)` → block (extended pre_bash_guard) — **this is the must-fix from v1**
- [ ] Manifest with `RC_LANG_ALLOW=py` set → `Tests/foo.py` allowed
- [ ] Path under `scripts/` allowed even without env override
- [ ] PreCompact writes `sessions/<key>.json` with manifest + task_spec; subsequent resume injects `additionalContext` with task-language anchor
- [ ] Cumulative drift = 5.0 → warn; = 6.5 → deny; thresholds recalibrated from synthetic CUSUM in P7

### Dependencies

Requires P-1, P0. Cumulative drift recalibration needs P7 (concurrent with shadow).

---

## Phase 4: Validation harness + concurrent calibration

### Reviewer corrections folded in

- P7 calibration runs concurrent with shadow, not after (correction #5)
- Enforcement promotion gated by FPR ≤ 2% on labeled benign + zero blocks on 50-edit golden set, not by calendar (correction #6)
- OOD plan detection via kNN density estimator (correction #13)
- Calibration corpus filter: exclude amend, force-push, reverts-touching-different-files (correction #14)
- 4-week shadow inadequate for FPR estimation; need 9 weeks for ±2% CI half-width on 5% FPR

### Changes

**File `eval/validate_embedder.py` (new)** — Run for both Mamba-130M and MiniLM-L6. Pass gate is 3σ separation on intra-code vs cross-modal cosine PLUS Cohen's d ≥ 0.8 on generic-vs-specific plan corpus (n=30 each). MiniLM is the default; Mamba enabled only if it also passes both gates.

**File `eval/calibration_corpus.py` (new)** — Walk last 6mo git history. Label `merged-and-stable-7d` as negatives, `reverted-within-7d` as positives. **Exclusions**: amend commits, force-pushes (compare reflog), reverts where revert-diff touches different file set than original. Stratified by file_kind. Output ≥ 200 rows, ≥ 30 per kind.

**File `eval/golden_set.py` (new)** — 50 known-benign edits hand-curated. Used as the regression gate before promoting any P1/P2/P3 invariant from shadow → enforcement. Zero blocks required.

**File `src/hooks/_ood_detector.py` (new)** — kNN density estimator on plan embeddings. Plans more than k-nearest-mean cosine + 3σ from existing approved plans → route to human review, never auto-reject.

**File `eval/shadow_mode.py` (new)** — All P1-P3 invariants honor `RC_SHADOW_MODE=1` (audit-only). Promotion criteria below; not time-based.

### Promotion criteria (replaces "4-week window")

Promote shadow → enforcement when ALL hold:
- Shadow-FPR ≤ 2% on labeled benign corpus from `calibration_corpus.py`
- Zero blocks on the 50-edit golden set
- For embedder-dependent gates (CGS): leave-one-out CV bootstrap CI excludes 0
- For mutation-gated mock-detector: heuristic↔mutation Spearman r ≥ 0.6 on 30-file corpus
- Adversarial CGS test: ≥ 80% of red-team padded plans score CGS < 0.5

These typically require 8–10 weeks of shadow data given the scientist reviewer's
n-estimate (450 negatives for ±2% CI on 5% FPR), not 4.

### Success Criteria

#### Automated

- [ ] `python -m eval.validate_embedder --model minilm-l6` exits 0 with both fitness gates passing
- [ ] `eval/calibrated/labels.jsonl` has ≥ 200 rows, ≥ 30 per kind, with exclusion filters logged
- [ ] All P1-P3 hooks accept `RC_SHADOW_MODE=1` and exit 0 with audit-only

### Dependencies

Requires nothing for harness itself. Promotion criteria gate P1-P3 enforcement.

---

## Phase 5: Generative critic head with broker + budgets + Linux variant

### Reviewer corrections folded in

- Single sidecar broker, not two independent servers (correction #16) — Mamba and Qwen multiplexed via one supervisor
- `RC_GEN_BUDGET_MS=2500` per Qwen call with hard timeout, fail-open to BM25 (correction #15)
- `RC_REASONER_BACKEND=llama` Linux GGUF path mandatory (correction #18) — without it, eval not reproducible off-Mac
- Qwen HumanEval is wrong CDGS proxy (LLM scientist) — pre-deployment require Cohen's κ ≥ 0.7 vs 70B+ teacher on 200-pair grounding set
- Server-side iteration with hard cap (3 critic passes, 6s wall) — agent-harness reviewer; do not expose iteration tool to agent

### Changes

**File `src/sidecar_supervisor.py` (new)** — Single broker process. Owns lifecycles
of Mamba sidecar (port 8765) and Qwen sidecar (port 8766). Symmetric `/health`
checks every 5s. On either child death: exponential backoff restart, circuit-break
after 3 consecutive failures (60s cooldown). Exposes `/score` and `/critic` to
hooks; multiplexes underneath. launchd `KeepAlive` `Crashed=true,SuccessfulExit=false`.

**File `scripts/start-sidecar.sh`** — Now launches the supervisor. Honor
`RC_REASONER_BACKEND={mlx, llama, remote}`:
- `mlx`: `mlx_lm.server --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit`
- `llama`: `llama-cpp-python` server with `Qwen2.5-Coder-1.5B-Instruct.Q4_K_M.gguf` (Linux CI default)
- `remote`: HTTP client to a hosted endpoint

**File `src/gen_client.py` (new)** — Async client. Hard `RC_GEN_BUDGET_MS=2500` per
call. On timeout or 5xx: fail-open to BM25-only CDGS. Temperature pinned to 0 for
gate paths. **Server-side iteration**: max 3 critic passes, 6s total wall.

**File `eval/qwen_grounding_eval.py` (new)** — 200-pair internal (claim, diff)
set scored by 70B+ teacher (e.g., Claude Sonnet via API). Required: Qwen+BM25
agreement κ ≥ 0.7 on this set before CDGS is trusted in the gate path.

### Success Criteria

#### Automated

- [ ] `curl -fsS http://127.0.0.1:8765/health` returns Mamba + Qwen status from supervisor
- [ ] Killing Qwen mid-session: supervisor restarts within 5s; `gen_client` falls open to BM25 within 2.5s deadline
- [ ] `RC_REASONER_BACKEND=llama` works on Linux CI (GGUF artifact in CI cache)
- [ ] `qwen_grounding_eval.py` reports κ ≥ 0.7 vs 70B teacher

### Dependencies

Requires P0, P-1. Optional for P2/P3 (graceful degradation).

---

## Phase 6: Eval framework operational changes

[Content unchanged from v1 (zero methodology edits) — n=3, second judge, Krippendorff α gate]

### Additional reviewer correction folded in

- Judge-bias eval: 20 anchor items judged by both, compute per-dimension systematic offset (judge A − judge B); Krippendorff α hides additive bias (LLM scientist correction #13)

### Changes (additive only)

**File `eval/judge_bias_eval.py` (new)** — Anchor-item bias check. 20 plans/diffs scored by both judges. If per-dim systematic offset > 0.5 BARS points, flag and apply post-hoc correction or escalate.

---

## Phase 7: Calibration concurrent with shadow

### Reviewer corrections folded in

- Mahalanobis distance over 9-dim risk space (replaces "any dim > 0.9" OR rule whose effective FPR is ~22.6% at k_eff≈5)
- Hierarchical Bayes per-kind shrinkage; James-Stein
- Page-Hinkley/CUSUM monthly recalibration; force after >20% LOC churn
- Bootstrap from synthetic drift trajectories for cumulative_drift threshold (correction #19)
- **Critical reorder**: P7 fit runs concurrent with P4 shadow, not sequentially (correction #5)

### Changes

**File `src/calibration.py` (new)** — Mahalanobis with hierarchical Bayes shrinkage. Bootstrap CI (B=1000) on each threshold; report width.

**File `eval/recalibrate.py` (new)** — Monthly Page-Hinkley/CUSUM on rolling 90-day window. Force recalibration after >20% LOC churn.

**File `eval/synthetic_drift.py` (new)** — Inject known pivots at known steps into synthetic session traces. Fit CUSUM detector at 5% type-I. Output: drift threshold values for `cumulative_drift` (replacing v1's placeholder 4.0/6.0).

### Success Criteria

#### Automated

- [ ] Mahalanobis threshold: FPR ≤ 5% on labeled benign corpus
- [ ] Per-kind James-Stein shrinkage produces stable thresholds at n_kind = 5
- [ ] CUSUM-derived `cumulative_drift` threshold has documented type-I error rate

### Dependencies

Requires P4 (labeled corpus). **Runs concurrent with P4 shadow window**, not after. Promotion of P1-P3 enforcement requires P7 thresholds in place.

---

## Deferred Items Tracker

Items intentionally not shipped in P-1/P0/P1/P2 because they depend on the
shadow-window corpus, P5 generative critic, or are nice-to-haves that follow
the must-haves. Tracked here so promotion gates remain honest.

| # | Item | Blocked by | Lands in |
|---|---|---|---|
| 1 | Stryker mutation-score correlation gate (require Spearman r ≥ 0.6 between heuristic and mutation score on 30-file corpus) | Labeled corpus (P4) | P4 / P7 |
| 2 | Adversarial-robustness CGS gate (red-team 20 cosmetically-padded plans, require CGS<0.5 on ≥80%) | Adversarial corpus (P4) | P4 |
| 3 | WWDS (What/Why Differentiation) signal | P5 generative critic | P5 |
| 4 | CDGS (Claim-to-Diff Grounding) signal | P5 Qwen critic + 200-pair grounding eval | P5 |
| 5 | `python -c "os.environ['RC_BYPASS_NEXT']=1; ..."` Bash-escape closure | regex extension on pre_bash_guard | P3 |
| 6 | `bash -c "rc bypass-next"`, base64-decoded eval, etc. — escape-vector hardening | regex + AST screen on pre_bash_guard | P3 |
| 7 | SHADOW vs FAIL_CLOSED priority — when sidecar is down + SHADOW=1 + S2_FAIL_CLOSED=1, currently fail-closed wins | requires deciding shadow semantics for sidecar-unavailable | P3 / P4 |
| 8 | (session_id, tool_use_id) audit dedup on hook re-fire | follow-up to P-1 audit-log | P3 |
| 9 | `${RC_REPO:?...}` fail-loud fallback in Setup B settings paths | follow-up to P0 | P3 |
| 10 | `rc tail` / `rc decisions --since=1h` CLI for shadow-audit inspection | follow-up to P-1 CLI | P3 / P4 |
| 11 | `.rc-ignore` file alternative to in-source `# rc:skip` magic comments | follow-up to P-1 | P4 |
| 12 | Glob allowlist for legit msw/Cypress mock patterns (e.g. `**/*.component.test.*` exempt) | calibration corpus | P4 |
| 13 | Mystery-guest "no seeds" audit breadcrumb (currently silent zero) | trivial follow-up | P3 |
| 14 | CGS weight fitting via leave-one-out logistic | n ≥ 60 labeled plans | P7 |
| 15 | Per-kind risk-dim env knobs (`S2_KIND_THRESHOLDS` JSON) | follow-up to P-1 calibration | P7 |
| 16 | `RC_AUDIT_LEGACY_FALLBACK` reader (was dropped from .envrc; legacy `/tmp/rc-events` paths get no data) | scope decision: re-add or accept silent loss | P4 |
| 17 | Cosine-distance refactor for coherence_delta (replace `raw_l2 / sqrt(hidden)` with `1 − cos_sim`) | full re-tune of per-kind cd thresholds | P7+ |
| 18 | Docs-drift sweep: 11 stale `/tmp/rc-events/` references in README/ARCHITECTURE/board.json | follow-up | P3 / P4 |
| 19 | Mock-detector lexicon coverage gaps (responses, vi.mock CallExpression vs setupServer false-pos) | corpus + adversarial calibration | P4 |
| 20 | Lexicon-Goodhart hardening for NRD (fixed list invites buzzword-stuffing) | adversarial CGS corpus | P4 |
| 21 | 9th risk dim `integration_authenticity` wired into `_compute_risk_vector` (currently flows via audit `signal_source` field, not as a dedicated 8→9 dim refactor) | s2_core risk-vector refactor + threshold recalibration | P7 |
| 22 | `eval/spawner.py` pre-flight smoke probe (eval framework lives in a separate repo at /Users/jakubsikora/research-claude-code-setup-eval-scripts/) | eval-framework repo work, out of reasoning-core scope | external |
| 23 | `tests/test_plan_quality_adversarial.py` (red-team 20 cosmetically-padded plans) | needs adversarial corpus from P4 | P4 |
| 24 | Stryker correlation evidence (Spearman r ≥ 0.6 between heuristic and mutation score on 30-file labeled corpus) | corpus collection during shadow window | P4 |
| 25 | LLM scientist's Goodhart attack on `declared=None` worktrees — agent learns "low-signal worktree = unguarded". Mitigation: route declared=None warning to log file, not stderr | follow-up to P3 hardening | P4 |
| 26 | post_batch_lang_audit threshold uncalibrated (default 0.33 was reviewer-tuned, not data-driven) | calibration corpus | P4 |
| 27 | Bash regex still misses obscure write paths (`dd of=`, `awk > path`, `install`, base64-decoded `eval`) | follow-up to P3 part-2 | P3 follow-up |
| 28 | `rc decisions --since=1h --kind=shadow_blocked` CLI for operator visibility into shadow-mode | follow-up to P-1 CLI | P4 |
| 29 | Manifest staleness drift across 24h refresh boundary — re-walk could legitimize agent drift | persist original declared_language as immutable | P4 |
| 30 | Anchor blurb cross-session bleed protection requires CLAUDE_SESSION_ID env (not always set) — soft-mitigation only | upstream Claude Code env contract | P4 |

Promotion criteria (already in §P4): an enforcement flip from shadow → real
block requires (a) labeled-corpus FPR ≤ 2%, (b) zero blocks on the 50-edit
golden set, (c) for mock-detector: heuristic↔mutation Spearman r ≥ 0.6, (d)
adversarial CGS test passes ≥80%. Items #1, #2, #5–#9 must land before any
P1/P2/P3 enforcement promotion.

---

## Risk Assessment (revised)

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| MiniLM-L6 also fails embedder-fitness on plans (both fail) | Low | High | Fall back to BM25 + lexicon-only signals; CDGS deferred until viable embedder found |
| Mutation-test gate latency too slow for daily use | High | Medium | Mutation runs in CI not at edit time; heuristic is the edit-time signal, mutation is the second-pass gate |
| Adversarial CGS gate <80% on red-team corpus | Medium | High | Iterate: extend NRD lexicon, add structural-diversity check, retest |
| Linux CI llama.cpp path slower than Mac MLX | High | Low | Ship `RC_GEN_BUDGET_MS=5000` for CI; degrade to BM25 sooner if needed |
| PreCompact `additionalContext` doesn't reliably reach next turn | Medium | High | Belt-and-suspenders: also write CLAUDE.md anchor; UserPromptSubmit hook re-injects on every turn for first 5 turns post-resume |
| Goal still unmeasurable at n=3 even with sign-test | Low | High | Sign-test 8/8 → p=0.0039 is reachable; if 7/8 with 1-tied → use BARS-gap as tiebreaker per pre-registered criterion |
| Daily-use override pattern abused (every block bypassed) | Medium | Medium | `rc:override <reason>` requires a reason string logged in audit; analyze override rate per session in shadow review |

## Rollback Strategy

Each phase ships behind an env knob; defaults off until shadow + promotion criteria pass. P-1 ergonomics hooks (magic comments, kill switches) are non-blocking — a broken `rc_cli.py` doesn't stop reasoning-core. Setup B's `eval-setups/B/` files are version-controlled.

## Sequencing (revised — concurrent calibration)

```
Week 1-2:  P-1 ergonomics (CLI, magic comments, kill switches, audit log relocation)
Week 1-2:  P1 + P2 in parallel after P-1 lands
Week 3-4:  P3 (5 hook files, includes pre_bash_guard extension) + P5 (broker + Qwen + Linux)
Week 3+:   P4 shadow window OPENS (audit-only); P7 calibration runs CONCURRENT
Week 5-8:  Shadow data accumulates; P6 framework changes (n=3, second judge, bias eval)
Week 8-10: Promotion criteria assessed (FPR, golden-set, adversarial, mutation-r)
Week 10+:  Run iter-2 eval with enforcement promoted ONLY where criteria pass
```

P4 shadow window typically takes 8-10 weeks (LLM scientist: 9 weeks for ±2% FPR CI), not 4.

## File Ownership Summary (revised)

[27 v1 files + ~12 new from corrections — full table in v2 commit]

Net new in v2:
- `src/rc_cli.py`, `src/hooks/_kill_switches.py`, `src/hooks/_magic_comments.py` (P-1)
- `eval/stryker_runner.py` (P1 mutation gate)
- `tests/test_plan_quality_adversarial.py` (P2 red-team)
- `src/hooks/session_resume_inject.py` (P3 corrected mechanism)
- `eval/golden_set.py` (P4 promotion gate)
- `src/hooks/_ood_detector.py` (P4 OOD plan detection)
- `src/sidecar_supervisor.py` (P5 broker)
- `eval/qwen_grounding_eval.py` (P5 κ gate)
- `eval/judge_bias_eval.py` (P6 bias check)
- `eval/synthetic_drift.py` (P7 CUSUM)

Modified additionally in v2: `src/hooks/pre_bash_guard.py` (P3 manifest consume), `src/hooks/audit_log.py` (P-1 path move + rotation).

## Expected outcome (revised, falsifiable)

| Task | Iter-1 Setup B | Iter-2 Setup B target | Mechanism |
|---|---|---|---|
| T1 | LOST (3.0 impl) | WIN (≥4.5 impl) | P1 heuristic warning at edit; CI mutation-score gate catches vacuous tests |
| T2-T8 (6 wins) | WIN | WIN (≥4.5 impl avg) | maintain via P-1 ergonomics (no friction), P2 plan-quality keeps margins |
| T9 | LOST (1.0 plan) | WIN (≥3.5 plan) | P2 CGS rejects "Read full diff once" at write; adversarial-robustness verified |
| E1 | WIN at gate | WIN (≥4.0 impl quality) | P3 Invariant 1 + extended pre_bash_guard prevents language pivot via either Edit or Bash |

**Pre-registered acceptance**: ≥7/8 task-mean wins for Setup B with ≥1.0 BARS gap; sign-test p ≤ 0.05 across the 8 tasks; suite-mean BARS bootstrap CI excludes 0. Drop "measurable margin" — replaced with explicit BARS-gap and CI criteria.
