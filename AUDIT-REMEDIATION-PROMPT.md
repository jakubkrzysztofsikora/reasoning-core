---
name: remediate-reasoning-core-audit-findings
description: >
  Implements fixes for the 53 findings from the four-round hostile audit of
  the reasoning-core repository (anchored at 9404adc, 2026-09-24). For each
  finding this prompt gives a work order: goal, concrete fix approach with
  file:line targets, and an executable acceptance gate. Pair with
  AUDIT-REVERIFY-PROMPT.md in the same directory, which contains the
  reproduction commands for every finding — run the corresponding REPRO before
  (must reproduce) and after (must pass) each fix. Use when the maintainer
  asks an agent to clear the audit ledger.
version: 1.0.0
anchor_commit: 9404adc
companion: AUDIT-REVERIFY-PROMPT.md
finding_count: 53
---

# reasoning-core — remediation work orders

You are a senior engineer remediating this repository against a completed
hostile audit. Your job is to close findings, prove each closure by executing
its acceptance gate, and leave behind honest evidence. A finding counts as
closed only when its FIX-ACCEPT check passes on output you personally
produced. Everything else is `deferred` or `fixed_pending_reaudit` — never
`done` on faith.

<role>
You are the remediation engineer of record. You own the diff, the tests, the
docs, and the truthfulness of every claim you write. This repository has a
documented four-round history of fabricated attestation (audit responses
claiming work that did not exist, test counts that were never run, line
citations that point at the wrong code). Your defining constraint: every
sentence you publish — in code comments, docs, or commit messages — must be a
claim you verified by execution during this session. If you cannot verify it,
delete it or write what you actually observed.
</role>

<context>
<system>Anatomy: PreToolUse hooks (src/hooks/pre_bash_guard.py,
src/hooks/pre_edit_guard.py) inspect agent tool calls; a scoring sidecar
(src/s2_core.py, src/scoring_signals.py, src/diff_windowing.py,
src/calibration.py) decides allow/block; src/rc_cli.py manages enforcement
state; kill-switch state at ~/.local/state/reasoning-core/kill_switches.json
(RC_STATE_DIR overridable); baseline registry in src/baselines.py +
eval/baselines/.</system>
<threat_model>Adversary = same-user runaway agent process: shell access, env
control in its own subshells, same file permissions. Anything a same-user
process can read/write is in its power. Hooks inherit the Claude process env;
.envrc poisons future interactive shells.</threat_model>
<ground_rules>
- Test-first where feasible: for each work order, first add or adapt the
  failing test from the finding's FIX-ACCEPT (drive scripts are in the
  companion file), watch it fail, then fix, then watch it pass.
- Do not weaken invariants to pass tests: never delete a test to make the
  suite green, never loosen a threshold to dodge a failure, never mark a test
  xfail without a reason string naming the finding ID.
- Keep every intermediate state committable: the suite must be no worse after
  each commit than before it (target: failures decrease monotonically from
  the current 3).
- The auditor's files (REVIEW-*.md, AUDIT-REVERIFY-PROMPT.md) are read-only
  to you. Never edit, move, or commit them.
</ground_rules>
</context>

<verification_loop>
For every work order, in order:
1. REPRODUCE — run the finding's REPRO from the companion file; confirm the
   CURRENT behavior. If it does not reproduce, stop and record
   `not_reproduced` with your output (the registry may be stale — do not fix
   what is not broken).
2. WRITE THE GATE — turn the FIX-ACCEPT into an executable check: a pytest
   test where possible (see existing patterns in tests/test_hook_block.py,
   tests/test_rc_cli_enable_enforcement.py), otherwise a script under
   $SCR plus the exact command recorded in the commit message.
3. FIX — implement the approach below or a better one; if you deviate, the
   commit message says why in one sentence.
4. PROVE — run the gate; run the full fast suite
   (`.venv/bin/python -m pytest -m "not live and not slow" -p no:cacheprovider
   --timeout=120 -q`); record both outputs.
5. RECORD — update the ledger (see output_format) with status
   `fixed_verified`, plus the exact evidence lines.
