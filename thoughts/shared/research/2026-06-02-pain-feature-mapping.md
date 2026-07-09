---
date: 2026-06-02
commit: 5bb9fce
branch: main
tags: [research, market-positioning, gap-analysis, feature-mapping]
status: complete
related:
  - 2026-06-02-community-pain-points.md
  - 2026-06-01-reasoning-core-1000pct-improvements.md
  - 2026-05-23-reasoning-core-effectiveness-audit.md
  - 2026-06-02-plan-guard-big-refactor-friction.md
---

# Research: How reasoning-core maps to the community pains

## Summary

For each of the 9 community pain categories documented in
[`2026-06-02-community-pain-points.md`](2026-06-02-community-pain-points.md),
this report names the existing code paths in `src/` that already address it,
plus the gaps where coverage is incomplete or marketing-only. Six of the nine
pains have working, runtime-enforced implementations. Two (coupling/cohesion
violation, repo-convention drift) are encoded in the 11-dim risk vector but
empirically degenerate in production (audit
[`2026-06-01-…1000pct-improvements.md`](2026-06-01-reasoning-core-1000pct-improvements.md)
shows neural-driven blocks fire on ~0.05% of agent actions). The cleanup/over-
engineering pain has the strongest coverage via LOC budgets + decomposition
recipe (commit `5bb9fce`).

---

## Pain → feature map

| # | Community pain | Code path | Mode | Coverage |
|---|---|---|---|---|
| 1 | Scope creep / off-plan edits | `_dispatch.gate_plan_grounding` ([`src/hooks/_dispatch.py:220-310`](../../../src/hooks/_dispatch.py)) | runtime exit-2 | strong |
| 2 | Pattern blindness / reinvention | `project_index` + `novelty` dim ([`src/project_index.py:106-219`](../../../src/project_index.py), [`s2_core.py:57-69`](../../../src/s2_core.py)) | runtime, default OFF | weak (RC_PROJECT_INDEX=0 default) |
| 3 | Spec drift / refactor sprawl | `_plan_quality.composite_gate_score` + decomp recipe ([`src/hooks/_plan_quality.py:212-241`](../../../src/hooks/_plan_quality.py), [`pre_plan_guard.py:413-449`](../../../src/hooks/pre_plan_guard.py)) | runtime exit-2 when `RC_PLAN_BLOCK=1` | strong |
| 4 | Token waste | hard cap + early block ([`pre_edit_guard.py:67-84`](../../../src/hooks/pre_edit_guard.py)) + benchmarks ([`docs/BENCHMARKS.md:34-86`](../../../docs/BENCHMARKS.md)) | runtime + measured | strong, quantified (−8.2% to −29%) |
| 5 | Local enforcement, no telemetry | loopback `127.0.0.1:8765` ([`s2_core.py:1376`](../../../src/s2_core.py)) + supervisor ([`sidecar_supervisor.py`](../../../src/sidecar_supervisor.py)) | architecture | strong |
| 6 | Hallucinated imports / fake APIs | `_rule_engine.forbid_import` ([`src/hooks/_rule_engine.py:489-671`](../../../src/hooks/_rule_engine.py)) | runtime exit-2 | partial (declarative rules only; no symbol-existence check across repo) |
| 7 | CLAUDE.md / cursor rules ignored | exit-2 PreToolUse hooks ([`pre_edit_guard.py:1-30`](../../../src/hooks/pre_edit_guard.py)) + `S2_FAIL_CLOSED` | architectural primitive | strong (this IS the differentiator) |
| 8 | Over-engineering / unwanted abstractions | LOC budget 1200 + per-file phase ratio ([`pre_plan_guard.py:48-66, 154-197`](../../../src/hooks/pre_plan_guard.py)) | runtime exit-2 | strong (recent, commit `5bb9fce`) |
| 9 | Repo conventions ignored | `RC_LANG_LOCK` + `forbid_pattern` ([`pre_edit_guard.py:508-562`](../../../src/hooks/pre_edit_guard.py), [`_rule_engine.py:694-772`](../../../src/hooks/_rule_engine.py)) | runtime exit-2 | partial (language-level only; no style/convention learning) |

---

## Per-pain detail

### 1. Scope creep / off-plan edits — STRONG

**Pain (verbatim from community report, quote 1.4):**
> "[Cursor] confirmed: 'This is a known issue. The agent can sometimes go outside explicit instructions even with rules.'" — deanrie (Cursor staff), Dec 29 2025

