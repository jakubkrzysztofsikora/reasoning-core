---
name: rc-controls
description: Operate reasoning-core enforcement inside omp — check gate state, read audit evidence, explain decisions, verify installation. Use when a gate denial needs interpretation, when verifying rc is active in this repo, or when tuning rc behavior for omp.
---

# reasoning-core controls (omp plugin)

## Gate states

The plugin gate resolves per session cwd:

- **dormant** — neither `.reasoning-core/omp-runtime.json` nor `.reasoning-core/omp-owned.json` exists (no dir, or an rc install for other hosts only). rc does nothing; omp behaves stock.
- **failclosed** — omp install markers present but `omp-runtime.json` missing, corrupt, or partial. bash, file writes, edits, subagent spawns, and session stop are blocked with `reasoning-core runtime unresolved`. Fix: run `rc init --host omp`.
- **active** — valid runtime json. bash, file writes, subagent spawns, and session stop are gated; edits route through the native precommit protocol (patched builds) and are blocked on unpatched builds.

Only those routes are gated. Read-only tools, `eval`, `task`, `todo`, `ask`, web tools, non-file `write` targets (`xd://`, `local://`, `ssh://`), and operator `!`/`$` commands pass through rc untouched.

Check state: the omp log line `reasoning-core gate loaded` records the resolved mode (`~/.omp/logs/omp.<date>.<pid>.log`).

## Verify installation

```bash
rc doctor --host omp --project-dir . --json
```

`omp_plugin_installed` + `omp_plugin_loaded` must both be true. `omp_loaded`/`omp_qualified` refer to the patched-build native scope and may be false on unpatched hosts.

## Read evidence

- Audit log: `$RC_AUDIT_ROOT/<YYYY-MM-DD>/<session_id>.jsonl` (default `~/.local/share/reasoning-core/events`).
- Explain one gate decision: `rc explain <decision_id>`.
- Never assert a gate outcome without a decision id or audit row.

## State and knobs (read-only for agents)

omp-side knobs live in `~/.omp/config.yml` (or project `.omp/config.yml`):
`tools.approvalMode`, `tools.approval.<tool>`, `tools.restrictedFolders`,
`ttsr.*`, `advisor.*`, `mcp.enableProjectConfig`. Read them; never write them.

rc-side knobs are environment variables (project `.envrc`/`.envrc.local`).
Effective values are always visible via `rc status --host omp`:

```bash
rc status --host omp
```

Shows gate mode, plugin install state, qualification evidence count, RC_* env knobs, and today's audit/advisor-ledger row counts. Fast and side-effect-free — agents may run it any time. Deep verification (spawns omp probes): `rc doctor --host omp`.

Knobs are environment variables read by the bridge worker per event. omp's `config.yml` has no rc settings; set rc knobs in the project `.envrc`/`.envrc.local` or the session environment. Effective values are always visible via `rc status --host omp`.

| Knob | Effect |
| --- | --- |
| `RC_MODE` | `advise` = audit+warn only; `copilot` = hard blocks on deterministic violations |
| `RC_STRUCTURAL_BLOCK` | `1` (default) = import cycles/duplicate definitions block; `0` = warn only |
| `RC_PLAN_GROUNDING` | `0` off, `1` warn, `2` block edits not grounded in an approved plan |
| `RC_RULE_ENGINE` | `1` = `.reasoning-core/rules.yaml` policies enforced |
| `RC_ORACLE_T1` / `RC_ORACLE_T2` / `RC_ORACLE_BLOCK` | Pre-execution oracle thresholds and blocking |
| `RC_IMPORT_CYCLE_ALLOWLIST` / `RC_DUPLICATE_ALLOWLIST` | Comma-separated `symbol@path` exemptions |
| `RC_OMP_BRIDGE_TIMEOUT_S` | Bridge subprocess budget, 1–120 s (default 30) |
| `RC_AUDIT_ROOT` | Audit log root (default `~/.local/share/reasoning-core/events`) |

## Mutations (operator CLI only)

Agents never mutate enforcement state. Operators only:

- `rc bypass-next` — arm one authenticated bypass for the next edit (requires `RC_ENFORCEMENT_AUTH`).
- `rc enable-enforcement --hard` / `rc disable-enforcement` — switch RC_MODE posture.
- `rc init --host omp` — write/repair the runtime json.

## Repair

- Runtime unresolved → `rc init --host omp` (writes `.reasoning-core/omp-runtime.json`).
- Plugin missing → `omp plugin install <git-url>` or `omp plugin link integrations/omp-plugin`, then restart omp.
- Advisor roster → copy `WATCHDOG.yml.example` to the project root or `~/.omp/agent/WATCHDOG.yml`.
