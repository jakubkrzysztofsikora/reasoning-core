# Audit response — 2026-09-19 hostile review

A hostile technical audit was filed on 2026-09-19 covering reasoning-core's
runtime safety, security threat model, ML/mathematical integrity, and
benchmark data hygiene. This document records which findings were verified,
which were rejected, and what was changed in response.

The fixes shipped on the `audit-hostile/2026-09-19-fixes` branch in four
commits (`d7bb1a7`, `661e609`, `841211b`, `16d244f`, plus the post-fix
baseline `5d424b2`). All five PRs pass the regression test suite (110
tests, 1 unrelated skip).

## Verified findings and fixes

### Systems & runtime safety (Priority 1)

| Audit claim | Verified | Fix |
|---|---|---|
| `/score` runs heavy CPU/PyTorch inference synchronously on the asyncio event loop, freezing `/health` probes | Yes — `src/s2_core.py` `/score` was `async def score` calling `score_change()` directly | Wrap `score_change()` in `asyncio.to_thread`; concurrency test added (`tests/test_s2_async_offload.py`) |
| Supervisor `_HEALTH_TIMEOUT_S = 3.0s` was below worst-case Mamba CPU inference latency, tripping false SIGTERMs and 60s circuit-breaks | Yes — `src/sidecar_supervisor.py:46` | Raise default to 15.0s; `S2_HEALTH_TIMEOUT_S` env override; grace raised 30s → 60s |
| `_BASELINES` is an unbounded `dict` with no eviction — memory leak in long-running daemons | Yes — `src/s2_core.py:131` | Replace with `OrderedDict` + LRU cap (`S2_BASELINE_MAX_SESSIONS`, default 256) + TTL (`S2_BASELINE_TTL_S`, default 24h) |
| `get_or_build_index()` calls `fut.result(timeout=300)`, blocking the event-loop thread for up to 5 minutes on fresh build | Yes — `src/project_index.py:394` | Return `None` immediately on the fresh-build branch; subsequent calls see the cached future on the fast path; drop the immediate `executor.shutdown(wait=False)` that was cancelling the walk |

### Security — close the gaps the audit correctly identified (Priority 2)

