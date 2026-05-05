---
date: 2026-05-05
commit: 8a2c3520f6518b1055a8cddf13fb73ca3a2d186e
branch: main
tags: [research, audit, scope-drift, doc-gap, compliance-matrix]
status: complete
---
# Research: State of Current Implementation vs Initial Plans & Specs

## Summary

The original 6-deliverable spec (D1–D6 from `docs/PLAN.md`) is **fully shipped on
the source side**, with the implementation **substantially exceeding scope**:
14 languages (vs 5 mandated), 5 hook layers (vs 1), normalized coherence math,
audit JSONL, MCP bridge, eval toolkit, and CI workflows. Quality bar holds:
**173 offline pytest pass, 0 fail.** The single non-trivial gap is **doc drift** —
README and `ARCHITECTURE.md` still claim 5 languages, `RC_PLAN_BLOCK` is
undocumented in user-facing material, HARDENING.md lacks an L4 (Task hook)
subsection, and the most recent calibration commit (`8a2c352`) has not been
re-verified in `VERIFICATION.md`. One advertised feature (`.vue` support)
errors at runtime because no PyPI wheel exists.

## Files Involved

### Spec / Plan
| File | LOC | Purpose |
|---|---:|---|
| `docs/PLAN.md` | 121 | Original 6-deliverable spec (D1–D6) |
| `docs/EVAL_DESIGN.md` | 310 | A/B methodology, n=100 power 0.87, BCa+Wilcoxon |
| `docs/HARDENING.md` | 134 | 3-layer agent-bypass threat model (L4 missing) |
| `docs/ARCHITECTURE.md` | 413 | Architecture incl. SSM Backbone Selection §; **language tables stale** |
| `docs/VERIFICATION.md` | 615 | Three QA cross-verifications dated 2026-05-01; **calibration commit not re-logged** |
| `docs/EVAL_RESULTS.md` | 144 | Smoke-001 writeup (n=2 stub, inconclusive) |
| `board/board.json` | 1043 | 35-task kanban; phase-2 RC-200..RC-217 plus env tasks RC-101..RC-103 |

### Sidecar / System 2
| File | LOC | Purpose |
|---|---:|---|
| `src/s2_core.py` | 1058 | FastAPI HTTP server (port 8765), `score_change`, `parse_source`, `build_call_graph`, /health, /score, /metrics, /baseline |
| `src/grammars.py` | 396 | Tree-sitter loader; `SUPPORTED_LANGUAGES` = 13 advertised (.vue broken at runtime) |
| `src/ssm_backbone.py` | 344 | `state-spaces/mamba-130m-hf` loader, fallback ladder to mamba2-130m → tiny-gpt2, deterministic embed() |
| `src/mcp_reasoner.py` | 154 | FastMCP `hybrid-reasoner`; `reason_over_edit(file_path, proposed_change, change_kind)` |

### Hooks / Policy
| File | LOC | Spec'd? | Purpose |
|---|---:|---|---|
| `src/hooks/pre_edit_guard.py` | 442 | yes (D4) | PreToolUse Edit/Write/MultiEdit; SSM regression block; guard-file lock |
| `src/hooks/pre_bash_guard.py` | 291 | additive | Bash escape route — 4-stage screen (kill / settings.json / process / source-write) |
| `src/hooks/pre_plan_guard.py` | 362 | additive | Plan markdown heuristics + SSM novelty; **default warn-mode** (`RC_PLAN_BLOCK=1` for hard block) |
| `src/hooks/pre_task_guard.py` | 220 | additive | Task subagent prompt screened for mutation-verb + guarded-path |
| `src/hooks/post_bash_revive.py` | 138 | additive | PostToolUse Bash; respawns sidecar if killed |
| `src/hooks/audit_log.py` | 263 | additive | JSONL appender to `/tmp/rc-events/<date>/<session>.jsonl`; redaction |

