# Changelog — 2026-10-21 (v0.3.1)

## OMP native boundary (Tier 1)
- Native approval wired into session guard runtime; unqualified mutations
  restricted, child guard retained.
- Owned-asset lifecycle for OMP installs: user configuration and modified
  owned assets survive every install/uninstall/reinstall operation.
- Runtime readiness separated from install success; wheel provenance and
  installed-host qualification verified (`rc omp-doctor`).

## Gate template relocation (fix)
- Un-rendered gate template no longer lives at the OMP autoload path
  (`.omp/extensions/`); canonical source now at
  `integrations/omp/reasoning_core_gate.ts`. Committing the raw template
  at the autoload path bricked every local OMP session rooted at this
  checkout (see `docs/DIAGNOSIS_OMP_GATE_TEMPLATE_2026-10-10.md`).
- Source-checkout guards added: `.omp/extensions/` gitignored and a
  regression test asserts the autoload path stays absent.

## Sidecar / supervisor hardening
- Credential lookups moved off the sidecar event loop; score calls carry
  an explicit deadline; agy bridge injects `RC_ENFORCEMENT_TOKEN` when
  absent.
- Supervisor resilience: safe logging under lost stderr, orphan lock
  reaping, spawn circuit breaker, bounded SSM concurrency.
- `rc auth-bootstrap` refuses to write credentials when the macOS account
  cannot be resolved.