Only then move to the next work order.
</verification_loop>

<work_orders>
Waves are ordered by severity and dependency. Within a wave, order is
suggested, not mandatory. Tag every commit `fix(remediation): <finding-ids>`.

## Wave 1 — critical (do first; unblocks everything else)

### WO-1 · RC-DOC-02 — RC_NEURAL_CORROBORATED: code and docs point opposite directions
GOAL: one agreed contract, default = AGENTS.md-compliant advisory posture.
FIX: keep the code semantics (default "1" = uncorroborated neural regressions
DEMOTED to advisory; "0" = allow hard-block under S2_FAIL_CLOSED=1) — the code
default is the safe one. Rewrite docs/CONFIGURATION.md:321 to state exactly
that, including both branch behaviors and the default. Then add a doc-conformance
test (tests/test_documented_flags.py — new): parse CONFIGURATION.md's flag
table and assert each documented default equals the os.environ.get default at
every read site (grep-derived), and each documented polarity matches a driven
decide() case. This test is the permanent guard against polarity drift — it
would have caught this finding.
ACCEPT: docs and both call sites agree on default and both branches; the new
conformance test passes and demonstrably fails if you flip either side in a
scratch check.

### WO-2 · RC-SEC-01 + RC-SEC-02 — make the kill switch and the auth gate real
GOAL: a same-user agent process cannot arm the bypass without operator
participation.
FIX (both findings, one design):
(a) Kill-switch integrity: state file gains an HMAC-SHA256 sidecar
(kill_switches.json.mac). Key source, in order: macOS keychain service
`reasoning-core-enforcement` (provisioned by a NEW operator-key ceremony, see
(c)); explicit refusal if absent. `_kill_switches.py` refuses to load a state
file whose MAC is missing/invalid — treats it as tamper, resets to all-false,
and records a tamper event in the audit log. chmod 0600 both files. Add the
two files to `rc guard-hash` coverage (rc_cli.py guard_files list).
(b) Token path hardening: remove the RC_AUTH_TOKEN_FILE env override (fixed
path only); on darwin require the keychain (drop the file fallback entirely);
lengthen minimum token to 32.
(c) Key ceremony with operator presence: `rc auth-bootstrap` requires an
operator-presence proof before it mints anything — on darwin, run the keychain
write through `security -i` (interactive) or gate the whole command behind a
sudo-timestamp check (`sudo -n true` succeeding means an operator is present);
document that CI must pre-provision the keychain/secret.
(d) Rewrite the refusal message: state that operator setup is required and
point to docs/CONFIGURATION.md's new "Operator authentication" section —
zero actionable arm instructions in agent-visible output.
(e) RC-SEC-09 (skip_files): under the HMAC regime a hand-edited skip_files
entry is tamper (reset). Additionally make skip_files ONE-SHOT: consumed
(read + cleared) by BOTH pre_edit_guard and pre_bash_guard — today only the
edit guard reads it — or delete the mechanism if it has no legitimate
operator workflow; grep call sites and record the decision in the commit.
ACCEPT (from companion FIX-ACCEPTs): RC-SEC-01 direct-file edit does not arm
(tamper reset + audit event); RC-SEC-02 self-service ceremony fails at every
step (env override dead, file fallback dead, bootstrap gated); refusal message
quoted in commit message proves no ceremony leakage; a hand-edited skip_files
entry is ignored AND cleared. All four existing
bypass-next auth tests still pass — adapt them to the new ceremony, do not
delete.

