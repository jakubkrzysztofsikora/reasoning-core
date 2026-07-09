---
date: 2026-05-23
role: engineer-review
parent_plan: 2026-05-23-plan-grounding-gate-dynamic-locations
status: complete
tags: [adversary-review, plan-grounding, threat-model, integration-ux, reasoning-core]
---
# Adversary Review — AI Agent Harness Engineer Perspective

## Scope and posture
Reviewing the plan-grounding-gate implementation plan from a production-ops lens. Concerns are architecture soundness, threat model, integration UX, override semantics, filesystem behavior, and acceptance-criterion shape. Read `src/hooks/_dispatch.py`, `src/hooks/_plan_paths.py`, `src/hooks/pre_plan_guard.py`, `src/hooks/pre_edit_guard.py`. Ran four empirical probes against the audit corpus and the local filesystem.

## Accepted aspects
1. **The problem is real.** Probe 4 confirms: 100% of the 3,542 `audit_only:no_plan_md` events come from project_dirs that today have plan files on disk somewhere reachable (either `thoughts/shared/plans/*.md` or root `PLAN.md`). The gate is genuinely missing plans the operator has authored. This is not a phantom audit-only count.
2. **Single source of truth in `_plan_paths.py`** is the right move. Right now `pre_plan_guard.py:42-46` and `_dispatch.py:197-217` disagree on what a plan file is; that's a code smell.
3. **Audit extras** (`plan_paths`, `plan_refs_count`, `plan_resolution_strategy`, `plan_window_days`) are correctly scoped to the plan-grounding gate's row and give downstream tooling what it needs to reason about the gate's decision.

## Rejected aspects

### 1. Acceptance criterion is misframed
The plan says "reduce `audit_only:no_plan_md` from 3,542 to under 100" via "offline replay." Engineering objection: **there is no offline replay path against the existing audit corpus.** The 3,542 events are already on disk; you cannot retroactively re-execute the gate against them. The right framing is forward-looking:
- For each top-10 audit-era project_dir that today has plans on disk, run a smoke test that exercises an Edit and assert the gate resolves at least one plan and emits `in_plan` or `plan_impl_drift` (not `no_plan_md`).
- For a synthetic project with no plan files, assert `no_plan_md` is still emitted.

The "under 100" target also implicitly assumes the operator's plans-on-disk state at acceptance time matches the audit-era state. It won't — repos move on. **Acceptance should be code-behavior assertions, not historical-data ratios.**

### 2. mtime-based recency is fragile
`git checkout`, `git stash pop`, branch switches, IDE reformatters, rsync, and time-machine restores all bump mtime. Concrete failure mode: operator switches from a feature branch back to main mid-session; old plans that haven't been touched in a month suddenly have today's mtime because git restored them. The "newest plan" resolver picks the wrong one. The plan does not address this.

Counter-proposal: prefer the date prefix in the filename (`YYYY-MM-DD-<slug>.md`) when present, fall back to mtime only when no date prefix exists. The user's own convention (verified in audit data: all 50+ unique plan paths follow `thoughts/shared/plans/YYYY-MM-DD-<slug>.md`) supports this.

### 3. Filesystem scan on every Edit is not addressed
Default behavior (no `RC_PLAN_GLOB`) walks `thoughts/shared/plans/*.md` plus root `PLAN.md` plus `*.plan.md` on every Edit/Write. Probe 1 shows `/Users/jakubsikora` has 28 historical plans; sikoras-chat has 9; reasoning-core has 9. That's bounded today but unbounded in growth: a repo that's been used for six months will have ~100+ plans. Per-Edit `os.listdir + stat × N` is a real cost for a hook that already lives on the latency tail (p95 3.6s, p99 5.3s).

Counter-proposal: cache resolved plan candidates per `session_id`, invalidate only on (a) session start, (b) explicit `rc cache-invalidate`, or (c) the gate seeing a Write to a path that matches the plan-shape pattern (operator just authored a new plan, refresh the cache).

