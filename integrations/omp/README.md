# OMP native precommit boundary

This patch adds a complete-batch approval callback to OMP's native edit engine.
It is tested against source `aafb6551616fccec65cbfb264298e7f98df8ce5f`
(version 18.8.8), with Rust `nightly-2026-10-06` and Bun 1.3.14.
`native-precommit.json` records the archive, patch, source-file, and tested
development-addon digests.

The complete native `pi-natives` addon was built and exercised. This is
component verification. The normal OMP EditTool currently supplies only its
writer callback; Task 2 must wire approval through the extension runtime.
Applying this patch alone does not activate reasoning-core enforcement.

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

`https://codeload.github.com/can1357/oh-my-pi/tar.gz/aafb6551616fccec65cbfb264298e7f98df8ce5f`

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