### Eval Toolkit
| File | LOC | Purpose |
|---|---:|---|
| `eval/run_suite.py` | 207 | Deterministic sampling, randomized arm order, dry-run-by-default |
| `eval/run_task.sh` | 315 | Per-(task, arm) runner; vanilla strips settings; treatment installs them |
| `eval/aggregate.py` | 334 | Joins task JSON + audit JSONL; 10 metrics; Wilcoxon + BCa + Holm |
| `eval/stats.py` | 296 | Stats lib with stdlib fallback when scipy/statsmodels missing |
| `eval/metrics.py` | 256 | AST edit distance, cyclomatic delta, fan-in/out delta |
| `eval/datasets/swe_bench_verified_python_subset.json` | 61 | 5-task offline bootstrap |
| `eval/Dockerfile` | 113 | Multi-stage `python:3.11-slim`, ~2.1 GB on disk, sha256-pinned Mamba |
| `eval/scripts/prefetch_mamba.sh` | 106 | Spec called this `prefetch_checkpoint.sh`; renamed |
| `eval/runs/smoke-001/` | 8 files | n=2 stub-mode smoke run output (commit `24d7ece`) |

### Settings / Skill / Scripts
| File | LOC | Purpose |
|---|---:|---|
| `.claude/settings.json` | 67 | 4 PreToolUse matchers + 1 PostToolUse |
| `.claude/skills/reasoning/SKILL.md` | 156 | Risk vector → prose translator |
| `scripts/start-sidecar.sh` | 73 | Boot, poll /health |
| `scripts/configure-scaleway.sh` | 218 | Live `api.scaleway.ai` POST + y-router probe |
| `scripts/test-prototype.sh` | 271 | E2E gate: bad refactor blocks, benign passes |

### CI
| File | LOC | Purpose |
|---|---:|---|
| `.github/workflows/lint-and-test.yml` | 121 | Always-on offline gate, pytest -m "not live" |
| `.github/workflows/eval.yml` | 339 | eval-smoke (push, n=2 default), eval-full (dispatch n>5) |
| `.github/dependabot.yml` | 76 | Weekly Monday; tree-sitter + ml-stack groups |
| `.github/SECURITY.md` | 55 | Hooks = policy code; PRs need human review |

### Tests (14 files, 173 passing offline)
| File | Tests | Surface |
|---|---:|---|
| `tests/test_s2_core.py` | 33 | Sidecar core (parse, scoring, HTTP, /metrics) |
| `tests/test_mcp_reasoner.py` | 9 | MCP bridge happy/fail-open/fail-closed/415 |
| `tests/test_hook_block.py` | 21 | pre_edit_guard exit codes + payload shapes |
| `tests/test_pre_bash_guard.py` | 40 | Largest — bash command screening |
| `tests/test_pre_plan_guard.py` | 11 | Plan heuristics |
| `tests/test_pre_task_guard.py` | 11 | Task wiring |
| `tests/test_audit_log.py` | 15 | JSONL emission + redaction |
| `tests/test_baseline_drift.py` | 10 | /baseline + cumulative_drift |
| `tests/test_eval_metrics.py` | 9 | AST/cyclo/fan |
| `tests/test_eval_stats.py` | 9 | Wilcoxon/BCa/Holm |
| `tests/test_eval_aggregate.py` | 4 | report.md/json |
| `tests/test_scaleway_smoke.py` | 1 offline + 2 live (gated) | Scaleway probe |

## Data Flow

### A. Edit-time policy
```
Claude Edit/Write/MultiEdit
  → .claude/settings.json:16-24 → pre_edit_guard.main() (pre_edit_guard.py:284)
  → guard-file lock check (:313-348)              ← additive vs PLAN
  → _extract_changes (:89-130) → POST /score
  → s2_core.score() (s2_core.py:858) → score_change() (:719)
    → select_grammar → parse_source → build_call_graph
    → ssm_backbone.embed() (real Mamba forward pass)
    → coherence_delta = L2 / sqrt(hidden_size)    ← normalized vs PLAN raw L2
    → regression = AIS<0.4 OR cd>1.5 OR any dim>0.9   ← matches PLAN exactly
  → ImpactReport JSON
  → exit 2 + structured stderr | exit 0 silent | exit 0 fail-open
  → audit_log.append_event → /tmp/rc-events/<date>/<session>.jsonl
```

