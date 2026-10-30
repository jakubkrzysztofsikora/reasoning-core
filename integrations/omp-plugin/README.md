# reasoning-core plugin for omp

Enforcement gates + advisor evidence feed for the [omp](https://github.com/badlogic/pi-mono) harness, backed by [reasoning-core](../..).

## What it does

- **Gate extension** (`extensions/reasoning_core_gate.ts`) — loads in every omp session. Two knobs, each resolved `env > settings file > default`:
  - `mode` — `off` (registers no handlers; stock omp behavior), `advisory` (default), `enforce`.
    - `advisory` — every gated route is evaluated and nothing blocks. A policy refusal comes back as `additionalContext` (`reasoning-core advisory (policy_downgraded): would have blocked: <reason>`); infrastructure failures are logged once and the call proceeds.
    - `enforce` — a policy refusal blocks. Needs a resolved runtime; without `.reasoning-core/omp-runtime.json` the session degrades to `advisory`.
  - `on-error` — `open` (default) or `closed`. Controls what happens when the gate cannot decide: bridge down, timeout, malformed reply, unresolved runtime, handler exception, or a worker-side failure (audit append, screen crash, scoring sidecar). `open` allows the call and warns with the reason; `closed` blocks it. A gate that cannot decide reports; only `on-error closed` makes that fatal.
  - Env: `RC_GATE=off|advisory|enforce`, `RC_GATE_ON_ERROR=open|closed`.
  - Settings file: `~/.omp/agent/reasoning-core-gate.json` (`{"mode": "...", "onError": "..."}`), written by `/rc`. Override the path with `RC_GATE_SETTINGS`.
  - Gated routes in `advisory`/`enforce`: `bash`, `write`, native edits, `before_subagent_spawn`, `session_stop`.
  - Edits route through `native_edit_prepare`/`native_edit_result` only on builds that expose `api.nativeEditProtocol === 1`. Official builds have no such API: edits are not governed there — one advisory note, never a block.
  - Every other route is ungated: read-only tools, `eval`, `task`, `todo`, `ask`, web tools, non-file `write` targets (`xd://` devices, `local://`, `ssh://`, `agent://`), `write` targets that resolve outside the session project root or exceed the 4 MiB governed text limit (reported as `not_governed`, never blocked), and operator `!`/`$` commands. Mutations through those routes (for example `eval` writing files or `xd://ast_edit`) bypass rc.
- **Operator command** `/rc` — no env-var dance, no agent self-service:
  - `/rc` — current mode, on-error policy, setting source (`env`/`file`/`default`/`session`) and settings path.
  - `/rc mode off|advisory|enforce` (aliases `enable` = `enforce`, `disable` = `off`), `/rc on-error open|closed`, `/rc reset`, `/rc help`.
  - Also accepts the env spellings: `/rc RC_GATE enforce`, `/rc RC_GATE=enforce`, `/rc RC_GATE_ON_ERROR closed`.
  - Changes take effect immediately in the running session and persist to the settings file for later sessions. A live env var still wins at load; `/rc` reports when one is present.
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

Every gate decision crosses the extension → bridge → worker boundary. The table below is the `enforce` contract; in `advisory` every row is allow plus one warning, because nothing in that mode is allowed to stop work. `on-error` decides the infrastructure rows: `open` (default) allows and warns, `closed` blocks. "Fail-closed" means the action is blocked; "fail-open" means the action proceeds.

| Event | Failure | `on-error open` | `on-error closed` |
| --- | --- | --- | --- |
| `tool_call` (bash/write) | Missing command or path, non-file target, path outside project root, >4 MiB | Not governed — one `not_governed` note | Not governed — one `not_governed` note |
| `tool_call` (bash/write) | Bridge timeout, worker crash, unparseable reply, unresolved runtime | Allow + advisory naming the reason | Blocked (`reasoning-core: <reason>`) |
| `tool_call` (bash/write) | Worker-side infrastructure failure (screen crash, audit append fails, scoring sidecar down) | Allow + advisory naming the reason | Blocked (`reasoning-core: <reason>`) |
| `tool_call` (bash/write) | Policy refusal from rc (`bash_screen_blocked`, guarded path, deterministic oracle) | Blocked | Blocked |
| `native_edit_prepare` | Bridge failure, malformed reply, or worker-side infrastructure failure | Allowed | Denied (`reasoning-core: <reason>`) |
| `native_edit_prepare` | Policy refusal from rc | Blocked | Blocked |
| `tool_result` (write) | Missing pending approval or refused receipt | Warning only (receipts never block) | Warning only |
| `before_subagent_spawn` | Any handler error, including a worker-side infrastructure reply | Spawn allowed + advisory naming the reason | Blocked (`reasoning-core: <reason>`) |
| `session_stop` | Bridge/worker/audit failure | Stop allowed + warning | Blocked (`reasoning-core session audit unavailable`) |
| `session_stop` | Duplicate-review pipeline error | **Fail-open** (`dup_stop_fail_open`) — the dup oracle is advisory; a broken reviewer must not trap the session | Same |
| `session_stop` | Policy refusal from rc | Blocked | Blocked |
| `advisor_cost_changed` / `advisor_yielded` | Any failure (bridge, worker, audit) | **Fail-open, always** — observational ledger rows; the gate swallows every error so advisor status updates are never disrupted | Same |

Infrastructure reasons, the ones `on-error` governs, are `bridge_unavailable`, `bridge_timeout`, `bridge_process_failed`, `invalid_bridge_output`, `invalid_bridge_response`, `invalid_event`, `bridge_audit_unavailable`, `guard_evaluation_failed`, `bash_screen_error`, `sidecar_unavailable`, `sidecar_auth_failed` and `invalid_score_report`. Every other blocked reason is a policy refusal and blocks in both `on-error` modes.

Known OMP internal URL writes use OMP scheme-specific write approval. OMP
rejects unknown URI-like write targets before dispatch.

Two asymmetries are deliberate:

1. **`tool_call` throws block, `before_subagent_spawn` throws do not.** omp's runner fail-closes tool calls on handler exceptions but fail-opens subagent spawns, so the gate wraps the subagent path and converts every error into an explicit decision instead of relying on the host.
2. **`session_stop` dup-review errors fail open, audit errors follow `on-error`.** Losing a duplicate-detection verdict costs a review opportunity; losing the audit row breaks the evidence chain every other guarantee rests on.
