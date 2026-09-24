<!--- Logo --->
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/logo-dark.svg">
    <img alt="reasoning-core logo" src="./docs/logo.svg" width="300">
  </picture>
</p>

<p align="center">
  <strong>reasoning-core</strong> — local pre-edit gate for AI coding CLIs.
</p>

<p align="center">
  Audit and warn on AI edits that drift off-plan, import what's banned, or break invariants — before they hit disk.
  <br/>
  Opt-in enforcement mode can block these edits.
  <br/>
  At runtime: loopback-only sidecar, no telemetry, no cloud relay.
  <br/>
  One-time model download from HuggingFace is required for the default SSM embedder.
</p>

<p align="center">
  <a href="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/lint-and-test.yml">
    <img src="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/lint-and-test.yml/badge.svg" alt="lint-and-test">
  </a>
  <a href="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/eval.yml">
    <img src="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/eval.yml/badge.svg" alt="eval">
  </a>
  <a href="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/reasoning-core-pr-score.yml">
    <img src="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/reasoning-core-pr-score.yml/badge.svg" alt="PR score">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT">
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  </a>
</p>

---

## What it is

A local scorer reads every Edit / Write an AI coding agent proposes, runs cheap
execution-grounded oracles on it (`py_compile`, `ruff`, `ast.parse`, your
`.reasoning-core/rules.yaml`), and scores it against an 8-dim risk vector. In the
**default advisory mode** it warns and audits; in **opt-in copilot mode** it can
block the change before it lands if it drifts off-plan, invents a helper you
already have, or violates a coupling/coherence threshold. One sidecar, six
CLIs (Claude Code, OpenAI Codex, Gemini, Moonshot Kimi, GitHub Copilot,
Mistral Vibe). At runtime the sidecar binds to loopback only; no telemetry,
no cloud relay, no ongoing network calls.

**Evidence status:** The benchmark figures in [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md)
and the whitepaper are historical controlled-study results from one codebase,
using an earlier risk-label schema. They are not a current real-usage guarantee.
The shipped install remains advisory/shadow by default; current real-session
quality evidence is being built from decision-linked labels and repository
outcomes.

## Quick start

Two commands. No git clone, no venv, no launchd dance, no `pip install -r
requirements.txt`. `pip install reasoning-core[full]` puts the framework
and every Python dep (torch, transformers, tree-sitter, fastapi, mcp,
ruff, …) on PATH and installs an `rc` console script. `rc init` wires
hooks into the current repo, downloads the default embedder, and brings
up the sidecar supervisor as a per-user daemon.

```bash
# 1. Install the framework (one-shot, ~5 min on broadband — pulls ~500 MB
#    of Python deps and the 250 MB mamba-130m checkpoint up front so the
#    gate is complete by the time you reach step 3).
pip install reasoning-core[full]

# 2. Wire it into the repo you want gated
cd /path/to/your-repo
rc init                         # adds .envrc, .claude/, .codex/, .gemini/,
                                # .copilot/, .kimi/, .vibe/, .pi/ + sidecar daemon

# 3. Run your CLI — hooks fire automatically
claude                           # or: codex / gemini / copilot / kimi / vibe / pi
```

If `pip install reasoning-core[full]` fails on your platform, the
fallback flow is `pip install reasoning-core && pip install -r
requirements.txt` (still no git clone). See [`docs/MIGRATION_v1.md`](docs/MIGRATION_v1.md)
for moving off the old clone-and-install flow.

To move between framework versions without re-doing the install:

```bash
rc upgrade                  # pip install --upgrade + rc init --check (idempotent)
rc upgrade --ref v0.3.0     # pin a specific tag/branch
rc upgrade --dry-run         # see the plan before it runs
```

When the gate blocks an edit, you see a `Decision ID` you can inspect or
override from the terminal:

```
[reasoning-core] BLOCKED: oracle failure (ruff)
  file: src/sidecar_boot.py
  line: 27
  reason: Unused import `tempfile`

[hybrid-reasoner] Decision ID: 92644989594e
  Inspect: rc explain 92644989594e
  Override: rc bypass-next
```

