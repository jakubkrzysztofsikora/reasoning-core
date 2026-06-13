---
date: 2026-06-13
commit: 5bb9fce
branch: main
tags: [reasoning-core, effectiveness, audit-logs, rc-cli, transcripts, monitoring, north-star-metric]
status: complete
---

# Research: Reasoning-Core Efficiency — Transcripts, Audit Logs, and `rc` CLI

## Summary

Reasoning-core measures and reports its own effectiveness through three interconnected surfaces: (1) **structured JSONL audit logs** written by every hook invocation, (2) **transcript parsers** that extract agent diffs from Claude Code session files, and (3) the **`rc` CLI** (`bin/rc`) that aggregates audit events into operator-visible metrics. The `rc reasoning-efficiency` subcommand (shipped 2026-06-01) computes a composite north-star metric from the audit log: `(drift_caught - false_drifts) / (gate_wall_clock_s + 1) * repo_idiom_delta_norm * (1 - sidecar_unavailability_rate)`. A separate monitoring script (`scripts/monitor-effectiveness.py`) bins events into 9 community pain categories to verify whether new gates address real operator complaints. The audit log itself lives at `~/.local/share/reasoning-core/events/<YYYY-MM-DD>/<session_id>.jsonl[.gz]`, is schema-versioned (v3), redacts secrets, and auto-rotates. All data stays local; no telemetry leaves the machine.

---

## Files Involved

### Backend (Python)
| File | Layer | Purpose |
|------|-------|---------|
| `src/rc_cli.py` | CLI driver | `rc` command-line tool; subcommands: `status`, `explain`, `bypass-next`, `skip-file`, `unskip-file`, `reasoning-efficiency` |
| `src/hooks/audit_log.py` | Audit writer | Per-session JSONL appender; redaction; `GATE_IDS` registry; schema v3; retry-marker helpers |
| `src/hooks/_audit_rotation.py` | Rotation helper | Daily gzip + eviction + disk-cap enforcement |
| `src/hooks/post_assistant_diff_audit.py` | Post-tool hook | Extracts last assistant diff from `transcript.jsonl` |
| `src/hooks/post_batch_lang_audit.py` | Post-tool hook | Batch language audit after session end |
| `src/gen_client.py` | Sidecar client | `_audit_emit` fallback logger for BM25/gen-critic events |
| `src/hooks/_dispatch.py` | Gate chain | `gate_kill_switch_and_magic`, `gate_lang_lock`, `gate_plan_grounding`, `gate_mock_detector`, `gate_drift`, `gate_calibration`, `gate_rule_engine`, `gate_regression` |
| `src/hooks/pre_edit_guard.py` | Pre-tool hook | Edit/Write/MultiEdit entrypoint; `_post_score` → sidecar `/score` |
| `src/hooks/pre_bash_guard.py` | Pre-tool hook | Bash command screening; 4 layers (hard-deny → guarded-path → kill → shell-write) |
| `src/hooks/pre_plan_guard.py` | Pre-tool hook | PLAN.md write screening; novelty/boundary/framework-pivot warnings |
| `src/s2_core.py` | Sidecar core | HTTP server on `127.0.0.1:8765`; `_compute_risk_vector` (8-dim, 11-dim with `RC_PROJECT_INDEX=1`) |
| `src/ssm_backbone.py` | Embedder | `mamba-130m` (default), `codestral-mamba`, `bge-code`, `unixcoder-base` |
| `src/sidecar_supervisor.py` | Supervisor | Sidecar lifecycle + restart |
| `src/sidecar_boot.py` | Sidecar entry | HTTP entry on `127.0.0.1:8765` |
| `src/_supervisor_env.py` | Supervisor env | Environment resolution for supervisor-spawned children |
| `src/hooks/_calibration_gate.py` | Helper | Mahalanobis per-repo threshold calibration |
| `src/hooks/_shadow_mode.py` | Helper | `RC_SHADOW_MODE` resolution |
| `src/hooks/_plan_scaffold.py` | Helper | Auto-scaffold PLAN.md from README |
| `src/hooks/_guard_paths.py` | Helper | Guard-file discovery & override check |
| `src/hooks/_kill_switches.py` | Helper | Bypass-next / disabled-globally / file-skip |
| `src/hooks/_magic_comments.py` | Helper | `# rc:skip` operator-authored directive parse |
| `src/hooks/_session_manifest.py` | Helper | Declared-language manifest |
| `src/hooks/session_start_best_effort.py` | Session hook | `best_effort_spec` injection |
| `src/hooks/session_start_manifest.py` | Session hook | Per-session declared-language manifest |
| `src/mcp_reasoner.py` | MCP server | `validate_imports` tool registration |
| `src/mcp_gate.py` | MCP server | `hybrid_reasoner_gate_edit` tool for Tier-2 hosts |
| `src/mcp_diff_validator.py` | MCP server | `validate_unified_diff` tool |
| `src/project_index.py` | Indexer | Per-session call-graph + import index; gated on `RC_PROJECT_INDEX=1` |
| `bin/rc` | CLI shim | Executable wrapper invoking `python3 -m src.rc_cli` |
| `scripts/monitor-effectiveness.py` | Monitoring script | Bins audit events into 9 community pain categories; prints CSV-like verdict table |
| `scripts/backfill_audit_log_schema_version.py` | Backfill script | Schema-version migration helper |

