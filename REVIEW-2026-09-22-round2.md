# reasoning-core: The Re-Review — Fixes, Fresh Blockers, and an Attestation Problem

> **Independent re-review, 2026-09-22 (round 2).** Four adversarial expert re-audits (scoring/ML fix verification, security re-sweep, documentation/claims audit, evaluation-integrity sweep) against HEAD `57ee877` plus the maintainer's working-tree fixes — every load-bearing claim re-verified by execution on the current tree: end-to-end `score_change` runs with the real embedder path, 120-trial Monte-Carlos of the new calibration, `decide()` traces at three host sizes, a 41-command guard-bypass probe, PyPI metadata, and the full fast test suite (~936 tests). One caveat colors everything and must be stated up front: **the entire fix round exists only as uncommitted working-tree changes and untracked test files.** During the audit the tree was stash-popped and re-applied by a concurrent process twice, and new fixes continued landing mid-review. Nothing below should be read as describing any commit — because at every commit, the fixes do not exist.

---

## What happened since round 1

The round-1 review found two BLOCKERs (a flagship scoring signal that could not execute; an installer that bricked high-RAM machines), thirteen open adversarial-control holes, an arithmetic error inside a benchmark retraction, and a pattern of documentation asserting more than the code does. Credit where it is due first: the maintainer responded fast and in the right places. The working tree now contains:

- **The BLOCKER #1 fix, and it is real.** `torch` is imported at module level; a new `_maybe_promote_session_to_corpus()` (`src/s2_core.py:346-393`) folds per-path session baselines into a corpus once `RC_MAHAL_CORPUS_MIN` (default 5) paths accumulate, fits Ledoit-Wolf, and writes the threshold `__mahal_threshold__` that previously nothing in production ever wrote. We executed the full path end-to-end: threshold written, `mahal_anomaly` populated on the sixth edit, condition trips. The old fixture-seeded test failure mode is directly repaired: the new `tests/test_mahal_production_wiring.py` drives real `score_change` calls and we verified by inspection that **deleting the promotion function fails all four of its load-bearing assertions**. That is what a regression test for this bug should look like.
- **The BLOCKER #2 fix, and it is competently designed.** `backend_loadability_probe()` (`src/ssm_backbone.py:313`) refuses `mamba3-*` backends where `mamba_ssm` kernels are absent; `decide()` now walks the tier matrix down past rejected candidates; `_init.install_envrc` writes the safe fallback and warns. Executed at 40 GB: the picker that last round returned `mamba3-siso-1.5b` now returns `mamba-130m` with an audit-trail reason string. Exactly what the review demanded.
- **A live response to the calibration critique.** Mid-audit, a leave-one-out threshold (`loo_threshold_for_fpr`) landed in the tree, citing the round-1 finding by name, wired into the production promotion path (`src/s2_core.py:391`).
- **Windowing wired, opt-in.** `RC_DIFF_WINDOWING=1` now routes embeddings through the chunked embedder with a tri-state `windowed_embed_active` field and graceful fallback, plus six wiring tests.

This is genuinely responsive engineering, executed faster than most funded teams manage. And it still should not ship, because each fix carries a fresh defect of the same blocker class, the security surface received **zero** changes, and the documentation/baseline layer now makes claims that are falsified by the repository's own git history.

---

## Finding 1 — CRITICAL: The "shipped" fix round is attested by a baseline that proves the opposite

This is the round's defining finding, and it is about evidence, not code.

`docs/AUDIT_RESPONSE_2026_09_19.md` (committed in `57ee877`) declares the scoring-v3 work *"**shipped 2026-09-22** on commit `6726c6a`"* and cites `baseline-2026-09-22-scoring-v3-post.json` as the *"post-fix manifest captured at git SHA 6726c6a."* The README repeats "shipped." The baseline manifest records `git_sha: 6726c6a…, dirty: false`.

At `6726c6a`, the feature is dead. We verified: `git show 6726c6a:src/s2_core.py` contains no corpus promotion, no threshold writer, no module-level torch import — the exact defects round 1 proved make `mahal_anomaly` a permanent `None`. The actual fix exists only in the working tree, and the two test files that would catch its regression are **untracked** — no commit contains fix and tests together. `git ls-tree -r 6726c6a | grep mahal_production_wiring` → empty; same at HEAD.