Every block is keyed by an ID linked to the audit log. `rc explain` shows the
verdict, `rc bypass-next` arms one override, and the override + decision stay
paired in the shadow report so calibration stays grounded in operator behavior,
not the gate's guesses.

## What you get

**Pre-execution oracles — milliseconds, free**
- `py_compile` — Python syntax on the proposed diff
- `ruff` — lint, imports, style on the proposed diff
- `ast.parse` — semantic parse smoke test
- `.reasoning-core/rules.yaml` — your project's `forbid_import` / `forbid_pattern` list

**Neural risk vector — 8 dims per edit**

The default scoring vector is always-on: `cyclomatic`, `fan_in`, `fan_out`,
`depth`, `churn`, `coupling`, `cohesion`, `novelty`. Three additional optional
dimensions (`session_centroid_drift`, `project_fan_in`, `project_coupling`) are
emitted only when **both** conditions are met: a session baseline is registered
(via the sidecar's `/baseline` endpoint) **and** the hook passes `session_id` to
`/score`. The shipped `.envrc` sets `RC_PROJECT_INDEX=1` by default, but the
session-baseline path is rarely hit in practice — so the 8-dim vector is what
production edits get. All dimensions are scored in [0, 1] with a chord-distance
`coherence_delta` on [0, 2].

**Decision-ID footer on every block** — `exit-2` blocks always end with
`Decision ID: <hex>` and an `rc explain` / `rc bypass-next` follow-up so the
audit log and operator action stay linked by the same ID.

**Self-calibrating from your git history** — `rc audit-history` labels a
commit negative if it was followed within 48 h by a fix/revert/hotfix on the
same files. The labeled feedback recalibrates thresholds where you actually
make mistakes.

**Same hooks across 6 CLIs**

| CLI | Hook surface | Tier |
|---|---|---|
| Claude Code, OpenAI Codex, Gemini CLI, Moonshot Kimi | runtime PreToolUse | 1 |
| GitHub Copilot CLI, Mistral Vibe CLI | MCP `gate_edit` + post-turn audit | 2 |

Tier-2 means the gate runs at the model layer — the LLM is asked to call
`gate_edit` before every write. Under context pressure it sometimes skips;
for mission-critical work use a Tier-1 host. Detail:
[`docs/CLI_PARITY.md`](docs/CLI_PARITY.md).

**Local-only by construction** — sidecars bind `127.0.0.1` only, refuse
external NIC. The hook chain, the audit log, and the rule engine all run on
your machine.

## Configure

Defaults installed by `rc init` are honest-opt-in — the gate warns and audits,
never blocks. Enforcement is opt-in via `rc enable-enforcement` and requires an
authenticated operator action. See [`docs/HARDENING.md`](docs/HARDENING.md) for
the guard-integrity model.

```bash
export RC_MODE=advise              # advise | copilot | autopilot
export S2_HARD_CAP_MS=1500         # client cap on /score POST
export S2_COHERENCE_THRESHOLD=0.09 # chord-distance ceiling (95th-pct)
export S2_TIMEOUT=30               # server cap on /score
export RC_RULE_ENGINE=1            # enforce .reasoning-core/rules.yaml
```

Per-machine overrides → `.envrc.local` (gitignored). Run `rc enable-enforcement`
after ~48 h of shadow review and manually authoring a `PLAN.md` to promote to
`RC_MODE=copilot`. `rc init` auto-picks the largest embedder that fits
the host's available RAM and disk via `src/embedder_tier.py`; the
chosen tier + backend are written to `.envrc` (override at any time
with `export RC_EMBEDDER=<backend>` before `direnv reload`). The
auto-pick honours an existing `RC_EMBEDDER` pin. Full env-var table:
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).


## Audit & hardening

A hostile technical audit of this codebase was filed on 2026-09-19 and a
hostile follow-up on 2026-09-19-reaudit. Both are publicly addressed in
[`docs/AUDIT_RESPONSE_2026_09_19.md`](docs/AUDIT_RESPONSE_2026_09_19.md).

**Round 1** (`audit-hostile/2026-09-19-fixes`, merged): async-event-loop
starvation, supervisor self-termination, `_BASELINES` memory leak,
singleflight blocking, several shell-guard bypasses, direnv auto-approval,
wheel-install import crash. Claims the audit got wrong (label polarity, the
`ais<0.4` dead-code claim, install.sh direnv trust) are documented as
rejected.

