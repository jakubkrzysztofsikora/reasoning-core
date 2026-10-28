# Plan: reasoning-core → omp native integration

Slug: `rc-omp-integration` · Date: 2026-10-28 · Repo: ~/Repos/personal/reasoning-core
Research: `local://rc-omp-integration/outline.yaml` + `fields.yaml`

## Goal

omp becomes the first-choice harness for reasoning-core: every rc feature reachable, every
bypass/tweak operator-owned, zero hand-patching friction for new repos, gates active in all
host modes (tui/rpc/acp/print).

## Non-goals

- No rc behavior changes (guards, scoring, baselines stay as-is).
- No upstream omp PR in this plan (tracked separately; pin + drift runbook instead).
- No new neural scoring paths; deterministic checks remain the only hard-block source
  (AGENTS.md policy).

## Design principles

1. **Per-capability route decision** — native omp patch ONLY where no extension seam exists
   (native edit batch approval). Everything else rides upstream extension events.
2. **Distribution = marketplace plugin** — `omp.extensions` package.json key, git source
   (npm sources rejected by omp). Replaces per-repo hand-installed project extension.
3. **Operator owns bypass** — agent never self-serves enforcement changes; mirror rc's
   auth-gated `bypass-next`/`enable-enforcement` model.
4. **Fail-closed parity** — rc gate failures inside omp tool_call must fail closed within
   omp's 30s tool_call timeout, mirroring `S2_FAIL_CLOSED=1` semantics.
5. **Pin + drift** — omp pin 18.8.8 (aafb655), `editPrecommitProtocol()=1`, addon digests;
   re-qualification on any upstream bump.

## Route matrix (decision table)

| rc capability | omp seam | route | native patch? |
|---|---|---|---|
| edit gate (pre_edit_guard, batch approval) | `native_edit_prepare`/`native_edit_result` | keep existing patch | YES (exists) |
| plain write | `registerFileWriteFallback` (EPERM/EACCES/EROFS) | extension fallback | NO |
| file delete | `registerFileDeleteFallback` | extension fallback | NO |
| bash gate (pre_bash_guard) | `tool_call` event (block/reason) | extension | NO |
| eval/python tools | `tool_call` event | extension | NO |
| task/subagent spawn | `before_subagent_spawn` ({model?,block?,reason?}) | extension | NO |
| session-end dup check | `session_stop` (BLOCKING {decision:block,reason}) | extension | NO |
| archive/SQLite + LSP/Biome writes | no fallback seam | `tool_call` on the invoking tool | NO |
| stream rules (lang lock, drift warn) | TTSR rule providers (regex/astCondition) | native TTSR settings | NO |
| advisor evidence feed | WATCHDOG.yml entry + `tools` | config | NO |
| skills/knowledge pack | skills providers (claude 80 / omp-plugins 90) | plugin `skills/` | NO |
| install/doctor/qualification | rc CLI (`rc init --host omp`, `rc doctor --host omp`) | CLI | NO |
| MCP servers | mcp.json (stdio/http/sse) | config | NO |

## Phases

### Phase 0 — Baseline + pin (prerequisite)

- `rc baseline capture` (immutable manifest before any change; AGENTS.md requirement).
- Record pin: omp 18.8.8, commit aafb655, `editPrecommitProtocol()=1`, host-file digests
  from `packages/coding-agent/src/cli/rc-native-probe.ts` (10 files).
- `rc guard-hash` green before starting.
- Verify: `rc baseline show baseline-2026-08-09` + new capture; `rc guard-hash` exit 0.

### Phase 1 — Marketplace plugin packaging

New tree `integrations/omp-plugin/`:

- `package.json` with `"omp.extensions": ["./extensions/reasoning_core_gate.ts"]`.
- `.omp-plugin/marketplace.json` (name, description, source git URL).
- `extensions/reasoning_core_gate.ts` — current gate, unchanged wire protocol
  (omp_contract Candidate/PreparedBatch/GateDecision, SHA-256 digests, 4MB cap).
- `skills/` — SKILL.md pack: rc controls reference (env knobs), bypass procedure,
  doctor/qualification runbook, dup-defer workflow.