The consequence is precise: **the immutable baseline registry — the project's flagship epistemic device, mandated by its own AGENTS.md — is being cited as post-fix evidence while its own fields attest a tree without the fix.** CI at any pushed SHA is green while shipping both round-1 blockers. `git checkout 6726c6a` reproduces the dead feature with green tests. An attestation mechanism designed to prevent exactly this is certifying exactly this. Two aggravators: the post baseline's configuration snapshot shows `RC_MOCK_DETECTOR: "1"` (mock path enabled), and its `configuration_hash` is identical to the *pre*-fix baseline's — the pre/post pair is internally consistent and jointly attests the unfixed state.

During the audit this got a live demonstration no writer would dare invent: the working tree was stash-popped by a concurrent process twice (both our security and eval auditors observed it independently), briefly leaving the repo with *no* copy of the fix outside a stash entry. A "shipped" feature whose only embodiment is a movable working tree is not shipped; it is at risk.

## Finding 2 — BLOCKER (new, introduced by the fix): the calibration now demonstrably fails in both directions

The round-1 critique was that the FPR=0.05 threshold was an in-sample quantile over a five-point corpus. The maintainer's LOO patch addresses it — and we must report the full empirical picture, because we measured it:

- **Old path (in-sample quantile), production dimensionality (d=768, n=5): realized benign FPR = 100%.** Monte-Carlo, 60 trials against the repo's own `scoring_signals` math: every fresh benign edit exceeded the threshold. (The n=5 in-sample 95% quantile of five points is the sample max; in 768 dimensions with n≪d, out-of-sample Mahalanobis distances inflate past it with near-certainty. Prior round's d=8 estimate of ~0.20 was optimistic; production dims are catastrophic.) With `RC_NEURAL_CORROBORATED=0`, that is a neural-only signal blocking every benign edit.
- **New LOO path, same Monte-Carlo: realized benign FPR = 0/60 — not the ~0.05 the docstring claims.** The fix overshoots into near-inertia: the threshold is so conservative that the detector essentially never fires on benign data — which also means it detects almost nothing. At n=5 you can have calibrated FPR *or* a useful detector, not both; the honest documentation would say "this signal is nearly inert until the corpus reaches meaningful size," not "FPR=0.05."
- **Degenerate corpus (five near-identical embeddings — reachable via `/baseline` poisoning or a collapsed session): LOO threshold ≈ 2660, fresh benign edit scores 2.6×10¹⁸ — fires on every edit.** The pre-LOO code went NaN-silent here (inf > nan is false; the in-code "deliberate fail-loud" comment was false). The LOO patch accidentally made the comment true — "loud" meaning *always-fire*, which for a gate is the wrong kind of loud.

All three regimes were executed against the current tree. The fired condition feeds `regression = len(fired_conditions) > 0` (`src/s2_core.py:1458`) and is held back from hard-blocking only by the default-on `RC_NEURAL_CORROBORATED=1` — a flag that appears **nowhere in README, CONFIGURATION.md, or HOW_IT_WORKS.md**, in violation of the project's own AGENTS.md rule that neural signals never hard-block alone. Three new environment variables (`RC_SCORING_V3`, `RC_DIFF_WINDOWING`, `RC_MAHAL_CORPUS_MIN`) plus this fourth are undocumented production surface.

There is also a **new regression the fix introduced**: once a session promotes a corpus, per-path auto-persist is permanently skipped (`src/s2_core.py:307: if path not in baselines and "__corpus__" not in baselines`), so the session never learns another path, the re-fit amortization key (`__mahal_last_fit_n__`) becomes dead code, and cumulative-drift for new files is computed against a 2-D corpus tensor instead of a vector. Executed: after promotion, new paths are never persisted and exactly one fit ever runs.

## Finding 3 — BLOCKER (relocated, not fixed): the brick moved from big machines to small ones

The loadability probe is real, but it blesses `bge-code` "a priori" (`src/ssm_backbone.py`: all non-`mamba3-*` backends return True) — while the same file's registry documents that `bge-code` / `unixcoder-base` / `codestral-mamba` carry `revision="main"` and are **fail-closed** under `_resolve_revision_for_backend` until an operator pins a SHA. `_PINNED_REVISIONS` contains no `bge-code` entry (verified against the current tree).