### 4. Backup/tmp suffix UX gap (confirmed)
Probe 2 tested the existing `_PLAN_PATH_PATTERNS` against common backup/tmp suffixes:
- `thoughts/shared/plans/foo.md.bak` — no match
- `thoughts/shared/plans/foo.md.tmp` — no match
- `thoughts/shared/plans/foo.md~` — no match
- `PLAN.md.bak` — no match
- `foo.plan.md.tmp` — no match

Concrete failure mode: operator authors a plan via the atomic-write pattern `echo > PLAN.md.tmp && mv PLAN.md.tmp PLAN.md`. The intermediate `.tmp` write is NOT plan-shaped, so the gate's "plans never blocked" exemption does not apply, and the `.tmp` file's edit can be blocked by `plan_impl_drift`. The audit corpus already shows this pattern in use (`circit-app-evals-B-t7]: echo "x" > PLAN.md.tmp && mv PLAN.md.tmp PLAN.md && cat PLAN.md`).

Counter-proposal: extend `_PLAN_PATH_PATTERNS` with optional suffix `(?:\.(?:bak|tmp|swp))?~?$` for the exemption check only (the resolver still ignores them as candidates — only the "self-exempt" path expands).

### 5. Threat model: self-grant escalation
The current single-root-PLAN.md design has a weak but real property: the operator typically authors PLAN.md before the agent starts. The new resolver scans `thoughts/shared/plans/*.md` which the agent can write to (`pre_plan_guard.py` only emits warnings, not blocks, by default). Failure mode:

1. Agent decides it needs to edit `src/foo.py` and `src/bar.py`, neither of which is in any existing plan.
2. Agent runs `Write thoughts/shared/plans/2026-05-24-self-grant.md` listing src/foo.py and src/bar.py.
3. `pre_plan_guard` emits warnings (does not block by default).
4. Plan-grounding gate now picks up the new plan (mtime-newest), resolves it, and accepts the agent's edits as `in_plan`.

This is a self-grant. The single-PLAN.md design partially mitigates it because operators tend to author PLAN.md before agent run-time; the new resolver removes that protection without acknowledging the tradeoff.

Counter-proposal: at session start, snapshot the set of plan files that existed on disk and their hashes. Only treat plans from the session-start snapshot as authoritative; plans authored during the session are flagged in audit (`plan_authored_during_session=true`) and either declined or require `RC_PLAN_TRUST_NEW_PLANS=1` to be active for grounding decisions.

### 6. Env-var surface is too wide
Four new env vars (`RC_PLAN_PATH`, `RC_PLAN_GLOB`, `RC_PLAN_WINDOW_DAYS`, `RC_PLAN_MAX_FILES`) for one feature. Specific concerns:
- `RC_PLAN_PATH` and `RC_PLAN_GLOB` both set: precedence is in the plan ("RC_PLAN_PATH wins") but conflict-resolution audit semantics aren't documented. What gets logged in `plan_resolution_strategy`?
- `RC_PLAN_WINDOW_DAYS` and `RC_PLAN_MAX_FILES` together: if 5 plans modified in last 7 days, fine. If 6 plans modified in last 7 days, one gets silently dropped — which one? The plan says "sort by mtime desc, cap at N" but the audit row should explicitly log dropped candidates count.

Counter-proposal: collapse to two env vars (`RC_PLAN_PATH` for explicit override; `RC_PLAN_LIMIT` combining window/max). Internal defaults can stay separate.

### 7. Resolution-strategy vocabulary undefined
Plan says audit extras include `plan_resolution_strategy` but does not enumerate the values. From the plan's resolution-order list I count at least 6 distinct strategy values: `pinned_path`, `glob`, `default_root`, `default_plans_dir`, `mtime_stale_fallback`, `no_plan`. The plan must define this vocabulary or downstream aggregators will be looking at free-text strings.

