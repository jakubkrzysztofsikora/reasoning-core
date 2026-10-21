# Diagnosis: rc omp gate template shipped into the reasoning-core checkout

**Date:** 2026-10-10
**Status:** investigation only — remediation (already merged locally, uncommitted) documented in §6, not repeated here as a change
**Verdict:** **Not an rc CLI bug.** No rc fix to roll out. Defect class: source-repo authoring/hygiene, plus one absent self-guard.

---

## 1. Question

> "Was it a bug in rc cli that we have to fix and roll out?"

**Answer: no.** The rc CLI rendered the gate correctly and installs it correctly into target repos. The brick was caused by an un-rendered *template* being committed inside the **reasoning-core source checkout itself**, at the path omp auto-loads. rc never wrote that file into the source repo — a human/agent commit did.

The one genuine gap is that **nothing in rc or in this repo's checks scans the source checkout** for an un-rendered gate at a live autoload path. That is a repo-hygiene guard, not an rc CLI defect, and it needs no CLI release to land (it is a `.gitignore` entry plus a test). Details in §5.

---

## 2. Root cause

omp auto-loads `<cwd>/.omp/extensions/*.ts`. The reasoning-core checkout contained:

```
.omp/extensions/reasoning_core_gate.ts   <- un-rendered TEMPLATE (2 placeholders), committed
```

The gate at that path is a hard guard:

- `integrations/omp/reasoning_core_gate.ts:143-144` — `tool_call` blocks every tool except `edit, read, grep, glob, find` (so `bash`, `write`, etc. are all gated pending a bridge receipt).
- `integrations/omp/reasoning_core_gate.ts:42` — the bridge subprocess is spawned as `[PYTHON, "-I", "-c", BOOTSTRAP, ROOT]`.

Because the committed file was the raw template, lines 7-8 read:

```ts
const PYTHON = "__RC_PYTHON__";
const ROOT = "__RC_ROOT__";
```

So the bridge could never spawn (literal placeholder interpreter path), `native_edit_prepare` never returned an `allowed` receipt, and every mutating tool stayed blocked — read-only tools still worked. **Every omp session rooted at this checkout was degraded to read-only with no way to recover**, because the file is committed (not owned/installed), so nothing in rc would rewrite it.

Verified identical to the packaged template at the offending commit:

```
$ diff <(git show 21b0f55:.omp/extensions/reasoning_core_gate.ts) \
       <(git show 21b0f55:src/data/templates/omp.reasoning_core_gate.ts)
IDENTICAL          # and: grep -c '__RC_' -> 2
```

## 3. Provenance / timeline

Relevant series (newest last = the fix commits; all omp work is one chain):

| Commit | Date | Role |
|---|---|---|
| `21b0f55` | Oct 10 22:36 | **Commits the defect.** Adds BOTH `src/data/templates/omp.reasoning_core_gate.ts` (correct — packaged template) AND `.omp/extensions/reasoning_core_gate.ts` (the mistake — a redundant raw copy inside the live autoload dir). Also adds the TS tests that imported from the old path. |
| `185f44a` | — | Native mutation restriction. |
| `b2bce82` | — | **Adds the gitignore guard, one commit too late.** Adds `.omp/extensions/reasoning_core_gate.ts` to `install.sh` and `src/_init.py::_GITIGNORE_ENTRIES`. |
| `e3f18bd`, `b87a7c8` | — | Runtime readiness / wheel provenance. |
| working tree | — | Fix: file relocated to `integrations/omp/` (§6). |

**Why the guard missed it (two independent reasons):**

1. At `21b0f55` the `.omp/extensions/...` gitignore entry did not exist yet (`git show 21b0f55:.gitignore | grep -c omp/extensions` -> `0`; same for `_GITIGNORE_ENTRIES`). The file was committed before the protection was written.
2. Even after `b2bce82`, the protection only applies to **target repos** that run `rc init`/`install.sh`. The **reasoning-core checkout's own `.gitignore` has no `.omp/` entry at all**:

```
$ git check-ignore -v .omp/extensions/reasoning_core_gate.ts
(not ignored)
```

The reasoning-core repo's `.gitignore` is the rc *source* repo's, and it was never given the rc-block entries that `_GITIGNORE_ENTRIES` writes into targets. So the source repo — the one place a committed template is most damaging — is the one place unprotected.