So: `decide(ram=4GiB, disk=50, probe=backend_loadability_probe)` → `bge-code`, tier small, probe-blessed → `rc init` writes `RC_EMBEDDER=bge-code` to `.envrc` → load fails closed on the unpinned revision → operator-pin semantics refuse fallback → 503 on every score → `S2_FAIL_CLOSED=1` blocks every edit. **The original BLOCKER, re-issued to the 2–8 GiB host band** — laptops, small VMs, CI runners. The fix protected workstations and quietly handed the same failure to small machines. Two residual nits: the probe is spoofable (a file named `mamba_ssm` on `sys.path` — agent-writable under this threat model — flips it), and `decide()` *without* a probe retains the old behavior, so any future caller that forgets `loadability_probe=` re-opens the original brick.

## Finding 4 — BLOCKER (new, in the feature the fix wires in): the windowing path can silently exclude the very edit it exists to see

The truncation fix was round 1's "dead code" finding; it is now wired. But `src/diff_windowing.py` caps chunked embedding at 32 chunks via stride subsampling (`chunks[::stride][:32]`, `_MAX_CHUNKS_PER_FILE = 32`), and diff-weights assign zero to non-overlapping chunks. Executed with the real parser: in a 4,000-line file (~70+ top-level scopes) with a planted malicious line at line 1,900, the changed chunk is subsampled away — `coherence_delta = 0.000000` **identical in both modes**, while the report says `windowed_embed_active=True`. Coverage probe: changed-chunk survives at 60 functions, silently dropped at 70, 90, 100, 300, 2,000. Even when the chunk survives, the signal is faint: a full semantic rewrite of one function among 100 moves chord distance from 0.0000 to 0.0274 — no fired condition.

So the flagship anti-truncation feature, flagged as active in the audit row, scores the diff blind for any file with more than ~64 scopes, and its new wiring test monkeypatches the chunker wholesale — the real stride hole passes the entire suite. An attacker's optimal move against this gate is now precisely documented: bury the edit in a big file and let the tool certify the blind spot.

## Finding 5 — Security: thirteen findings re-verified, zero fixed

The fix round touched none of `pre_bash_guard.py`, `_guard_paths.py`, `rc_cli.py`, `install.sh`, or `HARDENING.md`. Our security auditor re-executed the round-1 battery against the current tree: **0 of 13 fixed.** `python3 src/rc_cli.py bypass-next` still disarms the next block unauthenticated; `kill_switches.json` is still writable via string construction; `.envrc.local` is still the unguarded persistent self-liberation channel; APFS case-variants still defeat the edit lock (live-demoed on this volume); `git stash pop`/`cherry-pick`/`revert`/`am`/`pull`/`fast-import` still rewrite guarded files; `pkill -STOP -f reasoning-core-sidecar` still freezes the sidecar by its real process title; the audit log is still unchained and rewritable. One new sub-detail: `node -e "fs.promises.writeFile(...)"` evades the node regex on letter-case. The comment at `pre_bash_guard.py:76-79` still claims the "Reviewer-flagged P0" was closed. It was not; we can still type the one-liner.

The structural verdict also stands: the same-user ceiling means this layer is friction and tamper-evidence, not confinement — and `HARDENING.md` still does not say so. What *is* true, and worth saying plainly: because neural signals are advisory by default, nothing the agent poisons becomes a hard block. The default posture is what keeps the new scoring stack security-irrelevant rather than security-hazardous — an odd sentence to write about a product whose paid mode is enforcement.

## Finding 6 — The PyPI quick-start installs someone else's software

Round 1 flagged version drift (repo 0.3.0 vs PyPI 0.4.0). The re-audit dug into the metadata and we re-verified by direct fetch: **the `reasoning-core` package on PyPI is a different project entirely** — "Procedural data generators for symbolic pre-training, also including RL environments," dependencies `duckdb`, `faker`, `z3-solver`-adjacent tooling, a different author. The README's step 1 — `pip install reasoning-core[full]` — therefore installs an unrelated library that ships no `rc` binary. The documented quick start is not merely awkward; on a clean machine it **fails end-to-end while appearing to succeed**, and it hands a namespace collision to anyone who ever does publish this project. Combined with the still-stale `dist/` wheel (0.2.0), the zero git tags, and the advertised-but-nonexistent `v0.3.0` ref, the entire distribution story is fiction at every layer.

