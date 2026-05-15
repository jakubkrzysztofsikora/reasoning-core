# Configuration

Every `RC_*` and `S2_*` environment variable. Override per-machine in
`.envrc.local` (gitignored, sourced last).

---

## Embedder backend

Selectable via `RC_EMBEDDER`. The legacy `S2_SSM_CHECKPOINT` path still
works for the `mamba-130m` backend only.

| Backend | Checkpoint | Params | Hidden | Pool | Notes |
|---|---|---|---|---|---|
| `mamba-130m` (default) | `state-spaces/mamba-130m-hf` | 130M | 768 | mean | SHA-pinned, ships out of the box |
| `codestral-mamba` | `mistralai/Mamba-Codestral-7B-v0.1` | 7B | 4096 | mean | Apache 2.0, code-pretrained Mamba-2; **needs `RC_MISTRALAI_MAMBA_CODESTRAL_7B_V0_1_REVISION=<40-char SHA>`** |
| `bge-code` | `BAAI/bge-code-v1` | ~4GB | 768 | cls | Code-specialised transformer; **needs SHA pin** |
| `unixcoder-base` | `microsoft/unixcoder-base` | small | 768 | cls | MIT-licensed baseline; **needs SHA pin** |
| `random-mamba` | _in-process control_ | 130M | 768 | mean | Random-init Mamba-2 for falsifiability tests |

Non-default backends carry `revision="main"` in the registry and are
**fail-closed under `_resolve_revision_for_backend`** until an operator
provides a pinned SHA via `RC_<REPO_SLUG>_REVISION` (uppercase, non-alpha
→ `_`). Supply-chain hardening — branch names are explicitly rejected.

## Sidecar runtime

| Env var | Default | Purpose |
|---|---|---|
| `S2_DEVICE` | `cpu` | `cpu` or `cuda` |
| `S2_PORT` | `8765` | Sidecar bind port |
| `S2_URL` | `http://127.0.0.1:$S2_PORT` | Override hook target; non-loopback rejected unless `S2_ALLOW_REMOTE=1` |
| `S2_ALLOW_REMOTE` | _unset_ | `1` permits a non-loopback `S2_URL` (off by default — blocks SSRF-style source exfil via `.envrc`/`.mcp.json` injection) |
| `S2_TIMEOUT` | `30` | Hook `/score` timeout (seconds) |
| `S2_FAIL_CLOSED` | `0` | `1` blocks edits when sidecar unreachable; `0` (default in code) fails open |
| `S2_HEALTH_TIMEOUT` | `120` | Sidecar boot wait for `model_loaded:true` (seconds) |
| `S2_LOG_LEVEL` | `INFO` | Sidecar log level |
| `S2_LOG_FILE` | `/tmp/reasoning-core-sidecar.log` | When `BACKGROUND=1`, redirect sidecar logs here |
| `S2_SSM_CHECKPOINT` | _unset_ | Legacy override; only honoured when `RC_EMBEDDER` is unset |
| `HF_HOME` | `$HOME/.cache/huggingface` | HF cache (shared with sibling repos / eval worktrees) |
| `RC_<REPO_SLUG>_REVISION` | _unset_ | 40-char hex SHA override for `RC_EMBEDDER` backends with `revision="main"` |

## Source-code thresholds

Per-kind ceilings for `test_code` / `plan_md` / `doc_md` / `config` are not
env-overridable — see `_KIND_THRESHOLDS` in `src/s2_core.py`. The three vars
below control only the `source_code` kind.

| Env var | Default | Purpose |
|---|---|---|
| `S2_AIS_THRESHOLD` | `0.4` | Architectural-impact floor for `source_code` (cos→AIS mapping; lower = stricter) |
| `S2_COHERENCE_THRESHOLD` | `0.5` | Chord-distance ceiling for `source_code` (metric is in [0, 2]; values >2.0 logged as unreachable — see `_l2_distance` migration note) |
| `S2_RISK_DIM_THRESHOLD` | `0.9` | Per-dim ceiling for `source_code` |

### Per-kind thresholds (informational; not env-overridable)

