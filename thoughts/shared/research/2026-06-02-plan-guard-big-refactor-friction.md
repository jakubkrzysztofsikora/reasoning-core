---
date: 2026-06-02
commit: 54eed96
branch: main
tags: [plan-guard, refactor, decomposition, prm, agent-planning]
status: complete
type: deep-research
---
# Research: Resolving plan-guard friction on big refactors

## 1. Problem statement

`src/hooks/pre_plan_guard.py` enforces a small set of plan-quality rules at PreToolUse for writes to `PLAN.md` (and similar). With `RC_PLAN_BLOCK=1` (the production default, `.envrc:79`), any warning escalates to `exit 2`. The block-tier hard caps:

| Rule | Threshold | File:line |
|---|---|---|
| `per_file_loc_block` | `_LOC_BUDGET_BLOCK = 800` LOC per file | `pre_plan_guard.py:51, 143` |
| `phase_file_ratio` (warn → block under RC_PLAN_BLOCK=1) | phases > distinct files | `pre_plan_guard.py:166` |
| `boundary_crossing_prose` | regex match on "refactor entire", "rewrite", "migrate all" patterns | `pre_plan_guard.py:182` |
| `novelty_drift` | ~~chord-distance > 3.0 vs last 5 plans~~ — **superseded 2026-06-21 (iter-4)**: now a self-calibrating distance ratio (`plan_dist / floored-median-peer-distance > RC_PLAN_NOVELTY_RATIO`, default 1.8) vs last 8 plans. See `_check_novelty`; knobs in `docs/CONFIGURATION.md`. | `pre_plan_guard.py` `_check_novelty` |
| `ood_plan` | OOD vs corpus | `pre_plan_guard.py:280` |
| `specificity` | too few file paths | `pre_plan_guard.py:302` |
| `framework_pivot_in_plan` | language/framework name change | `pre_plan_guard.py:348-384` |

The gate exists for good reason — iter-3 reviews showed agents writing plans like "refactor entire auth module" and then producing 4 kLOC of off-plan changes. But on **legitimate large refactors** the agent now hits an unrecoverable wall: the plan is honest about needing 1.2 kLOC of edits in `s2_core.py`, and the gate refuses to let the agent even *write* that plan to disk. The agent then either (a) shrinks the plan dishonestly, (b) abandons the refactor, or (c) operator overrides with `RC_PLAN_BLOCK=0` and the protection is gone for the whole session.

## 2. Research questions

1. Which of the 7 block rules actually catch real reasoning-quality issues vs which are false-positive-heavy on legitimate big refactors?
2. What's the SOTA in 2026 for *decomposing* large refactors so a "big" plan becomes a chain of small plans the gate happily accepts?
3. Is the right fix in the **gate** (relax thresholds, add escape hatches) or in the **planner** (force decomposition before plan write)?
4. What's the minimum-disruption path that ships in ≤ 1 day?

## 3. Evaluation criteria

| Criterion | Weight | Why |
|---|---|---|
| Preserves the gate's iter-3 win (+0.32 plan-quality, +0.43 repo-fit) | high | The block rules exist because they worked |
| No FPR > 30% on legitimate large refactors | high | Today's pain point |
| Agent-recoverable (agent can fix without operator intervention) | high | Operator override is the escape hatch, not the path |
| Ships behind an env flag (rollback-able) | medium | Standard reasoning-core ergonomics |
| Implementation cost ≤ 1 eng-day | medium | Bias toward small wins |

## 4. Options surveyed

### Option A — Relax `_LOC_BUDGET_BLOCK` 800 → 2000