### 8. `gate_id` scope inconsistency
Plan adds `gate_id="plan_grounding"` to one gate only. Six other gates remain without `gate_id`. From an engineer's perspective this is the worst of both worlds: a half-instrumented codebase makes "filter by gate_id" partially work, which trains downstream tooling to assume the field is populated when it usually isn't. Either commit to full plumbing or defer entirely until a separate plan addresses all gates.

Defensible counter-position: the plan's author can argue "land the plumbing pattern here, replicate to other gates in follow-up." That's fine, but the plan must explicitly state "follow-up issue tracks gate_id plumbing for other gates" and link it. The current "Out of scope" section doesn't do this.

### 9. Stale-fallback semantics need a UX surface
Probe 3 (median plan age 4d, mean 7.2d) shows the default 7-day window is right on the edge. reasoning-core's own most-recent plan is 15 days old — using the framework on its own repo, the resolver will fire `stale_fallback` from day one. The plan acknowledges stale fallback exists but doesn't say what the operator sees. Without `rc status` surfacing "I'm using a 15-day-old plan because nothing newer exists," operators won't know the gate's behavior has degraded.

Counter-proposal: `rc status` should show the resolved plan files and their ages; `decision=warn` should fire on the next Edit after stale-fallback engages, once per session.

## Risks not addressed
1. **Concurrent plan writes across host processes.** Two parallel Claude tool calls can each write a new plan in `thoughts/shared/plans/`; whichever wins mtime-newest is unpredictable. The resolver should make this deterministic (filename-date wins, falling back to lexicographic tiebreak).
2. **Symlinks in `thoughts/shared/plans/`.** If a plan is a symlink to an out-of-repo file, resolver behavior is undefined. Plan should pin: follow-symlinks or no?
3. **Case-insensitive filesystems (macOS HFS+/APFS default).** `PLAN.md` and `plan.md` resolve to the same file. The resolver's default scan list includes both `PLAN.md` and `*.plan.md` — on case-insensitive FS, `foo.PLAN.md` would match both patterns and be counted twice. Edge case, but defensive code in the resolver should de-duplicate by `os.path.realpath`.
4. **Plans referencing files via globs.** Some plans use `src/handlers/*.ts` notation. Current `_plan_paths.distinct_file_paths` does not glob-expand; the new resolver inherits that behavior. Plan should explicitly acknowledge globs in plan-text are NOT resolved, and edits to glob-matched files will still trigger `plan_impl_drift`.

## Counter-proposals (engineer's prioritization)
1. **Reframe acceptance criterion** as forward-looking code-behavior assertions, not historical replay.
2. **Snapshot plans at session start; flag mid-session-authored plans separately.** Mitigates the self-grant escalation path.
3. **Cache resolved candidates per session.** Avoids per-Edit filesystem scan.
4. **Prefer date-prefix over mtime when resolving "most recent."** Robust against branch-switching mtime noise.
5. **Extend exemption regex to cover .bak/.tmp/.swp/~ suffixes** for plan-shaped files only on the self-exempt check.
6. **Define `plan_resolution_strategy` vocabulary** explicitly in the plan.
7. **Either commit to full `gate_id` plumbing or defer this gate's `gate_id` to a follow-up.** Half-instrumentation is worse than either extreme.
8. **Add `rc status` surface for resolved plans + stale-fallback notice.**
9. **Collapse env-var surface from 4 to 2.**

## Bottom line
The plan correctly identifies a real, well-quantified problem (3,542 audit-only events, 100% from projects with plans on disk that the gate can't see). The proposed resolution direction is right. The execution details have four material gaps that should be addressed before implementation:
- mtime-based recency is fragile; use filename date prefix as primary signal.
- The self-grant escalation path needs an explicit mitigation (session-start snapshot).
- Acceptance criterion should not be "replay historical data" — it should be code-behavior assertions.
- The `gate_id` scope is internally inconsistent; pick one strategy and commit.

Land those four and the plan is shippable. As written, it ships a real improvement but with avoidable UX and threat-model debt.
