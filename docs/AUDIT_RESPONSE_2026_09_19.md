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
