<!-- OMP-CONTINUITY-STATE v1 -->

# OMP Work Checkpoint — Reasoning Core / OMP Integration

Last verified: 2026-11-15. This is a navigation checkpoint, not proof of any
cloud, training, or production state.

## High-level goal

Improve the user's OMP setup so long multi-week work survives compaction,
restarts, model/provider switches, and handoffs, and so Reasoning Core governs
mutations instead of merely advising. Continuity and quality outrank spend.

## Current state (verified 2026-11-15)

- OMP integration patches were rebased from 18.11.3 to **18.14.0**
  (`31147f9d1a76608549821c3759f8487b4d66c72d`); both apply cleanly to the
  pinned archive and every patched-file digest is recorded in
  `integrations/omp/native-precommit.json` and `host-precommit.json`.
- Rust `pi-edit` tests (433) pass, `pi-natives` builds, N-API bindings
  regenerate, and the patched **18.14.0 standalone** passes native-boundary (21),
  host-runtime (12), and extension-runtime (37) suites plus the full Python
  suite.
- Two adversarial-review rounds (GPT-6-Astra via codex, per-file chunks) were
  run against the diff. Fixes landed: candidate-set digest binding with a
  length-prefixed pre-image over resolved paths, live-store re-staging so
  approved bookkeeping matches an unguarded apply, distinct denial reasons
  (no-verdict / digest-mismatch / timeout / denied), a failed receipt no longer
  masks a successful write, move rollback, lock-queue cleanup, and bounded
  probe diagnostics.
- **Activated**: `~/.local/bin/omp` is now the qualified patched build
  (sha256 `a6bcb6b73cffdd4c5bbc1ddf9873dc1f20dc813d7793f663e2d0dfba826c2816`).
  The official build is preserved at `~/.local/bin/omp.official`
  (sha256 `608843959ab34295d52460dd09dd7d548b6c903c08a83c67954c2d4c36fd1f5a`)
  for instant rollback. A disposable pilot reached all six doctor states:
  installed, loaded, plugin installed, plugin loaded, authenticated, qualified.
  Evidence: `integrations/omp/evidence/qualification-18.14.0-local-pilot.json`.
- Two real defects were fixed earlier: doctor ignored the persisted gate-mode
  file, and the qualification binding mixed a disposable-copy config digest
  with the real project so a valid report could never verify.
- The **global gate stays `off`**; the pilot at `~/Repos/personal/omp-pilot`
  runs the scoped adapter in advisory mode via `omp-pilot.sh`.

## Durable references

- Plan and status: `docs/superpowers/plans/2026-10-10-omp-tier1-delivery.md`
  ("Status Update (2026-11-15)").
- Pin and compatibility: `integrations/omp/PIN.json`,
  `src/data/templates/omp.compatibility.json`, `integrations/omp/README.md`.
- Patches: `integrations/omp/native-precommit.patch`,
  `integrations/omp/host-precommit.patch`.
- Broader OMP engagement plan: `/tmp/omp-personalized-implementation-plan.md`
  (ephemeral; workstream 1 = continuity, workstream 2 = Reasoning Core).

## Constraints

- Do not replace the active `omp`, flip the gate to `enforce`, or install into
  user projects without explicit approval; the official build stays active
  until activation is authorized.
- Keep the patched build side-by-side and reversible; the official install is
  the rollback.
- Preserve unrelated dirty work in this checkout (see `git status`); several
  unrelated files are already modified by other work.
- The macOS development build stubs Apple Foundation Models (no Swift
  6.4+/macOS 27 SDK) and is not a release artifact.

## Next action (one at a time)

1. Watch the advisory pilot's receipts for a week, then promote that single
   project to `enforce`; keep every other project on `off`.
2. Curate domain profiles (`ai-data`, `debug`, `azure`, `security`) from the
   general setup using OMP named profiles, so each workstream gets its own
   models, skills, MCP set, and checkpoint. **Done 2026-11-15**: the four
   profiles exist under `~/.omp/profiles/`, each with a curated model-role map,
   advisor cadence, pruned `mcp.json` and its own dedicated system prompt
   (`~/.config/omp/modes/*.md`). One shared extension
   (`~/.config/omp/extensions/domain-mode.omp.ts`) adds `/mode` so the domain
   prompt, tool deny-list and model can be switched inside a live session; the
   selection is stored in `<project>/.omp/MODE` so it survives restarts.
   Plan: `~/Repos/personal/omp-pilot/PROFILES.md`.
3. Still-open delivery gates (unchanged): GitHub Actions exact-commit artifact
   and wheel install on both Macs, MacBook Pro activation report, and
   both-machine acceptance.

## Resume procedure

1. Read this file, then the "Status Update" section of the delivery plan.
2. Verify `~/.local/bin/omp` matches the recorded qualified SHA-256 and that
   `~/.local/bin/omp.official` still matches the official build's SHA-256.
3. Re-run the doctor sequence in a disposable project before touching any real
   project; the qualification report is immutable and must never be
   overwritten.
4. Continue only the next approved step; update this file after each verified
   milestone.
