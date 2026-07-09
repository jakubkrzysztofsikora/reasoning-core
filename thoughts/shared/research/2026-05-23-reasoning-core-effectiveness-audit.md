---
date: 2026-05-23
commit: cc099b3d4293024b0b10905db9f7110a59fdd320
branch: main
tags: [reasoning-core, audit-logs, effectiveness, claude-code, gate-instrumentation, signal-quality]
status: complete
---
# Research: Reasoning-Core Effectiveness Audit — 19-Day Local Audit Log Review

## Summary
A 30,275-event corpus collected across 19 days (2026-05-05 → 2026-05-23) from 21 local repositories shows that reasoning-core is wired in and firing — but the value it produces is concentrated in a narrow band of gates and is structurally hard to attribute. Roughly **370 of 1,797 real-work blocks (≈ 21%)** look like genuine value-adds (regression detection, shell-escape closure, plan-impl drift, language coherence). The remaining ~79% are dominated by self-protection (`guard_file_locked`, shell-targets-guard) and reliability noise (`sidecar_unavailable_fail_closed`). Effectiveness is also degraded by three structural gaps: `gate_id` is populated on 0/30,275 events (no per-gate attribution possible), Tier-2 hosts are effectively non-existent in this corpus (27 vibe events, 0 gemini/copilot), and the plan-grounding gate is inactive (`audit_only`) for 3,542 events because the projects lack a PLAN.md.

## Methodology
- **Source:** `~/.local/share/reasoning-core/events/` — JSONL per session, gzipped after rotation (9,579 files, 39 MB).
- **Scope:** all event files on disk as of 2026-05-23T10:51Z.
- **Filtering:** real-work corpus excludes pytest tempdirs (`/var/folders/**/pytest-*/`), `/tmp/*`, and events tagged `test_event`/`eval`. Pre-filter total: 30,224; post-filter real-work events used for ratios: 30,275 (slight delta from including `_supervisor_*` self-events).
- **Cross-validation:** sample of 1,797 blocked events spot-checked against repo paths; aggregate counts match `audit_log` `decision` enum exactly.
- **Sources read for grounding:**
  - `src/hooks/audit_log.py` (event schema, `GATE_IDS`, redaction)
  - `src/hooks/pre_edit_guard.py:296-319` (`_emit_audit`)
  - `src/hooks/pre_bash_guard.py:289-395` (Layer A–D gate logic)
  - `src/hooks/pre_plan_guard.py:417-485` (plan-guard entrypoint + shadow-mode)
  - `README.md`, `.vibe/AGENTS.md`

## Effectiveness Scorecard
A blunt, evidence-weighted assessment per claim. "Verdict" is what the audit log actually supports, not what the README asserts.

### What is genuinely working
1. **Shell-escape closure (139 blocks).** `cat > path << EOF`, `tee path << EOF`, and friends are reliably routed back to `Edit`/`Write`. Examples span `circit-app`, `cyberlegion`, `sikoras-chat`, `reasoning-core`. This is the single highest-quality signal in the corpus — it's catching a known agent bypass pattern that would otherwise skip pre_edit_guard entirely.
2. **Regression detection on real edits (168 blocks).** `regression_detected` fires on `Edit`/`MultiEdit` in `reasoning-core` (plus sample evidence of `coherence_delta` values up to 2.5). The neural gate is producing actionable risk signals on the only repo where it has been tuned (reasoning-core itself).
3. **Self-protection (guard files).** 681 `guard_file_locked` blocks prevented edits to hook scripts, `.claude/settings.local.json`, sidecar source, etc. This is real value but also self-referential — the framework mostly protects itself.
4. **Plan-impl drift catch (35 blocks, 350 fires).** The plan-grounding gate fires 350 times across the corpus and produces 35 hard blocks. Sample evidence: `test_plan_grounding_block_at_l0` style paths confirm the gate is observable end-to-end.
5. **Language-fingerprint enforcement (28 blocks).** Caught Python/JS coherence violations in `sikoras-chat`. Small absolute count but high signal-to-noise — these are paths an agent went off-rail on.