**Round 2** (`audit-hostile/2026-09-19-reaudit-fixes`, current): `/baseline`
offload (RC-SYS-02), per-session file cap (RC-SYS-03), symlink-resolved
guarded-path block (RC-SEC-05 -- the resolver now blocks unconditionally on
realpath hits, not gated on the source-extension regex), `direnv allow .`
sentinel (RC-SEC-06), the whitepaper + headline-numbers retraction of the 8-cell set
(RC-ML-02 / RC-ML-03), and the Phase C `mahal_anomaly` independent
signal (RC-ML-04 closed by adding a non-redundant signal alongside
the dead-but-back-compat `ais<0.4` check). The 8-cell table in `BENCHMARKS.md`
and `whitepaper/sections/results.tex` is now boxed as
`RETRACTED 2026-09-19`; the surviving-5 cells yield an exact two-sided
sign-test p-value of **1.00 (coin flip)** and we make no directional product
claim. The pre-registered iter-2 acceptance criterion (>=7/8 wins with
paired bootstrap 95% CI excluding 0 on impl quality) requires additional
n>=3 evidence before any directional claim can be made.

**Round 3** (`audit-hostile/2026-09-22-hostile`, current): the
2026-09-22 hostile re-review ([`REVIEW-2026-09-22.md`](REVIEW-2026-09-22.md))
identified two BLOCKERs against the round-3 work and one MAJOR
statistical bug. All three are closed in commit `d20c7be`:

* **BLOCKER #1: `mahal_anomaly` was a fixture-only feature.**
  The 22 previous Phase C tests seeded `__mahal_*__` keys directly into
  `_BASELINES`, but no production code path populated them. The fix:
  `_persist_session_baseline_for_path` now folds accumulated per-path
  embeddings into a session-level `__corpus__` when the per-session
  file count crosses `RC_MAHAL_CORPUS_MIN` (default 5), then runs
  Ledoit-Wolf shrinkage + a leave-one-out FPR threshold. End-to-end
  verified: 5 path edits -> `__corpus__` `(5, 768)` -> `__mahal_threshold__`
  ~7.16 -> `ImpactReport.mahal_anomaly` ~1.32M -> fired condition
  `mahal_anomaly_above_threshold` fires correctly.
* **BLOCKER #2: `rc init` bricked ≥32GiB hosts.** The picker returned
  `mamba3-siso-1.5b` on a 40GB host but the loader refused fallback for
  operator-pinned backends, so every `/score` 503'd. The fix:
  `src/ssm_backbone.backend_loadability_probe()` returns False for any
  `mamba3-*` backend unless `mamba-ssm>=2.0.0` is importable; `decide()`
  walks the tier matrix past unloadable candidates; `install_envrc()`
  writes `safe_backend_for_envrc` rather than `backend` when the
  auto-pick is unloadable. End-to-end verified: simulated 40GB host,
  `rc init` writes `RC_EMBEDDER=mamba-130m` with a clear "not loadable
  on this host" reason.
* **Finding 3 (MAJOR): in-sample FPR over-fits at small n.** The
  `threshold_for_fpr()` quantile was calibrated and evaluated on the
  same corpus; at n=5 the realised FPR was 0.20 vs the nominal 0.05.
  The fix: new `loo_threshold_for_fpr()` does leave-one-out calibration;
  the production path uses it; `threshold_for_fpr` is retained as the
  in-sample estimator for tests that want it. 5 new tests, 20/20
  scoring_signals tests pass.

Full re-audit response: [`docs/AUDIT_RESPONSE_2026_09_22.md`](docs/AUDIT_RESPONSE_2026_09_22.md).
Post-fix baseline: `baseline-2026-09-22-blocker-fixes-post.json`.

**Round 4** (`audit-hostile/2026-09-22-round2`, current): the
2026-09-22 hostile re-review ([`REVIEW-2026-09-22-round2.md`](REVIEW-2026-09-22-round2.md))
identified one CRITICAL attestation defect and three BLOCKERs
against the round-3 fixes. All four are closed in commit
`00fdc0d`:

