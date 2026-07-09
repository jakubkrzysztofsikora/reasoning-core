---
date: 2026-05-23
role: engineer-review
parent_report: thoughts/shared/research/2026-05-23-reasoning-core-effectiveness-audit.md
status: complete
tags: [adversary-review, architecture, reliability, observability, threat-model, reasoning-core]
---
# Adversary Review — AI Agent Harness Engineer Perspective

## Scope and posture
Reviewing the report from the perspective of someone who has built and operated production agent gating systems. Concerns are architecture soundness, reliability behavior under load, observability/instrumentation priority, threat model coverage, and integration UX. Read every hook file end-to-end plus `src/mcp_gate.py`, `src/hooks/_host_env.py`, and `src/sidecar_supervisor.py`. Posture is adversarial: report's prioritization is wrong in several places and I'll show where.

## Architecture concerns

### 1. The "fix gate_id first" prioritization is wrong
The report's top recommendation is plumbing `gate_id` through every hook. This is research instrumentation — it lets a future report do per-gate ablation. It does not change behavior on the next edit. The actual highest-leverage instrumentation fix is **`tokens_in` / `tokens_out` per session**, because that's the only field that proves or refutes the framework's commercial headline (`-8.2% tokens averaged`). Without it, every external reader of BENCHMARKS.md has to take it on faith. With it, every run produces evidence.

