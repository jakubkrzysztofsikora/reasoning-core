# OMP native boundary implementation status

Date: 2026-10-10
Plan: docs/superpowers/plans/2026-10-10-omp-tier1-delivery.md
Status: Tasks 1-6 implemented and tested; final review, exact Actions artifact and both-machine activation pending.

## Verified boundary

The installed OMP 18.2.11 native declarations expose EditSession.apply with a
serial per-file writer callback. They do not expose complete staged candidates
to an extension before the first write.

Upstream source aafb6551616fccec65cbfb264298e7f98df8ce5f confirms that
crates/pi-edit/src/session.rs stages the complete native batch in memory and
checks plan-mode permissions before entering the writer loop. This is the
required approval insertion point. The N-API bridge in
crates/pi-natives/src/edit.rs needs to expose that batch and await approval
before the native engine enters the writer loop.

Sources:
- https://github.com/can1357/oh-my-pi/blob/aafb6551616fccec65cbfb264298e7f98df8ce5f/crates/pi-edit/src/session.rs
- https://github.com/can1357/oh-my-pi/blob/aafb6551616fccec65cbfb264298e7f98df8ce5f/crates/pi-natives/src/edit.rs

Collecting requests from serial writer callbacks is insufficient: the native
engine commits snapshot/register updates between callbacks. Fabricating writer
success to collect candidates would advance native state without persisting
the corresponding content. Streaming previews also cannot establish complete
candidate parity. Neither approach implements the approved contract.

## Python contract slice

src/omp_contract.py validates protocol version, attribution, final argument
digest, complete candidate sets, plain absolute paths, project containment,
source-content digests, and approval bindings. Frozen records retain the
resolved paths observed during validation; source rechecks reject changed
resolution, changed bytes, and unexpected existence.

Wire clarification: before_src and expected_before_sha256 are both null for
a nonexistent source. An existing empty file uses an empty string and the
SHA-256 of its empty bytes. after_src is null only for deletion. A move is a
source-delete candidate plus a destination-write candidate, including the
destination's actual prior content. This prevents empty-file and missing-file
approvals from being interchangeable.

GateDecision binds batch_sha256, final_input_sha256, and candidate_sha256.
The batch digest includes attribution and resolved paths. These contracts
perform no writes, provide no filesystem lock, and cannot eliminate races
between verification and commit. The native commit boundary must enforce
source/input binding again and restrict concurrent mutation routes.

## Native contract milestone

The prior Python-only session limit is resolved. Task 1 now includes
integrations/omp/native-precommit.patch against pinned upstream source
aafb6551616fccec65cbfb264298e7f98df8ce5f (18.8.8). The patch extends
EditSession.apply with a third asynchronous full-batch approval callback.
Native staging uses an isolated store; denied or stale preparation preserves
shared snapshots, registers, and no-op counters. Approved no-ops retain native
loop escalation.

The callback receives raw persisted before/after UTF-8 bytes and SHA-256
source/input bindings. Moves include the source-delete and destination-write,
including an overwritten destination's prior content. Native code rechecks
every source after approval and the current write's source and destination
before each serial writer dispatch. Rewritten arguments during approval reject
the old preparation and require a fresh session/approval.

The full pi-natives addon builds with Rust nightly-2026-10-06. Bun 1.3.14
passes 20 native hashline boundary fixtures against that full addon. These cover
batch denial, actual byte parity/CRLF, registers, snapshot recovery, move/delete
and overwrite, stale sources and symlinks, revised arguments, callback failures,
repeated no-op escalation, and observed OS-failure prefixes. The full pi-edit
suite passes 349 tests. The reasoning-core Python suite passes 1401 tests,
with 7 skipped and 1 xfailed.

Independent review found source rechecks missing between writer callbacks and
lost approved no-op counters. Both have observed RED-to-GREEN regression tests.
Rust/patch gate_edit calls were allowed with unsupported_language; they do not
constitute neural Rust validation. Compilation and native tests are the evidence.

Pinned source/archive/patch/file/addon hashes and precise API fields are recorded
in integrations/omp/native-precommit.json. Reproduction and installation
requirements are in integrations/omp/README.md. Patch SHA-256:
8a0370cc5c017b2b5eb4c8563c5a27282e3b1c22ff6de0e0e7b61743e05972a5.

The existing installed OMP 18.2.11 remains unchanged. The full tested 18.8.8
addon is a development build in /tmp/rc-omp-native-20261010/target/debug.
The full build required installing Ninja 1.13.2. Apple Foundation Models was
stubbed by upstream because Swift 6.4/macOS 27 SDK is unavailable; no model
was invoked.

## Outstanding runtime and delivery gates

The normal upstream OMP EditTool still uses the two-argument native apply.
Task 2 must expose the native callback through a session-scoped OMP extension
event and invoke the Python bridge with final identity/input/candidate bindings.
Regenerate N-API declarations and build the OMP package against the patched API.
Applying the native patch alone does not activate reasoning-core enforcement.

