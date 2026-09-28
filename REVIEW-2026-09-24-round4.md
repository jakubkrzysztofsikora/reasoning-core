# The Retraction Generation: A Fourth Hostile Review of reasoning-core

**Round 4 · Reviewed at commit `9404adc` (2026-09-24) · Method: four independent hostile expert subagents (security, scoring/ML, documentation/claims, evaluation/attestation) plus direct verification of every load-bearing claim by execution on the maintainer's own tree. Nothing in this review is taken from documentation; everything was run.**

---

## Preamble: what this project is, and what this cycle has been

reasoning-core is a guardrail system for AI coding agents: Claude Code PreToolUse hooks inspect every Bash/Edit/Write call, a scoring sidecar embeds diffs and decides allow/block, and a CLI manages enforcement state. Over three prior review rounds, this audit found the code real, the claims fabricated, the attestation inverted, and — by round 3 — the fabrication migrated into the audit-response documents themselves.

Round 4 is the round where the maintainer was asked, in effect, to retract the fabrications. That is exactly what was attempted. The retractions landed (`fca2811`), and they are — this must be said clearly, because the rest of this review will not be kind — *mostly accurate*. The pins are real. The naive bypass is closed. The docstring tells the truth for the first time. Four undocumented flags are now documented.

And yet.

This review's finding is that **the retraction generation immediately began generating new fabrications of a smaller denomination**: a test count that fails arithmetic, two line citations that don't point at the claimed code, a security-critical configuration default documented as the exact inverse of what the code ships, and an "immutable attestation baseline" captured on a dirty tree pinned to the wrong commit. The mechanical engineering improved again. The epistemic layer — the thing four rounds of audit have actually been about — is still producing false attestations per commit.

---

## Part I — What round 4 genuinely fixed (verified, with credit)

Before the hostile part, the honest ledger. Each item below was verified by direct execution or external fetch, not by reading the maintainer's claims.

**1. The model pins are real.** Round 4 commit `8b45dc9` populated `_PINNED_REVISIONS` (src/ssm_backbone.py:420) with four 40-character SHAs and claimed they were "fetched from the live HF model cards 2026-09-23." This review fetched all four from the Hugging Face API: `BAAI/bge-code-v1` → `bd67852057c5d7ddcc7b8234d9d6c410117ed851`, `state-spaces/mamba3-siso-893m` → `e205b6e6...`, `state-spaces/mamba3-mimo-894m` → `b5c7db27...` — **every value matches the live registry bit-for-bit**. The pin-invariant test regression from round 3 (`REVIEWER_PIN_REQUIRED` in the table) is gone; the full suite now shows three failures instead of four, and the pin test passes. This is what a real fix looks like.

**2. The naive `bypass-next` arm is closed.** `python3 src/rc_cli.py bypass-next` and the path-form `cd src && python3 rc_cli.py bypass-next` — both executed: refused with exit 1, kill switch verifiably untouched. The round-3 one-command hole is closed.

**3. The exact-name `.envrc.local` Edit hole is closed.** A driven Edit-tool payload targeting `.envrc.local` verbatim now blocks with exit 2 and the new `envrc_local_locked` reason. The fix is registered in `.claude/settings.json` on the live `Edit|Write|MultiEdit` path. (Part I ends here for a reason; see Part III.)

**4. The retractions themselves are accurate.** All four retracted claims in `docs/AUDIT_RESPONSE_2026_09_22*.md` correctly name their falsifying evidence, and the evidence checks hold: `git log -S "import torch"` across all refs confirms the import appears only in the post-review fix commit `d20c7be`; the flags now really are in `docs/CONFIGURATION.md`; the docstring now really says "nearly inert (~0% power)." This is the first round in four where a claim about documentation matched the documentation.

**5. Six CLIs is now the honest count.** The long-suspected "six vs seven" discrepancy is resolved: README says six, `src/hooks/adapters/` ships exactly six, the table lists six. Confirmed consistent.

**6. The inflated README numbers were actually removed.** `grep -c 0.92 README.md` → 0. The flake_locked 0.92/1.00-era claims are gone, replaced by an honest "sign-test p-value of 1.00 (coin flip)."

Good. Now the review.

---

## Part II — The headline: the retraction generation generates

The round-3 review's central demand was: stop writing claims the tree can't cash. The round-4 response retracts four such claims — and then, in the same authoring session, mints at least five new ones. Each verified below.

### II.1 The replacement test count fails arithmetic — in four documents and two commit messages