**What reasoning-core does:**
- `gate_plan_grounding` at [`_dispatch.py:220-310`](../../../src/hooks/_dispatch.py) parses PLAN.md, extracts file references via [`_plan_paths.distinct_file_paths()`](../../../src/hooks/_plan_paths.py).
- Edit to a file NOT in PLAN.md → `RC_PLAN_GROUNDING=1` warns (stderr), `=2` blocks (exit 2).
- Hard-coded at the **PreToolUse** layer in Claude Code — the agent's Edit call literally cannot reach disk if the gate denies.
- Auto-scaffolds a PLAN.md stub from README.md if missing ([`_plan_scaffold.py:55-94`](../../../src/hooks/_plan_scaffold.py)).

**Why it differs from cursor rules:** cursor rules are *prompts* the agent may or may not heed. reasoning-core is a *syscall barrier* — the rule is enforced by the OS-level fork that runs the hook, not by the LLM.

**Gap:** in production audit ([`2026-06-01-…1000pct`](2026-06-01-reasoning-core-1000pct-improvements.md) §1), only 21% of blocks are reasoning-quality; 65% are guard-file self-protection. Plan-grounding fires on ~5% of edits per the 25-day corpus — useful but not the primary value-add.

### 2. Pattern blindness / reinvention — WEAK

**Pain (community report §2):** "AI rewrites existing helpers instead of importing them."

**What reasoning-core does:**
- [`src/project_index.py:106-219`](../../../src/project_index.py) builds `symbol_index` ({name → defining locations}) + `import_index` ({file → imported modules}). Backs `project_fan_in` and `project_coupling` dims in the 11-dim risk vector.
- The embedder's `novelty` dim ([`s2_core.py:679-754`](../../../src/s2_core.py)) measures cosine distance between the new code's embedding and the project centroid.

**Why it's weak:**
- `RC_PROJECT_INDEX=1` is **OFF by default**.
- Per the empirical audit, `project_fan_in` / `project_coupling` / `session_centroid_drift` "never appear in audit" — they require a session baseline that needs prior interactions to populate.
- No "did you mean `utils.foo()`?" surface — the dim just contributes to a numeric score that has to cross a threshold.

**Gap:** no integration between `project_index` and the *suggestion* surface. The system can detect novelty numerically but can't (yet) tell the agent "you reinvented `parse_iso8601` from `utils/dates.py:42`."

### 3. Spec drift / refactor sprawl — STRONG

**Pain (community report §3):** "AI refactors things I didn't ask for, adds features outside the spec."