### B. Plan-time policy (entirely additive vs PLAN; lives in HARDENING gap #3)
```
Claude Write thoughts/shared/plans/*.md
  → settings.json:5-14 → pre_plan_guard.py:303
  → _is_plan_path filter (:314)
  → _gather_warnings: per-file LOC, phase/file ratio, boundary prose, Mamba novelty (:284)
  → default exit 0 with stderr warnings  (warn mode)
  → RC_PLAN_BLOCK=1 ⇒ exit 2 if any warning   ← undocumented in README/HARDENING
```

### C. Bash escape block + revival
```
Claude Bash → settings.json:25-34 → pre_bash_guard.screen_command (:193)
  → Layer A hard-deny | B guarded-path+write | C kill-token | D src-write
  → 5s timeout; RC_ALLOW_GUARD_EDIT=1 escape
PostToolUse Bash → post_bash_revive.py
  → KILL_HINT regex + /health probe → Popen scripts/start-sidecar.sh detached
```

### D. Eval pipeline
```
eval/run_suite.py --n N (RC_LIVE=1)
  → seed=42 sampling → randomized arm-order schedule
  → bash eval/run_task.sh <task> <arm> <out>
    → clone repo at base_commit → swap settings per arm → claude → tests
    → write <task>.<arm>.json
  → eval/aggregate.py <run_dir>
    → join with /tmp/rc-events JSONL
    → Wilcoxon + BCa + Holm on 10 metrics
    → render report.{md,json}
```

## Existing Patterns

- **Override env vars** are uniformly named `RC_*` (`RC_PLAN_BLOCK`,
  `RC_ALLOW_GUARD_EDIT`, `RC_FAIL_CLOSED`, `RC_LIVE`, `RC_EVAL_STUB_CLAUDE`,
  `S2_DEVICE`, `S2_SSM_CHECKPOINT`, `S2_TIMEOUT`, `S2_FAIL_CLOSED`).
- **Hook structure**: stdin JSON → validate `tool_name` → extract payload →
  policy → emit audit → exit 0/2 with structured stderr block. All 5 hooks
  follow this template (`pre_edit_guard.py:284`, `pre_bash_guard.py:260`,
  `pre_plan_guard.py:303`, `pre_task_guard.py:142`, `post_bash_revive.py:90`).
- **Stdlib-only hooks**: every hook uses only `urllib.request` / `json` / `re`
  so a broken venv doesn't disable policy. The MCP bridge and sidecar can
  use heavy deps (httpx, FastAPI, transformers).
- **Fail-open vs fail-closed**: default fail-open; `S2_FAIL_CLOSED=1` flips
  to block-on-sidecar-down. CI sets it to 1 by default.
- **Stub mode for tests**: `RC_EVAL_STUB_CLAUDE=1` replaces the live Claude
  call with a recorded patch so the eval pipeline can run offline in CI.

## Architecture Notes

### Spec Compliance Matrix (D1–D6 from PLAN.md)

| ID | Acceptance | Status | Proof |
|---|---|---|---|
| **D1** Scaleway/y-router config | ⚠️ partial | `scripts/configure-scaleway.sh` exists; offline negative test passes; live probe gated on creds + y-router (RC-102/RC-013) |
| **D2** S2 sidecar | ✅ shipped + extended | Risk vector dims match PLAN exactly (`s2_core.py:56-65`); regression formula matches; **extends** to 13 langs, normalized coherence, /metrics, /baseline |
| **D3** MCP bridge | ✅ shipped | Server name `hybrid-reasoner` (`mcp_reasoner.py:63`), tool signature exact, fail-open/closed |
| **D4** PreToolUse hook | ✅ shipped + extended | `Edit\|Write\|MultiEdit` matcher; **extensions:** Bash + Plan + Task hooks, guard-file lock, audit log |
| **D5** Reasoning skill | ⚠️ unverified-in-trace | File present (156 LOC); content not exercised in this audit |
| **D6** test-prototype.sh | ⚠️ unverified-in-trace | File present (271 LOC); CI runs `bash -n` only, not full e2e |

