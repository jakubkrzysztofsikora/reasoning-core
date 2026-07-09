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
| `codestral-mamba` | `mistralai/Mamba-Codestral-7B-v0.1` | 7B | 4096 | mean | Apache 2.0, code-pretrained Mamba-2; **defaults to fp16** (~14 GB RAM) — override with `RC_EMBEDDER_DTYPE`; **needs `RC_MISTRALAI_MAMBA_CODESTRAL_7B_V0_1_REVISION=<40-char SHA>`** |
| `codestral-mamba-gguf` | `gabriellarson/Mamba-Codestral-7B-v0.1-GGUF` | 7B (quantized) | 4096 | mean | Apache 2.0, **same code-pretrained Codestral** loaded via `llama-cpp-python`; default file `Mamba-Codestral-7B-v0.1-Q2_K.gguf` (~2.5 GB) — override with `RC_CODESTRAL_GGUF_FILE`. **Needs `RC_GABRIELLARSON_MAMBA_CODESTRAL_7B_V0_1_GGUF_REVISION=<40-char SHA>` and `pip install llama-cpp-python`.** |
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
| `S2_HARD_CAP_MS` | `1500` | Client-side hard cap on `/score` POSTs. On timeout the hook invokes the symbolic gates and emits `signal_source="symbolic_fallback"`. |
| `S2_FAIL_CLOSED` | `0` | `1` blocks edits when sidecar unreachable; `0` (default in code) fails open |
| `S2_HEALTH_TIMEOUT` | `120` | Sidecar boot wait for `model_loaded:true` (seconds) |
| `S2_LOG_LEVEL` | `INFO` | Sidecar log level |
| `S2_LOG_FILE` | `/tmp/reasoning-core-sidecar.log` | When `BACKGROUND=1`, redirect sidecar logs here |
| `S2_SSM_CHECKPOINT` | _unset_ | Legacy override; only honoured when `RC_EMBEDDER` is unset |
| `HF_HOME` | `$HOME/.cache/huggingface` | HF cache (shared with sibling repos / eval worktrees) |
| `RC_<REPO_SLUG>_REVISION` | _unset_ | 40-char hex SHA override for `RC_EMBEDDER` backends with `revision="main"` |
| `RC_EMBEDDER_DTYPE` | _unset_ | `float32` \| `float16` \| `bfloat16` \| `auto`. When unset, `codestral-mamba` defaults to `float16` (memory-saver on the 7B model, ~14 GB vs ~28 GB at fp32); other backends use transformers' native dtype (fp32). Invalid values rejected at load time. `low_cpu_mem_usage=True` is always passed to `from_pretrained` |
| `RC_CODESTRAL_GGUF_FILE` | `Mamba-Codestral-7B-v0.1-Q2_K.gguf` | Filename to pull from `gabriellarson/Mamba-Codestral-7B-v0.1-GGUF` when `RC_EMBEDDER=codestral-mamba-gguf`. Pick a larger quant (e.g. `Q4_K_M.gguf` ~4 GB) for better quality, or a smaller quant for tighter RAM. |
| `RC_GGUF_THREADS` | _unset_ (= all cores) | Thread count for llama.cpp inference; matches CPU count by default. |
| `S2_MEM_LOG_INTERVAL_S` | `30` | Sidecar memory sampler interval (seconds). `0` disables. Logs process RSS / VSS, system memory %, swap usage — useful for diagnosing delayed OOM kills under codestral-mamba. |

## Source-code thresholds

Per-kind ceilings for `test_code` / `plan_md` / `doc_md` / `config` are not
env-overridable — see `_KIND_THRESHOLDS` in `src/s2_core.py`. The three vars
below control only the `source_code` kind.

| Env var | Default | Purpose |
|---|---|---|
| `S2_AIS_THRESHOLD` | `0.4` | Architectural-impact floor for `source_code` (cos→AIS mapping; lower = stricter) |
| `S2_COHERENCE_THRESHOLD` | `0.09` | Chord-distance ceiling for `source_code` (metric is in [0, 2]; values >2.0 logged as unreachable) |
| `S2_RISK_DIM_THRESHOLD` | `0.9` | Per-dim ceiling for `source_code` |

### Per-kind thresholds (informational; not env-overridable)

| kind | `cd` | `ais` | `dim` |
|---|---:|---:|---:|
| `source_code` | env (`0.09`) | env (`0.4`) | env (`0.9`) |
| `test_code`   | `0.14` | `0.3` | `0.95` |
| `plan_md`     | `0.30` | `0.3` | `1.0` |
| `doc_md`      | `0.30` | `0.3` | `1.0` |
| `config`      | `0.08` | `0.5` | `0.9` |