**What**: change the constant + add env override `RC_PLAN_LOC_BLOCK`.
**Pros**: 1-line fix; demoting 800-LOC files from block to warn aligns with iter-2 empirics (median file in reasoning-core's own diff history is ~340 LOC, p95 ≈ 1100).
**Cons**: blunt instrument; a single 2000-LOC plan entry could still be a runaway hallucination (the rule was designed to force the agent to *think* before authoring).

### Option B — Replace block with **propose-then-decompose** flow

**What**: when a plan would block under the current rules, the gate emits stderr with the violating rule + the offending file path AND **auto-spawns a decomposition prompt** that the agent reads back. The agent then writes a `PLAN.md` with smaller phases. Inspired by [CodeTaste (arXiv:2603.04177)](https://arxiv.org/pdf/2603.04177): "agents perform well when refactorings are specified in detail; propose-then-implement decomposition improves alignment."

**Pros**: agent self-recovers; gate stays strict; introduces the structural improvement (decomposition) the audit doc §B1 already wanted.
**Cons**: the gate has to format a recovery prompt the agent can actually act on — that's the interesting design work.

### Option C — Per-rule severity matrix (block / warn / audit) instead of one switch

**What**: split `RC_PLAN_BLOCK=1` into `RC_PLAN_BLOCK_LOC`, `RC_PLAN_BLOCK_PHASE_RATIO`, `RC_PLAN_BLOCK_NOVELTY` etc. Operator chooses which rules block, which warn.
**Pros**: maximum control; mirrors the per-kind `_KIND_THRESHOLDS` pattern in `s2_core.py:813`.
**Cons**: env-flag sprawl; the operator has to know which rules they care about — most won't.

### Option D — Plan checkpoints + per-phase commit

**What**: instead of one monolithic PLAN.md, the gate accepts `PLAN-phase-1.md`, `PLAN-phase-2.md` etc. Each is small; the gate scores each independently; the agent advances through them. Inspired by [scheduler-theoretic LLM agent execution (arXiv:2604.11378)](https://arxiv.org/html/2604.11378v1): "Task-Decoupled Planning decomposes tasks into a DAG of sub-goals with scoped contexts, confining replanning to the active sub-task and reducing token consumption by up to 82%."

**Pros**: structurally correct; aligns with the §B1 PRM gate direction; each phase is a natural checkpoint for `gate_prm` scoring.
**Cons**: bigger lift — needs new file-name convention, audit-log schema work, and a `rc plan next` operator command. > 1 day.

### Option E — Adversarial backtracking escape hatch

**What**: when the gate blocks, the agent is given a one-shot `RC_PLAN_BYPASS_THIS_WRITE=1` self-arm (analogous to `rc bypass-next`), but **must include a justification stanza** that lands in the audit log under `signal_source="plan_block_bypass"`. Inspired by [Devil's Advocate (arXiv:2405.16334)](https://arxiv.org/pdf/2405.16334): anticipatory reflection on potential failures before action execution.

**Pros**: agent-recoverable without operator intervention; audit trail preserved.
**Cons**: the bypass is a hole the agent can drive a truck through; needs a hardening pass.

## 5. Comparison matrix

| Option | Win-preserve | FPR ↓ | Agent-recoverable | Env-flag | ≤ 1 day | Score |
|---|---|---|---|---|---|---|
| A — Relax LOC threshold | medium | medium | n/a (no recovery needed) | yes | yes | **3.4 / 5** |
| B — Propose-then-decompose | high | high | high | yes | tight | **4.4 / 5** |
| C — Per-rule severity matrix | high | medium | n/a | yes | yes | 3.6 / 5 |
| D — Plan checkpoints + per-phase | high | high | high | yes | no (≥ 3 days) | 4.0 / 5 (deferred) |
| E — Adversarial bypass w/ justification | low | high | high | yes | yes | 3.2 / 5 |

## 6. Recommendation

**Land Option B (propose-then-decompose) as the headline fix, with Option A as the safety net in case B's recovery prompt doesn't yield useful decomposition.** Defer Option D (checkpoints) as the follow-up once we have PRM scores running.

### Concrete spec for Option B

1. **New env flag**: `RC_PLAN_DECOMPOSE=1` (default on after 1-week shadow).
2. **In `pre_plan_guard.main`** (line 417): when `RC_PLAN_BLOCK=1` AND a rule fires AND `RC_PLAN_DECOMPOSE=1`:
   - Audit the violation as today.
   - Emit on stderr a **decomposition recipe block** instead of the existing message:
     ```
     [plan-guard] BLOCK: per_file_loc_block on src/foo.py (1240 LOC vs budget 800).
     Decomposition required. Rewrite this plan as ≥ 2 sequential phases:
       - Phase 1: src/foo.py changes that are net-additive (extract helper, add tests).
         Target: ≤ 800 LOC, no breaking changes to public API.
       - Phase 2: rest of src/foo.py changes (refactor body, remove dead code).
         Target: ≤ 800 LOC, may break public API but tests from Phase 1 catch.
     For each phase, write a separate `PLAN-phaseN.md` (or rewrite this PLAN.md
     so each phase is < ${budget} LOC per file and phases ≤ distinct files).
     ```
   - **Exit 2** (block still fires) — but the agent now has a concrete recovery path.
3. **Audit-log emit** with `signal_source="plan_decompose_hint"` so operators can measure how often agents successfully self-decompose vs request operator override.
4. **Threshold loosening**: while we're touching this code, lift `_LOC_BUDGET_BLOCK` from 800 to 1200 (audit doc §B5 found 800 was below the iter-3 p75 — fires on legitimate single-file work).

### Concrete spec for the Option A safety net

Same patch can add `RC_PLAN_LOC_BLOCK="${RC_PLAN_LOC_BLOCK:-1200}"` env override. Operator can then bump to 2000 per-repo when working on a known large module.

## 7. Out of scope (deferred)

- **Option D (checkpoint files)** — wait until `gate_prm` is on and we have a PRM score per checkpoint to validate the split.
- **Option E (agent bypass w/ justification)** — wait until we see whether B's decomposition recipe actually works in production. Don't add an escape hatch we don't need.
- **PRM-graded plan decomposition** — natural successor to B once the PRM corpus from `eval/build_prm_corpus.py` is trained.

## 8. Implementation sketch (~ 4 hr)

```python
# src/hooks/pre_plan_guard.py — added near _gather_warnings (line 395)

def _format_decompose_recipe(blocking_warnings: List[Dict[str, Any]]) -> str:
    """Build a stderr block telling the agent how to split the plan."""
    by_file: Dict[str, int] = {}
    for w in blocking_warnings:
        if w.get("rule_id") == "per_file_loc_block":
            fp = w.get("file_path") or "?"
            # crude LOC extract from message
            import re
            m = re.search(r"LOC (\d+)", w.get("message", ""))
            by_file[fp] = int(m.group(1)) if m else 0
    if not by_file:
        return ""
    lines = ["[plan-guard] DECOMPOSITION REQUIRED:"]
    for fp, loc in by_file.items():
        phases = max(2, (loc // _LOC_BUDGET_BLOCK) + 1)
        lines.append(f"  - {fp}: {loc} LOC → split into {phases} phases of ≤ {_LOC_BUDGET_BLOCK} LOC each.")
    lines.append("Rewrite PLAN.md (or use PLAN-phase-1.md / PLAN-phase-2.md).")
    lines.append("Each phase must be net-additive before introducing breaking changes.")
    return "\n".join(lines)


# in main(), where we currently exit(2) on block:
if blocking and os.environ.get("RC_PLAN_DECOMPOSE", "1") == "1":
    recipe = _format_decompose_recipe(blocking)
    if recipe:
        # emit recipe + audit row
        audit_log.append_event(audit_log.new_event(
            tool_name=TOOL_NAME, decision="blocked",
            signal_source="plan_decompose_hint",
            gate_id="plan_grounding",
            reason="decomposition_required",
            ...
        ))
        _exit(2, stderr_lines=[recipe])
```

Tests: `tests/test_plan_decompose_recipe.py` — fixture plan with 1500 LOC entry, assert recipe stderr contains "≥ 2 phases" and exits 2. Reuse `tests/test_pre_plan_guard.py` patterns.

## 9. Open questions (operator decisions)

1. **Default for `RC_PLAN_DECOMPOSE`**: ship as `=1` (warn-with-recipe by default) or `=0` (opt-in for 1 week first)?
2. **LOC budget bump 800 → 1200**: ship in same commit or separate so we can measure the recipe's impact independently?
3. **Track FPR**: should we add `retry_after_block` parsing to `rc reasoning-efficiency` so we can compute "decomposition attempts that succeeded / total decomposition hints"?

## 10. Sources

- [CodeTaste — arXiv:2603.04177](https://arxiv.org/pdf/2603.04177) — propose-then-implement decomposition for refactors; agents perform well with detailed specs, fail on focus-area-only prompts.
- [Scheduler-theoretic LLM agent execution — arXiv:2604.11378](https://arxiv.org/html/2604.11378v1) — Task-Decoupled Planning DAG, 82% token reduction via scoped sub-goals.
- [Generator-Assistant Stepwise Rollback — arXiv:2503.02519](https://arxiv.org/pdf/2503.02519) — proposer/verifier split with action-level rollback.
- [Devil's Advocate — arXiv:2405.16334](https://arxiv.org/pdf/2405.16334) — anticipatory reflection before action execution.
- [LLM Agent Task Decomposition — apxml.com](https://apxml.com/courses/agentic-llm-memory-architectures/chapter-4-complex-planning-tool-integration/task-decomposition-strategies) — classical HTN patterns applied to LLM agents.
- [Best LLMs for agentic coding 2026 — dev.to](https://dev.to/danishashko/the-best-llms-for-agentic-coding-in-2026-real-world-not-just-benchmarks-96n) — per-file token budget framing; tiered routing for refactor sessions.

## 11. Internal artifacts cited

- `src/hooks/pre_plan_guard.py:51, 143, 166, 182, 240, 280, 348, 417, 454`
- `.envrc:79`
- `thoughts/shared/research/2026-06-01-reasoning-core-1000pct-improvements.md` §B5
- `thoughts/shared/research/2026-05-23-plan-grounding-plan-review-engineer.md`
