# Why the reasoning-core omp gate blocks work, and the redesign that ends it

Research date: 2026-10-30. Sources: local repo, installed `omp` binaries, bundled
omp documentation (`omp://*.md`), upstream oh-my-pi issues and releases.

## 1. Executive finding

The gate does not block because the policy is strict. It blocks because four
independent infrastructure conditions are wired to `block: true`, and three of
them are **normal states** rather than faults:

| # | Condition | Current behaviour | Frequency |
|---|---|---|---|
| B1 | `api.nativeEditProtocol !== 1` | every `edit` blocked | **always**, on any official omp build |
| B2 | runtime files absent/corrupt/partial | mode `failclosed` → bash, write, edit, subagent blocked; `session_stop` refuses completion | any un-`rc init`ed project, any half-written `.reasoning-core/` |
| B3 | `ctx.cwd` not a string on the event context | `runtimeConfig("")` throws → `failclosed` → same as B2 | any handler whose ctx lacks `cwd` |
| B4 | bridge slower than host budget | host times out first → omp fails the call closed | every slow Python start on a loaded machine |

B1 is the decisive one. `nativeEditProtocol` / `native_edit_prepare` /
`native_edit_result` are **not omp APIs**. They exist only in the locally
patched build produced by `integrations/omp/host-precommit.patch`. Verified by
string search over all three installed binaries:

```
omp                                   (18.14.0, official)  nativeEditProtocol=0  native_edit_prepare=0
omp.official-18.11.3.bak              (18.11.3, official)  nativeEditProtocol=0  native_edit_prepare=0
omp.1793365178494.91194.0.bak         (patched build)      nativeEditProtocol=2  native_edit_prepare=9
```

The active install (`~/.local/bin/omp`, 214 MB, mtime 2026-10-30 14:00) is
official 18.14.0. `integrations/omp/PIN.json` still records
`"version": "18.11.3", "patched": true, "editPrecommitProtocol": 1`. The pin
went stale when the operator upgraded, and the gate's precondition silently
disappeared with it. Nothing warned, because the gate treats "protocol absent"
as a policy violation rather than as a host capability mismatch.

Consequence if the gate were installed today: `edit` blocked unconditionally,
`before_subagent_spawn` blocked unconditionally, `user_bash`/`user_python`
refused, and `session_stop` refusing completion. That is a total work stop, and
it is the historical failure mode being reported.

Nothing is currently loaded (`~/.omp/agent/extensions/` holds only `bicz`,
`moshi-hooks`, `nexgate-history-guard`, `tts-cast`; `disabledExtensions` names
three RC *skills*, not the extension). So the stop is latent, and re-armed by
the next `RC_OMP_STRICT=1 rc init --host omp` or plugin install.

## 2. The second-order defect: blocking is not even enforcement