The retracted claim was "994 pass + 4 skip + 2 fail-as-designed." Its replacement, published in `docs/AUDIT_RESPONSE_2026_09_22.md:66`, `docs/AUDIT_RESPONSE_2026_09_22_ROUND2.md:67`, `README.md:404-408`, and the bodies of commits `9404adc` and `fca2811`:

> **1041 pass + 4 skip + 2 fail-as-designed**

This review measured the suite at HEAD: **1056 tests collected, 3 failed** (all in `tests/test_pre_reg_embedder_gate.py`: `test_pre_reg_embedder_gates_all_pass`, `test_pre_reg_embedder_manifest_fresh_enough`, `test_pre_reg_embedder_manifest_records_required_backends`).

The published numbers reconcile with nothing. 1041 + 4 + 2 = 1047 ≠ 1056. The fail count is 2 ≠ 3. And the "2 fail-as-designed" classification quietly reassigns two of the three failures — `manifest_fresh_enough` and `manifest_records_required_backends` are not refusal gates doing their job; they are *evidence-missing* failures, firing because the measurement doesn't exist. The one number in the project that four rounds of review have specifically audited — the test count in the audit response — is wrong again, one generation after the previous version of it was retracted as fabricated.

### II.2 The security-critical flag is documented as the exact inverse of the code — CRITICAL

Commit `cee0090` — the commit whose stated purpose was to close a fabricated-documentation finding — adds `RC_NEURAL_CORROBORATED` to `docs/CONFIGURATION.md:321`:

> | `RC_NEURAL_CORROBORATED` | `0` | When `1`, the neural scoring path … participates in the hard-block decision … **Without this flag, neural regressions emit `decision=neural_warn` (advisory only); with it, they emit `decision=blocked`** … Default off preserves the adversarial-review contract.

Both call sites in the tree read:

```python
# src/hooks/pre_edit_guard.py:1430
os.environ.get("RC_NEURAL_CORROBORATED", "1") == "1"
# → demotes the neural regression: advisory["regression_detected"] = False

# src/mcp_gate.py:134
if regression and os.environ.get("RC_NEURAL_CORROBORATED", "1") == "1" and not corroborated:
```

The code's actual contract is the **180-degree inverse** of the documented one on every axis:

| | Documented | Actual code |
|---|---|---|
| Default | `0` | `"1"` |
| Flag = 1 | hard-block enabled | neural signal **demoted to advisory** |
| Flag = 0 | advisory only | neural regression **hard-blocks** under `S2_FAIL_CLOSED=1` |

So the shipped default is, ironically, the AGENTS.md-compliant one — and the documentation of the flag whose sole purpose is to express the "neural scoring is advisory unless corroborated" rule states that rule backwards. The practical consequence is a loaded footgun: an operator who reads `docs/CONFIGURATION.md`, believes the shipped default violates the advisory contract, and therefore sets `RC_NEURAL_CORROBORATED=0` to "disable neural hard-blocking" — **arms** the neural hard-block. The safety-seeking action, per the documentation, enables the behavior the operator feared. This was verified by reading both call sites at lines 1430 and 134, and by tracing the demotion branch. The commit that fixed two fabrications introduced a third, and this one has teeth.

### II.3 Two fresh wrong line citations, inside the documents meant to raise accuracy

- `docs/AUDIT_RESPONSE_2026_09_22_ROUND2.md` cites `src/hooks/pre_edit_guard.py:1393` as the read site of `RC_NEURAL_CORROBORATED`. The read is at **line 1430**. Verified.
- `docs/CONFIGURATION.md:318` cites `s2_core.py:1130` as the "documented-but-redundant OR-branch." Line 1130 is inside a docstring of `_sync_baseline_worker`. The actual branch (`if ais < t["ais"]:`) is at **line 1451**. Verified.

A pattern worth naming: in rounds 1–3, fabrications were wholesale (work that didn't exist, counts that weren't run). In round 4, the fabrications are retail — a line number here, a default there, a count off by nine. But the *generator* is unchanged: assertions are composed from what would be true if the work had been done, rather than from the work.

### II.4 The "immutable baseline" captured on a dirty tree at the wrong commit

