# OMP Tier 1 Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. The user reviews this plan and selects execution method before OMP implementation.

**Goal:** Support native OMP through rc init, build the reviewed package in GitHub Actions, and install and verify the same wheel on this machine and Jakub's MacBook Pro.

**Architecture:** An OMP runtime extension obtains complete native prepared mutations and calls a Python bridge into the shared deterministic guard chain. Decisions are bound to final arguments and source digests; unqualified mutation paths are restricted. Installed-wheel qualification, exact-commit Actions artifacts, and two-machine activation establish delivery.

**Tech Stack:** Python, TypeScript/Bun, OMP native edit engine, setuptools wheels, GitHub Actions, macOS launchd, key-only SSH.

**Spec:** docs/superpowers/specs/2026-10-10-omp-tier1-design.md

## Global Constraints

- Native hashline support is the target; do not silently substitute replace-only support.
- Deterministic policy/plan/parse/lint/structural checks are standalone hard-block sources; neural scoring stays advisory.
- Preserve enforcement posture and configured infrastructure-failure behavior.
- Credentials remain in Python/shared authentication; never embed them in extension assets or logs.
- Preserve existing dirty work; all source/test writes require successful gate_edit validation.
- Record exact tested OMP version/build; 18.2.11 and 18.8.8 were inspected/reported during research, not qualified.
- Read eval/baselines/README.md and inspect baseline-2026-08-09 before relevant policy/claim changes; capture a new immutable context before comparison claims.
- Build through .github/workflows/wheel-build.yml and install the same verified wheel on both named Macs.
- workflow_dispatch artifacts suffice; no public tag/release or PyPI publication is required.
- Never select paid fallback models implicitly during qualification.
- Service recovery work precedes OMP implementation; test registered service, child recovery, authenticated scoring, and cache access separately.

## Review Focus

- Paths with spaces, symlink escapes, rename destinations, and internal URLs must not evade candidate resolution (Tasks 1, 3).
- Later argument rewrites, changing source files, and denied final batch members must not permit stale approval or partial policy-denied writes (Tasks 1, 3).
- Isolated children, delegated native calls, and parent merges must retain the gate (Task 3).
- Existing user-modified files and same-version stale/editable package imports must not be silently overwritten or mistaken for the delivered wheel (Tasks 4, 7).
- GUI logout, SSH/background startup, inaccessible external model caches, and asynchronous launchd teardown must not strand installed guards (Tasks 5, 7).

## File Responsibilities

- src/omp_contract.py: validated candidate/event/decision wire contracts.
- src/hooks/omp_bridge.py: stdin entry point into shared guards, authentication, and audit.
- .omp/extensions/reasoning_core_gate.ts: canonical OMP runtime adapter.
- src/data/templates/omp.reasoning_core_gate.ts: bundled canonical asset; assert parity.
- integrations/omp/native-precommit.patch: only if the supported upstream build lacks the required public API.
- src/data/templates/omp.compatibility.json: version/build and native-boundary compatibility metadata.
- src/_init.py: OMP installation and owned-file lifecycle.
- src/_omp_install.py: focused OMP installer/upgrade/uninstall helpers.
- src/_omp_doctor.py: discovery, load, authentication, and qualification checks.
- src/hooks/_host_env.py and src/hooks/_guard_paths.py: identity and guard integrity.
- src/rc_cli.py: init/upgrade/uninstall/doctor integration.
- pyproject.toml and .github/workflows/wheel-build.yml: shipped dependencies/assets and package checks.
- eval/host_enforcement_qualification/omp.py: version-bound host qualification.
- scripts/verify-omp-install.py: installed-runtime readback on either machine.
- scripts/install-supervisor-launchagent.sh and src/_launchd.py: shared resilient launchd setup.
- Tests: tests/test_omp_contract.py, tests/test_omp_bridge.py, tests/omp_native_boundary.test.ts,
  tests/omp_runtime.test.ts, tests/test_omp_install.py, tests/test_omp_doctor.py,
  tests/test_omp_wheel.py, tests/test_supervisor_launchd.py.
- README.md, docs/INSTALL.md, docs/CLI_PARITY.md: demonstrated support and operational instructions.

Existing authentication changes are user-owned. Integrate with the current shared
resolver without committing those unrelated changes. If native API findings change
the chosen architecture, revise the written spec before continuing implementation.

### Task 1: Establish the Native Pre-Commit Contract

**Interfaces:** Candidate has path, operation (write/delete), before_src, after_src,
and expected_before_sha256; move is source-delete plus destination-write. PreparedBatch
has protocol_version=1, project_dir, session_id, agent_id, parent_agent_id,
tool_call_id, final_input_sha256, and candidates. GateDecision has decision
(allowed/blocked), reason, decision_id, and candidate/input digests.

- [x] Add native-boundary fixtures for multi-file hashline, persistent register paste,
  snapshot recovery, moves/deletion, a denied final candidate, and revised arguments.
  Assert complete candidate parity with native execution and zero writes on denial.
- [x] Run bun test tests/omp_native_boundary.test.ts; observe failure because the
  complete pre-commit approval boundary is unavailable.