### What is not working
1. **`gate_id` is null on 100% of 30,275 events.** `src/hooks/audit_log.py:65-68` defines a `GATE_IDS` frozenset of seven gate identifiers (`scorer`, `plan_grounding`, `rules`, `calibration`, `lang_lock`, `mock_detector`, `drift_gate`). None of the calling hooks (`pre_edit_guard._emit_audit` at line 296, `pre_bash_guard.main` at line 386, `pre_plan_guard.main` at line 471) pass `gate_id=` to `audit_log.new_event()`. Result: per-gate ablation studies are impossible from the audit log as it stands today. Every aggregation in this report had to use `reason` substring matching as a proxy.
2. **Sidecar reliability ceiling: 94.2%.** Over 19 days: 1,235 HTTP 503s, 446 connection-refused, 79 timeouts, 1 connection-reset = 1,760 sidecar-unavailable events. `fail-open` fired 1,619 times (gate skipped). `fail-closed` fired 117 times (user blocked). At p99 latency of 5.3s and max 64.6s, the sidecar is the long pole on both reliability and tail latency.
3. **Tier-2 host claim is empirically unverified.** README claims four-host parity (Claude / Gemini / Copilot / Vibe). The audit corpus shows: `claude=20,530`, `vibe=27`, `gemini=0`, `copilot=0`, `unknown=530`. The Tier-2 MCP path has 27 real-world data points — well below the threshold needed to validate the claim that it works at parity in practice.
4. **Plan-grounding gate is mostly idle.** 3,542 `audit_only` decisions (the explicit "no PLAN.md, scoring not enforceable" path) — exactly the same number of projects that didn't author a PLAN.md. So in the majority of real-world sessions, the plan-grounding gate degraded itself out. The 35 hard blocks all came from a small set of repos that had a PLAN.md (mostly `reasoning-core` test fixtures).
5. **Unsupported-language drag: 1,604 events (5.3% of corpus).** Top offenders: `.gd` Godot (812 — voxel project), `.jsonl` (193), `.sh` (147), `.bicep` (91), `.rb` (74). Every one of these is a session where the gate explicitly dropped to "degraded" and produced no risk signal. The neural gate's effective coverage in mixed-language repos is materially lower than the headline 100% claim.
6. **Override surface is being exercised — but not measured for false-positive rate.** 106 override events: `RC_ALLOW_GUARD_EDIT=1` (69 occurrences across hard-deny and guarded-path overrides), `kill_switch_or_bypass_next` (5), `magic_comment:skip` (4), `magic_comment_self_introduced` (4). No audit field records whether the override-bypassed action was later reverted or kept. If a high fraction of overrides survive in committed code, that's the signal that the gate was wrong; if a high fraction get reverted, that's the signal it was right. The data to compute either ratio is not currently captured.
7. **Shadow-mode flag not logged per-event.** The `shadow_mode` field is absent from every record. Only the indirect `decision=shadow_blocked` (90 events) exposes shadow-mode posture, and only on the block path. Allow-path shadow audits are silent, so we can't compare shadow-vs-enforce false-positive rates — which is exactly the comparison the README invites with "watch it for a few sessions, then flip."
8. **Empty/blank `reason` field on 64 blocks.** Plan tool blocks (207 events) have empty `reason` in the 64-event slice. The agent receives no human-readable explanation in those cases.