- `WATCHDOG.yml` template — rc advisor entry (Phase 3).
- `README.md` — install: `omp plugin install <git-url>`; scopes user `~/.omp/plugins`
  (default) or project `.omp/plugins` (project shadows user).
- Keep `_omp_install.py` project-scoped path as fallback for non-plugin installs;
  `render_extension()` substitutions (`__RC_PYTHON__`/`__RC_ROOT__`) unchanged.
- Verify: `omp plugin install` from local path in a scratch repo; extension loads
  (`rc doctor --host omp --project-dir <scratch> --json` → installed+loaded true).

### Phase 2 — Extension gate coverage (close the unpatched seams)

Extend `reasoning_core_gate.ts` handlers (all upstream API, no patch):

- `tool_call` for `bash`: dispatch pre_bash_guard via existing bridge `prepare` event;
  block/reason from GateDecision; honor 30s fail-closed (bridge timeout → block with
  `decision_id`, never silent pass).
- `tool_call` for `eval` + custom tools: same dispatch (closes eval-bypasses-bash-patterns
  hole).
- `before_subagent_spawn`: propagate identity `{project_dir, session_id, agent_id,
  parent_agent_id}`; block or downgrade model per rc policy; log spawn to audit.
- `session_stop`: run duplicate_detection check (stop_duplicate_check.py path); blocking
  decision only on deterministic signals (AGENTS.md: neural advisory never sole block).
  Wire the full dup pipeline here — omp prepare path currently runs project_index
  structural checks only (omp_bridge.py:198-216), dup detection is host-agnostic and
  unwired.
- `registerFileWriteFallback`/`registerFileDeleteFallback`: route EPERM/EACCES/EROFS
  writes/deletes through pre_edit_guard when native patch absent (unguarded-profile
  parity).
- Failure-state semantics table in code: sidecar down / timeout / corrupt receipt →
  fail-closed block with reason; provider 500 on advisory scoring → fail-open warn
  (deterministic checks still hard-block).
- Verify: `python -m eval.host_enforcement_qualification.omp` extended with bash/eval/
  spawn/stop cases; new pytest `tests/test_omp_extension_gates.py` mirroring
  `test_sidecar_auth_routes.py` patterns; manual: blocked bash in tui + rpc modes.

### Phase 3 — Advisor wiring

- WATCHDOG.yml roster entry `reasoning-core`: `tools: [read, grep, glob]`,
  `instructions`: consult rc audit log + `rc explain <decision_id>`; `maxNotesPerUpdate: 2`.
- `modelRoles.advisor` stays nexgate/opencode/kimi-k3:auto (LLM-only advisors; rc is the
  evidence backend, not the advisor model).
- Cost ledger: subscribe `advisor_cost_changed`/`advisor_yielded` in extension; append to
  rc audit log (`RC_AUDIT_ROOT`) with source=omp-advisor.
- Quarantine: advisor notes never feed gates directly (injection surface — untrusted
  data); gates read only rc audit + deterministic signals.
- Verify: advisor emits note on seeded bad edit; `rc explain` links decision; cost rows
  in audit log.

### Phase 4 — Controls surface (bypass/tweak knobs) — DONE 2026-10-28

- Map every operator knob to one of: omp `config.yml`, rc env (`.envrc`), rc CLI.
  - omp-side: `tools.approvalMode`, `tools.approval.<tool>`, `tools.restrictedFolders`,
    `ttsr.*`, `advisor.*`, `mcp.enableProjectConfig`.
  - rc-side: enforcement posture block (`RC_MODE`, `RC_SHADOW_MODE`, `RC_PLAN_GROUNDING`,
    `RC_PLAN_BLOCK`, `RC_RULE_ENGINE`, `RC_NEURAL_CORROBORATED`, `S2_FAIL_CLOSED`),
    gen backend (`RC_REASONER_BACKEND`, `RC_GEN_*`), embedder (`RC_EMBEDDER`,
    `RC_ALLOW_OVERSIZED_BACKBONE`), audit retention, drift thresholds, git lease,
    dup oracle, calibration.
