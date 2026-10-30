# reasoning-core plugin for omp

Enforcement gates + advisor evidence feed for the [omp](https://github.com/badlogic/pi-mono) harness, backed by [reasoning-core](../..).

## What it does

- **Gate extension** (`extensions/reasoning_core_gate.ts`) — loads in every omp session. `RC_GATE` picks the mode:
  - `off` — registers no handlers; stock omp behavior.
  - `advisory` (default) — every gated route is evaluated and nothing blocks. A policy refusal comes back as `additionalContext` (`reasoning-core advisory (policy_downgraded): would have blocked: <reason>`); infrastructure failures (unresolved runtime, bridge down, timeout, malformed reply, host build without the native edit boundary) are logged once and the call proceeds.
  - `enforce` (`RC_GATE=enforce`) — a policy refusal blocks. Infrastructure failures still allow: a gate that cannot decide reports, it does not stop work. Needs a resolved runtime; without `.reasoning-core/omp-runtime.json` the session degrades to `advisory`.
  - Gated routes in `advisory`/`enforce`: `bash`, `write`, native edits, `before_subagent_spawn`, `session_stop`.
  - Edits route through `native_edit_prepare`/`native_edit_result` only on builds that expose `api.nativeEditProtocol === 1`. Official builds have no such API: edits are not governed there — one advisory note, never a block.
  - Every other route is ungated: read-only tools, `eval`, `task`, `todo`, `ask`, web tools, non-file `write` targets (`xd://` devices, `local://`, `ssh://`, `agent://`), `write` targets that resolve outside the session project root or exceed the 4 MiB governed text limit (reported as `not_governed`, never blocked), and operator `!`/`$` commands. Mutations through those routes (for example `eval` writing files or `xd://ast_edit`) bypass rc.
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

Advisory is the default, so a user-scope install cannot block work in any project: where `rc init --host omp` has not run, the gate logs the unresolved runtime once and every call proceeds.

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

- omp 18.11.3+ for `session_stop`; advisor cost events use `advisor_cost_changed` / `advisor_yielded`. Verified advisory on official 18.14.0.
- reasoning-core CLI on PATH (`rc`), initialized per project (`rc init --host omp`).
- Native edit batch approval needs a patched omp build (see `../omp/`). On official builds edits are ungated (advisory note only); writes stay gated through the extension's write prepare/result events.

## Failure semantics

Every gate decision crosses the extension → bridge → worker boundary. The table below is the `enforce` contract for what happens when each stage fails; in `advisory` every row is allow plus one warning, because nothing in this gate is allowed to stop work. "Fail-closed" means the action is blocked; "fail-open" means the action proceeds.

| Event | Failure | Outcome |
| --- | --- | --- |
| `tool_call` (bash/write) | Missing/invalid fields | Blocked (`invalid_event`) |
| `tool_call` (bash/write) | Bridge timeout, worker crash, unparseable reply | Blocked (`bridge_timeout` / `bridge_process_failed` / `invalid_bridge_output`) |
| `tool_call` (bash) | Screen command errors (exit 1) | Blocked (`bash_screen_error`) |
| `tool_call` (bash) | Audit append fails | Blocked (`bridge_audit_unavailable`) |
| `tool_call` (write) | Path outside project, `file://` URI, or bracketed path | Blocked |
| `tool_result` (write) | Replay of an already-consumed approval | Blocked (`receipt_replay`) |
| `tool_result` (write) | Missing pending approval for the tool call | Extension throws → omp blocks the tool natively |
| `before_subagent_spawn` | Any handler error | Gate catches internally and blocks (`subagent audit unavailable`) — omp itself fail-opens on this event, so the gate must not throw |
| `session_stop` | Bridge/worker/audit failure | Blocked fail-closed |
| `session_stop` | Duplicate-review pipeline error | **Fail-open** (`dup_stop_fail_open`) — the dup oracle is advisory; a broken reviewer must not trap the session |
| `session_stop` | Audit append fails | Blocked (`bridge_audit_unavailable`) |
| `advisor_cost_changed` / `advisor_yielded` | Any failure (bridge, worker, audit) | **Fail-open, always** — observational ledger rows; the gate swallows every error so advisor status updates are never disrupted |

Known OMP internal URL writes use OMP scheme-specific write approval. OMP
rejects unknown URI-like write targets before dispatch.

Two asymmetries are deliberate:

1. **`tool_call` throws block, `before_subagent_spawn` throws do not.** omp's runner fail-closes tool calls on handler exceptions but fail-opens subagent spawns, so the gate wraps the subagent path and converts every error into an explicit block.
2. **`session_stop` dup-review errors fail open, audit errors fail closed only in `enforce`.** Losing a duplicate-detection verdict costs a review opportunity; losing the audit row breaks the evidence chain every other guarantee rests on.