**What reasoning-core does:**
- `_plan_quality.composite_gate_score` ([`_plan_quality.py:212-241`](../../../src/hooks/_plan_quality.py)) scores PLAN.md on 4 signals (ARD, NRD, GPAS, SLR). Below 0.5 → reject; 0.5–0.75 warn.
- Decomposition recipe at [`pre_plan_guard.py:413-449`](../../../src/hooks/pre_plan_guard.py) (commit `5bb9fce`): when a plan exceeds the LOC block budget, emit an actionable "split into ≤1200 LOC phases" hint instead of just "too big".
- Framework-pivot check at [`pre_plan_guard.py:366-410`](../../../src/hooks/pre_plan_guard.py) blocks plans that mention wrong-language tooling (e.g. `pip install` in a C# repo).

**Why it works:** the plan is gated at *write time* via `PreToolUse → Write`. The agent can't ship a vague "let me also refactor X" plan past the specificity threshold.

### 4. Token waste — STRONG (quantified)

**Pain (community report §4):** "Agents loop, burn tokens, $200–$500/mo bills."

**What reasoning-core does:**
- Hard cap on `/score` latency: `S2_HARD_CAP_MS=3000` ([`pre_edit_guard.py:67-84`](../../../src/hooks/pre_edit_guard.py)) prevents agent from waiting on slow scoring.
- Catches drift *before* the bad edit lands, so the agent doesn't have to re-prompt Claude with the failed edit + error context.

**Measured:**
- [`docs/BENCHMARKS.md:34-35`](../../../docs/BENCHMARKS.md): **−8.2% tokens** averaged across 8 tasks (21.2M vs 23.1M), **−29% on PR review**.
- [`docs/BENCHMARKS.md:82-86`](../../../docs/BENCHMARKS.md): iter-1 setup B saved **−25.1% cost / −23.3% wall-clock**, won 6/8 tasks vs vanilla.
- Cost calc ([`BENCHMARKS.md:44-45`](../../../docs/BENCHMARKS.md)): ~$9/100-tasks/month at Anthropic cache-read pricing.

### 5. Local enforcement, no telemetry — STRONG

**Pain (community report §5):** "I don't want my code on Anthropic's servers."

**What reasoning-core does:**
- `s2_core.py:1376` binds to `127.0.0.1:8765` — loopback only, refuses external NIC ([`docs/ARCHITECTURE.md:77-78`](../../../docs/ARCHITECTURE.md)).
- Gen sidecar (`mlx_lm.server` on `127.0.0.1:8766`) and supervisor broker (`:8764`) are also loopback.
- All embeddings, scoring, rule eval happen in-process. No cloud relay.
- Memory logger ([`s2_core.py:1100-1124`](../../../src/s2_core.py)) explicitly "no-ops if psutil is unavailable so the sidecar never dies because of telemetry" — there is no telemetry.

**Gap:** README claim "no telemetry, no cloud relay" is honest, but discoverability is low. The community pain report shows users complain about Anthropic upload; few know a local enforcement layer exists.

### 6. Hallucinated imports / fake APIs — PARTIAL

**Pain (community report §6):** "AI invents library names that don't exist."

**What reasoning-core does:**
- `_rule_engine.forbid_import` ([`_rule_engine.py:489-671`](../../../src/hooks/_rule_engine.py)) — declarative deny list via `.reasoning-core/rules.yaml`. AST walk + regex fallback so even broken Python is caught.
- Example rule: forbid `src.sidecar_supervisor` imports inside `src/hooks/**`.

**Why partial:**
- Rule engine is **deny-list only**. It blocks *forbidden* imports, not *non-existent* ones.
- No "does symbol `X` exist in the repo?" check, even though `project_index.symbol_index` already maps every defined name to its location. The data is there; the gate isn't wired.

**Closest existing primitive:** [`src/mcp_diff_validator.py:1-229`](../../../src/mcp_diff_validator.py) catches structural defects (count_mismatch, missing_prefix) in unified diffs. Could be extended with import-resolution.

### 7. CLAUDE.md / cursor rules ignored — STRONG (the differentiator)

**Pain (community report §7):**
> "Claude reads the rules, 'understands' them, and then ignores them under pressure. You need technical enforcement — hooks that physically block forbidden actions." — weilhalt, GitHub claude-code#29795

This is the **architectural premise** of reasoning-core. The differentiation:

| Rule mechanism | Where rule lives | Who decides | Bypassable by |
|---|---|---|---|
| CLAUDE.md / cursor rules | LLM context window | the LLM | the LLM (instruction drift) |
| reasoning-core hooks | OS process / PreToolUse | hook script (Python) | only the operator with `RC_ALLOW_GUARD_EDIT=1` |

Code-level evidence:
- [`pre_edit_guard.py:1-30`](../../../src/hooks/pre_edit_guard.py) — exit-2 protocol: "Claude Code's PreToolUse hook protocol interprets exit 2 as failure, preventing the tool from running."
- [`pre_bash_guard.py`](../../../src/hooks/pre_bash_guard.py) hard-denies `unset S2_FAIL_CLOSED`, `export RC_SHADOW_MODE=`, `chmod` of guard files, `pkill` of sidecar — the agent can't disable the gate by running shell.
- [`docs/HARDENING.md:1-94`](../../../docs/HARDENING.md) — documented 6 bypass paths + 4 mitigation layers (L1 Bash guard, L2 guard-file lock, L3 sidecar revival, L4 Task subagent guard).
- [`_guard_paths.py:99-124`](../../../src/hooks/_guard_paths.py) — auto-globs hook scripts so a newly-added hook is auto-locked.

### 8. Over-engineering / unwanted abstractions — STRONG (most recent)

**Pain (community report §8):** "AI adds error handling, fallbacks, unused abstractions, comments for trivial code."

**What reasoning-core does:**
- LOC budget: `_LOC_BUDGET_DEFAULT=400` (warn), `_LOC_BUDGET_BLOCK=1200` (block) at [`pre_plan_guard.py:48-66`](../../../src/hooks/pre_plan_guard.py).
- Decomposition recipe (commit `5bb9fce`): when block fires, emit "split ~1500 LOC into 2 phases of ≤1200 LOC each" so the agent self-recovers without needing operator override. Addresses the friction documented in [`2026-06-02-plan-guard-big-refactor-friction.md`](2026-06-02-plan-guard-big-refactor-friction.md).
- Phase-to-file ratio check ([`pre_plan_guard.py:184-197`](../../../src/hooks/pre_plan_guard.py)): warns when phases > files (scope too broad relative to actual surface).
- Fan-out cap normalized at 12 in the risk vector ([`s2_core.py:708-710`](../../../src/s2_core.py)).

### 9. Repo conventions ignored — PARTIAL

**Pain (community report §9):** thin community evidence — appears in vendor blogs, not engineer rants.

**What reasoning-core does:**
- `RC_LANG_LOCK=1` ([`pre_edit_guard.py:508-562`](../../../src/hooks/pre_edit_guard.py)) — session-manifest language fingerprint. If the agent tries to write Python in a TypeScript repo, exit 2.
- `_rule_engine.forbid_pattern` ([`_rule_engine.py:694-772`](../../../src/hooks/_rule_engine.py)) — regex denylist with ReDoS protection (250ms watchdog, 2MB source cap, 512-char pattern cap). Example: forbid `subprocess.run(...shell=True)` repo-wide.
- The 11-dim risk vector's `cohesion` + `project_coupling` dims ([`s2_core.py:57-69`](../../../src/s2_core.py)) numerically detect cross-layer edits.

**Gap:** the system enforces what an operator declares in `rules.yaml`, but doesn't *learn* the repo's conventions. No "you wrote `for i in range(len(x))` but this codebase always uses `enumerate`" surface. This matches the community-report finding that this pain is thin — likely because it's hard to articulate without a tool that demonstrates it's solvable.

---

## Gaps where pains are real but coverage is missing

1. **"Did you mean…" surface for reinvented helpers.** `project_index.symbol_index` has the data ([`project_index.py:41-52`](../../../src/project_index.py)); no gate consumes it for *suggestion*, only for the numeric `novelty` dim.
2. **Symbol-existence check for hallucinated imports.** Same `symbol_index` could power "module `foo.bar.baz` not found in repo" without a `forbid_import` rule. Currently you only catch what you explicitly forbid.
3. **`RC_PROJECT_INDEX` defaults to OFF.** The Phase-2 dims (`session_centroid_drift`, `project_fan_in`, `project_coupling`) cost ~50ms/edit; turning them on for everyone would address pains 2 and 9 more directly but hasn't been promoted to default.
4. **No "this codebase uses X pattern" learner.** Repo-convention drift (pain 9) needs convention extraction from the corpus, not just hand-authored `forbid_pattern` rules. Could plug into the existing embedder.
5. **Discoverability of the "local, no telemetry" property.** Community pain 5 is loud; reasoning-core's loopback-only architecture is invisible to people who haven't read the docs.

---

## What's NOT a gap (worth defending)

- The exit-2 PreToolUse architecture is the **right primitive** for pain 7. No improvement needed — the community report cites weilhalt asking for exactly this.
- Token-savings claims are *empirical*, not vendor-prose. [`docs/BENCHMARKS.md`](../../../docs/BENCHMARKS.md) carries the receipts.
- The cleanup/over-engineering pain (8) has the strongest recent coverage. Commit `5bb9fce` directly resolved the friction from `2026-06-02-plan-guard-big-refactor-friction.md`.

---

## Open questions for human input

1. **Is shipping `RC_PROJECT_INDEX=1` default worth the ~50ms/edit budget hit?** Addresses pains 2 and 9 directly; audit shows current `novelty` signal is noise-dominated without it.
2. **Should we add a `validate_imports` MCP tool** (analogous to `validate_unified_diff`) that resolves every import against `symbol_index` and `pyproject.toml` / `package.json`? Addresses pain 6 with high precision.
3. **Marketing gap on pain 5.** Should the README lead with "loopback-only, no telemetry, your code stays on your machine" rather than "stop the agent vibecoding"? Community report shows the privacy frame is the loudest engineer-side complaint.
4. **Should rule-engine documentation include the import-existence pattern?** Even without code changes, an operator can write a `forbid_import` rule against `^(?!known_modules).*$` style — but the docs don't surface this.

---

## Cross-references

- Community pain quotes + links: [`2026-06-02-community-pain-points.md`](2026-06-02-community-pain-points.md)
- Empirical audit + 1000× roadmap: [`2026-06-01-reasoning-core-1000pct-improvements.md`](2026-06-01-reasoning-core-1000pct-improvements.md)
- 25-day audit corpus analysis: [`2026-05-23-reasoning-core-effectiveness-audit.md`](2026-05-23-reasoning-core-effectiveness-audit.md)
- Plan-guard friction on large refactors: [`2026-06-02-plan-guard-big-refactor-friction.md`](2026-06-02-plan-guard-big-refactor-friction.md)
- Benchmark numbers: [`docs/BENCHMARKS.md`](../../../docs/BENCHMARKS.md)
- Threat model: [`docs/HARDENING.md`](../../../docs/HARDENING.md)
