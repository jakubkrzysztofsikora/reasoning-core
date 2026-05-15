# Configuration

Every `RC_*` and `S2_*` environment variable. Override per-machine in
`.envrc.local` (gitignored, sourced last).

---

## Sidecar runtime

| Env var | Default | Purpose |
|---|---|---|
| `S2_DEVICE` | `cpu` | `cpu` or `cuda` |
| `S2_PORT` | `8765` | Sidecar bind port |
| `S2_URL` | `http://127.0.0.1:$S2_PORT` | Override hook target |
| `S2_TIMEOUT` | `60` | Hook /score timeout (seconds) |
| `S2_FAIL_CLOSED` | `1` | `1` blocks edits when sidecar unreachable |
| `S2_LOG_LEVEL` | `INFO` | Sidecar log level |
| `S2_SSM_CHECKPOINT` | `state-spaces/mamba-130m-hf` | Override SSM backbone |
| `HF_HOME` | `$HOME/.cache/huggingface` | HF cache (shared with sibling repos / eval worktrees) |

## Source-code thresholds

Per-kind ceilings for `test_code` / `plan_md` / `doc_md` / `config` are not
env-overridable yet — see `_KIND_THRESHOLDS` in `src/s2_core.py`. The three
vars below control only the `source_code` kind.

| Env var | Default | Purpose |
|---|---|---|
| `S2_AIS_THRESHOLD` | `0.4` | AIS threshold for `source_code` |
| `S2_COHERENCE_THRESHOLD` | `1.5` | `coherence_delta` threshold for `source_code` |
| `S2_RISK_DIM_THRESHOLD` | `0.9` | Per-dim ceiling for `source_code` |

## Hook policy posture

| Env var | Default | Purpose |
|---|---|---|
| `RC_SHADOW_MODE` | `1` | Log decisions, do not enforce |
| `RC_PLAN_BLOCK` | `1` | Plan-guard warnings escalate to hard block |
| `RC_PLAN_QUALITY` | `0` | Enable plan-quality CGS gate |
| `RC_MOCK_DETECTOR` | `1` | Reject placeholder code patterns |
| `RC_LANG_LOCK` | `1` | Reject edits introducing un-fingerprinted languages |
| `RC_LANG_ALLOW` | _unset_ | Comma-list of additional languages to permit |
| `RC_LANG_OVERRIDE` | _unset_ | Per-edit language override |
| `RC_LANG_LOCK_MAX_FILES` | `20000` | Cap files scanned when fingerprinting the repo |
| `RC_LANG_AUDIT_THRESHOLD` | `0.33` | PostToolUse foreign-language ratio that triggers an audit row |
| `RC_DRIFT_WARN` | `4.0` | **Placeholder** cumulative-drift warn level pending Phase 3.5 calibration; not production-tuned (tracker #78). |
| `RC_DRIFT_DENY` | `6.0` | **Placeholder** cumulative-drift hard-deny level pending Phase 3.5 calibration. |
| `RC_DRIFT_OVERRIDE` | _unset_ | `1` disables drift policy (hard-denied if set inline via Bash) |

## Generative repair head

| Env var | Default | Purpose |
|---|---|---|
| `RC_REASONER_BACKEND` | `mlx` | `mlx` (Apple) / `llama` / `remote` |
| `RC_GEN_URL` | local mlx port | Override for hosted endpoint (e.g. Scaleway `https://api.scaleway.ai/v1/chat/completions`) |
| `RC_GEN_API_KEY` | _unset_ | Bearer token for hosted endpoint; falls through to `SCALEWAY_API_KEY` |
| `RC_GEN_MODEL` | `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit` | Model id sent in chat-completions body |
| `RC_GEN_BUDGET_MS` | `2500` | Generation budget per repair call (ms) |

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
| `RC_AUDIT_ROOT` | `$HOME/.local/share/reasoning-core/events` | Audit log root |
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

Project-scoped via the per-repo `.envrc`. Both default OFF.

| Env var | Default | Purpose |
|---|---|---|
| `RC_BEST_EFFORT_SPEC` | _unset_ | SessionStart overlay nudging the agent away from `DIVERGENCES.md`-alone gameability on missing-infra tasks |
| `RC_PLAN_GROUNDING` | _unset_ | Warn when the agent edits a file not in PLAN.md (`=2` escalates to hard block) |

See [`iter3-levers.md`](iter3-levers.md) for the full design.

## Multi-host

| Env var | Default | Purpose |
|---|---|---|
| `RC_HOST` | _auto_ | Force host identity (`claude` / `gemini` / `copilot` / `vibe`); auto-detected from `<HOST>_PROJECT_DIR` env |
| `RC_PROJECT_DIR` | _auto_ | Override project root (takes precedence over `<HOST>_PROJECT_DIR`) |
| `RC_SESSION_ID` | _auto_ | Stable per-launch session id; synthesised if unset and re-exported for child processes |