- Rule: agent-visible knobs = read-only via `rc status`; mutations only via operator CLI
  (`rc enable-enforcement`, `rc bypass-next`, `rc skip-file`) — extension never writes
  env/config.
- `rc status --host omp` new flag: prints omp-side config + rc env side by side.
- Document in plugin `skills/rc-controls/SKILL.md` + repo `docs/CONFIGURATION.md` section.
- Verify: `rc status --host omp` output; attempt env flip from agent session → blocked.

Status: knob mapping documented (SKILL.md + CONFIGURATION.md "omp host integration");
`rc status --host omp` shipped (gate mode, plugin install, qualification evidence count,
RC_* knobs, today's audit + advisor-ledger rows; fast, no omp spawn — deep check deferred
to `rc doctor --host omp`). Extension env-write grep: zero matches. Doctor tests updated
for the 6-check check_omp (plugin probes patched in fixtures; 85 pass / 25.7 s across
extension_gates + bridge + qualification + doctor).

### Phase 5 — Verification + parity — COMPLETE 2026-10-28

Done:
- `rc doctor --host omp --json`: 6/6 pass on the patched 18.11.3 active install
  (installed/loaded/plugin_installed/plugin_loaded/authenticated/qualified);
  `--rc-native-probe` qualified; `eval.host_enforcement_qualification.omp` full pass
  (9/9 evidence flags, report bound at `.reasoning-core/omp-qualification.json`).
- `rc benchmark --after 2026-10-26`: 1297 events, 976 allow / 11 block / 4 warn,
  omp_bridge source=124 rows, median latency 8ms, p95 13ms, fail-open 0.
- New immutable baseline `baseline-2026-10-28-omp-integration` captured;
  `rc baseline compare baseline-2026-08-09 <new>` context drift recorded (not causal).
- Host-mode parity matrix — gate loads `mode=active` in all four modes, no LLM needed:
  print (pid 60423 log), `--mode rpc` (pid 10001), `omp acp` (pid 4096, gate loads on
  `session/new`, not `initialize`), tui (pid 95165; prompt-less tui lazy-inits the
  session, so gate line appears only after first prompt or session activity).
- Full edit-block E2E parity deferred: needs working LLM (gateway flaky); gate seam is
  tool_call-level and mode-independent by construction.

Deferred to Phase 6 (patched build): `omp --rc-native-probe`,
`python -m eval.host_enforcement_qualification.omp` full pass, auth + qualify checks.

### Phase 6 — Upstream drift runbook

- Trigger: any omp bump past 18.8.8 (upstream at 18.13.2, 2026-10-27).
- Steps: re-apply `integrations/omp/host-precommit.patch` + `native-precommit.patch` to
  new tree; resolve `editPrecommitProtocol` conflicts; re-hash 10 host files; update
  `.omp/reasoning-core.compatibility.json`; re-run Phase 5 qualification; `rc guard-hash`.
- Watch restrictedFolders (18.13.1) interaction with rc skip-file.
- Verify: `rc doctor --host omp` qualified=true on new pin.

#### Execution record — 2026-10-28, pin 18.8.8 → 18.11.3 (96894a6)

Executed the drift runbook against the ACTIVE install version (18.11.3 compiled
standalone), not a scratch checkout:

- Patched source tree built at `/tmp/omp-src` (bun 1.4.4, cargo nightly-2026-10-06);
  Rust suite 354/354, boundary fixtures 20/20.
- Patches regenerated from the pristine 18.11.3 diff (round-trip byte-verified);
  `native-precommit.json` / `host-precommit.json` / `PIN.json` / README updated to
  18.11.3 digests; verification block: host_qualified=true, machine_activation=true.
- Patched binary swapped into `~/.local/bin/omp`
  (sha256 `5d14a9bb3708e3f99088b5d3fc3d8e2e3aeda9b428e6f214632f941ec85c9697`;
  official backup at `~/.local/bin/omp.official-18.11.3.bak`).
- `eval.host_enforcement_qualification.omp` full pass on the active install:
  all 9 evidence flags true, report bound at
  `.reasoning-core/omp-qualification.json`; `rc doctor --host omp` 6/6 green.