Defaults match the chord-distance scale (`coherence_delta` in `[0, 2]`). The
old `L2/sqrt(D)`-scale values (cd: 1.5/2.0/3.0/1.2) are out of bounds on this
metric and would silently disable the gate.

## Hook policy posture

| Env var | Default | Purpose |
|---|---|---|
| `RC_MODE` | `advise` | Canonical posture: `advise` (warn/audit only), `copilot` (block on contract/oracle/rule failures), `autopilot` (block + auto-repair). |
| `RC_SHADOW_MODE` | `1` | Legacy log-only flag; `1` log decisions, `0` enforce. Equivalent to `RC_MODE=advise` when `RC_MODE` is unset. |
| `RC_PLAN_GROUNDING` | `1` | `0` off, `1` warn when an Edit drifts from `PLAN.md`, `2` hard block. |
| `RC_PLAN_BLOCK` | `0` | (legacy, `pre_plan_guard.py`) escalate plan-doc write warnings to hard block. |
| `RC_BEST_EFFORT_SPEC` | `1` | SessionStart overlay that nudges the agent away from `DIVERGENCES.md`-only gameability. |
| `RC_PLAN_QUALITY` | `0` | Enable plan-quality CGS gate |
| `RC_PLAN_NOVELTY_RATIO` | `1.8` | Novelty-drift cutoff: flag a plan that sits this many × farther from the recent-plan cluster than the typical peer. Raise to tolerate broader scope, lower to police drift more tightly. |
| `RC_PLAN_NOVELTY_MIN_SPREAD` | `0.05` | Floor for the novelty-drift denominator, as a fraction of the peers' vector scale. Stops a near-duplicate peer cluster from exploding the ratio and false-firing on a barely-changed plan. |
| `RC_PLAN_NOVELTY_MUZZLE` | `1` | Redirect the backbone's fd-level stderr (e.g. the transformers/Mamba "fast path not available" warning) to `/dev/null` during embedding, so library noise is not surfaced as a hook error. Set `0` to debug the backbone. |
| `RC_PLAN_NOVELTY_CACHE` | `1` | Content-addressed disk cache for peer-plan embeddings (avoids re-running the SSM forward on unchanged prior plans every Write). Set `0` to always re-embed. |
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
| `RC_RULE_ENGINE` | `1` | Enables the symbolic gate co-emitted with the neural risk vector. Fail-closed by default; set `RC_RULE_ENGINE_LENIENT=1` to soft-degrade load/eval errors. |
| `RC_RULE_ENGINE_LENIENT` | _unset_ | `1` downgrades schema/load errors and per-rule exceptions to warn-only |
| `RC_RULE_ENGINE_ALLOW_BASIC_YAML` | _unset_ | `1` allows the in-tree `_basic_yaml_parse` fallback when PyYAML is missing (security-review flagged) |

Rules can carry per-call bypass comments: `# rc:skip-rule:<id>` (Python) or
`// rc:skip-rule:<id>` (JS/TS).

## Plan-to-contract compiler (Phase 1)

`PLAN.md` is compiled into a machine-readable contract on every edit. The
gate first looks for an explicit `.reasoning-core/contract.yaml`; if absent,
it derives a minimal contract from `PLAN.md` (allowed paths are the files
mentioned in the plan). Explicit contracts support:

- `allowed_paths` / `forbidden_paths`: glob lists.
- `phases`: ordered implementation phases; only the active phase's allow-list
  is enforced.
- `import_rules`: forbid specific imports per module scope.
- `invariants`: regex-based advisory/deny checks.

| Env var | Default | Purpose |
|---|---|---|
| `RC_PLAN_GROUNDING` | `1` | `0` off, `1` warn when an Edit drifts from the contract, `2` hard block. |

## Execution-grounded oracles (Phase 2)

Fast, local checks run against the proposed source before the sidecar call.
The cumulative session diff is tracked in `~/.cache/reasoning-core/rc-scratch/`
for future worktree-based oracles.

| Env var | Default | Purpose |
|---|---|---|
| `RC_ORACLE_T1` | `1` | T1 syntactic checks (`py_compile`, AST smoke). |
| `RC_ORACLE_T2` | `1` | T2 static checks (`ruff` on changed files, when installed). |
| `RC_ORACLE_BLOCK` | `0` | `1` blocks on oracle failures in `copilot`/`autopilot` mode; `0` warns only. |
| `RC_CACHE_DIR` | _unset_ | Override `~/.cache/reasoning-core` for scratch storage. |

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