**Quality bar**:
- ✅ `pytest -q -m "not live"`: 173 passed, 0 failed (CI green at `efe2b82`).
- ✅ `py_compile` clean.
- ✅ `.claude/settings.json` valid JSON.
- ⚠️ `bash scripts/test-prototype.sh` not enforced in CI; only syntax check.

### Scope Expansions Since Original Brief

| # | Expansion | Doc reflects? |
|---|---|---|
| 1 | Real `state-spaces/mamba-130m-hf` (not mock) | ✅ ARCHITECTURE.md §SSM Backbone Selection |
| 2 | Real Scaleway live POST (not config-only) | ✅ VERIFICATION.md + README §5 |
| 3 | Languages: 5 → 8 → 13 (md/json/yaml + css/scss/html/dockerfile + vue-broken) | ❌ **README + ARCHITECTURE.md still say 5** |
| 4 | Plan-time guard | ⚠️ shipped but HARDENING.md still calls it gap #3 |
| 5 | Bash + Task hooks (3-layer hardening) | ⚠️ Bash documented; **Task hook missing L4 subsection** |
| 6 | Eval toolkit + 2 CI workflows | ✅ ARCHITECTURE.md §Evaluation Subsystem |
| 7 | Coherence-delta normalization (`/sqrt(hidden_size)`) | ✅ ARCHITECTURE.md L199-211 (commit `8a2c352`) |
| 8 | Cohesion saturation fix | ❌ commit message only, no doc section |

### Acceptance Pass Rate (13 done/test tasks on board)

- ✅ Provably-met: **8** (RC-001/003/006/007/009/010/011/013)
- ⚠️ Partial / blocked-on-environment: **5** (RC-002 creds, RC-004/005/008/014 HF cache via Cato proxy)
- ❌ Unproven: **0**

All "partial" cases share two environmental root causes: HF 403 (Cato corp
proxy) and absence of live Scaleway creds. **No source-code AC is unproven.**

### Known Contract Drift

1. **Vue support is fake-shipped.** `src/grammars.py:62` advertises `vue` in
   `SUPPORTED_LANGUAGES`; `EXTENSION_MAP[".vue"]="vue"` (`:111`); but the per-
   language loader at `:281-290` raises because no `tree-sitter-vue` wheel
   exists on PyPI for Py 3.13. `.vue` files return runtime error, not 415.
2. **Plan-guard defaults to warn-only.** `pre_plan_guard.py:340-358` exits 0
   even on severity=block unless `RC_PLAN_BLOCK=1`. Operator running with
   defaults gets zero plan-time blocking and not know.
3. **Subagent prompt-content scoring is partial.** `pre_task_guard.py` does
   regex-screening for mutation verbs + guarded-path mentions. SSM-based
   prompt-intent scoring was never implemented.
4. **Cumulative repo-baseline drift is shipped-but-dormant.** Sidecar code
   path exists; threshold 3.0 is a placeholder; no operator instruction tells
   how/when to register a session baseline. RC-204 calibration deferred.
5. **Plan→code coherence gate** (cross-checking that the file Claude writes
   matches what the plan promised) was scoped in phase-2 follow-up but not
   shipped.
6. **HARDENING.md L4 (Subagent inheritance) subsection** does not exist.
   `pre_task_guard.py` ships, but HARDENING.md L121-127 still calls subagent
   inheritance "open issue."

### Documentation Gaps (cited refs)