- [x] Identify a versioned public API providing the entire prepared batch before
  the first write. Otherwise implement the narrow patch in integrations/omp/
  native-precommit.patch against a pinned source SHA; record its digest and installation contract.
- [x] Define validated Python contracts in src/omp_contract.py; reject malformed
  fields and paths before dispatch. Add Python schema/parity tests.
- [x] Require native final-input/source digest validation immediately before commit;
  reset preparation after later argument changes. Reuse native parser/register state.
- [x] Re-run Bun and Python contract tests. All fixtures pass without using live-file
  mutation to discover candidates; otherwise report the native support blocker.
- [x] Commit only this verified slice.

### Task 2: Shared Guard Bridge and Runtime Adapter

**Interfaces:** src/hooks/omp_bridge.py exposes handle_event(event: dict) -> dict
and main() -> int. Events are prepare, result, session_start, and session_stop;
prepare consumes Task 1's PreparedBatch and returns GateDecision. Result references
tool_call_id, decision_id, persisted digests, and success/error.

- [x] Add bridge/runtime tests asserting forbidden imports block, valid edits pass,
  neural-only regressions stay advisory, authentication uses the shared resolver,
  and missing/invalid bridge output does not allow a hard-enforcement mutation.
- [x] Run pytest tests/test_omp_bridge.py and bun test tests/omp_runtime.test.ts;
  verify the new adapter is missing.
- [x] Implement normalized subprocess JSON stdin/stdout with fixed argv, bounded
  timeout, and no shell interpolation. Use context cwd/session/agent identity.
- [x] Reuse shared deterministic guard decisions and existing authenticated scoring;
  keep diagnostic stdout separate from the protocol and keep secrets out of it.
- [x] Add omp identity to _host_env; pass identity per invocation without mutating
  the shared OMP process environment. Correlate pre-decisions and post-receipts.
- [x] Run bridge/runtime/host-environment tests and existing auth regressions;
  commit the verified slice.

### Task 3: Close Mutation and Delegation Escapes

**Interfaces:** Every supported mutation produces Task 1 candidates; every
unsupported mutation produces an explicit restriction in the qualification profile.

- [x] Add denial fixtures for protected extension/settings, symlink escape,
  move-overwrite, internal URL dispatch, archive/SQLite writes, shell/eval writes,
  auto-repair/format changes, AST/LSP edits, and custom tool delegation.
- [x] Add isolated-child and parent-merge cases proving the runtime gate stays
  loaded and no denied candidate reaches the parent filesystem.
- [x] Observe failing escape/coverage tests before implementing restrictions.
- [x] Route exact candidates through the bridge; restrict unqualified execution
  routes. Arbitrary Python/shell cannot be certified by regex matching alone.
- [x] Protect OMP guard assets through _guard_paths and the applicable runtime
  execution boundary. Avoid granting tools operator authority to disable the guard.
- [x] Run all native/runtime tests; require all-file nonmutation on policy denial
  and accurate landed-prefix receipts on actual OS write failure.
- [x] Commit the verified coverage slice and document precise exclusions.

### Task 4: rc init and Owned-File Lifecycle

**Interfaces:** src/_omp_install.py exposes install_omp(target, manifest,
substitutions, result) -> None, called by the default src/_init.py init path.
Ownership metadata records path and installed SHA-256; uninstall/upgrade operate
only on unchanged owned files.

- [x] Test fresh init, re-init, paths containing spaces, wheel resource loading,
  absent OMP binary, existing unrelated settings/extensions, changed owned files,
  upgrade, and uninstall. Assert user modifications survive.
- [x] Run pytest tests/test_omp_install.py tests/test_rc_init.py; observe missing
  OMP integration failures.
- [x] Install .omp/extensions/reasoning_core_gate.ts from the bundled asset with
  resolved Python/framework paths. Preserve settings arrays and MCP/profile files.
- [x] Wire default rc init, upgrade, uninstall, manifest entries and gitignore
  handling. Preserve current unrelated host semantics; do not require a separate manual installer.
- [x] If the shell installer remains supported, make it use the same assets and
  ownership rules. Pin any required OMP patch/build in compatibility metadata.
- [x] Run lifecycle and existing installer regressions; commit the verified slice.

### Task 5: Readiness and Service Recovery

**Interfaces:** src/_omp_doctor.py exposes check_omp(project: Path) -> list[dict].
Each check has name, ok, detail, and state; state distinguishes installed,
loaded, authenticated, and qualified. scripts/verify-omp-install.py emits a
JSON machine report and exits nonzero when required runtime checks fail.

- [ ] Test disabled discovery, wrong version/build, healthy-but-unauthenticated
  sidecar, stale qualification digests, unavailable executable, and malformed check output.
- [ ] Observe failing doctor tests; implement OMP checks in doctor/rc init --check
  without making another host's missing hooks fail an OMP-only check.
- [ ] Verify the resolved interpreter/import path and actual extension load via
  a bounded no-paid-model probe. Never treat /health alone as authenticated readiness.