## Process reward model (PRM) gate (Phase 3)

Scores `(plan_claim, diff_hunk)` via the generative repair head. In shadow mode
it audits every score; after promotion criteria are met it can block edits.

| Env var | Default | Purpose |
|---|---|---|
| `RC_PRM_GATE` | `0` | `1` enables the PRM gate (default OFF until training corpus lands) |
| `RC_PRM_BLOCK` | `0` | `1` allows the promoted PRM gate to block low scores |
| `RC_PRM_THRESHOLD` | `0.25` | Fraction of supported plan claims below which the edit is flagged |
| `RC_PRM_PROMO_MIN_REPOS` | `5` | Min distinct repo installs needed for promotion |
| `RC_PRM_PROMO_MIN_EVENTS` | `1000` | Min shadow events needed for promotion |
| `RC_PRM_PROMO_MIN_DAYS` | `14` | Min shadow observation period (days) before promotion |

Shadow events are stored in `$RC_CACHE_DIR/prm-shadow-state.jsonl` with mode
`0600`. The CLI command `rc audit-history` mines recent git history and labels
commits as positive/negative; the resulting labels can be fed back into
calibration and PRM threshold tuning.

## Self-improving calibration / commit miner (Phase 4)

| Env var | Default | Purpose |
|---|---|---|
| `RC_CACHE_DIR` | `~/.cache/reasoning-core` | Scratch + PRM shadow state + session diff cache |

The `rc audit-history` subcommand reads the last `n` commits, labels a commit
_negative_ if it was followed within 48 hours by a fix/revert/hotfix/patch
commit touching the same files, and prints a table (or JSON with `--json`).
These labels provide the feedback signal for recalibrating neural and symbolic
thresholds without manual labeling.

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

## Iter-3 levers (project-scoped)

| Env var | Default | Purpose |
|---|---|---|
| `RC_RUN_DIR` | _unset_ | Override for PLAN.md resolution; precedence: `RC_RUN_DIR` > `CLAUDE_PROJECT_DIR` > cwd |

`RC_PLAN_GROUNDING` and `RC_BEST_EFFORT_SPEC` were originally iter-3 opt-ins;
in Phase 0 they are enabled by default (see Hook policy posture above).

## Sidecar memory caps

The S2 sidecar and the gen sidecar each run a memory watchdog that polls
process memory and exits 75 (or SIGKILLs the gen child) when the cap is
exceeded. On macOS the reader is
`proc_pid_rusage(RUSAGE_INFO_V4).ri_phys_footprint` — the same value
Activity Monitor shows, including MLX / Metal / IOSurface unified-memory
allocations. The earlier `ru_maxrss` reader missed those entirely
(2026-06-02 incident: gen sidecar hit 37 GB under a "32 GB" cap because
the cap counted only CPU resident pages).

| Env var | Default | Purpose |
|---|---|---|
| `S2_MEM_LIMIT_GB` | `16` (`25` via plist) | S2 sidecar watchdog cap |
| `S2_GEN_MEM_LIMIT_GB` | `S2_MEM_LIMIT_GB` | gen sidecar watchdog cap (independent) |
| `S2_GEN_MEM_POLL_S` | `5.0` | gen watchdog poll interval seconds |
| `S2_SINGLE_INSTANCE` | `1` | S2 sidecar flock (`0` disables) |
| `S2_GEN_SINGLE_INSTANCE` | `1` | gen sidecar flock (`0` disables) |
| `S2_BACKBONE_FAIL_COOLDOWN_S` | `60` | Negative cache for failed backbone loads — prevents repeated multi-GB GGUF mmaps under request load |

Set in `launchd/com.reasoning-core.supervisor.plist` under
`EnvironmentVariables` so launchd-spawned children inherit them
(`.envrc` is shell-only). The supervisor's child-env allowlist
(`src/_supervisor_env.py`) forwards `S2_MEM_LIMIT_GB`,
`S2_GEN_MEM_LIMIT_GB`, `S2_GEN_MEM_POLL_S`, and the single-instance
flags to children.

## Multi-host

| Env var | Default | Purpose |
|---|---|---|
| `RC_HOST` | _auto_ | Force host identity (`claude` / `gemini` / `copilot` / `vibe`); auto-detected from `<HOST>_PROJECT_DIR` env |
| `RC_PROJECT_DIR` | _auto_ | Override project root (takes precedence over `<HOST>_PROJECT_DIR`) |
| `RC_SESSION_ID` | _auto_ | Stable per-launch session id; synthesised if unset and re-exported for child processes |