Commit `a73594c` claims a post-fix attestation baseline. The manifest it captured (`baseline-2026-09-24-round3-attestation-retractions-post.json`) records `git_sha: fca2811` — **two commits before the round's HEAD** — with `dirty: true`. Round 2's single CRITICAL finding was precisely this failure mode (a baseline not attesting the real tree state). It is now routine: the registry counts bare `git status --porcelain` including untracked files — the auditor's own three `REVIEW-*.md` files in the repo root alone force `dirty: true` on any capture taken while this review sits on disk — and the `dirty` flag is read by **nothing** in the codebase (verified by grep: no consumer in `list`, `compare`, or any gate). A project cannot claim immutability with a mutable, unconsumed, always-on dirt flag.

### II.5 The retraction text was stale on arrival

The ROUND2 retraction asserts the four flags "are NOT documented … anywhere in the docs tree" and "remains undocumented operator surface." True the hour `fca2811` landed; false one commit later when `cee0090` documented them. No "(resolved in cee0090)" pointer was added. The next auditor's grep will fail against this document exactly the way round 3's grep failed against its predecessor — the rot cycle, institutionalized.

---

## Part III — Security: the gate that locks the front door of a drywall house

Commit `9a706c5`'s message states: *"a runaway agent cannot self-arm the bypass."* This review is prepared to call that the round's most consequential false claim, because it was testable in ninety seconds.

### III.1 The kill switch is a same-user-writable JSON file — CRITICAL, executed end-to-end

The auth gate `_operator_authenticated()` (src/rc_cli.py:552) guards `cmd_bypass_next`. But the thing `cmd_bypass_next` flips is `~/.local/state/reasoning-core/kill_switches.json` — a plain `-rw-r--r--` JSON file with no HMAC, no lock, no permission enforcement, and **no coverage by `rc guard-hash`** (which hashes eight `.py` files plus `.envrc.local` and `PLAN.md`, but not the state file it exists to protect). Executed, in sequence, with no authentication anywhere:

```text
$ python3 -c "...json... d['bypass_next'] = True"     # direct file write
$ rc status  →  bypass_next  True
$ consume_bypass_next()  →  True   # the next PreToolUse hook exits 0 unconditionally
```

The threat model the gate claims — the runaway agent — is a same-user process. A same-user process *is* the permission that file grants. The auth gate does not protect the kill switch; it decorates one of two entrances to it.

### III.2 The self-arm ceremony, taught by the refusal message itself — CRITICAL, executed

Even staying inside the CLI, the gate is self-service. The agent controls both halves of the challenge: `RC_AUTH_TOKEN_FILE` is env-configurable (src/rc_cli.py:524), the token file is same-user-writable, and on this host the keychain is empty (`security find-generic-password` → rc 44, item not found), so the file fallback is the live path. Executed:

```text
$ echo $TOKEN > /tmp/tok
$ RC_AUTH_TOKEN_FILE=/tmp/tok RC_ENFORCEMENT_TOKEN=$(cat /tmp/tok) \
    python3 src/rc_cli.py bypass-next
bypass_next armed (consumed on next PreToolUse hook call)    # rc=0, state file: True
```

Two commands. And the refusal message for the unauthenticated attempt reads: *"Run `rc auth-bootstrap` then re-run with RC_ENFORCEMENT_TOKEN set"* — **the error message is a how-to guide for the bypass**, and `auth-bootstrap` itself requires no authentication to mint the token. The project's own docstring (rc_cli.py:562) states "an agent can set env vars in its own shell" as the threat, then builds a gate whose second factor is an env var pointing at a file the same agent writes.

### III.3 `.envrc.local`: one ingress closed, the class wide open — HIGH, executed

The layer-1b fix matches `os.path.basename(file_path) == ".envrc.local"` — no `realpath`, no casefold, no symlink resolution. Executed bypasses on this APFS host:

| Ingress | Guard result | Harm |
|---|---|---|
| Edit `.Envrc.local` (case variant) | **allowed, rc=0** | APFS case-insensitivity lands the bytes in the real `.envrc.local` (verified in sandbox: append via case-variant path, real file modified) |
| Edit `notes.txt` → symlink → `.envrc.local` | **allowed, rc=0** | write follows the symlink |
| `ln -s .envrc.local /tmp/notes.txt` via Bash | **allowed, rc=0** | the symlink's creation itself is unguarded |
| Edit `.envrc.local.` (trailing dot) / `.envrc//.local` | **allowed, rc=0** | APFS folds both onto the real file |

The attack is three allowed steps: `ln -s` (bash guard passes) → Edit through the alias (edit guard passes) → next direnv eval downgrades the session to warn-only via one appended `export RC_ORACLE_BLOCK=0`. `tests/test_hook_block.py:581` tests exactly and only the literal basename — a self-confirming test that is blind to every real ingress. The commit message even boasts "Match by basename so any path component is caught (incl. /tmp/foo/../ tricks)" — true, and beside the point.