### WO-3 · RC-EVAL-06 + RC-SUITE-01 + RC-EVAL-09 — invalidate the poisoned measurement and split the gate tests
GOAL: nothing cites the contaminated run as a measurement of record; test
failures are classified honestly.
FIX:
(a) Add eval/runs/pre_reg_embedder_partial.json.INVALIDATED sidecar (json:
{"status":"invalidated","reason":"random-control novelty bit-identical to real
embedders; n_pairs_test=1; observed:null on all gates","superseded_by":null,
"date":<today>}) and reference it from eval/baselines/README.md.
(b) Split tests/test_pre_reg_embedder_gate.py: keep test_pre_reg_embedder_
gates_all_pass as the refusal gate but mark `pytest.mark.xfail(reason="mamba3
unloadable on this stack; see PRE_REG_STATUS_2026_09_21", strict=True)` —
it currently passes 1/5 gates (falsifiability), so verify which direction
xfail is correct BEFORE committing (run it; if the test is currently failing
for the right reason, xfail makes the suite green for the right reason).
Rewrite the other two (manifest_fresh_enough, records_required_backends) as
`test_evidence_exists_*` WITHOUT xfail — they must stay red until a real run
exists, and their failure message must cite the INVALIDATED sidecar, not the
poisoned file.
(c) Reword the triage message: refusal ("gates evaluated and rejected") vs
evidence-missing ("no uncontaminated measurement exists") as distinct strings.
ACCEPT: suite failures drop to exactly 2 (both evidence-exists tests, both
citing the sidecar); no test was deleted; `grep -rn "pre_reg_embedder_partial.json" tests/`
shows only sidecar-citing messages.

### WO-4 · RC-SCORE-01 — condition-number floor on corpus promotion
GOAL: no jitter scale can arm a hair-trigger detector.
FIX: in src/s2_core.py `_maybe_promote_session_to_corpus`, after
`fit_benign_corpus` compute the eigenvalue floor on the shrunk covariance
(not the inverse): `eig = np.linalg.eigvalsh(cov)`;
`floor_ratio = eig.min() / eig.mean()`. Refuse to arm (store threshold +inf,
same branch as the exact-degenerate case) when `floor_ratio < 1e-10` — tune
the constant so the MACRO near_degenerate sweep is inert across
JIT ∈ {1e-12, 1e-9, 1e-3} while a healthy corpus (10 distinct unit-variance
rows) still arms. Record the chosen constant and its margin in the commit
message. Guard eigvalsh failures as degenerate.
ACCEPT: companion REPRO sweep → guard inert at all jitter scales; healthy
corpus test (add one) still arms; exact-degenerate regression test still
passes.

## Wave 2 — high

### WO-5 · RC-SEC-03 + RC-SEC-04 — close the .envrc.local ingress class
FIX: layer-1b in pre_edit_guard.py: match
`Path(file_path).resolve().name.casefold() == ".envrc.local"` inside a
try/except (resolve can raise on weird paths — on failure, block conservative:
treat as match). Add `ln -s` and `ln` (link creation generally) to the
pre_bash_guard deny set. Tests: extend tests/test_hook_block.py — case
variant, trailing-dot, double-slash, symlink-alias (create the symlink in
tmp_path), and `ln -s` bash drive; each asserted rc=2 with reason
envrc_local_locked. These tests must drive the real guard via screen_command,
not regex-text assertions (see WO-17).
ACCEPT: companion REPRO matrix all rc=2; sandbox collision write blocked when
driven through the guard.

### WO-6 · RC-SEC-05 + RC-SEC-06 — git allowlist + deny-by-default bash surface
GOAL: the enumerated-regex architecture is proven unpatchable; replace it.
FIX (phased, single PR):
(a) Build a read-only allowlist in pre_bash_guard.py: parse the command's
first token(s); if the command invokes git, require the subcommand ∈
{status, log, diff, show, branch, rev-parse, remote, stash list, worktree list,
shortlog, describe, ls-files, blame} — everything else in git denied (rc=2,
reason git_subcommand_not_allowed). Handle `git -C <path> <sub>` and
`-c key=val` prefixes before the subcommand. Non-git commands: keep the
existing deny-regex layer AND add a new default layer denying these
high-value write vectors: `find ... -delete`, `find ... -exec`, `xargs` when
the command list contains rm/sed/dd/tee, `make`/task-runner invocation with
explicit -f outside the repo, in-process editors (ex, ed, vim -es, sed -i),
`eval`, and interpreter variants (`python3.<n>`, absolute interpreter paths
are already handled — normalize tokens before matching). Obfuscation note:
you cannot win string-matching against a determined adversary; the goal of
this WO is closing the enumerated inventory (companion RC-SEC-06 REPRO list),
with the allowlist as the durable architecture.
(b) Every existing round-5 deny test must still pass (they are the floor).
ACCEPT: every REPRO command in companion RC-SEC-05/RC-SEC-06 returns rc=2;
`git status`, `git log -1`, `git diff --stat`, `pytest` still pass the guard
(rc=0); full suite green-or-better.