Re-prioritization (engineer's view):
1. Token usage capture — proves/refutes the commercial claim
2. Post-block follow-up capture — measures false positive rate (the report's #4)
3. Override survival capture — measures whether overrides are correct (the report's #6 in "what is not working")
4. `gate_id` — only matters if the above three exist; ablation without false-positive ground truth is statistical theater
5. `shadow_mode` per event — useful but lower leverage than the above

The report's #1 is my #4. The reason is simple: research instrumentation that doesn't change deployment decisions has lower leverage than instrumentation that closes a commercial-credibility gap.

### 2. The Tier-2 host design has a structural ambiguity the report didn't catch
`src/mcp_gate.py:80-162` (`gate_edit`) and `src/hooks/_host_env.py:52-66` (`host()`) interact in a way that's worth flagging. `gate_edit` calls `_host_label()` which returns the `_host_env.host()` value. If `_host_env.host()` returns `"unknown"` (because RC_HOST is unset and no `<HOST>_PROJECT_DIR` is exported), the event is written with `host="unknown"`. There is no operator-visible warning.

Meanwhile, `src/hooks/audit_log.py:144-150` has a different fallback: if `_host_env` cannot import at all (degraded environment), the host falls back to `"claude"`. **Two different code paths produce two different defaults for the same field.** An operator reading `host="claude"` cannot distinguish "this is Claude" from "this is a host where _host_env's import failed."

The report concluded "Tier-2 is empirically untested" based on 27 vibe events. The real conclusion is closer to: "Tier-2 attribution is structurally unreliable; we don't know how many of the 9,137 host-missing or 530 host-unknown events should have been Tier-2." Two failure modes (low usage vs lost attribution) are confounded in the audit data.

### 3. The supervisor design vs. the observed failure mix
The report flagged "1,235 HTTP 503 vs 446 connection-refused vs 79 timeouts." `src/sidecar_supervisor.py:106-146` is built to handle process death (restart with exponential backoff up to 8s) and circuit-break after 5 consecutive health failures with a 60s cooldown.

Observed failure mix:
- 1,235 HTTP 503: the sidecar is up and accepting connections but returning 503. Likely model-level load-shed under contention (mamba + gen sharing Apple Metal per the supervisor module comments).
- 446 connection-refused: the sidecar is genuinely dead. Process supervision territory.
- 79 timeouts: a request hit the s2 timeout (default 30s per `mcp_gate.py:71`). Could be model-stuck or queue-overflow.

The supervisor only addresses category 2. **Category 1 — 73% of the failures — is not a supervision problem.** It's a model-serving capacity problem. Worse: the supervisor's "5 consecutive health failures → 60s cooldown → terminate" loop (`sidecar_supervisor.py:131-145`) can interact pathologically with 503-under-load. If health probes also 503 during peak load, the supervisor concludes the process is bad and kills it — turning a transient overload into a 60-second outage.

This is the kind of "the watchdog made the outage worse" pattern that's well-documented in distributed systems literature. The fix isn't more supervision; it's:
- A token-bucket admission control on `/score` so the sidecar returns 503 fast under load instead of letting requests queue indefinitely
- Separate `/health` from `/score` so health probes never share a worker pool with scoring requests
- A request-priority lane for `/health` (in-process counter, no model call)

The report's recommendation #2 ("ship `rc latency-report`") is fine but misses the real ops gap: the sidecar's load behavior under concurrent traffic.

### 4. Override granularity is too coarse
`pre_bash_guard.py:307-361` treats `RC_ALLOW_GUARD_EDIT=1` as a global bypass that unlocks Layer A (hard-deny) AND Layer B (guarded-path-write) AND Layer C (kill-sidecar) AND Layer D (shell-write). An operator who wants to write a `.claude/settings.local.json` for a one-off install gets the kill-sidecar override as a side effect.

The audit corpus shows 38 + 31 + 4 + 4 = 77 events where the operator hit `RC_ALLOW_GUARD_EDIT=1`. We have no idea how many of those were the operator's actual intent (e.g., "allow this guard-path write") vs. accidental scope creep (e.g., the override happened to also unlock a kill-sidecar pattern that fired in the same shell).

Engineer recommendation, not in the report:
- Split into `RC_ALLOW_HARD_DENY=1`, `RC_ALLOW_GUARD_PATH=1`, `RC_ALLOW_SHELL_WRITE=1`, `RC_ALLOW_KILL_SIDECAR=1`.
- Keep `RC_ALLOW_GUARD_EDIT=1` as a compound that sets all four (for backward compat), with a deprecation warning.
- This is a 30-line change in `pre_bash_guard.py:_override_active`.

The report's "improve" section lists "decompose guard_file_locked blocks by intent" — that's about audit metadata, not about runtime override. The runtime override is a more important fix because it affects what operators can actually do, not just what they can later see.

### 5. The `_emit_audit` reserved-keys list is brittle
`src/hooks/pre_edit_guard.py:249-256` defines `_AUDIT_RESERVED_KEYS` as a frozenset of 19 keys. The intent (per the comment at line 314-318) is to prevent `audit_extra` from a future gate from colliding with reserved schema fields. The problem: every time someone adds a new field to `new_event()` (the report's recommendation #2 adds `shadow_mode`; #3 adds `embedder`/`rule_engine_enabled`), they have to remember to also add the field name here. There's no test that asserts the two lists are in sync.

Engineer recommendation: invert the design. Namespace `audit_extra` under `gate_extra` sub-dict so there is no collision possible. Then drop `_AUDIT_RESERVED_KEYS` entirely. This is a 5-minute refactor that removes a class of silent-failure bugs.

### 6. The retry-marker has a race condition
`audit_log._retry_marker_path` (line 292) returns `<audit_root>/<session_id>.last_block`. `_load_retry_markers` and `_save_retry_markers` read/write this file without locking. If two hooks fire concurrently within the same session — possible when Claude runs parallel tool calls — the marker file can be lost-update'd. The portalocker lock at `append_event:249-253` covers only the JSONL append, not the marker file.

In practice this is rare (parallel-tool-use is bounded) but the fix is trivial: take the same portalocker lock on the marker file. The report didn't surface this at all.

## Reliability concerns
Summarized above in section 3. Headlines:
- 73% of sidecar failures are HTTP 503 (load), not process death (which is what the supervisor handles)
- Supervisor circuit-break can turn transient overload into 60s outage
- No admission control on `/score`
- No health-vs-score separation

## Observability gaps (re-prioritized)
1. **Token usage** — commercial credibility gap.
2. **Post-block follow-up** — false positive rate.
3. **Override survival** — were overrides correct?
4. **Per-host counters** — for the `host="claude"` vs `host="unknown"` vs `host="(missing)"` distinction. Today operators cannot see this without writing custom queries.
5. **Sidecar `/score` queue depth** — exposes load before it becomes 503.
6. `gate_id` — research instrumentation, important but downstream of the above.

## Threat model gaps the report did not surface

### 1. Magic-comment self-introduction is incomplete
`pre_edit_guard.py:399-411` honors `# rc:skip` only from on-disk content (good) and detects agent-introduced directives via Write/Edit (also good). What about Bash heredocs that overwrite a file with a self-introduced directive? Layer L3-extension in `pre_bash_guard.py:299-305` catches the language mismatch, but if the heredoc is a same-language file (e.g., Python writing Python), the heredoc bypasses the magic-comment self-introduction check entirely. An agent could:
```
cat > target.py << 'EOF'
# rc:skip
<arbitrary code>
EOF
```
Layer D (shell-write) catches this in the current corpus (152 events of "shell-level source write detected"), so practically it's covered. But the coverage is incidental — if Layer D were ever relaxed, this becomes an exploit. The threat model should make the dependency explicit.

### 2. Override surface is observable but not auditable
The report noted overrides are logged but their outcome isn't. Engineer extension: the override path emits `decision=allowed_via_override` but does NOT compute or log the risk vector that the gate WOULD have produced. The shadow signal is lost. An override should still run the scorer (in shadow) so the audit row has the counterfactual.

### 3. Session manifest as a trust anchor
`_session_manifest.py` declares the language family for a session and `pre_bash_guard.py:299` uses it to enforce Layer L3-extension. If an agent can write to the manifest (e.g., via `session_start_manifest.py:session_start_manifest` write to .reasoning-core/), the agent can downgrade its own language guard. Did not see a guard preventing session-manifest writes by the agent itself. Worth checking.

## Integration UX concerns

### 1. The shadow-mode → enforce flip is undermeasured
README says "watch it for a few sessions, then flip `RC_SHADOW_MODE=0`." The operator has no tool to compare shadow-vs-enforce decisions to decide if they trust the flip. The report's recommendation to log `shadow_mode` per event is necessary but not sufficient; what's also needed is `rc shadow-diff` — a CLI that surfaces "in shadow mode, the gate would have blocked X edits; here are the 10 most-recent." Without that, "watch for a few sessions" is hand-waving.

### 2. Auto-scaffold PLAN.md — engineer's counter-attack on the report's recommendation
The report recommends auto-scaffolding PLAN.md when a repo is seen 3+ times without one. This is the gate authoring code that the gate then reviews — a clean conflict of interest. Counter-recommendation:
- Don't auto-scaffold; the gate should not write into the project.
- Add a `rc init-plan` CLI command that emits a scaffold to stdout and asks the operator to redirect (`rc init-plan > PLAN.md`).
- Surface "this repo has had N edits with no PLAN.md; the plan-grounding gate is disabled here" in `rc status` once per session.

This keeps the gate ergonomically advisory rather than directive.

### 3. The `audit_only` decision needs a name change
"audit_only" is the silent-no-op state. An operator seeing `decision=audit_only` in their logs has no idea this means "the plan-grounding gate is disabled because there's no PLAN.md." Rename to `decision=plan_grounding_disabled` and put the reason in the `reason` field. Same data, less guessing.

## Recommendations counter-prioritized (engineer's order)
1. **Add a token bucket / admission control to the sidecar `/score` endpoint.** Without it, the 73% of failures that are 503-under-load are unfixable. Highest reliability leverage on the board.
2. **Separate `/health` from `/score`.** Sub-issue of #1; ensure health checks never share workers with scoring requests.
3. **Log `tokens_in`/`tokens_out`/session.** Closes the commercial-claim gap.
4. **Decompose `RC_ALLOW_GUARD_EDIT=1` into four narrower overrides.** Operator UX win, ~30-line change.
5. **Capture post-block follow-up via `decision_id` joins.** Necessary for false-positive measurement (matches the report's #4 in "fix").
6. **Namespace `audit_extra` under `gate_extra`.** Removes the `_AUDIT_RESERVED_KEYS` brittle invariant.
7. **Add `rc shadow-diff` CLI.** Makes the shadow→enforce flip defensible.
8. **Rename `audit_only` to `plan_grounding_disabled`.** Cheap UX win.
9. **Lock the retry-marker write.** Trivial race fix.
10. **Plumb `gate_id`.** Useful for research; downstream of everything above.

The report's order had `gate_id` at #1. I have it at #10. The disagreement is over what counts as "the work that moves the framework forward." The report optimizes for research clarity; an engineer running this in production optimizes for not having outages and not getting called out on commercial claims.

## Bottom line
The report's data work is solid. Its prioritization is research-flavored, not ops-flavored. The most important missing analysis is the sidecar's load behavior (the supervisor handles only ~25% of the observed failure mode), and the most important missing recommendation is admission control on `/score`. The override-granularity fix is a sleeper — small change, big UX win, and the audit data already proves it's exercised in the wild (77 events). The `gate_id` fix is fine but should not be the headline; the headline should be "fix what's breaking production today."