| kind | `cd` | `ais` | `dim` |
|---|---:|---:|---:|
| `source_code` | env (`0.5`) | env (`0.4`) | env (`0.9`) |
| `test_code`   | `0.7` | `0.3` | `0.95` |
| `plan_md`     | `1.0` | `0.3` | `1.0` |
| `doc_md`      | `1.0` | `0.3` | `1.0` |
| `config`      | `0.4` | `0.5` | `0.9` |

Defaults match the chord-distance scale (`coherence_delta` in `[0, 2]`). The
old `L2/sqrt(D)`-scale values (cd: 1.5/2.0/3.0/1.2) are out of bounds on this
metric and would silently disable the gate.

## Hook policy posture

| Env var | Default | Purpose |
|---|---|---|
| `RC_SHADOW_MODE` | `1` | Log decisions, do not enforce |
| `RC_PLAN_BLOCK` | `1` | Plan-guard warnings escalate to hard block |
| `RC_PLAN_QUALITY` | `0` | Enable plan-quality CGS gate |
| `RC_MOCK_DETECTOR` | `1` | Reject placeholder code patterns |
| `RC_LANG_LOCK` | `1` | Reject edits introducing un-fingerprinted languages |
| `RC_LANG_ALLOW` | _unset_ | CSV of additional languages to permit |
| `RC_LANG_OVERRIDE` | _unset_ | Per-edit language override |
| `RC_LANG_LOCK_MAX_FILES` | `20000` | Cap files scanned when fingerprinting the repo |
| `RC_LANG_LOCK_PATH_EXEMPT` | _unset_ | CSV of top-level dir prefixes exempt from the lock |
| `RC_LANG_AUDIT_THRESHOLD` | `0.33` | PostToolUse foreign-language ratio that triggers an audit row |
| `RC_DRIFT_WARN` | `4.0` | Cumulative-drift warn level (post-`_l2_distance` chord scale — re-tune for production) |
| `RC_DRIFT_DENY` | `6.0` | Cumulative-drift hard-deny level (chord scale) |
| `RC_DRIFT_OVERRIDE` | _unset_ | `1` disables drift policy (hard-denied if set inline via Bash) |

## Architectural rule engine (symbolic gate)

Reads `.reasoning-core/rules.yaml` from the project root. Two rule types:
`forbid_import`, `forbid_pattern`. Hard limits: ≤50 rules, ≤5 ms per rule.

| Env var | Default | Purpose |
|---|---|---|
| `RC_RULE_ENGINE` | _unset_ | `1` enables the symbolic gate co-emitted with the neural risk vector |
| `RC_RULE_ENGINE_LENIENT` | _unset_ | `1` downgrades schema/load errors and per-rule exceptions to warn-only |
| `RC_RULE_ENGINE_ALLOW_BASIC_YAML` | _unset_ | `1` allows the in-tree `_basic_yaml_parse` fallback when PyYAML is missing (security-review flagged) |

Rules can carry per-call bypass comments: `# rc:skip-rule:<id>` (Python) or
`// rc:skip-rule:<id>` (JS/TS).

## Project index (cross-file risk dims)

| Env var | Default | Purpose |
|---|---|---|
| `RC_PROJECT_INDEX` | _unset_ | `1` builds a per-session symbol/import index — fills `project_fan_in` and `project_coupling` risk-vector dims (`project_fan_in` counts files importing the edited module; coupling counts cross-file edges) |
| `RC_PROJECT_INDEX_MAX` | `64` | LRU bound on cached project-index futures across sessions |

## Diff audit / repair

Both surface through the `hybrid-reasoner` MCP server.

| Env var | Default | Purpose |
|---|---|---|
| `RC_DIFF_AUDIT` | _unset_ | `1` enables the Stop hook (`post_assistant_diff_audit.py`) that scans the last assistant diff in transcript with `validate_unified_diff` and injects an advisory + best-effort repair |

The `validate_unified_diff(patch)` MCP tool detects `missing_prefix`,
`empty_context`, `count_mismatch`, `bad_hunk_header`, `missing_hunk` and
returns a repaired patch when possible. Never raises.

## Generative repair head