### Tests
| File | Purpose |
|------|---------|
| `tests/test_audit_log.py` | Unit tests for audit-log subsystem |
| `tests/test_rc_reasoning_efficiency.py` | Tests for `rc reasoning-efficiency` composite metric |
| `tests/test_phase_minus_one.py` | Transcript-related assertions |
| `tests/test_guard_paths.py` | Guard-path interaction with audit-log paths |
| `tests/test_gen_sidecar_launcher.py` | Gen-sidecar launcher tests |
| `tests/test_mcp_import_validator.py` | MCP import validator tests |
| `tests/test_sidecar_boot.py` | Sidecar boot tests |

### Configuration / Docs
| File | Purpose |
|------|---------|
| `docs/CLI_PARITY.md` | CLI parity documentation |
| `docs/USAGE.md` | Usage guide covering transcripts and audit logs |
| `docs/ARCHITECTURE.md` | Architecture overview, includes audit-log component |
| `docs/CONFIGURATION.md` | Configuration reference |
| `docs/INSTALL.md` | Installation guide |
| `docs/CHANGELOG-2026-06-02.md` | Post-implementation changelog |
| `launchd/com.reasoning-core.supervisor.plist` | Launchd plist for supervisor auto-start |
| `.envrc` | direnv config; env knobs including `RC_AUDIT_ROOT`, `S2_HARD_CAP_MS` |

### Data paths (not in repo)
| Path | Format | Purpose |
|------|--------|---------|
| `~/.local/share/reasoning-core/events/<YYYY-MM-DD>/<session_id>.jsonl[.gz]` | JSONL | Audit log; one event per gate decision |
| `~/.local/share/reasoning-core/events/<session_id>.last_block` | JSON | Retry-marker file used by `is_retry_after_block` |
| `~/.local/share/reasoning-core/events/gen_fallback.jsonl` | JSONL | Gen-critic fallback events |
| `~/.local/state/reasoning-core/shadow_markers.json` | JSON | Shadow-block retry markers (separate namespace) |

---

## Data Flow

### Audit-log write path (every hook invocation)

```
Any hook (pre_edit_guard, pre_bash_guard, pre_plan_guard, etc.)
  → audit_log.new_event(tool_name, decision, **fields)      src/hooks/audit_log.py:190-218
     → fills ts, decision_id, session_id, project_dir, host, schema_version
  → hook adds latency_ms, reason, file_path, gate_id, etc.
  → audit_log.append_event(event)                           src/hooks/audit_log.py:221-282
     → _redact(event)                                        src/hooks/audit_log.py:172-188
        → secret-shaped paths → "[REDACTED]"
        → inline secrets (sk-..., ghp_..., Bearer ..., password=...) scrubbed
     → portalocker LOCK_EX (best-effort)                     src/hooks/audit_log.py:249-265
     → write one JSON line to <audit_root>/<YYYY-MM-DD>/<session_id>.jsonl
     → _audit_rotation.rotate(audit_root, today)              src/hooks/_audit_rotation.py:69-94
        → gzip older .jsonl, evict > retention days, enforce 5 GB cap
```

### `rc reasoning-efficiency` read path

```
rc reasoning-efficiency [--days N] [--audit-root PATH]
  → src/rc_cli.py:main()                                    src/rc_cli.py:267-289
     → argparse dispatch → cmd_reasoning_efficiency()       src/rc_cli.py:222-264
        → _walk_audit_events(audit_root, days)               src/rc_cli.py:191-219
           → scan YYYY-MM-DD dirs under audit_root
           → open .jsonl / .jsonl.gz transparently
           → yield parsed JSON objects
        → aggregate:
           drift_caught: reason=="plan_impl_drift" && decision in (blocked,warn,shadow_blocked)
           false_drifts:  drift_caught && retry_after_block==True && decision==blocked
           total_latency_ms: sum of latency_ms fields
           sidecar_unavailable: reason starts with "sidecar_unavailable"
        → compute composite:
           eff = (max(0, drift_caught - false_drifts) / (gate_wall_clock_s + 1.0))
                 * _REPO_IDIOM_DELTA_NORM (0.43)
                 * max(0.0, 1.0 - sidecar_unavailable_rate)
        → print key-value pairs via _print_kv
```