| Gap | Evidence |
|---|---|
| ARCHITECTURE.md still says "5 languages" | `docs/ARCHITECTURE.md:215` "supports five languages"; reality 13 in `src/grammars.py:50-64` |
| README still says "5 Tree-sitter languages" | `README.md:89,180,279`; roadmap L377 lists Java/Go/Rust as "next adds" while md/json/yaml/css/scss/html/dockerfile already shipped |
| `RC_PLAN_BLOCK` undocumented | 0 grep matches in README, HARDENING.md, ARCHITECTURE.md; only in `pre_plan_guard.py:11,340` |
| `RC_ALLOW_SUBAGENT_GUARD_EDIT` undocumented | `pre_task_guard.py:14-15` defines it; HARDENING.md silent |
| `RC_ALLOW_GUARD_EDIT` not in README config table | HARDENING.md L41/62/87/97 has it; README §Configuration L234-244 doesn't |
| README runbook: no `RC_PLAN_BLOCK=1` recommendation | README §5 L168-181 + §Configuration L232-244 silent |
| README runbook: no "restart Claude after settings change" note | HARDENING.md L92-93 has it; README quickstart doesn't |
| VERIFICATION.md missing calibration-fix smoke | Last entry "Cross-Verification — Dev Pass" L614 dated 2026-05-01; commit `8a2c352` (2026-05-05) has no QA log |
| EVAL_RESULTS.md never re-run after calibration | `docs/EVAL_RESULTS.md:11` references commit `503f7e1`; numbers are stub-mode (Δ=0); never refreshed under normalized math |
| `cumulative_drift` baseline registration undocumented | `pre_edit_guard.py:253-273` consumes it; no README/HARDENING guidance |
| HARDENING.md L4 (Task hook) subsection missing | `docs/HARDENING.md:18-79` has L1/L2/L3 only; `pre_task_guard.py` ships unmentioned |
| Cohesion saturation fix has no doc section | `8a2c352` commit message describes it; no entry in ARCHITECTURE.md or EVAL_DESIGN.md |
| Vue runtime error is misadvertised | `src/grammars.py:281-290` raises; `SUPPORTED_LANGUAGES` lists it; no caveat in any user-facing doc |

## External Dependencies

- **Mamba SSM**: `state-spaces/mamba-130m-hf` (HuggingFace). Fallback ladder
  to `mamba2-130m` and `tiny-gpt2`.
- **Tree-sitter grammars** (per-language wheels): Python, JavaScript, TypeScript,
  C#, SQL, Markdown, JSON, YAML, CSS, SCSS (1.0), HTML, Dockerfile. Vue not
  available on PyPI for Py 3.13.
- **FastAPI 0.135+** (sidecar HTTP).
- **FastMCP `mcp[cli]>=1.0`** (bridge).
- **Anthropic SDK** for live eval runs (claude-opus-4-7).
- **Scaleway Generative APIs** via y-router (`devstral-2-123b-instruct-2512`).
- **GitHub Actions runners** (free `ubuntu-latest` + `ubuntu-latest-large`
  for eval-full).

## Open Questions

1. **Should `RC_PLAN_BLOCK=1` be the default?** Current default is warn-only,
   but the user's session showed Claude ignoring warnings until a write-time
   block surfaced — exactly what plan-guard was meant to prevent.
2. **Is Vue support worth retaining as advertised?** Three options: (a) drop
   `vue` from `SUPPORTED_LANGUAGES` honestly; (b) route `.vue` through HTML
   grammar internally; (c) wait for upstream wheel.
3. **Should HARDENING.md and README be brought up-to-date in one doc-only
   commit?** Tasks: language tables (5→13), L4 Task subsection,
   `RC_PLAN_BLOCK` documentation, `RC_ALLOW_SUBAGENT_GUARD_EDIT` documentation,
   calibration-fix smoke entry in VERIFICATION.md.
4. **Should `test-prototype.sh` be wired into CI?** Currently only `bash -n`
   syntax-checked. Full e2e gate would need a Mamba checkpoint cached on the
   GH runner — non-trivial but EVAL_DESIGN.md §6 already mandates it for
   eval-smoke.
5. **Should the n=2 stub-mode smoke run be replaced with at least one live
   Claude n=2 run?** EVAL_RESULTS.md headline is "inconclusive (by design)"
   — useful for proving the toolkit, useless for proving the hooks help.
   ~$11 + ~25 min for n=2 live.
6. **Subagent SSM scoring**: the brief implied full prompt-intent scoring;
   reality is regex-only. Decision: ship that as a phase-3 task, or close
   the gap by relabeling the existing implementation as the intended scope?
