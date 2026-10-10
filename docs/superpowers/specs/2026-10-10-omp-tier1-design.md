# OMP Tier 1 integration through rc init

Date: 2026-10-10
Status: proposed written specification; conversational architecture approved.
Implementation requires review of this written specification and implementation plan.

## Intent and success criteria

Add Oh My Pi (OMP) as a first-class reasoning-core host through the normal
`rc init` workflow. The user explicitly requires installation as part of
`rc init`, rather than a separate manual setup command. The selected target
is native hashline support, preserving OMP's normal editing workflow.

Success means runtime interception before supported mutations, shared
deterministic enforcement, advisory neural scoring, correct session attribution,
and evidence from the exact supported OMP version. Configuration installation
alone does not establish Tier 1 qualification.

The installed package inspected was 18.2.11. npm and GitHub reported 18.8.8
as latest during the investigation. Qualification records must pin the actual
tested version and integration digest, not the moving upstream main branch.

## Current integration gaps

The Python init implementation installs each existing host unconditionally;
its clis parameter is currently unused. Doctor checks Claude post-tool wiring,
not OMP. The host environment shim does not recognize OMP.

The existing Pi extension is not a usable OMP adapter: it assumes incompatible
edit fields, sends unauthenticated score requests, checks block instead of its
helper's blocked field, and uses regression_detected as a standalone blocker.
The OMP integration must reuse the established guard chain rather than copy
that extension. Repairing the separate Pi integration is outside this scope.

Existing dirty authentication, supervision, documentation, and settings work
must be preserved. Any implementation must reconcile with those files without
discarding or treating their changes as part of this delivery.

## Installation and lifecycle

Extend the Python rc init path with an OMP installer. Install the owned runtime
entry point under .omp/extensions/reasoning_core_gate.ts, rendered with the
resolved reasoning-core Python executable and installation location. Do not
depend on direnv for host identity, Python selection, or project resolution.

Use OMP's native extension discovery rather than replacing the user's extensions
array. Preserve unrelated .omp settings, extensions, profiles, and MCP servers.
Do not register a fictitious Pi trusted-folders entry for OMP. Detect an existing
user-modified integration file and report a conflict rather than overwrite it.

Record owned files and their installed digests in the install manifest.
Re-running init is idempotent. Upgrade refreshes unchanged owned integration
files, reports modified files for operator reconciliation, and preserves user
configuration. Uninstall removes owned unchanged files and leaves modified files
with a clear report. Extend wheel/package template assets and the existing shell
installation path if that path remains supported, using the same canonical assets.

Follow the current default of wiring supported hosts through rc init. If the
existing clis selection interface is made functional for this feature, honor an
explicit omp selection without changing unrelated host-selection semantics.
OMP binary absence must produce an installed-but-unverified result rather than
silently claim readiness.

## Runtime boundary

The TypeScript entry point handles session lifecycle, pre-tool interception,
post-tool receipts, and child-agent attribution. It passes normalized events to
a Python OMP bridge through argv plus JSON stdin, never shell interpolation.
The bridge supplies host=omp, project root, session id, tool-call id, agent id,
and parent/child relationships explicitly for each invocation.

The bridge invokes the existing deterministic policy/plan/parse/lint/structural
checks, shell guard, authentication resolver, scoring client, and audit path.
It must not implement a second scoring policy or duplicate credential lookup.
Bearer credentials remain in the Python process; generated project files never
contain credentials.

Map bridge outcomes to OMP's block/reason contract. Preserve existing advise and
enforcement modes and configured infrastructure-failure policy. Timeouts,
malformed bridge output, process failure, and missing candidate data must be
reported distinctly. Under the qualified hard-enforcement contract, unknown
mutation candidates cannot be treated as allowed. Neural unavailability must
not bypass available deterministic checks.

Use OMP context cwd/session identity rather than process-global guessed values.
Do not mutate shared process environment for child sessions.

## Native edit candidate preparation

OMP supports hashline, replace, patch, apply_patch, and sloppy edit contracts.
Hashline includes multiple files, snapshot recovery, block anchors, persistent
registers, deletion, and moves. Each destination and deleted source must be
included in the candidate set.

Use OMP's native preparation semantics for complete candidate before/after
contents. Do not derive candidates from partial streaming previews or recreate
hashline parsing independently. The reviewed source exposes native EditSession
application through an internal writer callback, but no documented public
extension hook exposing an entire prepared batch for approval was established.

Resolve this boundary before shipping native Tier 1: establish an existing
versioned API that provides the complete prepared batch before any write, or
implement a narrowly scoped upstream-compatible pre-commit hook and qualify that
OMP build. Requiring a patched OMP build must be explicit in installation and
compatibility output. Do not silently downgrade to replace-only support.

The pre-commit boundary must validate the complete batch before the first write,
bind approval to final arguments and source digests, and reject stale approvals.
Later extension argument rewrites must trigger preparation and approval again.
Reuse native parsing, snapshot/register state, and write execution. No live-file
mutation may be used to discover candidate contents.