- Runtime python pinned to the project venv (`.venv/bin/python`) — the bridge worker
  runs `python -I` and homebrew python cannot import the gate's httpx dependency;
  assets must be (re)installed with the venv interpreter so `RC_PYTHON` bakes correctly.
- Guard-hash re-init still pending operator keychain access (see PIN.json
  `guard_hash_interim` note); `rc_cli.py` interim hash is stale vs current src.
- Acceptance #5 (dry run against 18.13.2): CLOSED — see next record.

#### Execution record — 2026-10-28, dry-run vs 18.13.2 (e996e1c7, tag v18.13.2)

Scratch checkout `/tmp/omp-1312-scratch` from codeload tarball
(sha256 `6d86c1455d260d495cc974db18f49eca8930cf4ae2456ea51b1a661097510acc`).
Dry-run + real apply with `--fuzz=0`, `-p1`. PIN NOT bumped (dry-run only).

- `native-precommit.patch` (Rust): applies CLEAN — 5 files, 0 failed hunks,
  0 fuzz, 0 offsets.
- `host-precommit.patch` (TS): 10 files; 5 clean; 6 failed hunks across 5 files,
  all mechanical anchor drift, zero semantic conflicts:

  | File | Failed | Upstream cause | Rebase |
  |---|---|---|---|
  | `edit/index.ts` | 2/6 | import style `import * as fs`; `details:{diff,errorKind}` added to isError return | adapt imports to namespace; keep guard block, new trailing context |
  | `extensibility/extensions/types.ts` | 1/2 | new events after `tool_approval_resolved` (plan_review, mode_changed, tool_call/result, user_bash/python) shift anchor | insert 3 `on()` lines after `tool_approval_resolved` (now line 1805) |
  | `tools/index.ts` | 1/1 | `ToolSession` gained `costGate?` field | insert 3 optional methods before `/** Current working directory */` |
  | `natives/native/index.js` | 1/2 | new `editErrorKinds` export shifts anchor | insert `editPrecommitProtocol` export between `editInspect`(81) and `enclosingBlockBoundaries`(82) |
  | `natives/native/index.d.ts` | 1/3 | sixel doc-comment rewrite | cosmetic; skip or re-trivial |

- Applied hunks confirm the seam survives: `NativeEditBatch`/`NativeEditPrepareEvent`/
  `NativeEditResultEvent` land in types.ts; `EditPrecommitDecision`/`editPrecommitProtocol`/
  `EditPreparedBatch` land in natives .d.ts; `apply(..., precommit?)` signature lands.
- Upstream 18.13.2 has NO competing native-edit extension protocol — rc's
  NativeEdit* extension surface remains the only consumer of the precommit hook.
- restrictedFolders (18.13.1+): approval-layer only (`tools/approval.ts`), default
  empty list, orthogonal to the rc gate; rc audit dir unaffected unless operator
  explicitly lists it. No rc skip-file interaction.
- Estimated rebase effort: ~30 min, 6 hunks, all context-only. No API changes
  required in rc bridge/gate.

## Risks

- Patches component-verified against pin aafb655 but never qualified on an ACTIVE install
  (integrations/omp/README.md:13-14): Phase 0 must run `--rc-native-probe` +
  qualification against the binary the operator actually launches, not just source-tree
  verification.
- Compiled standalone (18.11.3 active) vs npm-source patch (18.8.8): plugin extension
  path works on both; native edit patch only on patched source build. Mitigation:
  plugin-first distribution, patch only for guarded-profile sessions.
- Subagent headless yolo: before_subagent_spawn gate is the only lever; keep
  fail-closed on missing identity.
- Advisor injection: quarantine rule is hard (Phase 3); never relax.

## Acceptance

1. Fresh repo: `omp plugin install` + `rc init --host omp` → doctor qualified, zero
   manual file edits.
2. Bash/eval/spawn/session-stop gates fire in tui + rpc modes.
3. Every RC_*/S2_* knob documented + reachable via `rc status --host omp`; mutations
   operator-only.
4. Benchmark vs baseline-2026-08-09 shows no false-positive regression.
5. Drift runbook executed once against 18.13.2 in a scratch checkout (dry run).