* **CRITICAL: attestation defect (round-2 Finding 1).** The
  previous round shipped `d20c7be` (fixes + tests) and `d68d3ff`
  (docs + baseline) as separate commits; the docs cited
  `d20c7be` as the post-fix SHA, which is correct, but the
  post-fix baseline `baseline-2026-09-22-blocker-fixes-post.json`
  was captured at `d68d3ff` (HEAD after the docs). Both commits
  are now in the history and the baseline attests the fix commit
  directly. A new baseline `baseline-2026-09-22-round2-fixes-post.json`
  attests the round-4 fix commit `00fdc0d`.
* **BLOCKER: session-freeze regression (round-2 Finding 2).**
  `_persist_session_baseline_for_path` had a guard that
  permanently skipped per-path persistence after corpus
  promotion. Fix: replace the corpus-check with a reserved-key
  check (only guard against overwriting `__ts__`/`__corpus__`/
  etc.). End-to-end verified: 8 path edits -> all 8 file keys
  present in `_BASELINES[session_id]` (was 5 before the fix).
* **BLOCKER: degenerate-corpus always-fire (round-2 Finding 2).**
  When all 5 corpus embeddings are identical (reachable via
  `/baseline` poisoning), Ledoit-Wolf collapses to the shrinkage
  target, the LOO threshold is 0.0, and every fresh edit scores
  +inf. Fix: detect the degenerate case (`(cov_inv == 0).all()`)
  and set the threshold to `+inf` so the signal stays inert until
  the corpus is non-degenerate. End-to-end verified: same-vector
  corpus -> fired condition does NOT trip.
* **BLOCKER: brick relocated to 2-8GiB hosts (round-2 Finding 3).**
  The loadability probe blessed unpinned `bge-code`; the loader
  fail-closed on the unpinned revision; the picker picked
  `bge-code` for 4GB hosts. Same brick, different machine class.
  Fix: probe now refuses any non-`mamba-130m` backend without a
  SHA pin in `_PINNED_REVISIONS`; `BAAI/bge-code-v1` added with
  `REVIEWER_PIN_REQUIRED` placeholder. End-to-end verified:
  4GB host -> picker returns `unixcoder-base` (the pinned
  fallback), not `bge-code`.
* **BLOCKER: windowing stride-cap silently excludes the changed
  chunk (round-2 Finding 4).** `chunks[::stride][:32]` dropped
  chunks in files with > ~800 scopes; the audit verified by
  execution that a malicious line at scope 1900 in a 4000-function
  file produced `coherence_delta=0.0` while
  `windowed_embed_active=True`. Fix: chunker preserves every
  chunk when the cap is exceeded; cost cap moved to the
  embedder call site. End-to-end verified: 4000-function file
  with malicious line at scope 1900 -> `coherence_delta=0.12`,
  `coherence_delta_above_threshold` fires.

**Anti-spoof:** the probe now requires the real
`mamba_ssm.ops.selective_scan_interface` submodule to be
importable for Mamba-3, not just any module named `mamba_ssm` on
`sys.path` (the round-2 spoofing vector).

**Probe-required:** `decide()` now warns when called without
`loadability_probe=`, so any future caller that forgets the
kwarg re-opens the BLOCKER brick and is told so at the call site.

Full re-audit response:
[`docs/AUDIT_RESPONSE_2026_09_22_ROUND2.md`](docs/AUDIT_RESPONSE_2026_09_22_ROUND2.md).
Post-fix baseline: `baseline-2026-09-22-round2-fixes-post.json`.

**Round 5** (`audit-hostile/2026-09-23-round5-security`, current):
the round-2 review's own audit found the security layer untouched
and 0/13 findings re-verified. Per the Pareto 80/20 directive,
this commit closes the cheap, high-frequency subset of those
findings plus the arithmetic-error doc fix the review flagged:

* **Security (6/13 closed):** `python3 src/rc_cli.py bypass-next`,
  `.envrc.local` unguarded writes (shell + Python + Node),
  `git stash pop / cherry-pick / revert / am / pull / fast-import`,
  `node -E` (uppercase), and `pkill -STOP -f reasoning-core-sidecar`.
  End-to-end verified: 11/11 attack vectors now blocked, 3/3
  benign commands still allowed. The remaining 7 security
  findings (audit log chain rewritability, symlink TOCTOU races,
  etc.) are documented as known limits in `pre_bash_guard.py` --
  they require filesystem-level mitigations, not regex additions.