- [ ] Include the restored launchd behavior: Aqua/Background loading, unconditional
  KeepAlive with throttle, owned cache-path preservation, and asynchronous reload retries.
- [ ] Verify SIGTERM/clean-exit supervisor restart, scoring-child SIGKILL recovery,
  model cache accessibility, and successful authenticated score after recovery.
- [ ] Run pytest tests/test_omp_doctor.py tests/test_supervisor_launchd.py plus the
  focused supervisor/auth suite; commit the verified slice.

### Task 6: Host Qualification and Package Checks

**Interfaces:** eval/host_enforcement_qualification/omp.py writes an immutable
report with OMP version/build/source SHA, adapter hashes, configuration, mutation
coverage/exclusions, and allow/deny/receipt results. No cross-host quality inference.

- [ ] Add installed-wheel tests that import bridge dependencies, resolve the
  canonical extension/compatibility assets, and run init outside the source checkout.
- [ ] Observe failing package tests for missing assets/modules; update package
  discovery/data so dependencies such as src.hooks.adapters are actually shipped.
- [ ] Capture the OMP baseline context; run native and real-host allow/deny
  qualification in disposable repositories with the selected model/backend explicit.
- [ ] Run focused tests/lint, required CI-equivalent checks, and a local wheel smoke.
  Update support docs to the demonstrated scope only; preserve unrelated dirty prose.
- [ ] Extend wheel-build.yml with installed-wheel OMP checks and distribution
  checksum/provenance output; retain pinned Action dependencies.
- [ ] Commit scoped implementation and qualification/documentation changes.

### Task 7: GitHub Actions Build at the Reviewed Commit

- [ ] Review the full scoped diff and preserve all unrelated staged/dirty files.
  Push verified commits using the approved branch/worktree delivery procedure.
- [ ] Start wheel-build.yml through workflow_dispatch at the delivery ref.
  Record the expected commit before dispatch.
- [ ] Wait for required lint/test and package checks. Inspect each result and fix
  failures; a pushed branch or submitted workflow is not completion.
- [ ] Download reasoning-core-distributions from the exact successful run.
  Verify run headSha equals the reviewed commit; record run id, package version,
  wheel/sdist filenames, and SHA-256 digests in the delivery record.
- [ ] Stage that artifact for both machines. Do not rebuild locally and substitute
  a different wheel. No PyPI publication or public release tag is required.

### Task 8: Install and Activate on This Machine

- [ ] Inventory machine identity, rc/OMP paths, active Python/package imports,
  supervisor references, and intended initialized repositories; capture a rollback archive.
- [ ] Create an isolated versioned runtime environment and install the exact
  Task 7 wheel with required dependencies, reusing accessible model caches.
- [ ] Verify metadata, imported code paths, asset digests and any qualified OMP
  build before activating the intended rc entry point and owned supervisor/hooks.
- [ ] Run installed rc init on intended existing wiring with preservation checks,
  and restart the managed service only as needed.
- [ ] Run scripts/verify-omp-install.py from outside the checkout and through
  the normal user launch path. Verify init/re-init, doctor, authenticated scoring,
  native hashline allow/deny, unchanged digests after denial, receipts and child coverage.
- [ ] Write the local machine report; if activation fails, restore the archived
  entry points/owned config and leave local delivery incomplete.

### Task 9: Install and Activate on Jakub's MacBook Pro

- [ ] Connect using ssh -o BatchMode=yes -o ConnectTimeout=10 syncd-test.
  Confirm hostname/ComputerName identify Jakub's MacBook Pro before remote writes.
- [ ] Refresh the inventory using absolute paths. Earlier non-login SSH lacked rc/
  omp on PATH and distribution metadata contained multiple versions; do not trust those snapshots.
- [ ] Transfer the Task 7 wheel and verify its remote SHA-256 matches the local
  digest. Archive remote owned entry points/configuration without resetting dirty repos.
- [ ] Install in a compatible isolated versioned environment, activate the intended
  rc/OMP and supervisor references, and refresh intended wiring through installed rc init.
- [ ] Repeat Task 8's installed-runtime verification on the MacBook, including
  normal user launch and background/GUI service readiness.
- [ ] Write the remote machine report; restore previous owned configuration on
  failed activation. Retry temporary bridge unavailability; do not mark completion
  while the MacBook remains unreachable or unverified.

## Final Acceptance and Handoff

- [ ] All implementation/qualification tests pass and the exact reviewed commit
  has a successful GitHub Actions package build.
- [ ] Both machine reports identify the same verified wheel digest and imported
  installation, qualified OMP build, loaded extension, authenticated readiness,
  successful allowed writes and unchanged denied writes.
- [ ] Document coverage/exclusions, artifacts/run links, rollback archives and
  any remaining limitations. Make no unmeasured quality/comparison claims.
- [ ] Do not mark the feature complete at any earlier milestone.

## Plan Review

Self-review must check spec coverage, interface consistency, ownership safety,
native candidate approval, installed-wheel execution, exact-commit provenance,
and both machine acceptance reports. OMP implementation begins only after the
user reviews this plan and selects an execution method.
