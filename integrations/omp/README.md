# OMP native precommit boundary

This patch adds a complete-batch approval callback to OMP's native edit engine.
It is tested against source `96894a6147d8cc64b7e00ad3a7d7178fd93b9531`
(version 18.11.3), with Rust `nightly-2026-10-06` and Bun 1.4.4.
`native-precommit.json` records the archive, patch, source-file, and tested
development-addon digests.

The complete native `pi-natives` addon was built and exercised. This is
component verification. The companion `host-precommit.patch` wires the third
callback through session-scoped `native_edit_prepare` and `native_edit_result`
extension events, including isolated interpreter dispatch and filesystem receipts.
Both patches and the reasoning-core extension are required. Active installations
remain unchanged until mutation-route qualification and delivery are complete.

## Native API

`EditSession.apply(request, writer, precommit?)` accepts a third asynchronous
callback, following the existing error-first N-API convention:

```typescript
async function precommit(error, batch) {
  if (error) throw error;
  // Send batch.candidates and finalInputSha256 to the Python guard bridge.
  return { allowed: guardAllowed, finalInputSha256: batch.finalInputSha256 };
}
```

The callback runs after native staging and plan-mode checks, before the first
writer callback. Candidates describe actual persisted UTF-8 bytes, preserving
BOM/line endings and native recovery/register semantics. Fields are camelCase
at N-API; the Python protocol uses snake_case. Null source and digest mean
absence; an existing empty file has empty source and the empty-byte digest.
A move includes source deletion and destination write, with the destination's
actual previous content. Sources/paths must be unique after resolution.

The host must validate the entire guard decision against the prepared input
and candidates before returning native approval. A rejection, callback failure,
malformed decision, stale final-input digest, or argument rewrite during approval
ends the call before writes. Rewritten calls require a fresh session and approval.
Preparation uses a fork of the shared store: denied/stale staging does not publish
snapshot/register/no-op changes. Approved no-ops preserve native loop escalation.

Native code rechecks all candidate sources after approval and checks the current
write's source and move destination before each writer dispatch. Earlier writer
callbacks cannot silently invalidate a later source. A changed later source or
actual OS failure can still leave an already written prefix. The runtime must
record actual filesystem readback and correlate it with approval; native error
outcomes still have an empty `files` result. The tests' writer observations
demonstrate prefix behavior, not a shipped audit-receipt integration.

## Build and test

Use a disposable upstream checkout/archive tree. Verify its archive and the five
original file digests against the metadata before applying the patch. The archive
is available at:

`https://codeload.github.com/can1357/oh-my-pi/tar.gz/96894a6147d8cc64b7e00ad3a7d7178fd93b9531`

Apply with `patch -p1 < /absolute/path/to/native-precommit.patch` in that tree,
then verify the five patched file digests. This changes the upstream source,
not the active OMP installation. The patch adds sha2 0.10 and includes the matching lockfile change. Build with
--locked to keep the pinned dependency graph.

```sh
cargo +nightly-2026-10-06 test -p pi-edit --locked
cargo +nightly-2026-10-06 build -p pi-natives --lib --locked
# macOS: expose the built addon to Node/Bun without installing it.
cp target/debug/libpi_natives.dylib target/debug/pi_natives.node
# Run from the reasoning-core checkout; use Bun >= 1.3.14.
RC_OMP_NATIVE_MODULE=/absolute/upstream/tree/target/debug/pi_natives.node \
  bun test tests/omp_native_boundary.test.ts
```

The full macOS build requires CMake and Ninja. The development build on this Mac
stubs Apple Foundation Models because Swift 6.4/macOS 27 SDK is unavailable.
No model was invoked. Do not substitute this development addon for a delivered,
qualified OMP package.

`editPrecommitProtocol()` reports native protocol 1. The host extension API reads
this from the loaded addon; an older addon reports unavailable rather than silently
ignoring a third callback. `host-precommit.json` binds its ten host source files.
Apply `host-precommit.patch` after the native patch in the same pinned source tree.

A production OMP build must regenerate its N-API declarations after the patch,
build its JavaScript package against those declarations, wire the extension
approval event, and record the resulting runtime/asset hashes before activation.
The patch intentionally keeps two-argument apply behavior for unguarded upstream
users; the qualified adapter must always supply the third callback.

## Remaining boundaries

Non-UTF-8 sources, including deletions native OMP could otherwise perform, are
refused because the Python wire contract carries UTF-8 text. There is no
filesystem lock: an external process can race the final check and the writer
syscall. Runtime serialization and restrictions on concurrent/unqualified
mutation routes remain mandatory. Guard retention in children, shell/eval and
formatter/LSP/AST routes, installer/doctor, installed-wheel qualification, Actions
provenance, and both-machine activation remain in the delivery plan.

Runtime verification from the reasoning-core checkout:

```sh
RC_OMP_SOURCE=/absolute/upstream/tree bun test tests/omp_host_runtime.test.ts
bun test tests/omp_runtime.test.ts
```

The host fixtures exercise actual EditTool and ExtensionRunner calls, the real
isolated Python bridge, deterministic denial without mutation, allow with a
persisted receipt, and advisor identity. They invoke no model provider.

## Guarded runtime coverage

The scoped adapter permits native file editing and read/grep/glob/find. It
refuses shell/eval/Python, plain write, AST/LSP mutation, archive/SQLite
mutation, custom tools, task/eval spawning and automatic isolated merges.
User bash/Python execution events receive a refused result.
The global plugin alone does not qualify bash or plain writes: OMP can rewrite
paths after the plugin's checks. Keep the scoped adapter loaded for containment.

Guarded EditTool applies complete native batches under a lock keyed by resolved
project directory across in-process sessions. Its writer uses direct filesystem
operations, with internal-URL resolution, fallback handlers, LSP transformations
and automatic repair disabled. An OS write failure still permits an approved
landed prefix, recorded from all candidate paths in the receipt.

A real SDK child in a separate directory retains the adapter from its parent's
prepared extensions and supplies child/parent/session identity. Task-tool merging
is explicitly refused rather than inferred safe from that inheritance test.
External filesystem races remain outside the in-process lock.

## Readiness And Qualification

Default `rc init` records only the runtime and removes unchanged, RC-owned
strict adapter assets from earlier installs. Modified or unowned assets remain
and are reported. The global plugin gates bash/write/edit. Install the project
adapter (`.omp/extensions/reasoning_core_gate.ts`) with
`RC_OMP_STRICT=1 rc init --host omp`; it blocks every tool outside the
qualified native routes in that project.

Use `rc doctor --host omp --project-dir /absolute/project --json` for distinct
installed, loaded, authenticated, and qualified states. `rc init --check --host omp`
uses the same checks. The native loaded-state diagnostic discovers the owned
extension and hashes the loaded native addon without invoking a model provider.
The separate plugin-load probe uses `omp --print` and may invoke a model.

With the pinned patched OMP on PATH, run
`python -m eval.host_enforcement_qualification.omp --project-dir /absolute/project`
from the installed runtime. It exercises native hashline allow, complete-batch
guard-path denial, actual persisted receipt and inherited SDK-child denial in a
disposable directory. Restriction handlers and task merge refusal are checked.
The immutable `.reasoning-core/omp-qualification.json` binds runtime, plugin
gate, and policy digests; changing them invalidates doctor qualification.
Existing reports are never overwritten. These are containment checks, with
model behavior excluded.

The wheel workflow verifies bundled assets, adapter subpackage imports and init
outside the checkout, then attaches exact source SHA and distribution checksums.
Machine activation still requires the exact Actions artifact and local readback.