* **Doc arithmetic (Finding 7):** the surviving-5 plan-quality
  mean was published as `2.20 (-35.3%)` in `BENCHMARKS.md` and
  `docs/whitepaper/sections/results.tex`, but the doc's own rows
  recompute to `2.60 (-23.5%)`. Corrected in both documents;
  regression tests prevent reintroduction.
* **Tests:** `tests/test_pre_bash_guard_round5.py` adds 13
  regression tests (11 regex + 2 doc); the pre-existing pre-bash
  suite is now 83 tests (was 70). All green.

Post-fix baseline: `baseline-2026-09-23-round5-security-post.json`.

**Round 6** (`audit-hostile/2026-09-24-round3-retest`, current):
the 2026-09-23 hostile retest ([`REVIEW-2026-09-23-round3.md`](REVIEW-2026-09-23-round3.md))
identified four **fabricated-attestation claims** in the
round-3 / round-4 audit-response documents and one BLOCKER in
`_PINNED_REVISIONS` (round-4 had written `"BAAI/bge-code-v1":
"REVIEWER_PIN_REQUIRED"`, which broke the
`test_pinned_revisions_are_40char_hex` invariant). This commit
closes the cheap subset:

* **Fix: `_PINNED_REVISIONS` populated with real model-card SHAs.**
  Round-4 commit `00fdc0d` left `"BAAI/bge-code-v1":
  "REVIEWER_PIN_REQUIRED"` as a placeholder. The round-3 hostile
  retest caught this. Fetched the live 40-char SHAs from
  `huggingface.co/api/models` and baked them in:
  `bd67852057c5d7ddcc7b8234d9d6c410117ed851` (bge-code-v1),
  `e205b6e6d6075d089140d2e9170970aabc05c481` (mamba3-siso-893m),
  `b5c7db27d1c7781d27c203bfd18601b3bbff7bc0` (mamba3-mimo-894m),
  `5cfc721542ec9ccee768088b2fd6b7e8101219d8` (mamba3-siso-1.5b).
  The probe now blesses bge-code (real pin) and continues to
  refuse any non-`mamba-130m` backend whose pin is missing or
  reverted to the placeholder. `pin_model_cards.py` remains
  available for periodic refresh; the loader no longer requires
  it to proceed.