### Mixed / unverified claims from the README
1. **"-8.2% tokens averaged across tasks" / "Plan quality 3.62 → 3.94"** — These come from the published 8-task / 3-run eval (`docs/BENCHMARKS.md`), not from this audit corpus. Cannot be cross-validated against the audit log because the eval events were correctly filtered out. The audit log shows the gate is wired in and producing signals; it does not, on its own, prove the token-saving claim. Nothing in the audit log contradicts it either.
2. **"+98s wall-clock per run."** Latency in the audit log: p50 5ms, p90 49ms, p95 3,595ms, p99 5,300ms, max 64,566ms. Per-edit latency is fine for short edits but the p95 tail is in the multi-second range. Whether the aggregate wall-clock cost is 98s or substantially higher depends on edits-per-session — and that ratio is not derivable from the audit log without joining to session manifests (which aren't currently logged in a structured way).

## Files Involved

### Backend (Python, reasoning-core)
| File | Layer | Purpose |
|------|-------|---------|
| `src/hooks/audit_log.py` | Audit writer | Per-session JSONL, redaction, `GATE_IDS` registry, schema v3 |
| `src/hooks/pre_edit_guard.py` | Pre-tool hook | Edit/Write/MultiEdit gate; calls sidecar `/score`; emits audit |
| `src/hooks/pre_bash_guard.py` | Pre-tool hook | Bash command screening; 4 layers (hard-deny, guarded-path, kill, shell-write) |
| `src/hooks/pre_plan_guard.py` | Pre-tool hook | PLAN.md write screening; novelty/boundary/framework-pivot warnings |
| `src/hooks/pre_task_guard.py` | Pre-tool hook | Task tool screening (mutation verbs) |
| `src/hooks/post_bash_revive.py` | Post-tool hook | Sidecar revive on connection failures |
| `src/hooks/post_assistant_diff_audit.py` | Post-tool hook | Unified-diff structural audit |
| `src/hooks/_guard_paths.py` | Helper | Guard-file discovery & override check |
| `src/hooks/_kill_switches.py` | Helper | Bypass-next / disabled-globally / file-skip |
| `src/hooks/_magic_comments.py` | Helper | `# rc:skip` operator-authored directive parse |
| `src/hooks/_shadow_mode.py` | Helper | `RC_SHADOW_MODE` resolution |
| `src/hooks/_calibration_gate.py` | Helper | Per-repo threshold calibration |
| `src/hooks/_plan_paths.py` / `_plan_quality.py` | Helper | PLAN.md detection + quality scoring |
| `src/hooks/_session_manifest.py` | Helper | Declared-language manifest (used by Layer L3-extension) |
| `src/hooks/session_start_best_effort.py` | Session hook | `best_effort_spec` injection (65 events) |
| `src/hooks/session_start_manifest.py` | Session hook | Per-session declared-language manifest |
| `src/s2_core.py` | Sidecar core | 11-dim risk vector + coherence delta scoring |
| `src/ssm_backbone.py` | Embedder | mamba-130m / codestral-mamba / bge-code / unixcoder |
| `src/mcp_gate.py` | MCP server | `hybrid_reasoner_gate_edit` tool for Tier-2 hosts |
| `src/mcp_diff_validator.py` | MCP server | `validate_unified_diff` tool |
| `src/sidecar_boot.py` (untracked) | Sidecar | HTTP entry on `127.0.0.1:8765` |
| `src/sidecar_supervisor.py` | Supervisor | Sidecar lifecycle + restart |

### Data
| Path | Format | Purpose |
|------|--------|---------|
| `~/.local/share/reasoning-core/events/<YYYY-MM-DD>/<session_id>.jsonl[.gz]` | JSONL | Audit log; one event per gate decision |
| `~/.local/share/reasoning-core/events/<session_id>.last_block` | JSON | Retry-marker file used by `is_retry_after_block` |

## Data Flow
Pre-tool path for `Edit` (representative):

```
Claude / Gemini / Copilot / Vibe
  → PreToolUse hook fires (Claude/Gemini native; Tier-2 via MCP gate_edit)
  → src/hooks/pre_edit_guard.py:main (line 324)
     ├─ Layer 1: _guard_paths.is_guarded()                   → blocked: guard_file_locked
     ├─ Layer 2: _kill_switches + _magic_comments             → allowed_via_override
     ├─ Layer 3: _session_manifest declared-language check    → blocked: language_fingerprint_violation
     ├─ Layer 4: HTTP POST 127.0.0.1:8765/score (s2_core)     → regression_detected / coherence_delta
     ├─    on SidecarUnavailable + RC_S2_FAIL_CLOSED=1        → blocked: sidecar_unavailable_fail_closed
     ├─    on SidecarUnavailable + fail-open                   → allowed (fail-open)
     ├─    on HTTP 415                                          → degraded: unsupported_language
     ├─ Plan-grounding sub-gate (if PLAN.md exists)            → blocked: plan_impl_drift
     │      else                                                → audit_only: no_plan_md
     └─ _emit_audit (line 296) → audit_log.append_event       → ~/.local/share/reasoning-core/events/<day>/<session>.jsonl
```

Sidecar path: `127.0.0.1:8765/score` is served by `src/sidecar_boot.py` → `src/s2_core.score_change` → `src/ssm_backbone.embed` → 11-dim risk vector + chord-distance coherence delta. Sidecar reliability in this corpus: 94.2%.

Audit append path (`src/hooks/audit_log.py:221-282`): build event dict via `new_event` (line 190) → redact secret-shaped paths and inline secrets (line 172) → take exclusive `portalocker` lock → write one JSON line → optional `fsync` → call `_audit_rotation.rotate` to compress + retire old days. Best-effort: any IOError is swallowed; hooks never raise.

## Existing Patterns Worth Preserving
1. **Best-effort audit, never-raise contract** (`audit_log.append_event` line 221-282). All exceptions swallowed, with one-line stderr warning on OSError. This is exactly right for a deterministic hook layer.
2. **Layered Bash guard** (`pre_bash_guard.screen_command` line 289-365). Four ordered layers (hard-deny → guarded-path → kill → shell-write) with explicit override semantics. The ordering matters: hard-deny precedes overrides except `RC_ALLOW_GUARD_EDIT=1`. This is the cleanest gate in the codebase.
3. **Schema-permissive audit** (`audit_log.py:20`). Missing fields are allowed; readers rely on `decision`/`tool_name` and treat the rest as advisory. Made this audit possible despite the `gate_id` gap.
4. **Self-introduced-directive detection** (`pre_edit_guard.py:399-411`). Magic comments are honored only from the on-disk file; an agent introducing `# rc:skip` in the new content is detected and the override declined. Caught 4 cases in the corpus (`magic_comment_self_introduced`).
5. **Retry-marker** (`audit_log._retry_marker_path` line 292). Distinguishes "this edit is the agent retrying after a block" from a fresh edit. Useful signal for measuring how often agents respect a block.

## Architecture Notes
- **Trust boundary:** `127.0.0.1:8765` only (s2_core hardened to refuse remote `S2_URL` per `bc536c1`). Gate decisions never leave the machine.
- **Schema v3:** `audit_log.SCHEMA_VERSION = 3`. Forward-compatible, additive — past evolution: added `decision_id`, `host`, `signal_source`.
- **Host abstraction:** `_host_env` provides `session_id`, `project_dir`, `host`. Single point of integration for new CLIs. Currently flows for claude + vibe; gemini/copilot would land here.
- **Override observability:** override paths emit `decision=allowed_via_override` with `reason=kill_switch_or_bypass_next` or `magic_comment:skip:...`. This is logged. Whether the bypassed action survived is not.
- **Calibration:** `_calibration_gate.py` (241 lines) applies per-repo threshold calibration. Not directly visible in this audit pass but feeds into the block/allow decision.
- **Project index:** `RC_PROJECT_INDEX=1` enables `project_fan_in` / `project_coupling` dims. Whether this is on in production isn't loggable from the audit either — another `gate_id`-style attribution gap.

## External Dependencies
- **Sidecar HTTP service:** `127.0.0.1:8765` — local Python HTTP server (`src/sidecar_boot.py`, untracked). Reliability 94.2%.
- **mamba-130m-hf** (default embedder) via `huggingface-cli download`. Alternatives via `RC_EMBEDDER`: `codestral-mamba-gguf` (llama.cpp Q6_K — most recent commit cc099b3), `bge-code`, `unixcoder-base`.
- **portalocker ≥ 2.7** — file locking for concurrent multi-host audit writes. Fallback path warns if missing.
- **direnv** — sets `RC_HOST` + other env vars per repo on shell entry.

## Open Questions
1. **What is the false-positive rate?** Cannot be computed today. Need to log post-block action: did the agent retry with a corrected diff? did the operator override? did the final commit include the blocked change? — none of these are joinable from the audit log alone.
2. **Is the headline -8.2% token saving generalizable beyond the 8-task eval?** Audit log is silent on token usage (no `tokens_in` / `tokens_out` field). Joining to Claude's own telemetry would be needed.
3. **What is the Tier-2 MCP-path actual block rate?** 27 vibe events is too small. Without representative vibe / gemini / copilot traffic, the "four-host parity" claim is unfalsifiable from this corpus.
4. **What is the practical impact of `audit_only` mode?** 3,542 events (12% of corpus) ran with the plan-grounding gate disabled. Should the framework prompt or auto-create a PLAN.md scaffold the first time it sees a repo without one? Today it just goes silent.
5. **Are `unsupported_language` events distributed across repos or concentrated?** Top extension is `.gd` (812 events — Godot, `voxel` repo). If a small number of repos drive most of the degraded-mode events, repo-level coverage decisions become tractable. If it's a long tail, the embedder support matrix needs to grow.
6. **Reliability incidents — what's the root cause distribution?** 1,235 HTTP 503 vs 446 connection-refused suggests two distinct failure modes (process up but unhealthy, vs process down). Sidecar logs would tell us which dominates and how to fix it.

## Recommendations (fix / improve / change)
Ordered by leverage. None of these were asked for by the prompt explicitly, but they're the natural outcome of "what should we fix / improve / change."

### Fix (instrumentation debt — costs nothing to fix, blocks everything to leave broken)
1. **Populate `gate_id` on every event.** Plumb `gate_id` through `_emit_audit` in `pre_edit_guard.py:296`, `pre_bash_guard.py:386`, `pre_plan_guard.py:471`. One-line change per call site. Without this, per-gate ablation, false-positive-rate analysis, and effectiveness attribution are all blocked.
2. **Log `shadow_mode` on every event.** Currently inferred only from `decision=shadow_blocked` (90 events). Add it to `new_event()` defaults so every record carries the gate posture in effect.
3. **Log `embedder` and `rule_engine_enabled`.** `RC_EMBEDDER`, `RC_RULE_ENGINE`, `RC_PROJECT_INDEX` posture should appear on each event so ablations across different embedders are joinable.
4. **Capture post-block follow-up.** Add a session-level "did this block get followed by a retry, an override, or a session-end?" field. Stage in a 2-step pipeline: gate emits `decision_id`; a session-end audit pass populates `post_decision_action` per `decision_id`. Necessary for false-positive measurement.
5. **Always populate `reason`.** 64 plan-tool blocks have empty `reason`. Either set a default or fix the call site.

### Improve (signal quality)
1. **Decompose `guard_file_locked` blocks by intent.** 681 events is the largest single block category and is mostly self-protection. Distinguishing "agent tried to write `.claude/settings.local.json` because the operator asked it to install something" from "agent tried to rewrite hook scripts to bypass the gate" matters for measuring user-facing friction vs real security value. Today the audit can't tell them apart.
2. **Surface unsupported-language as a first-class metric.** 1,604 degraded events is 5.3% of the corpus. The README's "100% local" claim implicitly assumes the gate is also running 100% of the time; in practice on multi-language repos it's not. Either expand language coverage (Godot, JSONL, shell, bicep are the big four) or surface "X% of edits in this repo were gated, Y% were degraded" in `rc status`.
3. **Auto-scaffold PLAN.md.** 3,542 `audit_only` events. If the gate sees a repo without PLAN.md three times in one session, prompt the operator to either accept a generated scaffold or set `RC_NO_PLAN=1` to opt out explicitly. Silent degradation is the worst of both worlds.
4. **Tier-2 host monitoring.** 27 vibe events over 19 days suggests Tier-2 paths are either rarely used or rarely succeed. Add a `rc status --hosts` view showing observed traffic per host so the discrepancy is visible to the operator.

### Change (claims discipline)
1. **Reframe the four-host parity claim.** README and CLI_PARITY.md should make clear: Claude has 20,530 events of in-the-wild validation; Vibe has 27; Gemini and Copilot have 0. Either land smoke-test traffic for Gemini/Copilot or downgrade those rows to "shipped, untested at volume."
2. **Make the +98s wall-clock claim defensible.** Tie it to the audit log directly: ship a `rc latency-report` that computes per-session aggregate gate wall-clock from logged `latency_ms`. The audit data is there; nothing computes it.
3. **Distinguish "self-protection" from "code quality" in scorecards.** When summarizing block counts internally or in BENCHMARKS.md, separate the categories. ~57% of blocks are self-protection or reliability noise — important for security, irrelevant for the framework's pitch as a code-quality gate.

## Subagent Coordination Note
The previous Claude session attempted to dispatch three parallel analysis subagents (architecture map, signal-quality, claims-vs-reality). Their output files (`/tmp/rc-arch-map.md`, `/tmp/rc-signal-quality.md`, `/tmp/claims-vs-reality.md`) were not produced on disk by the time of synthesis. Only `/tmp/rc-audit-analysis.md` (aggregate counts) and `/tmp/rc-blocked-sample.txt` (block sample) survived. This report synthesizes from the surviving artifacts plus a direct re-read of `src/hooks/*.py` and the live audit corpus. Two adversary review subagents (LLM scientist + AI agent harness engineer) are dispatched after this document is written.