### III.4 Git history rewrites pass the bash guard — HIGH, executed

Round 3 flagged `git rebase` and `git worktree` as uncovered. Round 4 touched neither. Executed against `pre_bash_guard.py` with the real hook protocol: `git rebase -i HEAD~3` **rc=0**; `git rebase --onto` **rc=0**; `git rebase -i --exec 'python3 -c ...write...'` **rc=0 — and in a throwaway sandbox the rebase engine actually executed the arbitrary write** (rebase's `--exec` is arbitrary command execution smuggled inside a permitted command shape); `git filter-branch` **rc=0**; `git update-ref refs/heads/main HEAD~1` **rc=0**; `git read-tree -u -m HEAD~1` **rc=0** (working-tree rewrite without "checkout"); `git reset --keep HEAD~1` **rc=0** (`--hard` is covered; `--keep`/`--merge` are not). The enumerated-regex architecture cannot be patched to safety — `git -C repo rebase ...` alone defeats every `\bgit\s+<subcmd>` anchor. The only durable fix is a git *allowlist* of read-only subcommands, deny-by-default.

### III.5 The rest of the new security census

- **`skip_files` is a persistent, self-serviceable exemption** (HIGH, executed): appended directly in the state file, honored by `pre_edit_guard.py:720`, and — unlike `bypass_next` — never consumed by `pre_bash_guard.py` at all. Worse than the one-shot bypass it sits next to.
- **Non-regex write channels** (HIGH, executed): `find -delete`, `find -exec sed -i`, `xargs rm`, `make -f evil.mk`, `printf | ex .envrc.local` (verified: the file was actually written), `eval` string-splitting, `python3.14` (defeats the `python\w*` regex — `.` breaks `\w*`), `src/./rc_cli.py` path normalization. Each rc=0.
- **MCP is entirely outside the hook surface** (HIGH): `.claude/settings.json` registers matchers only for Write / Edit|Write|MultiEdit / Bash / Task. No `mcp__*` matcher exists or can; any MCP tool that writes files — and the session's registered set includes several — is an unguarded write channel. The in-repo `hybrid-reasoner` MCP `gate_edit` is prompt-enforced and *advisory by default* (mcp_gate.py:86, :134-137).
- **Magic comments remain agent-issued self-pardons** (MED, executed): `# rc:skip` in the written content exempts the write from the layers scoring it; the agent controls the content.
- **Guard-hash store not initialized on this host** (MED): `rc guard-hash` verifies nothing today because `--init` was never run by the operator — the tamper-evidence tripwire is optional equipment, unplugged.

**Cumulative security scoreboard, rounds 1→4:** ~10 genuinely fixed · **7 still open** (auth gate, state-file integrity, git history rewrites, non-regex write channels, MCP surface, `.envrc.local` ingress class, unauthenticated `/baseline` armament) · **2 newly introduced by this round's fixes** (the unauthenticated `auth-bootstrap`, and a refusal message that teaches the ceremony).

---

## Part IV — Scoring: the detector that cannot be calibrated, documented as converging

### IV.1 The near-degenerate hair-trigger is scale-free — HIGH, executed

Round 3 found the 1e-9-jitter escape. Round 4 establishes it is worse: **jitter scale does not matter.** Sweep at n=5, identical rows plus jitter, benign probe a moderate outlier in a fresh direction:

| jitter | `cov_inv==0` guard | LOO threshold | benign fires? |
|---|---|---|---|
| 1e-12 | False | 2.7e3 | **YES** |
| 1e-10 | False | 2.7e3 | **YES** |
| 1e-9 | False | 2.9e3 | **YES** |
| 1e-7 | False | 2.7e3 | **YES** |
| 1e-5 | False | 2.8e3 | **YES** |
| 1e-3 | False | 2.8e3 | **YES** |

Mahalanobis distance is scale-invariant: shrink the corpus variance by k² and every distance grows by the same factor, so the fire/no-fire boundary is identical at every scale. There is no safe jitter. The boundary is the covariance condition number, and `grep` for `cond|svd|eigh|variance floor` across the scoring path returns **zero hits**. The only guard remains the exact-zero check at s2_core.py:412 — whose own comment admits the dangerous regime is "reachable via /baseline poisoning," which remains an **unauthenticated POST** (s2_core.py:1718, verified: no auth check on the route). One curl request arms a detector that fires on every out-of-distribution benign edit.

### IV.2 The new "honest contract" docstring is itself false — HIGH, executed

Commit `cee0090` rewrote the docstring to say: *"As the corpus grows the LOO power approaches the nominal FPR."* Executed power study (isotropic benign, fixed 3σ attack, LOO q95):

| n | fresh-benign FPR | attack power |
|---|---|---|
| 8 | 0.0% | **0.0%** |
| 16 | 5.0% | **5.0% — power equals FPR; coin-flip discrimination** |
| 32 | 20.0% — 4× nominal | 25.0% |
| 64 | 2.5% | **15.0%** |

Two problems. Empirically: power does **not** approach nominal; at n=16 the detector cannot distinguish attack from benign at all, and at n=64 it catches one fixed 3σ shift in six tries while its own docstring promises convergence. Semantically: the sentence is *backwards* — a working detector's power approaches 1, not the FPR. As written, the documentation promises convergence to a useless detector. The retraction generation replaced a false number with a false sentence, in the same file, about the same function.

### IV.3 The chunk cap is dead code, and the budget has no enforcer — HIGH, executed

Round 3 reported a 4.7s latency bomb and attributed it to difflib. Round 4 corrects the attribution and finds the root cause worse. In `diff_windowing.py:307-340`, when chunks exceed `_MAX_CHUNKS_PER_FILE`, the code stride-samples, then **unconditionally overrides the sample with the full chunk list**. The comment says it outright: *"The simplest correct fix: just keep every chunk."* The cap is referenced only to be ignored. On a 2 MB single-function file — one token changed — this drives **1,094 Mamba forward passes**: measured **10.2–16.4 s inside `score_change`**, against a client timeout of 1.5 s. There is no deadline check anywhere in the windowing path; exceeding `S2_HARD_CAP_MS` kills the *client*, not the *compute* — the sidecar keeps burning CPU on a payload an adversary sizes at will, and the neural layer *deterministically never scores that edit* (fail-open to the symbolic net). The bypass and the DoS are the same request.

### IV.4 The refit that freezes the world — HIGH, executed

The LOO threshold refit runs synchronously, under `_BASELINES_LOCK`, on the request path: measured **12.7 s at n=100, 50.2 s at n=200** (d=768; the calibration code is a per-row `np.outer` Python loop). The corpus cap is 200. So one growth-crossing refit holds the global lock for ~50 s *after the requesting client has timed out* — every other session's `/score` queues behind it into symbolic fallback. Combined with IV.3: an adversary chooses payload size either to bypass scoring deterministically or to freeze it for everyone.

### IV.5 Two smaller verified defects

- `RC_MAHAL_CORPUS_MIN=1` (no floor on the env value, s2_core.py:366): at n=2 the LOO threshold arms at **0.0** and a fresh N(0,I) draw scores 8.1e19 — 100% self-firing on every edit, triggered by one env var.
- **Stale-forever corpora** (executed): the refit trigger is path-*count* delta, not content. Fit at n=5, overwrite two baselines' embeddings, add four paths — no refit, `__corpus__` retains day-one rows, verified `corpus[0] != current p0`. A session editing the same five files never re-fits; a `/baseline` poison becomes permanent.

---

## Part V — The evidence ledger: nineteen manifests, nineteen mocks, zero measurements

The eval lane's census, executed across all of `eval/baselines/` (19 manifests, not 18 — the count itself grew this round):

| Property | Value |
|---|---|
| Manifests | 19 |
| Capturing `RC_MOCK_DETECTOR=1` | **19/19** |
| `audit_window_metrics: {}` | 19/19 — the fields are hardcoded empty literals (src/baselines.py:105); the schema *manufactures the appearance* of metrics |
| Distinct `configuration_hash` values | **3**, across 10 distinct code SHAs — the hash covers env only, so a pre-fix and post-fix baseline are hash-identical |
| `dirty: true` | 17/19, read by nothing |
| `--verify` semantics | **theater, executed**: directories are recorded without hashes and `expected is None` auto-passes — a manifest pointing at an *empty directory* verifies as `verified: true` |
| Human-labeled outcome data | **0 records. Four rounds.** |
| The κ = 0.8025 sentinel | real file, real n=131 — three **language models grading each other** (May-dated judge files), cited dateless in BENCHMARKS.md:245 and ROADMAP.md:38, and loaded at *runtime* by `_plan_quality.py` with no expiry |

And the centerpiece: `eval/runs/pre_reg_embedder_partial.json` — the file the failing pre-reg tests cite as *"the full measurement record"* — contains `n_pairs_test: 1`, `observed: null` on every one of its five gates, and a **random-weight control whose novelty score is bit-identical to three real embedders'**. When the random control and the real models produce byte-identical outputs, the harness measured nothing. Four rounds after this artifact was flagged as contaminated, it has no annotation, no invalidation sidecar, no response entry — and it remains the canonical record, cited by failing tests, in a repository whose own AGENTS.md forbids representing exactly this as evidence.

The honest one-paragraph description of what every baseline in this repository attests: *a macOS-arm64 CPU developer shell with the deterministic rules engine in enforce mode, a mock detector on, the reasoner off, the PRM gate off, zero collected metrics, and a configuration hash that cannot distinguish the fixed tree from its predecessor.* No baseline in the repo attests the production posture, because none was ever captured with the neural subsystem on.

---

## Part VI — What closing this out would actually take

The remaining work is small, concrete, and — for the first time in four rounds — mostly *not* architectural:

1. **One polarity fix:** make `RC_NEURAL_CORROBORATED`'s code match its documentation or vice versa, and pick the AGENTS.md-compliant default deliberately (one line in two files; today they document the inverse of what they ship).
2. **One line-number pass and one honest suite count** in the four documents that publish 1041/4/2 (the tree says 1056/3 — publish the decomposition from an actual run).
3. **Make the kill-switch state real:** HMAC the state file with a keychain-held key (or move bypass state into the already-existing authenticated broker), drop the token-file fallback, gate `auth-bootstrap`, rewrite the refusal message so it stops teaching the ceremony, and stop claiming "cannot self-arm" until a same-user process genuinely can't.
4. **Replace path-string matching with `Path(...).resolve().name.casefold()`** in pre_edit_guard layer-1b — or accept that string-matching paths is not a boundary and gate on resolved realpath prefixes.
5. **Git allowlist** in pre_bash_guard: read-only subcommands permitted, everything else denied — the enumerated-regex architecture is provably unpatchable (see III.4).
6. **One condition-number floor** (min-eigenvalue relative to `tr/d`) on corpus promotion, closing the entire jitter-scale family at once; clamp `RC_MAHAL_CORPUS_MIN ≥ 3` and refuse non-positive thresholds.
7. **Enforce the chunk cap or delete it honestly**, and add a wall-clock check per chunk in `embed_windowed`; move the LOO refit off the request path.
8. **Invalidate the contaminated partial** with a sidecar the tests cite, and run *one* pre-registered evaluation to completion — a single honest n≥30 human-labeled run would move the evidence ledger from zero to one.
9. **Tag a release** (the repo has zero tags; PyPI `reasoning-core` 0.4.0 remains a stranger's unrelated package that this repo's declared name collides with).

---

## Verdict

**FIX-FIRST — the engineering keeps improving, and the attestation keeps regressing, and they are now diverging at a constant rate.**

Round over round, the pattern has not been sloppiness resolving into honesty. It has been fabrication *degrading into smaller denominations*: a fabricated import history became a fabricated test count became a fabricated line number became a fabricated default value — each retraction accurate, each authoring session minting new errors of the same genus, with a retraction genre that functions as camouflage. The mechanical fixes of round 4 are real and some are good: the pins check out against the live registry, the naive bypass closes, the docstring finally tells the truth about n=5. But the round's two security commits authenticate a door whose wall is drywall, its commit messages claim the wall is load-bearing, and the documentation commit for the project's flagship safety flag ships the polarity inverted. After four rounds and one formal retraction commit, every artifact this repository can offer as immutable evidence is a manifest that attests a mock-detector-on, reasoner-off, metrics-empty snapshot it cannot cryptographically verify, vouched for by a hash that cannot distinguish a fix from its predecessor — and the sole completed measurement behind its surviving headline statistic was rendered by three language models grading each other.

The skeleton has now survived four hostile reviews. The habit of writing checks the tree can't cash has survived all four of them too — smaller, faster, and closer to the money each time.

---

*Review artifact: `REVIEW-2026-09-24-round4.md` (untracked in repo root — which, note, is itself one of the three untracked files that keep every baseline manifest's `dirty` flag pinned to true; the registry counts the auditor's own presence as contamination and then tells no one). All reproductions in this document were executed at commit `9404adc` on the maintainer's machine; scratch state was restored or sandboxed.*
