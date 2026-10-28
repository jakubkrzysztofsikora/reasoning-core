# reasoning-core plugin for omp

Enforcement gates + advisor evidence feed for the [omp](https://github.com/badlogic/pi-mono) harness, backed by [reasoning-core](../..).

## What it does

- **Gate extension** (`extensions/reasoning_core_gate.ts`) — loads in every omp session, resolves per-cwd state:
  - `dormant` — no `.reasoning-core/` dir: stock omp behavior.
  - `failclosed` — runtime json missing/corrupt: all mutation routes blocked.
  - `active` — valid runtime: bash, subagent spawns, session stop, and file writes are all gated. Writes use the native precommit protocol on patched builds; on unpatched builds they use the extension's write prepare/result events.
- **Skill** `rc-controls` — auto-discovered; how to check gate state, read audit evidence, repair.
- **Advisor roster template** (`WATCHDOG.yml.example`) — gives the omp advisor read-only access to rc audit evidence.
- **Advisor cost ledger** — `advisor_cost_changed` / `advisor_yielded` session events append `omp:advisor` audit rows (`signal_source=omp-advisor`) so advisor spend and yield are part of the same evidence chain. Observational only: these events never gate.

## Install

```bash
omp plugin install https://github.com/craving-for-alignment/reasoning-core
```

The marketplace manifest lives at `integrations/omp-plugin/.omp-plugin/`; omp clones the repo and installs the plugin from that path.

### Scopes

- **User (default)** — `~/.omp/plugins/`: active in every project.
- **Project** — `<project>/.omp/plugins/`: active in that project only; shadows the user-scope copy.

The gate is dormant unless the project has a `.reasoning-core/` runtime, so user-scope install is safe everywhere.

### Local development

```bash
omp plugin link integrations/omp-plugin
```

Symlinks the repo checkout into `~/.omp/plugins/`; edits apply on next omp start.

## Verify

```bash
rc doctor --host omp --project-dir . --json
```

`omp_plugin_installed` and `omp_plugin_loaded` must both be `true`. Load evidence also appears as the `reasoning-core gate loaded` line in `~/.omp/logs/omp.<date>.<pid>.log`.

## Requirements

- omp ≥ 18.2.11 (plugin events: `session_start`, `tool_call`, `tool_result`, `user_bash`, `user_python`, `before_subagent_spawn`, `session_shutdown`); advisor cost ledger additionally uses `advisor_cost_changed` / `advisor_yielded` (present in 18.11.3).
- reasoning-core CLI on PATH (`rc`), initialized per project (`rc init --host omp`).
- Native edit batch approval additionally requires a patched omp build (see `../omp/`); on unpatched builds writes are gated by the extension's write prepare/result events instead.

## Failure semantics

Every gate decision crosses the extension → bridge → worker boundary. The table below is the contract for what happens when each stage fails. "Fail-closed" means the action is blocked; "fail-open" means the action proceeds.

| Event | Failure | Outcome |
| --- | --- | --- |
| `tool_call` (bash/write) | Missing/invalid fields | Blocked (`invalid_event`) |
| `tool_call` (bash/write) | Bridge timeout, worker crash, unparseable reply | Blocked (`bridge_timeout` / `bridge_process_failed` / `invalid_bridge_output`) |
| `tool_call` (bash) | Screen command errors (exit 1) | Blocked (`bash_screen_error`) |
| `tool_call` (bash) | Audit append fails | Blocked (`bridge_audit_unavailable`) |
| `tool_call` (write) | Path outside project, unknown URI scheme | Blocked |
| `tool_result` (write) | Replay of an already-consumed approval | Blocked (`receipt_replay`) |
| `tool_result` (write) | Missing pending approval for the tool call | Extension throws → omp blocks the tool natively |
| `before_subagent_spawn` | Any handler error | Gate catches internally and blocks (`subagent audit unavailable`) — omp itself fail-opens on this event, so the gate must not throw |
| `session_stop` | Bridge/worker/audit failure | Blocked fail-closed |
| `session_stop` | Duplicate-review pipeline error | **Fail-open** (`dup_stop_fail_open`) — the dup oracle is advisory; a broken reviewer must not trap the session |
| `session_stop` | Audit append fails | Blocked (`bridge_audit_unavailable`) |
| `user_bash` / `user_python` | n/a | Refused by design — operator actions never route through rc |
| `advisor_cost_changed` / `advisor_yielded` | Any failure (bridge, worker, audit) | **Fail-open, always** — observational ledger rows; the gate swallows every error so advisor status updates are never disrupted |

Two asymmetries are deliberate:

1. **`tool_call` throws block, `before_subagent_spawn` throws do not.** omp's runner fail-closes tool calls on handler exceptions but fail-opens subagent spawns, so the gate wraps the subagent path and converts every error into an explicit block.
2. **`session_stop` dup-review errors fail open, audit errors fail closed.** Losing a duplicate-detection verdict costs a review opportunity; losing the audit row breaks the evidence chain every other guarantee rests on.