* **Fix: Edit/Write/MultiEdit to `.envrc.local` now blocked.**
  `pre_bash_guard.py` already caught shell redirects and Python
  /Node writes; the Edit tool (Claude's primary write path) went
  through `pre_edit_guard.py` which had no equivalent deny rule.
  Layer-1b in `pre_edit_guard.py` matches by basename and emits
  `decision=blocked`, `reason=envrc_local_locked`; same
  `RC_ALLOW_GUARD_EDIT=1` override surface as the layer-1 guard
  file lock. 3 new tests in `tests/test_hook_block.py`.
* **Fix: `rc bypass-next` now requires operator auth.** Round-2
  fixed the shell form (`python3 -m src.rc_cli bypass-next`) via
  a `pre_bash_guard.py` regex; round-3 confirmed the path form
  (`python3 src/rc_cli.py bypass-next`) was unauthenticated and
  self-armable by an agent. `cmd_bypass_next` now calls
  `_operator_authenticated()` (the same gate `enable-enforcement`
  uses); on failure it prints the auth-bootstrap hint and returns
  1. The kill-switch stays un-armed.
* **Fix: `loo_threshold_for_fpr` docstring rewritten honestly.**
  The previous docstring claimed "With n=5 the realized FPR is
  approximately 0.05." Round-3 confirmed by execution that the
  realized power at n=5 is near 0% (the detector is nearly inert
  at small corpus sizes). New docstring states the honest
  contract: small n → advisory; large n → nominal FPR.
* **Fix: 4 previously-undocumented flags now in
  `docs/CONFIGURATION.md`.** `RC_SCORING_V3`, `RC_DIFF_WINDOWING`,
  `RC_MAHAL_CORPUS_MIN`, and `RC_NEURAL_CORROBORATED` were
  cited as "documented in CONFIGURATION.md" in the round-4
  audit-response but were not. Added a new "Neural-gate scoring
  flags" section with the actual contract for each flag,
  including the AGENTS.md "neural is advisory unless
  corroborated" rule for `RC_NEURAL_CORROBORATED`.
* **Retraction: 4 fabricated-attestation claims.**
  `AUDIT_RESPONSE_2026_09_22.md` and
  `AUDIT_RESPONSE_2026_09_22_ROUND2.md` each contained claims
  that were false when checked against the tree (git log, file
  grep, pytest run). The retractions are inline in the response
  docs as "Partially retracted (round-3 retest)" notes; each
  names the specific evidence (git pickaxe, `grep` return,
  pytest summary) that falsifies the original claim.

Post-fix baseline: `baseline-2026-09-24-round3-attestation-retractions-post.json`.

**Status of the round-3 review's open items:** the round-3 review
left these items out-of-scope for its blockers round:
  - 7 remaining security findings (audit log chain rewritability,
    symlink TOCTOU races, etc.) — round-5's regex work closed
    6/13, leaving 7 as known limits.
  - PyPI namespace collision (verified by direct fetch of
    `pip install reasoning-core` returning an unrelated project).
  - Pre-reg embedder eval ladder (Mamba-3 default flip blocked
    until `mamba-ssm>=2.0.0` is installed and the pre-reg gates
    pass).
  - **RC-SEC-04: `ln -s` symlink creation passes pre_bash_guard.** The guard
    blocks writes *through* symlinks that resolve to guarded paths, but does
    not block symlink creation itself. An agent can create a symlink alias to
    `.envrc.local` and then edit the alias; the edit guard catches this via
    realpath resolution (WO-5), but bare `ln -s` is not denied. Residual risk:
    low (requires two-step attack, second step caught by edit guard).

The shell-guard regex set now blocks `git apply`, `git checkout <sha>`,
`git restore`, `git stash apply`, `mv/cp/install/rsync` to source extensions,
`pathlib.Path().write_text(...)`, `base64 -d | bash|sh|zsh|eval`,
`python3 -c "open(... 'w')"`, and **any redirect whose target resolves
through a symlink onto a guarded path**. Tunable knobs: `S2_HEALTH_TIMEOUT_S`
(default 15s), `S2_HEALTH_GRACE_S` (default 60s),
`S2_BASELINE_MAX_SESSIONS` (default 256), `S2_BASELINE_TTL_S` (default 24h),
`S2_BASELINE_MAX_FILES_PER_SESSION` (default 200).

**Open research items** (deferred, not bugs):

- **Embedder swap (option 3, blocked 2026-09-21).** The default stays
  at `mamba-130m` until the pre-reg eval ladder passes five gates
  (AUC >= 0.70, inversion >= 0.05, anisotropy reduction >= 0.04,
  latency parity, falsifiability). The Mamba-3 candidates are
  registered in `_BACKENDS` (see `src/ssm_backbone.py`), the harness is
  shipped (`eval/pre_reg_embedder.py`), and the refusal-gate test is
  shipped (`tests/test_pre_reg_embedder_gate.py`). Model-card pulls
  succeeded (`state-spaces/mamba3-siso-893m` etc., see
  `eval/calibrated/model_cards.json` for the pinned metadata) but the
  checkpoints cannot be loaded by the current transformers stack (no
  `Mamba3*` class, no `mamba-ssm>=2.0.0` kernels). The gate test stays
  in skip mode until `mamba-ssm>=2.0.0` is installed and the harness
  re-run. Status doc: `eval/runs/PRE_REG_STATUS_2026_09_21.md`.
- **Windowed diff embeddings (Phase A shipped 2026-09-21).** New
  module [`src/diff_windowing.py`](src/diff_windowing.py) provides
  `embed_windowed(text, lang, diff_hunks)` with the same vector shape as
  `ssm_backbone.embed(text)`. AST-scope chunker per language family
  (Python, JS, TS, C#) with a 64-line stride line-window fallback.
  20 unit tests in `tests/test_diff_windowing.py` cover the chunker,
  diff-weight normalisation, non-overlapping chunk invariant, and L2
  determinism. The consumer swap into `s2_core.py:953-983` shipped
  in commit `d20c7be` under the `RC_DIFF_WINDOWING=1` flag (default
  off). 6 integration tests in `tests/test_diff_windowing_wiring.py`
  cover default-off no-op, on-path uses windowed embedder, long-file
  blindness fix, graceful fallback when chunker raises, fired-
  condition co-existence, JSON round-trip.
- **Auto-sizing on `rc init`.** `src/embedder_tier.py` detects the
  host's available RAM and disk and picks the largest variant that
  fits. The tier matrix (2026-09-21) is:

  | Tier    | RAM window    | Backend chosen (loadable only) | Notes |
  |---------|---------------|-------------------------------|-------|
  | xlarge  | ≥ 32 GiB      | `mamba3-siso-1.5b` if loadable, else legacy | Refused when Mamba-3 kernel missing (BLOCKER #2 fix) |
  | large   | 16-32 GiB     | `mamba3-siso-1.5b` or `-893m` if loadable | MIMO if RAM allows |
  | medium  | 8-16 GiB      | `mamba3-siso-893m` if loadable, else `unixcoder-base` | Best Mamba-3 fit |
  | small   | 2-8 GiB       | `bge-code` or `unixcoder-base` | Mamba-3 won't fit |
  | fallback| < 2 GiB       | `mamba-130m` (legacy default) | Last resort |

  **Loadability probe (BLOCKER #2 fix, 2026-09-22):** every entry above
  is now gated by `src.ssm_backbone.backend_loadability_probe()`, which
  returns False for any `mamba3-*` backend unless `mamba-ssm>=2.0.0`
  is importable on this host. When the auto-pick is unloadable,
  `decide()` walks the tier matrix down to the next loadable
  candidate; when no Mamba-3 variant loads, the safe fallback is
  `mamba-130m` (the legacy default), NOT an unloadable backend that
  would brick the gate. The probe is pure syntactic + import check;
  no I/O, no HF downloads.

  Operators can override the auto-pick via `export RC_EMBEDDER=<backend>`
  before `direnv reload`. Pre-reg: `tests/test_embedder_tier.py`
  exercises a 12-cell (ram, disk) grid + operator-pin + oversized
  warning paths. The auto-pick decision is recorded in the immutable
  baseline manifest (`embedder_tier`, `embedder_backend`,
  `embedder_working_set_gb`).
- **Scoring-v3 (Phase C, shipped 2026-09-22).** New module
  [`src/scoring_signals.py`](src/scoring_signals.py) exposes
  `fit_benign_corpus`, `mahal_anomaly_against_corpus`, and
  `threshold_for_fpr` -- pure-numpy wrappers over the existing
  `src.calibration._ledoit_wolf_cov` + `_mahalanobis_sq`. The new
  `ImpactReport.mahal_anomaly` field is computed when
  `RC_SCORING_V3=1` (default off, opt-in) and a benign corpus exists
  for the session. New fired condition `mahal_anomaly_above_threshold`
  trips when the squared Mahalanobis distance exceeds the per-session
  threshold at FPR=0.05 (production uses the leave-one-out
  `loo_threshold_for_fpr`; see BLOCKER-fix commit `d20c7be`). The
  algebraic-redundancy of AIS / CD / Novelty is preserved (the
  existing 3-way redundancy is *not* changed, per AGENTS.md:
  deterministic checks are the only hard block) but the new signal
  is independent by construction. 20 unit tests in
  [`tests/test_scoring_signals.py`](tests/test_scoring_signals.py)
  (15 initial + 5 LOO), 7 wiring tests in
  [`tests/test_scoring_v3_wiring.py`](tests/test_scoring_v3_wiring.py),
  and 4 end-to-end production-path tests in
  [`tests/test_mahal_production_wiring.py`](tests/test_mahal_production_wiring.py)
  cover the math (centroid symmetry, PSD inverse, distance scaling,
  FPR quantile match, OOD detection, determinism, LOO honesty), the
  fixture-path wiring (default-off no-op, on-path with corpus, no-
  corpus stays None, fired-condition co-exists, JSON round-trip),
  AND the production path (drives 5 real `score_change` edits
  through the unmonkeypatched stack and asserts the corpus /
  threshold / `ImpactReport.mahal_anomaly` are populated). Post-
  fix manifests `baseline-2026-09-22-scoring-v3-post.json` and
  `baseline-2026-09-22-blocker-fixes-post.json` both reflect the
  shipped state. The k-NN density and regression-head signals from
  the memo are deferred as separate workstreams (research notes
  preserved at
  `thoughts/shared/research/2026-09-19-audit-deferred-scoring-v3.md`).

Pre-baselines for the three research tracks are captured as
`baseline-2026-09-19-{embedder-ablation-pre, windowing-pre, scoring-v3-pre}.json`.
Per `AGENTS.md`, a pre-fix and post-fix immutable baseline were captured
before and after each change: see the AUDIT_RESPONSE doc for the full
registry and the `rc baseline compare` recipes.

## `rc` CLI

```bash
rc status                   # sidecar health + threshold posture
rc init                     # wire hooks into the current repo (replaces install.sh)
rc init --no-sidecar        # same, but skip the launchd/systemd daemon
rc init --no-model          # same, but skip the mamba-130m download
rc init-uninstall           # revert rc init via .reasoning-core/install.manifest
rc upgrade                  # pull the newest pip+git ref, re-verify hook wiring
rc doctor                   # verify agent-hook wiring and evidence capture
rc explain <decision-id>    # why the last edit was blocked
rc bypass-next              # arm one bypass for the next Edit/Write
rc confirm-next             # audit ground-truth (operator_confirmed event)
rc enable-enforcement       # flip repo to copilot mode (authenticated; requires PLAN.md)
rc disable-enforcement      # revert to advisory mode
rc auth-bootstrap           # generate + store enforcement token (Linux/macOS)
rc label <decision-id>      # label an audit decision (builds the eval training set)
rc label --random           # pick one unlabeled decision and label it
rc label-stats              # progress toward the 20-positive-per-label target
rc benchmark                # Markdown report from your local audit log
rc record-verification      # persist a deterministic test/lint/build result
rc real-session-eval        # correlate real sessions with labels and outcomes
rc episodes                 # derived edit episodes (checks, repairs, final status)
rc reasoning-efficiency     # composite north-star metric from the audit log
rc audit-history            # label commits negative if followed by fix/revert
```

## Use it from code

```python
from src.s2_core import score_change
r = score_change("/repo/util.py", before, after)
print(r.architectural_impact_score, r.regression_detected, r.file_kind)
```

```bash
curl -fsS -X POST http://127.0.0.1:8765/score \
  -H 'content-type: application/json' \
  -d '{"path":"/repo/util.py","before_src":"...","after_src":"..."}' | jq
```

## Documentation

- [`docs/INSTALL.md`](docs/INSTALL.md) — manual install, troubleshooting
- [`docs/USAGE.md`](docs/USAGE.md) — hook layers, rule engine, shadow mode, FAQ
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — every `RC_*` / `S2_*` env var
- [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) — System 1 + System 2 architecture
- [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) — eval numbers, per-task verdicts
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — what's shipped, what's open
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — deep technical dive
- [`docs/HARDENING.md`](docs/HARDENING.md) — threat model
- [`docs/CLI_PARITY.md`](docs/CLI_PARITY.md) — per-host caveats

## Contributing

Spec first ([`docs/PLAN.md`](docs/PLAN.md)). Self-verify before pushing:

```bash
pytest -m "not live and not slow"         # fast offline gate
pytest -m "slow" --timeout=600            # slow suite (SSM sidecar)
bash -n scripts/*.sh install.sh uninstall.sh  # syntax check
python3 -c "import json; json.load(open('.claude/settings.json'))"
```

## License

[MIT](LICENSE) © Jakub Sikora.

## Acknowledgements

[Mamba](https://huggingface.co/state-spaces/mamba-130m-hf) (Gu & Dao) ·
[Model Context Protocol](https://modelcontextprotocol.io/) (Anthropic) ·
[direnv](https://direnv.net) · [tree-sitter](https://tree-sitter.github.io/).