Formatting, automatic syntax repair, and LSP transformations must either traverse
the same candidate gate or be disabled in the qualified profile. A post-write
diff is a receipt, not a substitute for pre-write validation. OS write failures
may leave a landed prefix; audit and report that outcome without promising
transactional rollback.

## Mutation coverage and guard integrity

Plain write uses path and content. Internal URL, archive, SQLite, mounted-tool,
and conflict-resolution writes require separate target resolution; never apply
path.resolve blindly. Initially reject mutation routes that cannot supply exact
file candidates under the qualified enforcement profile.

Shell, eval/Python, LSP/AST edits, custom tools, delegated native invocations,
and child-session merge application are part of the coverage assessment.
Arbitrary code execution cannot be certified through command-pattern matching
alone: restrict unqualified execution routes or use an enforced filesystem
boundary. Read-only tools remain available.

Protect the owned extension, bridge, and OMP settings that can disable or redirect
enforcement. Check resolved paths, symlinks, rename destinations, and writes via
other execution tools. Document the operator authority boundary: an operator
can launch with extensions disabled; an agent in a qualified session must not
be able to disable its guard through tool execution.

Child sessions must load or retain the approved adapter, including isolated
workspaces and parent application of their changes. Qualification must verify
this directly rather than assume extension inheritance from upstream docs.

## Readiness and user feedback

Add host-aware doctor checks, reached through rc init --check, for:
- OMP executable and exact version.
- Owned extension presence, digest, and applicable discovery configuration.
- Actual extension loading and authenticated guard readiness.
- Supported edit mode and enabled mutation routes.
- Version-bound qualification evidence.

Distinguish installed, loaded, authenticated, and qualified states. A healthy
sidecar /health response does not prove authentication or runtime loading.
Readiness probes must be bounded and must not invoke a paid model implicitly.
A disabled extension or unsupported version reports unqualified support.

Expose deterministic denial reason and decision id to the model, with the same
explain/override workflow supported by the shared guard. Audit receipts correlate
candidate decisions with the actual tool result and persisted file digest.

MCP may expose explanatory tools through .omp/mcp.json if required later, but
MCP is not the enforcement path and no new MCP server is required for this scope.

## Qualification and delivery gates

Before changing an enforcement default, evaluation criterion, or quality claim,
inspect eval/baselines/README.md and baseline-2026-08-09. Capture a new immutable
OMP context baseline before making a comparison claim. Do not transfer another
host's outcome evidence to OMP.

Required acceptance cases in disposable repositories:
- Init/re-init, user settings preservation, modified-owned-file conflicts,
  package installation, upgrade, and uninstall.
- Exact candidate parity for supported edit modes and plain writes.
- Deterministic denial leaves every affected file unchanged, including a batch
  whose last member is denied.
- Allowed operations have matching candidate and persisted-content receipts.
- Neural-only regression stays advisory.
- Auth failure, timeout, malformed payload, stale digest, argument rewrite,
  unsupported target, and disabled guard produce explicit outcomes.
- Guard-file write, shell/eval escape, symlink, move/delete, repair/format,
  child-agent mutation, and isolated merge paths.
- A real OMP host run exercises both allow and deny through the installed
  extension, with model/backend and spending scope explicit.

Record the OMP version/build, integration hashes, configuration, tested tool
routes, and exclusions in an immutable qualification artifact. Update README,
INSTALL, and CLI_PARITY only to the support level demonstrated by that record.
No broad causal improvement claim follows from these containment checks.

## Scope and sequencing

One feature includes installer assets, host identity, bridge, runtime adapter,
native candidate approval boundary, doctor readiness, lifecycle handling,
qualification, and support documentation. Existing auth fixes and unrelated Pi
repairs are not bundled into this feature.

The implementation plan must resolve the native pre-commit API first, then build
installation and shared guard integration, qualify mutations and delegated paths,
and finally publish the evidence-backed support status. If the required native
API cannot be established, report the blocker and present a revised scope before
advertising native Tier 1 support.

## Evidence

Upstream source snapshot reviewed:
aafb6551616fccec65cbfb264298e7f98df8ce5f.

- https://github.com/can1357/oh-my-pi/blob/aafb6551616fccec65cbfb264298e7f98df8ce5f/docs/extension-loading.md
- https://github.com/can1357/oh-my-pi/blob/aafb6551616fccec65cbfb264298e7f98df8ce5f/docs/extensions.md
- https://github.com/can1357/oh-my-pi/blob/aafb6551616fccec65cbfb264298e7f98df8ce5f/docs/tools/edit.md
- https://github.com/can1357/oh-my-pi/blob/aafb6551616fccec65cbfb264298e7f98df8ce5f/docs/tools/write.md
- https://github.com/can1357/oh-my-pi/blob/aafb6551616fccec65cbfb264298e7f98df8ce5f/packages/coding-agent/src/extensibility/extensions/runner.ts
- https://github.com/can1357/oh-my-pi/blob/aafb6551616fccec65cbfb264298e7f98df8ce5f/packages/coding-agent/src/edit/index.ts
