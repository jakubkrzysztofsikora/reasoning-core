# OMP native boundary implementation status

Date: 2026-10-10
Plan: docs/superpowers/plans/2026-10-10-omp-tier1-delivery.md
Status: Python wire contracts and guard bridge implemented; native runtime pending.

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

## Outstanding dependency and delivery gates

This execution session permits Python production code only. Implementing the
required Rust native boundary and TypeScript OMP adapter is outside that
constraint. Task 1 remains incomplete; native Tier 1 support is unqualified.

Continue Task 1 in a session permitting those languages, using the existing
Python contracts and red/green tests. Add native parity tests for registers,
snapshot recovery, move/delete, final-member denial, and argument rewrites
before implementing the native patch. Preserve native state on denial and
audit a landed prefix on actual write failure.

The remaining plan still requires the shared Python bridge, mutation-route
restrictions, rc init lifecycle, doctor, exact-version native qualification,
independent review, exact-commit GitHub Actions wheel build, and verification
of that same wheel on both Macs. No package or machine activation is justified
by the Python contract tests alone.

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