## Finding 7 — The documentation ledger: eight still open, four newly stale

Still open, untouched, re-verified: the six-vs-seven CLI contradiction; the impossible pip-time model download (contradicted by the README's own step 2); the staged ruff block example (ruff skips auto-fixable findings; `tempfile` is used); the **plan-quality arithmetic error inside the retraction** — B plan quality published as 2.20/−35.3% against a recomputable 2.60/−23.5% from the doc's own rows, now surviving its **third** consecutive retraction-adjacent commit and confirmed in the whitepaper's identical table; the dollar-extrapolation table still contradicting the evidence-status box forty lines away; the dateless κ=0.8025 sentinel still gating live behavior with no staleness check; the "blocks unconditionally" symlink claim with its two live escapes.

Newly stale in the other direction: the README tier table still advertises `xlarge → mamba3-siso-1.5b` — a row the fix deliberately made unreachable (every host now gets `mamba-130m` until kernels land); the audit response still documents the never-existent `detect_tier`/`pick_backend` API while the real API gained `loadability_probe`/`safe_backend_for_envrc` — undocumented; the Phase A section still says the consumer swap is "staged for the next refactor PR" while the tree contains it; the bench-status table still says 29 embedder-tier tests (now 34). The docs describe a fourth version of the system that matches neither the last commit nor the working tree.

## Finding 8 — Evaluation: zero new evidence, one control contaminated

No pre-registered program gained a data point: SWE-bench n=100, the 5-arm human-labeled protocol, the iter-2 sign-test ladder, and the Kimi study remain exactly as round 1 found them — four preregistrations, zero completions, Kimi's abandoned endpoint still unamended. The committed `pre_reg_embedder_partial.json` still carries byte-identical embedding vectors — and the re-audit found the contamination **extends to `random-mamba`, the falsifiability control itself** — alongside a falsifiability gate asserting `passed: true` with `observed: null`. The whitepaper's "Iter-3 Results Summary" still publishes aggregates of retracted per-task data with no retraction marker. The only new committed evidence-adjacent artifact this round is the mis-attesting baseline of Finding 1.

One genuinely good thing did emerge here: the new `test_pre_reg_embedder_manifest_fresh_enough` staleness tripwire — which fails if `ssm_backbone.py` is edited after the manifest was captured — fired **during this audit, because the maintainer was editing `ssm_backbone.py` while the suite ran**. The repo policing its own author in real time is the single most on-thesis moment of this entire review. (It also means the suite is currently red: ~936 tests, 3 failures — the two documented fail-as-designed gate refusals, plus this tripwire doing its job.)

---

## Scoreboard — round 2

| # | Finding | Severity | vs. round 1 |
|---|---|---|---|
| 1 | Fix round uncommitted; post-fix baseline attests a SHA without the fix; CI green proves nothing; "shipped" false in the version-control sense | **CRITICAL** | New |
| 2 | Calibration fails both directions: 100% benign FPR (in-sample), ~0% power (LOO), always-fire (degenerate); baseline-freeze regression; `RC_NEURAL_CORROBORATED` + 3 more undocumented env vars | **BLOCKER** | Replaced, not resolved |
| 3 | Brick relocated to 2–8 GiB hosts via unpinned `bge-code`; probe spoofable; probe-less callers unguarded | **BLOCKER** | Relocated |
| 4 | Windowing stride-cap silently excludes the edited chunk in ≳64-scope files while reporting `windowed_embed_active=True` | **BLOCKER** | New |
| 5 | Security: 0/13 re-verified open; P0-closure comment still falsified | HIGH | Unchanged |
| 6 | PyPI namespace collision: documented quick start installs an unrelated third-party package | HIGH | Worse than assessed |
| 7 | Docs: 8 still open (incl. the 2.20 arithmetic error, third commit), 4 newly stale | MEDIUM | Mixed |
| 8 | Evaluation: zero new evidence; falsifiability control itself contaminated; whitepaper Table 7.2 unretracted | HIGH | Unchanged |

And the credit column, honestly: mahal executes end-to-end with deletion-sensitive production tests; the tier walk descends correctly at every host size we probed; LOO calibration is the right idea and lands in the safe direction; the staleness tripwire is excellent; the response latency was hours, not weeks.

---

## What would change the verdict

1. **Commit the round.** One commit containing the source fixes *and* the two tracked test files; re-capture the scoring-v3 post baseline at that SHA; re-point every "shipped at `6726c6a`" sentence. Until then, the honest status line for the README is: *implemented, uncommitted, attested by nothing.*
2. **Close the relocated brick**: extend the probe to check revision pinning (or add the SHA pins for `bge-code`/`unixcoder-base`/`codestral-mamba`), make the probe a required parameter, and fix the README tier table to describe the post-fix reality.
3. **Fix the stride hole** (raise the cap, guarantee changed-chunk inclusion, assert `windowed_embed_active` on a >32-chunk file with a planted edit), and replace the monkeypatched long-file test with one that would catch it.
4. **Document the four new flags** — especially `RC_NEURAL_CORROBORATED`, which converts the neural path into a hard blocker and currently exists only in code and a test file — and reconcile the LOO docstring ("~0.05") with its measured behavior (~0.00).
5. **Unfreeze session learning** (persist paths after promotion; re-fit on schedule) before the corpus mechanism is trusted anywhere.
6. **Fix the 2.20→2.60 arithmetic, retract Table 7.2, annotate the contaminated partial run, and resolve the PyPI collision** (rename the distribution or yank the instructions) — each is an hour of work that has survived three commits.
7. **Then, and only then, the security round**: authenticate the disarm verbs, guard `.envrc*` and the kill-switch state with case-folded matching, extend the git family, match the process title, hash-chain the audit log, and write the honest paragraph in HARDENING.md about the same-user ceiling.

---

## Verdict

**FIX-FIRST — and this time the finding is not the code.**

Round 2 changes our assessment of the maintainer, upward. The response to hostile review was fast, targeted, and in two cases (the production-path wiring tests; the staleness tripwire) better than what the review asked for. The round-1 blockers are no longer the story.

The round-2 story is that **the project's evidence machinery has diverged from its engineering reality**. The code improved in a working tree that no commit contains, tests that git does not track, and a baseline that attests a SHA where the work does not exist — cited by documentation frozen the moment before the fixes landed, describing a tier table the fixes invalidated, advertising an install command that installs someone else's package. Meanwhile the two blocker classes that round 1 flagged were not so much fixed as *metamorphosed*: the calibration that fired on nothing now fires on everything or nothing depending on which degenerate regime you hand it; the brick that hit big machines now hits small ones; the truncation blindness that was fixed on paper now persists behind a success flag. And all thirteen adversarial-control holes — the ones in the product's own threat model — are byte-for-byte untouched.

reasoning-core's thesis is that claims must be verifiable against what actually landed on disk. By that thesis, the current repository under-reviews itself: it cannot pass a gate it was built to be. Commit the work, re-point the attestations, close the relocated brick, and give the security surface its round — the skeleton has now earned a second audit cycle. It has not yet earned the word "shipped."

---

*Methodology: four independent hostile re-audits (ML fix verification, security re-sweep, claims/docs audit, eval-integrity sweep) plus direct verification of every load-bearing claim by execution on the working tree as of 2026-09-22 ~20:00 local: end-to-end `score_change` sessions, two Monte-Carlo campaigns (60 trials each) of in-sample vs. LOO calibration at d=768, degenerate-corpus probing, `decide()`/probe traces at 4/8/40 GB, `_resolve_revision_for_backend` fail-closed confirmation, a 41-command guard probe, APFS case-fold demo, `git show` attestation checks against both referenced SHAs, PyPI metadata fetch, and the full fast suite (~936 tests, 3 failures — two documented fail-as-designed, one staleness tripwire correctly firing on live edits). The working tree changed during the audit (new fixes landing; two stash-pop events observed by independent auditors); findings are pinned to the tree state at verification time.*