### Transcript diff extraction path

```
post_assistant_diff_audit hook
  → _find_last_assistant_diff(transcript_path)               src/hooks/post_assistant_diff_audit.py:62-91
     → read transcript.jsonl from end
     → find last assistant message with "diff --git "
     → extract fenced diff (```diff ... ```) or bare diff
     → return diff text for structural audit
```

### Effectiveness monitor path

```
scripts/monitor-effectiveness.py [days]
  → _iter_events(days)                                      scripts/monitor-effectiveness.py:38-57
     → scan ~/.local/share/reasoning-core/events/<YYYY-MM-DD>/*.jsonl
  → classify(ev) → set of pain categories                     scripts/monitor-effectiveness.py:106-123
     → 9 categories: scope_creep, pattern_blindness, spec_drift,
       token_waste, local_only_violations, hallucinated_apis,
       runtime_enforcement, over_engineering, repo_conventions
  → print verdict table: total / today / verdict (silent/weak/warm/active)
```

---

## Existing Patterns

### Pattern 1: Audit-log event walker with gzip transparency
**File:** `src/rc_cli.py:191-219`

Iterates date-stamped directories, transparently opens `.jsonl` or `.jsonl.gz`, yields parsed JSON. Handles `OSError` and `ValueError` silently. Template for any audit-log consumer.

### Pattern 2: Best-effort audit append with redaction
**File:** `src/hooks/audit_log.py:221-282`

Never raises into calling hook. Swallows all exceptions, writes one-line stderr warning on OSError. Redacts secrets before write. Uses `portalocker` for concurrent multi-host safety. This contract is the foundation of the entire hook layer's determinism.

### Pattern 3: Retry-marker for block-follow-up detection
**File:** `src/hooks/audit_log.py:292-346`

`record_block(file_path)` persists a timestamp; `is_retry_after_block(file_path)` checks within a 120-second window. Distinguishes "agent retrying after block" from fresh edit. Used by `cmd_reasoning_efficiency` to proxy false-positive rate (if agent retries and retry is allowed, prior block may have been false).

### Pattern 4: Gen-client fallback audit emitter
**File:** `src/gen_client.py:129-151`

`_audit_emit(reason, **fields)` writes to a restricted JSONL path with `O_NOFOLLOW`, `0o600` permissions, and path-prefix allowlist. Pattern for any component that needs to emit audit events outside the main hook flow.

### Pattern 5: Composite north-star metric from audit log
**File:** `src/rc_cli.py:222-264`

Aggregates drift_caught, false_drifts, latency, sidecar_unavailability into a single scalar. The formula is documented inline and tested in `tests/test_rc_reasoning_efficiency.py`. Template for any operator-facing effectiveness score.

---

## Architecture Notes

- **Trust boundary:** `127.0.0.1:8765` only. Gate decisions never leave the machine. No telemetry, no cloud.
- **Schema v3:** `audit_log.SCHEMA_VERSION = 3`. Forward-compatible, additive. Past evolution: added `decision_id`, `host`, `signal_source`.
- **Host abstraction:** `_host_env` provides `session_id`, `project_dir`, `host`. Single integration point for new CLIs. Currently flows for Claude + Vibe; Gemini/Copilot would land here.
- **Best-effort contract:** `audit_log.append_event` never raises. Any IOError is swallowed. This is intentional — hooks must remain deterministic.
- **Rotation:** Daily gzip + 90-day retention + 5 GB cap. Controlled by `RC_AUDIT_RETENTION_DAYS` and `RC_AUDIT_CAP_BYTES`.
- **Kill switches:** `rc bypass-next`, `rc skip-file`, `rc unskip-file` write to a shared file read by hooks. Audit log records `allowed_via_override` with reason.
- **Shadow mode:** `RC_SHADOW_MODE` produces `shadow_blocked` decisions (logged) but does not block. Shadow-block markers live in a separate namespace to avoid poisoning retry detection.
- **Calibration:** `_calibration_gate.py` applies per-repo threshold calibration. Reads from `eval/runs/calibration.json` and `eval/runs/qwen_kappa_gate.json`.
- **Default-off levers:** `RC_BEST_EFFORT_SPEC`, `RC_PLAN_GROUNDING`, `RC_CALIBRATION_ENABLED`, `RC_PROJECT_INDEX`. The pattern of "ship the lever in code, default it off" is intentional but fragile — users only get the value if they read the doc or have direnv configured.
- **Sidecar hard cap:** `S2_HARD_CAP_MS` (default 1500ms, bumped to 3000ms in `.envrc` commit `54eed96`) caps sidecar latency. On timeout, the gate falls back to symbolic rules or fail-open depending on `RC_S2_FAIL_CLOSED`.
- **Transcript parsing:** `post_assistant_diff_audit.py` reads `transcript.jsonl` (Claude Code session file) to extract the last assistant diff. This is a post-hoc audit, not a real-time gate.