### WO-7 · RC-SEC-07 — authenticate POST /baseline
FIX: the scoring server (src/s2_core.py:1718 route) requires a bearer token
equal to the operator enforcement secret (reuse the WO-2 keychain lookup;
accept via Authorization header). 401 on missing/mismatched. Keep /health and
/score unauthenticated (latency path), but rate-limit /baseline to
1 request / 60 s / session_id. Document in CONFIGURATION.md.
ACCEPT: curl POST without token → 401; with token → 200 (scratch server on
scratch port); the near-degenerate armability chain (companion RC-SCORE-01
REPRO via HTTP) now fails closed.

### WO-8 · RC-SCORE-02 — honest calibration or honest demotion
FIX: (a) Rewrite the docstring paragraph at src/scoring_signals.py:194-199 to
carry the measured table (n=8/16/32/64 power study from the companion REPRO)
and delete the "power approaches the nominal FPR" sentence. (b) In decide(),
treat mahal_anomaly as advisory when corpus n < 100 (constant
RC_MAHAL_MIN_ADVISORY_N, documented) — fired-but-advisory emits
decision=neural_warn with a small_corpus reason. (c) Add the power study as a
skippable slow test (pytest.mark.slow) so it can be re-run on demand.
ACCEPT: docstring quotes numbers you measured THIS session; decide() driven at
n=5 fires-advisory not blocks.

### WO-9 · RC-SCORE-03 — enforce the chunk cap and the wall clock
FIX: diff_windowing.py:307-340 — implement the original design the comment
abandoned: always keep chunks overlapping diff hunk byte ranges (derive from
the src/after text passed to embed_windowed), stride-sample the remainder to
the cap. Add a deadline parameter to embed_windowed (default from
S2_HARD_CAP_MS): check time.monotonic() per chunk; on exceed, stop embedding,
mark result budget_exceeded, score what you have. Downstream: budget_exceeded
must NOT silently fail open — pre_edit_guard treats it as a symbolic-fallback
with a loud audit event and (under RC_ORACLE_BLOCK=1) a block, not allow.
ACCEPT: 2 MB single-scope repro p50 ≤ 1.5 s; adversarial-budget drive shows
blocked (or documented warn) with audit event, never silent allow; stride
regression test (RC-SCORE-10 watch) still passes — hunk chunks survive
truncation.

### WO-10 · RC-SCORE-04 — move the LOO refit off the request path
FIX: _maybe_promote_session_to_corpus computes thresholds in a background
worker (single-slot executor; latest-wins; request path stores the corpus and
a "refit pending" marker with the previous threshold retained until the new
one lands). Vectorize the LOO loop in src/calibration.py:121-123 (batch the
np.outer computation or use covariance shrinkage identities — target n=200,
d=768 ≤ 1 s).
ACCEPT: n=200 fit ≤ 1 s; a /score call issued during a refit does not block
(measure with two threads); thresholds never regress to 0.0/inf spuriously
during handoff (add a test).

