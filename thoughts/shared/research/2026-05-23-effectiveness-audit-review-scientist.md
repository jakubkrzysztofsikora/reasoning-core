---
date: 2026-05-23
role: scientist-review
parent_report: thoughts/shared/research/2026-05-23-reasoning-core-effectiveness-audit.md
status: complete
tags: [adversary-review, methodology, signal-validity, reasoning-core]
---
# Adversary Review — LLM Scientist Perspective

## Scope and posture
Reviewing the audit report against the same corpus (~/.local/share/reasoning-core/events/, 9579 files, 30275 real-work events after filtering). Re-ran every quantitative claim with independent code. Posture is adversarial: validate nothing without re-computation; reject anything that doesn't survive a counter-test.

## Accepted claims (survived re-computation)

### 1. Corpus size and filter robustness — accepted
Re-derived three filter variants and they converge:
- Variant A (drop `test_event=true` only): 30,275 events
- Variant B (the report's filter — drop `test_event`, `eval`, pytest tempdirs, `/var/folders`, `/tmp/`): 30,275 events
- Variant C (B plus drop any record containing `iter1`/`iter2`/`iter3`/`swebench` substrings): 30,019 events

The 256-event delta between B and C is small enough that the report's headline 30,275 is robust. The filter wasn't load-bearing.

### 2. `gate_id` 0% — accepted, with one nuance
0 of 30,275 events carry a `gate_id` key. Re-grepped the hook entrypoints (`pre_edit_guard.py:296`, `pre_bash_guard.py:386`, `pre_plan_guard.py:471`, `post_bash_revive.py:133`, `session_start_best_effort.py:94`, `pre_task_guard.py:227,255`) — none pass `gate_id=` to `new_event()`. The report's smoking-gun is real and the proximate fix (one-line per call site) is correct. Nuance: `pre_task_guard.py` has two call sites, not one; the fix-count in the report's recommendation should be six call sites, not five.

### 3. The 8 unsupported-language extension list — accepted
.gd / .jsonl / .sh / .bicep / .rb / .tscn / .tmpl / .txt / .csproj match my recount within rounding. The framing "5.3% of corpus is degraded" is well-supported. The downstream interpretation that 812 of those are Godot files from one repo (voxel) is also correct.

## Rejected claims (did not survive re-computation)

### 1. "Shell-escape closure is the single highest-quality signal" — REJECTED
The report ranks 139 shell-escape blocks at the top of the "what is genuinely working" list. I drew a random sample of 20 (seed=42) and judged each. Counter-evidence:

Likely **false positives** (legitimate operator workflow, not agent bypass):
- `[circit-app] mkdir -p /tmp/waf-33115 && az network front-door waf-policy rule list --policy-name CoreWafPolicyNonProd ... -o json > /tmp/waf-33115/nonprod-before.json` — Azure CLI output redirect to `/tmp/`. Not a source-code write.
- `[circit-app-evals-B-t7] cat > PLAN.md << 'PLAN_EOF'` — operator-driven plan authoring via heredoc. This is the standard way to write a multi-line markdown file from bash. Block is gratuitous.
- `[poc] cat > /Users/jakubsikora/Repos/circit/poc/thoughts/shared/plans/2026-05-23-fix-circitron-failing-tests.md <<'PLANEOF'` — same pattern, plan file authoring.
- `[choyce-engine] git commit -m "$(cat <<'EOF' ... EOF)"` — heredoc inside command substitution for a commit message. Not a source write.
- `[circit-global-infrastructure] cat > /tmp/finops_detailed_report.md << 'EOF'` — temp report file. Not source.
- `[circit-app-evals-B-t7] echo "x" > PLAN.md.tmp && mv PLAN.md.tmp PLAN.md && cat PLAN.md` — atomic write pattern for a plan file. Blocked.

Likely **true positives** (legitimate gate value):
- `[circit-app-evals-B-t8] sed -i '' 's/providerType:/providerCategory:/g' ... .ctest.ts` — in-place source edit via sed that bypasses pre_edit_guard. This is exactly the bypass the gate should catch.
- `[cyberlegion] sed -i '' '75 a\\ ROUND_QUERY_URL...' config/ocf26_training.py` — same pattern, in-place source edit via sed. True bypass.
- `[choyce-engine] git commit -m ...` (when the diff under commit had skipped the gate) — borderline; depends on intent.
- Test fixtures (`tee bar.ts <<EOF`, `cat <<EOF > new.cs`, `node -e "...writeFileSync..."`) — high-quality catches of source-write bypasses in synthetic-test repo paths.

Conservative estimate: **at least 7 of 20 (35%) sampled shell-escape blocks are false positives on legitimate `/tmp/`, plan-file, or command-substitution workflows.** If this generalizes, the "139 blocks" figure has ~85-90 true positives and 50+ false positives. The report's framing materially overstates signal quality.

The report's specific claim that this is the "single highest-quality signal" needs to be downgraded. The shell-escape gate has a precision problem on temp files and plan files. Either narrow the pattern to exclude `/tmp/*` and `**/thoughts/shared/plans/*.md`, or stop calling it the highest-quality signal.

### 2. Host attribution numbers — REJECTED (off by ~9,000 events)
The report claims `claude=20530, vibe=27, gemini=0, copilot=0, unknown=530`. Recount under filter B yields:
- `claude`: 20,581
- `(host key entirely missing)`: 9,137
- `unknown`: 530
- `vibe`: 27

**The report missed 9,137 events (30.2% of the corpus) where the `host` key is not present at all** — distinct from `host="unknown"`. These are events written before the `host` field was added to `audit_log.SCHEMA_VERSION = 3`, or by call sites that bypass `new_event()`. The actual unattributed share is **32% (9,667 of 30,275)**, not 1.8%.

When we restrict to events that have a `host` field, the claude/vibe ratio is 20,581 / 27 = 762× — even more skewed than the report claimed. The Tier-2 underuse story holds, but the magnitude is different. More importantly: **the schema-evolution story the report told ("host abstraction in `_host_env` is the single integration point for new CLIs") is undercut by the existence of nine thousand records with no host attribution at all.** Either old code paths are not flowing through `_host_env`, or there's a schema-migration step that was never run.

This is the most impactful correction in this review.

### 3. "94.2% reliability" — REJECTED as misleading framing
Recomputed two alternative weightings:
- **Event-weighted (the report's number):** 1,736 sidecar-fail-or-fail-open events / 30,275 → 94.2% per-event reliability.
- **Edit-weighted (only events where the sidecar was actually called):** 1,736 / 11,018 edit-tool events → 84.3% per-edit reliability.
- **Session-weighted:** 194 sessions with at least one failure / 8,786 total sessions → **97.79% per-session reliability**.

These three numbers say very different things. Event-weighted dilutes the failure rate with read-only audit events that never touched the sidecar. Edit-weighted is the operator-relevant ratio: about 1 in 6 edits hits a sidecar issue. Session-weighted shows failures cluster: 2.2% of sessions account for the bulk of the failure events.

The report quoted only the most-flattering number (94.2%) without showing the others. That's not wrong arithmetic but it is bad framing for a report that promised to be "super honest, objective."

## Unverifiable claims (insufficient data)

### 1. "370 of 1797 real-work blocks (~21%) are genuine value-adds"
The 370 figure sums up category counts (regression 168 + shell-escape 139 + plan-grounding 35 + lang-coherence 28). My sample shows the shell-escape category overcounts true positives by ~35%, so the real "genuine value-add" count is more like 320-330, not 370. But without per-block label data (was this block followed by a retry? was the override-bypassed action reverted?), we can't compute the true precision of any category. The report acknowledges this gap in its Open Questions; the gap itself is real and the recommendation to log `post_decision_action` per `decision_id` is correct.

### 2. The README's "-8.2% tokens" claim
The audit log has no `tokens_in` / `tokens_out` field, so the headline claim cannot be cross-validated against this corpus. The report acknowledges this; correct posture.

### 3. The "+98s wall-clock per run" claim
Audit log has `latency_ms` per event, which lets us bound per-edit latency (p99 5.3s, max 64.6s). Aggregating to per-run requires joining edits to runs, which requires a stable run-id field that doesn't exist. The report's recommendation to ship `rc latency-report` is the right shape, but pending instrumentation it remains unverifiable.

## Confounders the report missed

### 1. Eval events leaked through the filter
The `audit_only` event distribution by project name shows 116 in `reasoning-core` itself and 490 in `ai-control-room`. Many of the 9,137 host-missing events have project_dir under `circit-app-evals-B-*` (1,483 + 666 + 480 + 430 + 409 + 381 + 265 = 4,114 events from `circit-app-evals-B-*` repos that look like worktrees of eval runs). These almost certainly are eval-context events that escaped the `eval=True` filter because the call site never set the tag. **Several thousand "real-work" events in this corpus may actually be eval traffic** — meaning the audit corpus is contaminated. The headline ratios (1797 blocks / 30275 events) might be off by 10-15%.

Counter-test the report should run before finalizing any decisions: re-aggregate after dropping `**/circit-app-evals-B-*` paths and see how the numbers shift.

### 2. The `host="claude"` default in `audit_log._host_label`
`audit_log.py:144-150` falls back to `host="claude"` if `_host_env` cannot import. That means: any failed import (degraded environment, broken sys.path, partial install) silently misattributes traffic to Claude. The report should not interpret `host="claude"` as evidence that Claude is the dominant host — it might be partially driven by silent fallback. This is a methodological hazard the report did not surface.

### 3. Sample bias by top-4 repos
Top 4 projects (`choyce-engine` 5021, `sikoras-chat` 4825, `reasoning-core` 3701, `circit-app` 2348) account for 15,895 events — **52.5% of the corpus**. Any aggregate ratio (block rate, fail-open rate, latency percentile) is dominated by these four. The report's claim "the framework works across 21 repos" is technically true but the data quality argument is "the framework was tested at volume in 4 repos."

This matters for generalization: the audit corpus is not a uniform sample of repos.

### 4. The retry-marker is not joined to subsequent events
The report mentions `_retry_marker_path` as a useful signal but doesn't actually use it. If we joined block events to subsequent edit events via the marker file, we could compute the retry-rate empirically. This is the single highest-leverage analysis missing from the report.

## Recommended additional evidence
1. **Per-block labeling sample.** Pull 100 random blocks across the 1,797, manually classify each as TP/FP/borderline. With 35% FP rate visible in the shell-escape sub-sample, the bootstrap confidence interval on overall precision is too wide to make policy decisions today.
2. **Decision-id join.** Add a join between block events and the next event with the same `session_id` and `file_path`. Compute: what % of blocks are followed by a retry? what % by an override? what % by a session end?
3. **Re-run the filter without B-evals paths.** Validate whether the headline ratios are stable after excluding the contamination.
4. **Separate the cohort by `host` populated vs missing.** The 9,137 host-missing events are a distinct cohort and need separate analysis.
5. **Edit-weighted reliability is the operator-facing number.** Whatever final number the report publishes, use edit-weighted (84.3%) not event-weighted (94.2%). The latter inflates the gate's apparent reliability by including read-only events that never touched the sidecar.

## Bottom line
The report is broadly directionally correct but has three quantitative defects: (a) the shell-escape category has materially lower precision than claimed (~35% FP in random sample), (b) host attribution gap is 32% not 1.8% (~9k events with no host field), and (c) reliability framing should use edit-weighted (84.3%) as the operator-facing number, not event-weighted (94.2%). The qualitative conclusions about instrumentation debt and Tier-2 underuse hold and are if anything stronger than the report stated.