---

## External Dependencies

| Dep | Used in | Risk |
|---|---|---|
| `state-spaces/mamba-130m-hf` | default embedder (`ssm_backbone.py`) | Single-tenant SSM; no batching ⇒ p99=60s tail latency |
| `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit` | gen_client critic | Required for plan-grounding scoring |
| `portalocker ≥ 2.7` | audit-log writes | Multi-host concurrency — works |
| `direnv` | env-flag distribution | If user skips `direnv allow`, all defaults stay off |
| `huggingface-cli` | embedder download | Network dependency on first run |

---

## Existing Research Documents

The following research documents already cover related topics. This document focuses specifically on the **transcript/audit-log/rc-cli triad** as the effectiveness-measurement infrastructure, rather than re-analyzing gate signal quality or making improvement recommendations.

| Document | Date | What it covers | What's new here |
|---|---|---|---|
| `2026-05-23-reasoning-core-effectiveness-audit.md` | 2026-05-23 | 19-day audit of 30,275 events; effectiveness scorecard; `gate_id` gap; sidecar reliability | This doc maps the **measurement infrastructure** (how the audit log is written, read, and aggregated) rather than the signal quality inside it |
| `2026-05-23-effectiveness-audit-review-engineer.md` | 2026-05-23 | Engineer review of the above audit | — |
| `2026-05-23-effectiveness-audit-review-scientist.md` | 2026-05-23 | Scientist review of the above audit | — |
| `2026-06-01-reasoning-core-1000pct-improvements.md` | 2026-06-01 | 25-day stratified sample; 5 ranked improvement bets with kill criteria; PRM roadmap; north-star metric formula | This doc documents the **implementation** of the north-star metric (`rc reasoning-efficiency`) and the monitoring script (`monitor-effectiveness.py`) that the 2026-06-01 doc proposed |
| `2026-06-02-effectiveness-monitoring.md` | 2026-06-02 | Live 1-day effectiveness monitor run; verdict per pain category; 1,832 events | This doc provides the **file-level map** of all components involved in effectiveness monitoring |
| `2026-06-02-community-pain-points.md` | 2026-06-02 | 9 community pain points that gates should address | — |
| `2026-06-02-pain-feature-mapping.md` | 2026-06-02 | Which features map to which pains | — |
| `2026-06-02-plan-guard-big-refactor-friction.md` | 2026-06-02 | Plan guard friction during large refactors | — |

---

## Open Questions

1. **Does `S2_HARD_CAP_MS=3000` actually reach launchd-spawned children?** The `.envrc` bump (commit `54eed96`) may not propagate to the supervisor because launchd reads the plist, not `.envrc`. The 2026-06-02 monitor found 234 `hard_cap_exceeded` events at 1500ms, suggesting the bump didn't take effect.
2. **Are Phase-2 dims (`session_centroid_drift`, `project_fan_in`, `project_coupling`) firing after the `session_id` fix?** Commit `b633cd1` (2026-06-01) plumbs `session_id` to `/score`, but the 2026-06-02 data is historical. Need re-run ~2026-06-15.
3. **What is the false-positive rate of `plan_impl_drift` blocks?** The `retry_after_block` proxy is coarse — it only catches agent retries, not operator overrides or commit survival. Need post-block follow-up instrumentation.
4. **Why is `cumulative_drift` always null in the audit?** The `gate_drift` logic references it (`src/hooks/_dispatch.py:387-403`) but it never appears in events. Is the supervisor computing it? Is it on the `/score` response path?
5. **Has anyone run `random-mamba` as a control recently?** `src/ssm_backbone.py:99` ships a randomly-initialised Mamba-2 for falsifiability. Without a real-vs-random A/B, we cannot prove the SSM signal is non-null.
6. **What is the operator-override survival ratio?** Of overrides (`RC_ALLOW_GUARD_EDIT=1`, `magic_comment_self_introduced`), how many bypassed diffs survived to a committed git ref? This is needed for true false-positive rate.