| Env var | Default | Purpose |
|---|---|---|
| `RC_REASONER_BACKEND` | `mlx` | `mlx` (Apple) / `llama` / `remote` |
| `RC_GEN_URL` | local mlx port | Override for hosted endpoint (e.g. Scaleway `https://api.scaleway.ai/v1/chat/completions`) |
| `RC_GEN_API_KEY` | _unset_ | Bearer token for hosted endpoint; falls through to `SCALEWAY_API_KEY` |
| `RC_GEN_ALLOWED_HOSTS` | _unset_ | CSV of non-loopback hosts the `Authorization` header may be sent to. Cross-origin redirects refused so the API key cannot leak via 30x to attacker-controlled hosts |
| `RC_GEN_MODEL` | `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit` | Model id sent in chat-completions body |
| `RC_GEN_BUDGET_MS` | `2500` | Generation budget per repair call (ms) |
| `RC_GEN_FALLBACK_LOG` | _internal default_ | Path-allowlisted log destination for repair-head fallbacks |

## Calibration & supervisor

| Env var | Default | Purpose |
|---|---|---|
| `RC_CALIBRATION_ENABLED` | _unset_ | `1` enables Mahalanobis calibration gate; default OFF until Phase 3.5 v3 corpus calibrates |
| `RC_CALIBRATION_PATH` | `eval/runs/calibration.json` | Override path to fitted calibration models |
| `RC_RECALIBRATE_POLL_S` | `60` | Supervisor watcher poll interval for `recalibrate.signal`; hot-reloadable per tick |
| `RC_BROKER_PORT` | `8764` | Supervisor broker `/health` aggregator port |

## Bypass / kill switches

| Env var | Default | Purpose |
|---|---|---|
| `RC_BYPASS_NEXT` | _unset_ | One-shot bypass; consumed on first guard fire |
| `RC_ALLOW_GUARD_EDIT` | _unset_ | Allow edits to guarded paths (captured at session boot) |
| `RC_ALLOW_SUBAGENT_GUARD_EDIT` | _unset_ | Allow Task prompts naming guarded paths |

## Audit log & state

| Env var | Default | Purpose |
|---|---|---|
| `RC_AUDIT_ROOT` | `$HOME/.local/share/reasoning-core/events` | Audit log root (schema v3 emitted; `scripts/backfill_audit_log_schema_version.py` backfills v1 on legacy untagged rows) |
| `RC_AUDIT_RETENTION_DAYS` | `90` | Prune older audit shards on session start |
| `RC_AUDIT_CAP_BYTES` | `5368709120` (5 GiB) | Per-shard size cap before rotation |
| `RC_STATE_DIR` | _internal default_ | Session manifest + sentinel state |

## Eval / calibration

| Env var | Default | Purpose |
|---|---|---|
| `RC_LIVE` | _unset_ | `1` enables live Scaleway eval tests |
| `RC_EVAL_STUB_CLAUDE` | _unset_ | Stub Claude in eval harness |
| `RC_QWEN_KAPPA_SENTINEL` | `0.7` | Min Cohen κ for grounding eval to pass |
| `RC_TASK_SPEC` | _unset_ | Active task spec path (read by every hook for audit context) |

## Iter-3 levers (opt-in)

Project-scoped via the per-repo `.envrc`. All default OFF.

| Env var | Default | Purpose |
|---|---|---|
| `RC_BEST_EFFORT_SPEC` | `0` | SessionStart overlay nudging the agent away from `DIVERGENCES.md`-alone gameability on missing-infra tasks |
| `RC_PLAN_GROUNDING` | `0` | `1`=warn / `2`=hard block when the agent edits a file not in PLAN.md |
| `RC_RUN_DIR` | _unset_ | Override for PLAN.md resolution; precedence: `RC_RUN_DIR` > `CLAUDE_PROJECT_DIR` > cwd |

See [`iter3-levers.md`](iter3-levers.md) for the full design.

## Multi-host

| Env var | Default | Purpose |
|---|---|---|
| `RC_HOST` | _auto_ | Force host identity (`claude` / `gemini` / `copilot` / `vibe`); auto-detected from `<HOST>_PROJECT_DIR` env |
| `RC_PROJECT_DIR` | _auto_ | Override project root (takes precedence over `<HOST>_PROJECT_DIR`) |
| `RC_SESSION_ID` | _auto_ | Stable per-launch session id; synthesised if unset and re-exported for child processes |