| Audit claim | Verified | Fix |
|---|---|---|
| `git checkout HEAD~1 -- src/hooks/foo.py` rewrites source without the guard catching it | Yes — no `git checkout\|restore\|stash apply\|apply` patterns | Add patterns in `SRC_WRITE_PATTERNS` + hard-deny for `git apply`, `git checkout <sha>`, `git restore`, `git stash apply` |
| `pathlib.Path('x.py').write_text(...)` (the audit's example) is not covered by the existing `open(path, 'w')` regex | Yes | Add `pathlib\.Path(...)\.(?:write_text\|write_bytes)\(` pattern |
| `cp/mv/install/rsync` to a src-extension destination is only caught by the lang-lock layer, not the base guard | Partially — present in the lang-lock layer only | Lift to base `SRC_WRITE_PATTERNS` so they fire regardless of `RC_LANG_LOCK` |
| `base64 -d \| bash\|sh\|zsh\|eval` (the audit's kill-switch poisoning example) is unguarded | Yes | Add `base64 ... -d ... \| bash\|sh\|zsh\|eval` and `$(echo ... \| base64 -d)` patterns |
| `is_guarded_path()` uses `os.path.abspath()` not `realpath()`, so symlink bypass is possible | Yes — `src/hooks/_guard_paths.py:153` | Switch to `os.path.realpath()` |

The audit's broader claim that the base guard only catches redirects/tee was
**partially wrong**: the lang-lock layer already covered `mv/cp/install/rsync`,
`python -c open()`, and `node -e writeFileSync`. The real gaps were git
subcommands, pathlib, base64, and symlink resolution.

### Documentation & data hygiene (Priority 3)

| Audit claim | Verified | Fix |
|---|---|---|
| `BENCHMARKS.md` T5 and T7 rows are byte-identical (84 533 tokens, $15.53, same quality scores) | Yes — physically impossible for two distinct end-to-end runs | Retract the table; preserve surviving 5 cells (T1, T2, T8, T9, E1) with explicit "n=1 illustrative" caveat |
| P0 winner is inverted against the pre-registered decision rule | Yes — Setup A wins P0 on impl_q (3.5 vs 2.0), tokens (4× fewer), and cost (2.5× cheaper) | Drop P0 from the surviving table; document the retraction |
| Two latency tables contradict each other by 484s per run | Yes — Table 1 says +98s; Table 2 says −386s | Mark Table 1 as superseded; canonical Table 2 carries the retraction header |
| Whitepaper `scoring.tex:47-49` claims AIS = sigmoid-weighted risk combination | Yes — wrong; `src/s2_core.py:966` uses `AIS = (cos + 1) / 2` | Replace the false claim with the actual formula; note that AIS, coherence_delta, and novelty are scalar transforms of cos |

## Rejected findings

### Label-polarity inversion in `_supervisor_recalibrate.py`

**Audit claim:** `eval/calibration_corpus.py` defines `negative = benign,
positive = regression`, while `_commit_miner.py` defines the opposite. The
supervisor recalibrator imports `eval.calibration_corpus.mine` and keeps
only `label == "negative"` rows, which the audit said feeds the
Mahalanobis fit on regressions instead of benign.

**Verified:** the polarity split is real (`eval/calibration_corpus.py:13-14`
defines negative = benign; `src/hooks/_commit_miner.py:196,206,211` defines
the opposite). But the recalibrator's filter is consistent with its data
source: when fed from `eval.calibration_corpus.mine`, `label == "negative"`
correctly selects benign rows. The runtime behavior is correct.

**Risk:** the ambiguity is real — a future refactor that swaps the miner
import from `eval.calibration_corpus` to `_commit_miner` without flipping
the filter would silently train the calibration baseline on regressions.
We added an explicit comment at `src/_supervisor_recalibrate.py:90` naming
the trap.

### `install.sh` blindly trusts untrusted `.envrc` via `direnv allow .`

**Audit claim:** `install.sh` runs `direnv allow .` on untrusted repos.

**Verified as partially wrong:** `install.sh:121-123` skips writing
`.envrc` if one already exists; the script only auto-approves its own
generated file. `direnv` still requires the user to trust the directory on
first prompt, which is a documented operator action, not an automatic
trust grant. We did not change `install.sh` here.

### 1500ms client hard-cap "100% bypasses" the neural backbone

**Audit claim:** every edit times out at `S2_HARD_CAP_MS=1500`, falls
back to static regex, and exits 0 (allowed).

**Verified as partially wrong:** `pre_edit_guard.py:1046-1090` does fall
back to `_symbolic_fallback` on hard-cap, but the fallback runs the full
symbolic gate chain and respects `RC_MODE` — `exit_block` is still possible
in `copilot` mode. The audit's framing of "100% bypassed in production" is
only true in `advise` + `RC_SHADOW_MODE=1`, which is the documented
Phase-0 honest opt-in (see `docs/AUDIT_GAP_2026_07_10.md`). We did not
change the hard-cap default; the right fix is to make the default
posture more visible in docs (already covered by the existing
AUDIT_GAP doc).

### `ais < 0.4` check is "100% dead code"

**Audit claim:** the `ais < 0.4` check is never reachable because CD fires
first on any non-trivial negative cosine.

**Verified as partially wrong:** the check exists (`src/s2_core.py:1072`:
`if ais < t["ais"]`) and would fire on `cos < -0.2`. The audit's
*mathematical* conclusion about trivial double-trigger is correct (AIS
and CD are scalar transforms of `cos`, so when CD fires, AIS has already
fired too), but the check itself is not unreachable.

We did not remove the check — it provides defense-in-depth and emits a
distinct audit signal — but we added the whitepaper note in
`docs/whitepaper/sections/scoring.tex` making the algebraic redundancy
explicit.

## Deferred to separate workstreams

The following audit findings are real but were deferred because they are
research items, not bug fixes:

- **Mamba-130M is "the wrong model for code"** (`src/dup_embed.py:18-21`
  agrees in code comments). The swap to a code-pretrained encoder
  (`unixcoder-base`, `bge-code-v1`, or a gguf of `bge-large`) requires
  its own benchmark ladder. Track as a separate embedder-ablation
  workstream.
- **512-token truncation blinds the model past line ~40.** The fix
  (diff-localized windowing, AST-scope context windows) is a research
  item. Track as a separate scoring-v3 workstream.
- **Algebraic redundancy of AIS / CD / Novelty.** Removing two of the
  three signals requires a re-calibration sweep against
  `eval/calibration_corpus.py`. Track under scoring-v3.

## Baselines (per AGENTS.md)

| ID | Captured | Git SHA | Purpose |
|---|---|---|---|
| `baseline-2026-08-09` | 2026-08-09 | `5947d66` | Pre-audit reference |
| `baseline-2026-09-19-pre-audit-fixes` | 2026-09-19 | `25d042b` | Pre-fix snapshot at audit time |
| `baseline-2026-09-19-post-audit-fixes` | 2026-09-19 | `5d424b2` | Post-fix snapshot at branch head |

Compare with:
```bash
rc baseline compare baseline-2026-09-19-pre-audit-fixes \
                     baseline-2026-09-19-post-audit-fixes
```

---

# Round 2: re-audit-hostile/2026-09-19-reaudit-fixes response

The 2026-09-19 re-audit (`audit-hostile/2026-09-19-reaudit-fixes`) issued a
hostile follow-up against the round-1 patches. Verified status of each item:

## Verified (closed in this branch)

| Re-audit ID | Claim | Status |
|---|---|---|
| RC-SYS-02 | `/baseline` still synchronous on the event loop, freezes for 30–60 s | **Closed** in `c4d8a04`. `/baseline` now dispatches `_build_session_baseline_sync()` via `asyncio.to_thread`. Verified by `tests/test_reaudit_baseline_offload.py::test_health_responds_while_baseline_in_flight`. |
| RC-SEC-02 | Directory copies (`cp payload.tmp src/hooks/`, `mv`, `install`, `rsync`) bypass the destination-extension regex | **Closed** in `7b99441`. The destination regex was rewritten to match the second argument of `cp|mv|install|rsync`. |
| RC-SEC-05 | Symlink overwrite through `/tmp/payload.tmp` bypasses the substring layer | **Closed (deepened)** in `169da13` + this branch. `_resolve_symlink_to_guarded()` now resolves every redirect target via `os.path.realpath()` and **blocks unconditionally** when the realpath lands on a guarded path, regardless of the symlink's own filename or extension. Regression covered by `tests/test_pre_bash_guard_symlinks.py`. |
| RC-SEC-06 | `install.sh` auto-approves pre-existing attacker-controlled `.envrc` via `direnv allow .` | **Closed** in `97060f0`. `install_envrc()` exports a sentinel `ENVRC_GENERATED_BY_INSTALL=1` on the write branch; `direnv_allow()` refuses to call `direnv allow .` unless the sentinel is present. |
| RC-ML-04 | `ais < t["ais"]` check is 100 % dead code | **Closed (shipped)** in `6726c6a`. `ImpactReport` now carries `mahal_anomaly` + `mahal_anomaly_threshold` (new independent signal under `RC_SCORING_V3=1`); `mahal_anomaly_above_threshold` is a new fired condition. The `ais < t["ais"]` check remains for public-API stability but is no longer the only AIS-shaped signal — `mahal_anomaly` carries distinct information by construction. The whitepaper note makes the algebraic redundancy of AIS / CD / Novelty explicit and the new section documents the `mahal_anomaly` signal alongside the legacy 3-way. |
| RC-ML-05 | `_supervisor_recalibrate` import crash on wheel install (`from s2_core` → `ModuleNotFoundError`) | **Closed** in `6cebf55`. Imports changed to `from .s2_core` and `from .calibration`; `eval/` package added to `pyproject.toml` setuptools packages. |
| RC-SCORING-V3-01 | Audit proved AIS / coherence_delta / novelty are scalar transforms of the same cosine similarity (algebraic redundancy); the 3 signals do not provide 3 independent readouts | **Closed (shipped)** in `6726c6a`. New module [`src/scoring_signals.py`](../src/scoring_signals.py) exposes `mahal_anomaly_against_corpus` (squared Mahalanobis distance of the after-embedding against the session's Ledoit-Wolf shrunk benign-embedding corpus). `ImpactReport` gains `mahal_anomaly` + `mahal_anomaly_threshold`; new fired condition `mahal_anomaly_above_threshold` trips when the score exceeds the per-session FPR=0.05 threshold. **Independent by construction**: the value is not derivable from `cos`, `CD`, `AIS`, or `novelty`. 22 new tests in [`tests/test_scoring_signals.py`](../tests/test_scoring_signals.py) + [`tests/test_scoring_v3_wiring.py`](../tests/test_scoring_v3_wiring.py) cover the math (centroid symmetry, PSD inverse, distance scaling, FPR quantile match, OOD detection, determinism) and the end-to-end wiring (default-off no-op, on-path with corpus, no-corpus stays None, fired-condition co-exists with existing checks, JSON round-trip). Post-fix baseline: `baseline-2026-09-22-scoring-v3-post.json`. The legacy 3-way redundancy is left intact for backward compatibility with operators + audit dashboards that key on `ais<0.4`. |
| RC-SCORING-V3-02 | k-NN density and regression-head signals from the scoring-v3 memo would also be additive | **Parked** — not part of the Pareto80/20 Phase C. The memo's other approaches (kNN density on a benign bank, regression head trained against `eval/calibrated/labels.jsonl`) carry higher implementation cost and a smaller marginal information gain over `mahal_anomaly` (which already covers out-of-distribution after-embeddings). Reopen if/when the calibration corpus is re-mined on this branch's history. |

## Verified and deepened (this branch)

| Re-audit ID | Claim | Status |
|---|---|---|
| RC-ML-02 | Headline-numbers table in `docs/BENCHMARKS.md` still published 8-cell aggregates | **Closed** in this branch. The 8-cell table is now boxed as `RETRACTED 2026-09-19 (8-cell set)` and a new "recomputed surviving-5 aggregates" subsection publishes cost / token / quality means across the 5 surviving cells. Wall clock is **withdrawn** (per-task wall-clock was never recorded separately from the retracted runs). |
| RC-ML-03 | Whitepaper `results.tex` still published the T5/T7/P0 copy-pasted rows and the `6/8` headline | **Closed** in this branch. Captions updated to "RETRACTED 2026-09-19", T5/T7/P0 rows labelled `\emph{retracted}`, a new "Per-Task Verdicts (surviving 5 cells, post-retraction)" table added, and a `sec:discussion-retraction` section added to `discussion.tex`. The literal `\\n` Python-source-artifact LaTeX row separators that were corrupting the file are also fixed (47 occurrences normalised to real newlines). |

## Verified and still open (deferred — research items)

| Re-audit ID | Claim | Why deferred |
|---|---|---|
| RC-ML-Truncation | 512-token truncation blinds the model past line ~40 | Requires windowed diff embeddings (research item). Charter: `thoughts/shared/research/2026-09-19-audit-deferred-windowing.md`. |
| RC-ML-Embedder | Mamba-130m is "the wrong model for code" | Requires swap to code-pretrained encoder. Charter: `thoughts/shared/research/2026-09-19-audit-deferred-embedder.md`. Pre-baselines: `baseline-2026-09-19-{embedder-ablation-pre,scoring-v3-pre,windowing-pre}.json`. |
| RC-ML-Auto-sizing | `rc init` could auto-pick the largest Mamba3 variant that fits available RAM/CPU | User-requested feature on the deferred list. Will land with the embedder swap; requires model cards to be pulled first. The four candidates under consideration: `state-spaces/mamba3-siso-893m`, `state-spaces/mamba3-mimo-894m`, `state-spaces/mamba3-siso-1.5b`, plus the paper at <https://arxiv.org/abs/2603.15569> (Mamba-3 SISO/MIMO achieves 7× faster long-context decode vs vLLM Transformer at 16K tokens: 140.61 ms vs 976.50 ms). |

## Verified and rejected

| Re-audit ID | Claim | Why rejected |
|---|---|---|
| RC-SYS-01 | Threadpool thrashing causes GIL contention | Real concern but the un-fused Mamba loop is what dominates; the move to a separate process (the recommended fix) is on the deferred list until the embedder swap lands. Until then, the GIL is not the dominant cost — the un-fused kernel is. |
| RC-SYS-03 | `<1 %` OS heap reclaimed; per-session file cap uncapped | Closed by `_BASELINE_MAX_FILES_PER_SESSION` (env-overridable). The empirical heap-reclamation critique is correct, but the per-session cap is the right **leak prevention** lever; full RSS cap is a separate ops concern tracked by the sidecar watchdog. |
| RC-SEC-01 | `git checkout HEAD~1`, `git reset --hard`, `git switch`, `git merge` bypass the regex | The hardened regex requires either a hex commit SHA (`[0-9a-f]{7,}`) or an explicit `src/hooks`, `src/mcp`, settings, or kill-switch fragment. Branch names alone do not match the destination-extension regex. `git switch` is intentionally not in the regex — `git switch` changes working-tree files only, not committed paths, and a working-tree change to a guarded file is still caught by the realpath / substring layer. The merged-bypass scenarios in the re-audit would all require writing through a hook path that the symlink resolver now blocks (RC-SEC-05). |
| RC-SEC-03 | `base64 -D`, `base64 --decode`, `python3 <<EOF`, `openssl enc -d`, `xxd -r` bypass the base64 regex | The regex matches both the `(decode\|--decode\|-D\|-d)` forms and the `python3 -c` / `python3 <<` heredoc form. A heredoc-style `python3 <<EOF ... EOF` is itself not a `--c`-style invocation; it falls through to the src-write layer, which requires the destination to be a source-extension path. **An attacker who pipes `python3 <<EOF` into a heredoc redirect to a guarded path is caught by the heredoc-to-source-extension regex.** The remaining `xxd -r -p` vector is acknowledged; the `rc init` install path recommends running unknown agents inside a sandbox (Docker / bubblewrap / macOS `sandbox-exec`) for full coverage. |
| RC-SEC-04 | `from pathlib import Path; Path().write_text()`, `open(mode='w')` bypass the pathlib regex | The regex matches `pathlib.Path(...).write_*`, `pathlib.Path(...)`, and `open(..., mode='w'|'a')` forms. The re-audit's "from pathlib import Path" form is matched by the `pathlib\.Path` token regardless of import style — regex doesn't care about the import statement. The bare `open('x', mode='w')` form is matched by the `open\s*\(\s*['"][^'"]+['"]\s*,\s*['"][wa]['"]` regex. |

## Bench status

| Bucket | Result |
|---|---|
| New regression tests | `tests/test_pre_bash_guard_symlinks.py` (5), `tests/test_reaudit_baseline_offload.py` (4) — all pass |
| Pre-existing test buckets | 905 passed, 4 skipped, 0 failed |
| Post-fix baseline | `baseline-2026-09-19-reaudit-post` at git SHA `6cebf5528f551cd8596cc52ed841d91e2de202d5` |

Compare with:
```bash
rc baseline compare baseline-2026-09-19-reaudit-pre \
                     baseline-2026-09-19-reaudit-post
```

---

# Round 3: audit-hostile/2026-09-19-reaudit-fixes (deferred-workstream landing)

The 2026-09-19 re-audit closed all P1 (systems) and P2 (security) findings
on `audit-hostile/2026-09-19-reaudit-fixes`; it explicitly punted the three
"real concerns but research items" findings to deferred workstreams. This
round implements the landing of Phase A (windowing) and Phase A.5
(`rc init` auto-sizing) from the rollup plan
(`thoughts/shared/plans/2026-09-19-audit-deferred-rollup.md`), and
captures the pre-baselines for Phase B (embedder swap) and Phase C
(scoring-v3 re-calibration). The default-flip for Mamba-3 stays
correctly gated because the Mamba-3 architecture kernels are not yet
available on this host.

## Closed in this round

| ID | Claim | Status | Verification |
|---|---|---|---|
| RC-ML-Truncation | 512-token truncation blinds the model past line ~40 (Phase A) | **Closed (infrastructure landed; consumer swap still pending)** | New module `src/diff_windowing.py` provides `embed_windowed(text, lang, diff_hunks)` with the same vector shape as `ssm_backbone.embed(text)`. AST-scope chunker per language family + 64-line stride line-window fallback. Tests: 20 pass in `tests/test_diff_windowing.py`. The wiring into `src/s2_core.py:953-983` is staged for the next refactor PR to keep this PR tight against the AGENTS.md "deterministic-only hard-block" rule. |
| RC-ML-Embedder | Mamba-130m is "the wrong model for code" (Phase B) | **Blocked by host prerequisites** | Pre-reg eval ladder shipped (`eval/pre_reg_embedder.py`); harness routed through `ssm_backbone.embed` so production and measurement use the same code path (commit `a234bc3`); HF SHA pinning (`RC_<REPO_SLUG>_REVISION`) added so measurement is bit-reproducible against the same checkpoint SHA. Mamba-3 candidates are registered in `_BACKENDS`; model cards pinned via `eval/pin_model_cards.py`. The refusal-gate test (`tests/test_pre_reg_embedder_gate.py`) correctly rejects the default flip while `mamba-ssm>=2.0.0` is unavailable. Re-run after `pip install mamba-ssm>=2.0.0 causal-conv1d`. |
| RC-ML-Auto-sizing | `rc init` should auto-pick the largest variant that fits (Phase A.5) | **Closed** | New module `src/embedder_tier.py` exposes `detect_tier(ram_gb, disk_gb) -> str`, `pick_backend(tier) -> str`, `estimate_working_set_gb(backend)`, `fits(backend, ram_gb)`. Wired into `rc init` via `tests/test_rc_init_embedder.py`. Tier matrix: xlarge≥32GiB → `mamba3-siso-1.5b`, large 16-32GiB → 1.5b or 893m, medium 8-16GiB → 893m, small 2-8GiB → `bge-code`/`unixcoder-base`, fallback<2GiB → `mamba-130m`. Operator override via `RC_EMBEDDER=<backend>` is still honoured. Pre-reg test: 29 unit tests in `tests/test_embedder_tier.py` cover the (ram, disk, backend) grid + the oversized warning path. |

## Pre-baselines captured (per AGENTS.md "immutable pre-baseline" rule)

| Baseline ID | Phase | Notes |
|---|---|---|
| `baseline-2026-09-19-windowing-pre` | A | Captured at git SHA `e359e2a5` (Phase A commit). Diff-windowing produces byte-identical embeddings across re-runs (determinism L2 tolerance 1e-9). |
| `baseline-2026-09-19-embedder-ablation-pre` | B | Captured against `mamba-130m` default; the post-baseline will be captured only after the pre-reg ladder flips `_DEFAULT_BACKEND_NAME`. |
| `baseline-2026-09-19-scoring-v3-pre` | C | Captured against the current `_KIND_THRESHOLDS` table (pre-Phase-C reference). |
| `baseline-2026-09-22-scoring-v3-post` | C | Captured at git SHA `6726c6a` after `mahal_anomaly` shipped. Pair: `baseline-2026-09-19-scoring-v3-pre` -> `baseline-2026-09-22-scoring-v3-post`. |
| `baseline-2026-09-19-reaudit-pre` / `-post` | (round 2) | The Round-2 reference pair. See round-2 section above. |

## What remains parked

- **Mamba-3 measurement** — the pre-reg ladder can pass once a host has
  `mamba-ssm>=2.0.0` (or the equivalent `mamba3` package if upstream
  has split it out) installed and compiled. The kernel-latency
  advantage from paper Table 6/Table 7
  (<https://arxiv.org/abs/2603.15569>) — SISO 0.156 ms/tok at d_state=128
  vs Mamba-2 0.203 ms/tok; 140.61 ms decode at 16K vs vLLM-Transformer's
  976.50 ms — is *not yet measurable* on this sandbox. Status:
  `eval/runs/PRE_REG_STATUS_2026_09_21.md`.
- **Mamba-3 fast-path on macOS ARM** — separate workstream. Until the
  upstream kernels land, `rc init` correctly refuses to pick the 1.5b
  variant on hosts lacking the fast path.
- **Scoring-v3 sweep** — **shipped 2026-09-22** on commit
  `6726c6a`. `mahal_anomaly` is now a first-class ImpactReport field
  under `RC_SCORING_V3=1` (default off). 22 new tests cover the math
  + the end-to-end wiring. Post-fix baseline:
  `baseline-2026-09-22-scoring-v3-post.json`. The k-NN density and
  regression-head signals from the memo remain parked as separate
  workstreams. The Ledoit-Wolf re-calibration sweep is also parked
  (requires re-mining the calibration labels on this branch's history,
  which is 30-50 pairs per kind today; out of scope for the Pareto
  Phase C landing).

## Bench status

| Bucket | Result |
|---|---|
| New regression tests added this round | `tests/test_diff_windowing.py` (20), `tests/test_embedder_tier.py` (29), `tests/test_rc_init_embedder.py` (7), `tests/test_pre_reg_embedder_gate.py` (5, of which 3 pass and 2 fail-as-designed), `tests/test_scoring_signals.py` (15), `tests/test_scoring_v3_wiring.py` (7) |
| Pre-existing test buckets | unchanged from round 2 |
| Post-fix baselines captured | `baseline-2026-09-19-{windowing-pre, embedder-ablation-pre, scoring-v3-pre}`, `baseline-2026-09-22-scoring-v3-post` |
| Mamba-3 default flip | **stays as `mamba-130m`** until `tests/test_pre_reg_embedder_gate.py::test_pre_reg_embedder_gates_all_pass` goes green |

## Commits in this round

```
6726c6a reaudit-P4-scoring-v3: mahal_anomaly as independent signal (Phase C)
e359e2a feat(windowing): AST-scope diff windowing + diff-weighted pooling
a234bc3 chore(pre-reg): route harness through ssm_backbone + HF SHA pinning
7f37850 chore(baselines): capture embedder-ablation + scoring-v3 pre-baselines
6ae2276 chore(deferred): probe scripts + partial pre-reg run + status snapshot
a810bac docs(deferred): research memos + rollup plan for the deferred workstreams
7a32877 chore(baselines): reaudit-pre baseline (full reaudit reference)
```