Only native hashline component behavior is demonstrated. Guarded deletion of
non-UTF-8 content is refused. There is no filesystem lock or protection against
the final check-to-write syscall race. Concurrent mutation routes still need
runtime serialization/restrictions. Native failed outcomes return an empty
files result; the fixtures observe writer prefixes, while correlated audit
receipts remain a runtime responsibility.

The remaining plan requires the TypeScript adapter, mutation/child restrictions,
rc init lifecycle, doctor, exact-version real-host qualification, independent
whole-branch review, exact-commit GitHub Actions wheel build, and verification
of that same wheel on both Macs. No wheel build or machine activation has
occurred. Native Tier 1 support remains unqualified.

## Python bridge milestone

src/hooks/omp_bridge.py provides prepare, result, session_start, and session_stop
JSON events. Each event executes in a fresh isolated interpreter with explicit
project/session/agent context. Subprocess arguments are fixed; input travels
through stdin, and worker diagnostics cannot enter protocol stdout. Timeout,
process failure, malformed output, and guard evaluation failures block unknown
mutations.

The bridge reuses shared deterministic language/plan/rule dispatch, parse/lint
oracles, structural checks, worktree lease checks, and guard-path protection.
Shared structural helpers accept the entire staged source view, including
created and deleted files, without persisting candidates. The authenticated
HTTP transport is shared through src/mcp_gate.py request_score; the existing
MCP gate retains its policy behavior. OMP evaluates neural regressions as
advisory, and a report's own deterministic_signal cannot establish independent
corroboration. Configured sidecar failure policy remains explicit.

The shared host shim recognizes omp; protection covers project/global OMP
settings and extension directories. Audit decisions include agent/parent ids,
tool-call ids, and the complete batch digest. Receipt verification requires a
recorded allowed approval and actual filesystem readback. A per-decision lock
prevents concurrent receipt replay. Failed writes may report an actual landed
prefix; receipt acceptance does not imply transactional rollback.

Live local verification used a disposable directory at
/tmp/rc-omp-bridge-pgIE61 with the real authenticated sidecar:
- Allow: 8aa0f005b7d8, deterministic_checks_passed.
- Deny: a6898b550781, rule_engine:no-os, original content unchanged.
- Receipt: acbd878bf845, receipt_verified.
- Replay: f5d7baabadec, receipt_replay.
- Persisted probe SHA-256:
  69ef4f409b530264b0da21ad4054978ada08895c93631a0da1957e2a10b592b5.

These are Python bridge checks, not native OMP execution or a host qualification
artifact. Task 2 remains partial until the TypeScript runtime invokes this
bridge through the native batch approval boundary. Operator bypass-next
integration, native shell/eval/delegation restrictions, real child inheritance,
rc init ownership/lifecycle, doctor, packaging, independent whole-branch review,
Actions build, and both-machine activation remain required before delivery.

## Runtime continuation

Task 2 now has canonical and bundled TypeScript adapters plus a pinned host patch.
EditTool supplies whole native batches through bounded session-scoped extension
handlers. Decisions bind input, candidates, and resolved project/path hashes;
receipts read the filesystem even after native failure. The loaded native addon
reports protocol 1; old addons do not pass capability checks. Operator bypass-next
uses the existing authenticated override state after integrity/lease checks.
Real EditTool/ExtensionRunner/Python allow and deterministic-deny fixtures pass;
these do not qualify child merging, other mutation tools, installed-wheel loading,
or either active machine. Tasks 3-9 remain in progress.

## Current Delivery Status (Tasks 1-6)

The earlier pending-runtime paragraphs are historical milestones. The pinned
18.8.8 host now invokes the native complete-batch callback through session-scoped,
bounded extension handlers. Canonical/bundled adapters use the isolated installed
Python bridge, validate all approval bindings and publish filesystem receipts.
The guarded writer serializes in-process project edits and disables fallback,
LSP transformations, internal URLs and model repair.

Native edit and read/grep/glob/find are the initial supported routes. Shell,
eval/Python, plain write, AST/LSP mutation, custom tools, archive/SQLite mutation,
automatic task spawning and isolated parent merges are refused. Operator-created
SDK children retain the prepared guard and exact child/parent/session identity.

Owned-asset init/upgrade/uninstall preserves modified or unowned assets and rejects
symlinked paths. Doctor reports installed, loaded, authenticated and qualified
separately. Its normal CLI probe verifies discovered adapter and loaded addon
hashes without a model call. Qualification uses real hashline editing, whole-batch
guard-path denial, actual audit receipt, inherited SDK child denial and route
restrictions; the immutable report binds runtime/configuration hashes.

Verification: 42 Bun native/runtime/host tests, 202 assertions; full Python suite
1432 passed, 8 skipped, 1 xfailed. Installed-wheel smoke catches and now fixes
missing adapter/evaluation subpackages, then runs init outside the checkout.
Managed supervisor SIGTERM and scoring-child SIGKILL recovery are verified locally,
with cache accessibility and authenticated post-recovery scoring.

Final whole-branch review is running. A release native build, exact reviewed-SHA
GitHub Actions wheel, and activation/readback on both Macs remain required.
The existing active OMP runtime is unchanged. These fixtures establish bounded
containment behavior, not model quality or protection against external syscall
races. Tasks 7-9 are not complete.