## 4. Why rc CLI is not at fault (positive findings)

- **Rendering is correct.** `src/_omp_install.py:73-80` (`render_extension`) substitutes `__RC_PYTHON__`/`__RC_ROOT__` and hard-fails on non-absolute paths (`ValueError: OMP runtime paths must be absolute`). Smoke: `rc init --target /tmp/rc-probe` produced `grep -c __RC_PY ... = 0`, with `PYTHON`/`ROOT` rendered to real absolute paths; `rc init-uninstall` cleaned up. Target install is unchanged and correct.
- **Ownership model is correct.** `install_omp` refuses to overwrite an existing unowned asset (warns `OMP unowned asset preserved` and `continue`), so a stray committed file would not have been silently clobbered — but it also means rc would never repair it. This is the "committed, not owned" trap.
- **No self-target behavior needed here.** `rc init --target .` inside reasoning-core is not what produced the defect (the file predates and matches the template, not a render). The rc self-install path is not exercised by this incident.

## 5. The real (latent) gap to close — repo guard, not CLI

The only defect worth a follow-up is the absent **source-tree guard**. rc ships the template at `src/data/templates/`; it must never appear as a committed, un-rendered file at any omp autoload path. Two cheap, deterministic, no-CLI-release guards:

1. **Ignore the live path in the source checkout.** Add the `.omp/` autoload path (or the specific gate file) to reasoning-core's own `.gitignore`. Gitignore does not untrack an already-tracked file, so this must accompany the tracked-file cleanup (already done in the working tree) to prevent recurrence.
2. **A source-tree assertion.** A test that fails if an un-rendered template (containing `__RC_` markers, or byte-identical to `data.read_text("omp.reasoning_core_gate.ts")`) exists anywhere in the checkout at an omp autoload path. The relocated file at `integrations/omp/` is a canonical *source* copy (currently byte-identical to the template by design — `test_canonical_asset_parity`), so the guard must key on **path** (`.omp/extensions/`), not on content, to avoid a false positive.

Neither requires an rc version bump or rollout; they are `.gitignore` + test changes local to reasoning-core. (`rc`'s own validators — `_omp_doctor.py`, `scripts/verify-omp-install.py` — deliberately inspect a **target** install's ownership manifest/compatibility/runtime, never the reasoning-core source tree, so they correctly did not flag this and would not.)

## 6. Remediation already applied in the working tree (for reference)

- `git mv .omp/extensions/reasoning_core_gate.ts integrations/omp/reasoning_core_gate.ts` (history preserved; `.omp/` removed). Template content unchanged — location only.
- Source-side references updated to the new path: 6 TS test imports (`tests/omp_host_runtime.test.ts`, `tests/omp_runtime.test.ts`) and `test_canonical_asset_parity` in `tests/test_omp_install.py` (now reads the template-parity source from `integrations/omp/`).
- All remaining `.omp/extensions/...` references are **target-side** (correct): `install.sh:555` gitignore block, `src/_init.py:465`, `src/hooks/_guard_paths.py:45`, `src/_omp_install.py:15` (`EXTENSION`, install target), tmp-dir test fixtures, `integrations/omp/host-precommit.patch` content, and the target assertion `omp_host_runtime.test.ts:261`.
- No enforcement-logic, threshold, or quality-criterion change (per `AGENTS.md`, baseline ceremony not triggered — this is location-only).
- `.pi/extensions/reasoning_core_gate.ts` is a **separate, tracked pi-sidecar gate** (0 placeholders, differs from the omp template, loaded only via the gitignored local `.pi/settings.json`). Not the defect; left untouched per constraints.

Verification: `test_omp_install/doctor/contract` 66 pass, `test_omp_bridge` 37 pass, `bun omp_runtime` 13 pass. `omp_host_runtime.test.ts` requires the pinned `RC_OMP_SOURCE` env var (pre-existing guard, fails identically without the relocation).

## 7. Adjacent observation (not acted on, FYI only)

The residual footgun: if anyone runs `rc init --target .` **inside** the reasoning-core checkout, rc will (correctly per its design) install a *rendered* gate into `.omp/extensions/`, which omp would then auto-load for this repo's own sessions. That is arguably intended for a normal target repo, but for the rc source checkout it re-creates a live gate at the autoload path. Nothing currently prevents it. (Flagged for the operator's judgement; not changed here.)