omp's `tool_call` block becomes a thrown error and then an **error tool-result**;
the agent loop continues and the model retries or routes around it. Upstream
tracks exactly this as open issue
[#2974](https://github.com/can1357/oh-my-pi/issues/2974), confirmed still
current on `main` by a maintainer reply on 2026-07-14:

- `ToolCallEventResult` is `{ block?, reason? }` — no `halt`.
- `ExtensionToolWrapper.execute` turns a block into `throw new Error(reason)`.
- `session_stop` owns only the *continuation* direction (`{continue,
  additionalContext}`), never termination.

So a fail-closed gate does not stop the run; it converts every tool call into a
recoverable error the model must spend turns working around. The observable
symptom is "the agent is stuck and keeps trying things", not "the agent
stopped". Fail-closed here buys no safety and costs the whole session.

## 3. omp contract facts the design must respect

From the bundled docs (`omp://extensions.md`, `omp://extension-loading.md`,
`omp://hooks.md`):

- `tool_call` **fails closed on handler error or timeout**, budget
  `extensionHandlers.toolCallTimeoutMs`, default **30 000 ms**. The hook layer
  behaves the same: a throwing handler blocks the call.
- Our bridge budget is `35 000 ms` (`options.timeoutMs ?? 35000` in both gate
  files) — **longer than the host budget it runs inside**. B4 is a direct
  consequence: on timeout omp blocks before the gate can answer "allow".
- `{ additionalContext }` on `tool_call` / `tool_result` is the sanctioned
  non-blocking advisory channel: trusted, developer/system priority, deduped by
  the host. This is the correct vehicle for "RC would have flagged this".
- `session_stop` accepts `{ continue: true, additionalContext }` (capped at 8) or
  `{ decision: "block", reason }`; the refusal persists until the hook allows
  completion or the operator interrupts. A gate that returns `block` on
  "audit unavailable" therefore pins the session shut.
- `before_subagent_spawn` returns `{ model?, block?, reason?, note? }`.
- Extensions run in-process, unsandboxed; a raw `setTimeout` that throws is an
  uncaught exception and tears the session down — `ctx.setTimeout` /
  `ctx.setInterval` only.
- Escape hatches that already exist: `--no-extensions`,
  `disabledExtensions: extension-module:<name>`, `--trusted-extension <abs>`,
  per-project `.omp/config.yml`.
- There is no per-directory trust prompt; `isProjectTrusted()` is always true.
  Discovery globs respect `.gitignore` with `hidden: false`, so a gitignored
  gate asset is never auto-discovered.

Upstream is moving fast: v18.14.0 shipped 2026-10-29 with breaking changes in
`@oh-my-pi/pi-agent-core` (Bun event-loop yield utilities removed). Any design
that hard-depends on an unpatched-in API will break again on the next bump.
That is the durable lesson: **negotiate capabilities, never assume them.**

## 4. Redesign: advisory-first, capability-negotiated, bounded

### 4.1 Mode model

One mode per session, resolved once, cached, logged once.

| Condition | Mode |
|---|---|
| `RC_GATE=off`, or extension not loaded | `off` |
| runtime files absent | `advisory` |
| runtime present but unresolvable (corrupt, partial, non-absolute) | `advisory` + one warning |
| runtime resolved, `RC_GATE` unset or `advisory` | `advisory` |
| runtime resolved, `RC_GATE=enforce`, session-start bridge probe OK | `enforce` |
| runtime resolved, `RC_GATE=enforce`, probe failed | `advisory` + one warning |

`advisory` is the default. Enforcement becomes a deliberate act, not the
consequence of a file existing. The strict project adapter keeps
`enforce` as its default (it is installed explicitly), but honours
`RC_GATE=advisory|off` and degrades per-path exactly as below.

### 4.2 Capability negotiation

Probed once at load / `session_start`, never thrown on:

- `nativeEdit`: `api.nativeEditProtocol === 1`. Absent → the native edit path is
  ungovernable. **Allow edits and say so once.** Blocking `edit` on an unpatched
  host is denial of service wearing an enforcement costume.
- `bridge`: one `session_start` round-trip. Failure → `advisory`.

### 4.3 Policy matrix

| Surface | `advisory` | `enforce` |
|---|---|---|
| `tool_call` bash | screen; on `bash_screen_blocked` → allow + `additionalContext` warning | screen; block on `bash_screen_blocked` |
| `tool_call` write | prepare; on blocked → allow + warning | block on explicit `blocked` |
| `tool_call` edit | never blocked (native path governs when present) | never blocked; native path governs |
| `native_edit_prepare` | `{allowed: true}` when no runtime; audit-only when runtime present | as today |
| `before_subagent_spawn` | allow + note on bridge failure | block only on explicit `blocked` |
| `session_stop` | never `{decision:"block"}` | block only on a real duplicate decision |
| bridge error/timeout | allow + one warning | block, reason `bridge_unavailable` |

Rule: **infrastructure failure never blocks in advisory mode; policy failure
always does.** Only four sources may produce `block: true` in `enforce`:
`bash_screen_blocked`, explicit prepare `blocked`, explicit subagent `blocked`,
and a real `session_stop` duplicate decision. Everything else — missing runtime,
missing protocol, missing `ctx.cwd`, bridge timeout, audit-write failure,
malformed reply — degrades to advisory.

### 4.4 Budgets

- Extension-side bridge timeout drops to **8 000 ms**, comfortably under omp's
  30 000 ms `toolCallTimeoutMs`. The host must always be able to receive an
  answer.
- No new `setInterval`/`setTimeout`; use `ctx.setTimeout` where a timer is
  genuinely needed.

### 4.5 Observability

- One `api.logger.warn` per session: resolved mode, why, and the exact command to
  change it.
- Advisory notices name the mode and the decision that was downgraded, so the
  model can act on the warning instead of retrying blindly.

## 5. Rollout

1. Ship the mode model in both gate files (plugin + strict adapter). Behaviour
   change is opt-in via `RC_GATE=enforce`.
2. `rc doctor --host omp` gains a `mode` line and a `nativeEdit` capability line,
   so a stale pin is visible instead of silent.
3. `eval/host_enforcement_qualification/omp.py` must qualify the **official
   unpatched** build as the baseline host. Today the qualification only passes
   on the patched binary, which is why the drift went unnoticed.
4. `integrations/omp/README.md` stops claiming a pinned 18.11.3 install and
   states the supported range plus the capability each version must expose.
5. Patch maintenance becomes optional: the patch buys the native edit boundary
   only. Everything else works, in advisory form, on stock omp.

## 6. Verification

- Advisory on official 18.14.0 with the gate loaded: edits, writes, bash and
  subagents all proceed; warnings appear once.
- Enforce with a healthy runtime: `bash_screen_blocked` still blocks; a bridge
  kill switch produces `bridge_unavailable`, not a hang.
- Enforce with `RC_GATE` unset never happens — the default is advisory.
- Bridge timeout < host budget asserted by test.
## 7. Fifth blocking source found live, and closed (2026-10-30)

The four conditions above were found by reading code. Running the gate against the
real harness (omp 18.14.0, `RC_GATE=enforce`, project with a resolved runtime)
exposed a fifth, which the earlier analysis had attributed to "malformed reply":

**B5 - the bridge contract rejects ordinary writes.** `src/omp_contract.py`
refuses two shapes the extension used to send without hesitation:

- `Candidate.from_dict` raises `candidate is outside project` for any write target
  outside the resolved project root (scratch files, `/tmp`, sibling repos,
  `~/.claude`). Writes outside the project are normal agent behaviour.
- `_text` raises `exceeds native text limit` above `_MAX_TEXT_BYTES` (4 MiB) for
  either `before_src` or `after_src`.

Either rejection reaches the extension as `decision: invalid_event`, which the old
code classified as `invalid_bridge_response` and blocked. That is the root cause of
the "write unreviewed: invalid bridge response" warnings: the batch was
ungovernable by construction, not corrupt.

Fix (plugin gate, `writePrepare`): resolve the target first and refuse to build a
batch the contract cannot accept.

| Condition | Before | Now |
|---|---|---|
| target outside project root | bridge call, `invalid_event`, block | no bridge call, `{ note }`, advisory `not_governed` |
| `before_src`/`after_src` above 4 MiB | same | same |
| bridge answers `invalid_event` anyway | block | `infraDenial`, advisory |

`writePrepare` returning `{ note }` is the third outcome beside allow and block:
the call is not governed, is reported once, and never blocks in any mode.

### Live evidence (official 18.14.0, `RC_GATE=enforce`)

- `omp -p "Write probe_live_gate.txt ..."` in this repo: file written with the exact
  bytes, audit `omp:prepare allowed` then `omp:result receipt_verified`, no gate
  warning in `~/.omp/logs/omp.*.log`.
- Same run: the bash screen refused `printf > file`, so enforcement still bites.
- Extension probe (`eval/host_enforcement_qualification/omp_extension_probe.ts`,
  enforce, ambient overrides stripped): `bash_allowed`, `bash_blocked`,
  `write_roundtrip`, `subagent_recorded`, `advisor_recorded` all true.
- `~/.omp/plugins/node_modules/reasoning-core` is a symlink into
  `integrations/omp-plugin`, so a repo edit is live after an omp restart;
  `plugin.json` integrity digests must be regenerated on every gate change.

### Remaining gap, stated plainly

On official builds `edit` is still ungoverned: `native_edit_prepare` does not
exist, `tool_call` sees only `{path, old_string, new_string, replace_all}`, and
reconstructing the post-edit bytes means reimplementing fuzzy whitespace matching,
where a wrong reconstruction produces a false digest and a false block.
`registerFileWriteFallback` is not a pre-write hook (omp consults it only after a
real I/O failure), so it cannot close this. Governing `edit` on official builds
needs either the patched build, or a `registerTool` shadow of `edit` that delegates
through `ctx.invokeTool` after recording its own receipt - a design to evaluate
separately, not a reason to block today.

### Upstream state, checked against `main` (2026-10-30)

- [`#3948`](https://github.com/can1357/oh-my-pi/issues/3948) "Extension tool_call
  handlers run without a timeout, so a hung extension blocks tool execution" is
  **closed (completed)**: the fix added a handler budget. It did not change the
  failure direction - `docs/extensions.md` on `main` still says "`tool_call` errors
  and timeouts block execution (fail-closed). Its budget is
  `extensionHandlers.toolCallTimeoutMs` (default 30,000 ms), paused during extension
  UI waits." So upstream will not fail open for us; the extension must not let a
  decision hang in the first place. That is why the bridge budget is 8 000 ms and
  every non-policy outcome degrades to advisory.
- [`#4223`](https://github.com/can1357/oh-my-pi/issues/4223) asks for a per-mode
  budget, noting that raising it "raises the fail-closed budget for every
  non-interactive handler" - an argument against ever relying on the host timeout
  as a safety net.
- `docs/hooks.md` on `main` documents two runners: the legacy hook runner imposes
  no `tool_call` timeout, the extension runner has its own budget. A gate that
  assumes one runner's semantics breaks on the other; ours bounds itself.
- `ctx.invokeTool(name, params, options)` exists on `main` and is present in the
  installed official 18.14.0 binary (14 string hits, alongside
  `toolCallTimeoutMs`; `nativeEditProtocol` has zero). It runs the native built-in
  when a registered tool shadows it, and the native call is not re-gated. This makes
  the `registerTool("edit")` shadow route real on official builds - it is the one
  path that can govern `edit` without a patched host, and it needs no fuzzy-match
  reimplementation if the shadow delegates: run the native edit, read the resulting
  file, then record the receipt against the observed bytes. That is post-hoc
  evidence rather than a pre-write veto, which is the correct trade for a gate whose
  contract is "report, never stop work".