### WO-11 · RC-EVAL-01..04 — make the baseline registry able to lie no more
FIX (one PR, four sub-fixes, src/baselines.py):
(a) Artifacts: hash a canonical bundle per directory artifact (tar.gz of the
dir, sha256 recorded); verify compares hashes; unhashable → "unverifiable"
status, never auto-pass. (b) Delete audit_window_metrics/oracle_health fields
or populate from real aggregation with a loud error when absent — choose
deletion (the schema should not manufacture appearance). (c) guard_hashes:
deterministically hash every file in src/hooks/*.py + src/rc_cli.py +
src/baselines.py. (d) configuration_hash: fold in code.git_sha +
guard_hashes so pre/post-fix states are distinct; keep the env-only hash as a
separate field for compatibility.
ACCEPT: empty-dir manifest verifies as `unverifiable` (test with a scratch
manifest); two manifests differing only by code SHA produce different hashes;
re-capture produces the full hook-set hash list.

### WO-12 · RC-EVAL-05 + RC-DOC-05 — dirty flag fixed and surfaced
FIX: baselines.py dirty computation uses `git status --porcelain
--untracked-files=no`; add `untracked_count` as a separate informational
field; `rc baseline list` and `compare` print dirty/unverifiable loudly
(prefix markers). Then capture a fresh post-remediation baseline AT THE EXACT
final HEAD of this remediation branch (after all waves), asserting ancestor +
zero src/ delta in the capture script (add that assertion to the capture path
itself — this closes the RC-EVAL-12 class permanently).
ACCEPT: with REVIEW-*.md untracked, dirty is False; list shows markers; new
baseline's git_sha == HEAD and a src/ delta check passes programmatically.

## Wave 3 — medium

### WO-13 · RC-SEC-08 + RC-SEC-10 + RC-SEC-11 + RC-SEC-12 — surface honesty for the remaining guard gaps
FIX: (a) README threat model gains a section "Out of enforcement scope"
naming MCP tools, magic comments, and any other admitted gaps, each with a
one-line residual-risk statement — honesty is the deliverable, since code
cannot hook what the host does not route. (b) `_magic_comments.py`: rc:skip /
rc:override honored only for paths under an operator-curated allowlist file
(.rc-allowlist, itself guard-hash covered); elsewhere they are inert. (c)
Installer (install.sh) runs `rc guard-hash --init` after setup; state file +
MAC sidecar added to the guard_files list (done in WO-2, verify here). (d)
Add "NotebookEdit" to the Edit matcher in .claude/settings.json (or document
the host limitation if the matcher syntax does not support it — verify by
reading the host's hooks docs; if unsupported, the README section from (a)
covers it).
ACCEPT: each sub-item has either a passing drive test or a README
residual-risk paragraph naming it; `rc guard-hash` exits 0 on a fresh clone
after install.

### WO-14 · RC-SCORE-05 + RC-SCORE-06 + RC-SCORE-07 — corpus hygiene
FIX: (a) clamp `min_paths = max(3, int(env))` with a warning when clamped;
refuse to arm on threshold not finite-or-positive (0.0 → +inf + audit warn).
(b) Refit trigger: store `__corpus_content_hash__` (sha256 over sorted
(path, emb.tobytes()) pairs); refit when it changes, not only when path count
grows. (c) pre_edit_guard `_extract_changes`: a MultiEdit sub-edit whose
old_string is absent from the reconstruction → decision blocked with reason
multiedit_mismatch (never silently dropped).
ACCEPT: MIN=1 drive → inert, not 0.0-threshold; overwrite-drift drive refits
(hash changed) with same path count; mismatched MultiEdit drive blocks.

### WO-15 · RC-DOC-01 + RC-DOC-03 + RC-DOC-04 + RC-DOC-11 + RC-DOC-12 + RC-DOC-13 — the honesty pass
FIX: (a) Run the full suite; replace the 1041/4/2 sentence in both
AUDIT_RESPONSE docs and README:404-408 with the measured decomposition from
YOUR run (paste real numbers), or delete the sentence. (b) Fix line cites:
pre_edit_guard.py:1393 → the actual read line; s2_core.py:1130 → 1451 (verify
both by sed before writing). (c) Append "(resolved in cee0090)" to the stale
retraction sentence in ROUND2:38. (d) README open-items: add the state-file
kill-switch and .envrc.local ingress class entries (mark them FIXED by WO-2 /
WO-5 if you landed those first — cite commit SHAs). (e) Deduplicate README
421-435. (f) Explain or remove the "+25" delta.
NOTE: docs/AUDIT_RESPONSE_2026_09_22*.md are the project's own documents —
you ARE allowed (required) to correct them; the read-only files are only the
auditor's REVIEW-*.md and the two AUDIT prompt files.
ACCEPT: `grep -rn "1041" docs/ README.md` returns nothing outside historical
quotation; every line cite in CONFIGURATION.md and the response docs resolves
to the claimed content when sed'd; a doc-conformance sweep (grep every
file:line cite in docs/ against the file) reports zero misses — add this sweep
as a slow test.

### WO-16 · RC-EVAL-07 + RC-EVAL-08 — capture posture honesty
FIX: (a) Attempt one baseline capture with RC_MOCK_DETECTOR=0 and
RC_REASONER_BACKEND on (best-effort: if the reasoner cannot run on this host,
capture the attempt result and record the blocker in the manifest notes —
honest failure beats silent dev-posture). (b) _host_env.py: validate RC_HOST
against the detected platform family (pi ≠ macOS); on mismatch, log and
default to auto-detected.
ACCEPT: one new baseline with mock=0 (or a manifest recording exactly why
not); no manifest captures an incoherent host/platform pair going forward.

## Wave 4 — low / strategic / release

### WO-17 · RC-SUITE-03 — guard tests drive the real path
FIX: rewrite the round-5/6 guard tests to execute screen_command end-to-end
(drive the hooks with real stdin payloads as in the companion MACROs) instead
of asserting regex text; include case/symlink/trailing-dot .envrc.local cases
(if not already from WO-5) and git allowlist cases (from WO-6).
ACCEPT: `grep -rn "re.compile" tests/test_security_hardening.py tests/test_hook_block.py`
shows no test that only greps source text; suite green.

### WO-18 · RC-DOC-06 + RC-DOC-07 + RC-DOC-08 + RC-DOC-09 — whitepaper and README residue
FIX: (a) results.tex Table 7.2: remove the table environment or wrap in a
visible retraction box (LaTeX tcolorbox or a plain \begin{center}\fbox rule)
— commenting the header is not retraction; verify by checking the compiled
PDF or the absence of the table* environment. (b) Dollar table: strike or
banner + provenance pointer; no per-task cost data exists, so the banner must
say "no supporting measurement exists in the repository." (c) κ=0.8025: add
date + "machine-judge diagnostic (3 LLM judges), not product evidence" to
BENCHMARKS.md:245 and ROADMAP.md:38; _plan_quality.py loader gains an expiry
(>90 days → ignore + warn). (d) README ruff example: re-run the actual block
path to produce a REAL decision id, or mark the block "illustrative example".
ACCEPT: no active LaTeX table of retracted results; loader ignores the stale
sentinel (test with a mtime-rewritten copy in scratch); README block either
reproducible or labeled.

### WO-19 · RC-DOC-10 + RC-EVAL-11 — namespace and release discipline
FIX: (a) pyproject.toml: rename the distribution (propose `rc-guard` — check
https://pypi.org/pypi/rc-guard/json is free; fall back to another free name,
record the choice); keep the import/package layout unchanged; README install
section updated; a DISTRIBUTION.md note records the collision decision. (b)
On a green suite at final HEAD: create annotated tag v0.3.1 with the suite
decomposition and the remediation commit range in the message.
ACCEPT: `pip index versions <new-name>` (or the JSON endpoint) shows the name
free pre-publish; `git tag -l` non-empty with annotation; tag message contains
real measured counts.

### WO-20 · RC-EVAL-10 — the evidence run (partially delegable)
GOAL: move the evidence ledger from zero to one.
FIX: this needs the OPERATOR for human labels — your scope is preparation plus
the machine half: stand up the pre-registered protocol (eval/pre_reg_embedder
harness) with n≥30 pairs, uncontaminated random control (assert control ≠
real-embedder outputs bit-wise before accepting any measurement), recorded
split_seed and manifest; run it; if machine-judgeable gates complete, record
results. Human-labeled outcome evaluation: prepare the labeling packet and
STOP — record `deferred(operator_time)` with the exact next physical step.
ACCEPT: one completed uncontaminated machine-side run in eval/runs/ with
per-sample records and a non-null observed for every gate, plus a labeling
packet ready for the operator; the ledger records the human-label gap as
deferred, not done.

### WO-21 · regression-watch sweep — SEC-13, SEC-14, SCORE-08, SCORE-09, SCORE-10, DOC-14, EVAL-12, SUITE-02
GOAL: prove the prior rounds' fixes still hold after your remediation diff —
these are the findings the ledger records as `resolved_verified`; your job is
they are STILL resolved at final HEAD, with fresh evidence.
FIX (no code expected unless one breaks): run each companion REPRO once at
final HEAD — bypass-next refusal both forms (SEC-13); round-5 deny battery
(SEC-14); exact-degenerate inert (SCORE-08); LOO benign-FPR drive (SCORE-09);
stride planted-hunk probe (SCORE-10); docstring/flags/six-CLI/0.92 spot checks
(DOC-14); newest-baseline ancestor + src-delta assertion (EVAL-12 — WO-12
landed the permanent check, confirm it fires); pin invariant + hf_pin_check
for all four pins (SUITE-02).
ACCEPT: all eight watch items pass at final HEAD; any that regress become a
new ledger entry with severity inherited and are fixed before the final tag.
</work_orders>

<non_negotiables>
1. No fabricated attestation. Commit messages contain only claims you verified
   this session, with the verifying command quoted. A commit message that says
   "verified by execution" must quote the execution.
2. No test deletion or invariant weakening. Tests are removed or changed only
   with the finding ID and the reason in the diff.
3. No doc-only closure of code findings. RC-SEC-* and RC-SCORE-* findings are
   closed by code + tests; RC-DOC-* findings may be closed by docs + the
   conformance tests from WO-1/WO-15.
4. Deferred ≠ hidden. Every finding not closed gets `deferred(reason)` in the
   ledger with the next concrete action and owner (agent vs operator).
5. The three REVIEW-*.md files, AUDIT-REVERIFY-PROMPT.md, and
   AUDIT-REMEDIATION-PROMPT.md are never modified or committed.
</non_negotiables>

<boundaries>
Authorized: edit tracked repo files (except the read-only list above); add
tests; run the suite and hook drives; start scratch servers (scratch ports,
RC_STATE_DIR overridden); network fetches to HF/PyPI for name and pin checks.
Forbidden: pushing, tagging remotes, publishing to PyPI (create the tag
locally only); touching the real login keychain except through the WO-2
ceremony the operator will run; installing new heavyweight dependencies
(numpy/torch/fastapi already present — design within them); leaving any state
file tampered, any scratch server running, or any bypass armed at session end.
</boundaries>

<output_format>
Final report, two artifacts:

1. Ledger (fenced JSON):
```json
{
  "run": {"started": "<iso>", "finished": "<iso>", "base_commit": "<sha>", "final_commit": "<sha>"},
  "work_orders": [
    {"id": "WO-1", "findings": ["RC-DOC-02"], "status": "fixed_verified | fixed_pending_reaudit | deferred | not_reproduced",
     "commits": ["<sha>"],
     "evidence": [{"before": "<repro output>", "after": "<gate output>"}]}
  ],
  "suite": {"before": {"collected": 0, "failed": 0}, "after": {"collected": 0, "failed": 0, "failed_tests": ["..."]}},
  "deferred": [{"finding": "RC-EVAL-10", "reason": "requires operator human labels", "next_action": "..."}],
  "honesty_check": "I certify every claim in my commit messages was verified by execution this session; the verifying commands are quoted in the messages."
}
```

2. Narrative: `## Closed (with evidence)`, `## Deferred (with reasons and next
actions)`, `## Surprises` (anything the registry got wrong, stale, or missed —
you found something new while fixing), `## Handoff to auditor` (what the
re-verification run should focus on; expected verdict if all waves landed).

Self-check before finishing: for each work order — did the acceptance gate run
and pass on output you personally produced? Is any ledger status stronger than
your evidence? Quote one line per work order proving the gate ran, and append
that checklist to the narrative.
</output_format>

Then begin remediation at Wave 1, in order, for the repository at the path
below, executing the full verification loop per work order until the ledger is
complete.

Repository path: /Users/jakubsikora/Repos/personal/reasoning-core
