# The Gate That Audits Itself: A Hostile Review of `reasoning-core`

**Round 6, independent.** Reviewed at commit `2146e93` (branch `audit-hostile/2026-09-19-reaudit-fixes`), macOS, Python 3.14.6 venv. Method: four independent hostile reviewers (claims-vs-code audit, application-security, evaluation methodology, code quality) working in isolation, plus a verification pass in which every load-bearing finding quoted below was re-checked against the tree by the lead author — including executing the guard functions against ~200 attack strings, recomputing the statistics from the repo's own tables, and running the project's own documented self-verify test command. Two dramatic findings produced during the review **failed verification and were killed**: an "arithmetic error" in the "surviving-5" benchmark table (the reviewer had misread a table row; the document's corrected number reproduces exactly), and an initial reading of the judge-independence gate as "passing below its own threshold" (the gate is a *ceiling* on inter-judge correlation — `gate_pass = max κ < 0.7` — and its verdict is semantically correct). Neither appears below as a finding. That is the evidentiary standard this review holds the project to, and holds itself to.

---

## TL;DR

`reasoning-core` is a local "pre-edit gate" that hooks AI coding CLIs, scores every proposed Edit/Write against deterministic oracles (`py_compile`, `ruff`, `ast.parse`, project rules) and an 8-dimensional neural risk vector, and — in opt-in enforcement mode — can block the edit before it lands. It is also the most self-flagellating repository this reviewer has ever audited: the README contains six rounds of hostile audit responses, three retractions of its own benchmark claims, and a frank list of the ways its own security model can be bypassed.

The verdict is genuinely uncomfortable to write, because the two halves of the project point in opposite directions.

**The artifact discipline is real and rare.** Benchmarks get retracted in place, with the retracting commit pinned. Contaminated measurement artifacts get renamed `.INVALIDATED` rather than quietly deleted. There are 949 test functions with almost no mock-theater. Model checkpoints are SHA-pinned and mutable refs are rejected. Source code is never written to the audit log — only hashes. Most projects that get audited six times respond with a blog post; this one responds with a baseline manifest.

**The prose discipline is not.** The README's quick start — the first command a new user runs — installs an **unrelated Inria-affiliated package that has owned the `reasoning-core` name on PyPI since 2025**, a fact the repo's own audit documents verified and then left standing in the README anyway. The README advertises `rc upgrade --ref v0.3.0` as example pin syntax, but no tags exist (so the example ref cannot resolve). It claims a git-history self-calibration loop that is print-only in the code. It claims a Decision ID on "every" block when exactly one of the four pre-guards emits one. It claims "no ongoing network calls" while documenting an opt-in cloud relay for non-Apple-silicon hosts. Its own FAQ says the neural scoring path takes p95 ~5 s per edit on CPU; its own client timeout is 1500 ms — meaning that, by the project's own numbers, the entire p95 tail of the machine-learning path on CPU blows the cap and falls back to the deterministic oracles with a stderr notice (the documents never reconcile the two figures).

And the security story — the part that would matter most if anyone used enforcement mode — is a regex tripwire that our battery showed blocks ~90% of the redirect-shaped attacks and approximately none of the exotic ones, with the single most valuable target on the board (`.envrc.local`, the file that can silently downgrade the entire gate for the next session) writable by a plain, flagless `cp` — because `.envrc.local` is in neither the guard's source-extension list nor its guarded-fragment list, so no copy-tool pattern can ever match it.

Scorecard, by domain:

| Domain | Grade | One-line verdict |
|---|---|---|
| Install & distribution | **F** | The documented quick start installs someone else's package; no release has ever existed. |
| Security enforcement surface | **C−** | Honest threat model, energetic regex work, structurally unclosable gaps — several undocumented. |
| Docs truthfulness | **C** | The audit history is honest; the product prose systematically overclaims. |
| Evaluation methodology | **B−** | Retraction and provenance discipline far above norm; the operating loop is circular and no headline number is reproducible from a clone. |
| Code quality | **C+** | Serious tests, real data hygiene; monoliths, a dead-metric pipeline with a latent `NameError`, and a deleted cost cap. |
| Test suite | **C+** | 949 mostly behavioral tests — that fail in full-suite order and pass in isolation, including the README's own pre-push gate. |

What follows is the evidence.

---

## 1. The front door does not open

This is the finding that should end any sales conversation until it's fixed, so it goes first.

The README's Quick Start is:

```bash
pip install reasoning-core[full]
```

The name `reasoning-core` on PyPI is owned by user `sileod` (version 0.4.0, live at time of review — verified by direct fetch of the PyPI JSON index). It is an Inria-affiliated project: *"reasoning-core is a suite of textual procedural data generators for language model pre-training and post-training."* It has no `full` extra, no `rc` entry point, and approximately forty dependencies, from `sympy` to `z3-solver`.

So the documented two-command quick start does not fail loudly. It **succeeds while installing the wrong product**, then `rc init` — the second command — fails with command-not-found for a user who has no idea why.

The remarkable part: the project knows. `docs/AUDIT_RESPONSE_2026_09_22_ROUND2.md` records the namespace collision as "Verified true by direct fetch." The README's own "status of open items" section lists it, *in the same document*, twelve sections above the quick start that still leads with the broken command. `docs/INSTALL.md` repeats the command as "Recommended (v0.2+)," and `docs/MIGRATION_v1.md` repeats it unqualified.

Behind the collision, the distribution story collapses entirely:

- `git tag -l` → **empty**. Zero tags in the repository's history. The README advertises `rc upgrade --ref v0.3.0 # pin a specific tag/branch` — a ref that cannot exist.
- `pyproject.toml` says version `0.3.0` — numerically *below* the squatter's `0.4.0`, so even a future upload would look like a downgrade to pip.
- The `rc upgrade` docstring's own words, per the audit: *"no PyPI release exists yet for this repo as of v0.2.0."*
- `docs/MIGRATION_v1.md` describes v0.2 wheels "signed … published via Trusted Publisher / SLSA" — describing a release pipeline that, for lack of a tag, has never fired.

A project that is this rigorous about baseline immutability has no excuse for a README whose first instruction installs an unrelated Inria-affiliated package.

**Fix:** until the name is resolved (rename or `reasoning-core-gate`), put a warning banner on the quick start and lead with the `git+https` flow the CLI already supports. Tag a release or stop advertising refs. It is a weekend of work.

---

## 2. The security model: a regex tripwire, and what regexes cannot do

`reasoning-core`'s enforcement story rests on four hook layers: a Bash-command regex guard (`pre_bash_guard.py`), an edit-path lock (`pre_edit_guard.py`), a sidecar-revive hook, and a subagent-prompt regex screen. `docs/HARDENING.md` is admirably frank that these are screens, not sandboxes, and the guard's header comment documents three residual gaps.

We executed roughly 200 adversarial probes against the actual guard functions (importing `screen_command`/`screen_prompt` from the repo venv and feeding them strings — no files harmed). Summary of the observed allow/block verdicts:

| Attack family | Blocked | Character |
|---|---|---|
| Redirect-family writes (`>`, `>>`, `tee`, `sed -i` relative, `python -c open(w)`, `pathlib.write_text`, base64-to-interpreter) | ~90% | Genuinely good — quoting, `$IFS`, `./`, `//`, traversal, ANSI and CR tricks mostly caught |
| Copy-tool family (`cp`, `mv`, `install`, `rsync`, `dd`, `ln`) | ~27% | The copy pattern only matches destinations with a *source extension* or a *guarded fragment* — anything else (a bare directory dest, a flag between verb and destination, or an unlisted file like `.envrc.local`) passes; for the flagship `.envrc.local` target even a plain flagless `cp` is allowed |
| Sidecar kills (`pkill` variants, by-port, by-pidfile, by-proc-scan, `launchctl bootout`) | ~40% | Literal tokens blocked; metacharacter fragmentation and port/pidfile kills all allowed |
| Git subcommand allowlist | good **only as first token** | `true; git config core.hooksPath /tmp/evil` — allowed; the allowlist is gated on `startswith("git")` (`pre_bash_guard.py:561`) |
| Subagent-prompt mutation screen | 1/17 | Eight canonical verbs block; *rewrite, overwrite, truncate, wipe, erase, empty, corrupt, replace, chmod 000* all pass |

### The chain that matters

The single most valuable target is not a source file — it's **`.envrc.local`**, the gitignored file sourced into the shell that launches the coding agent. Write `export RC_MODE=advise` and `export RC_ALLOW_GUARD_EDIT=1` there, and every layer of the gate is down for the next session, with nothing looking different to the operator.

The round-5 remediation added Edit-tool denial for `.envrc.local` (verified real). But on the shell path:

- `.envrc.local` is **absent from `GUARDED_PATH_FRAGMENTS`** (`pre_bash_guard.py:44-54` — verified by reading the tuple), so the symlink resolver can never catch it either; and
- the copy-tool family is not denied against it: `cp /tmp/payload ~/.<repo>/.envrc.local`, `dd if=/tmp/payload of=…`, `install -m 600 …`, `rsync …`, `mv …`, and `sed -i '' 's/copilot/advise/' …` on the absolute path **all return allowed** from the real guard function.

The edit-path lock is solid; the shell path next to it has a hole the width of coreutils.

### Findings the docs don't list

The guard documents 3–5 known limits. Our probes found roughly a dozen gap classes not on any list, including:

- **The revive hook executes `$CLAUDE_PROJECT_DIR/scripts/start-sidecar.sh`** (`post_bash_revive.py:56-65`) with no repo-root validation — the supervisor has a `_resolve_repo_root` check; the revive path doesn't. Hooks are installed into arbitrary target repos; a hostile repo can plant that script and get it executed detached, as the user, on the next sidecar death. This is the most consequential gap in the security surface because it fires automatically (not opt-in), targets arbitrary repos, and contradicts the stricter check in the same codebase.
- **`dd of=` overwrites any guard file in one command** and then defeats the tamper-evident layer, because the guard-hash store (`~/.local/state/reasoning-core/guard_hashes.json`) is itself unguarded and un-HMAC'd — rewrite the hashes, and `rc guard-hash` reports all green.
- **`__pycache__` planting** (shipped `-m` invocation form only): write a crafted `.pyc` whose embedded source-mtime/size match the `.py`, and CPython executes attacker bytecode for imported siblings (`audit_log`, `_guard_paths`, etc.) while the file-hash verification (which hashes only `.py`) stays green. Direct-script dev invocations are not affected.
- **`/score` is unauthenticated** and auto-persists per-path baselines under the *caller-supplied* `session_id` (`s2_core.py:1494`). The round-6 fix authenticated `/baseline` — the poisoning primitive survived by walking next door. Any local process can rewrite a victim session's drift corpus; there is also no body-size cap. Real but low-impact in the single-user local-tool threat model.
- **Port-kill the sidecar** (`fuser -k 8765/tcp`, `lsof -ti :8765 | xargs kill`) and the L3 revive — which matches kill-*shaped commands*, not sidecar-*state* — never fires. Gate's dead; nothing notices; advisory mode doesn't care.

### What no regex can fix — and the project should say so

To be clear about the deep point: several of these are not bugs awaiting a patch but **category errors** in the approach.

1. The write-op vocabulary is an **open set enumerated on the attacker's side**: `dd`, `jq`, `ed`, perl's 3-arg `open`, ruby, `sqlite3 .output`, `curl -o`, `pip install --target`, `make` codegen. Every one is a new regex, forever.
2. A string regex does not parse shell. Variable indirection (`V=src/s2_core.py; echo pwn > $V`), computed command strings, aliases, mid-command `IFS` redefinition — the guard sees characters; the kernel sees different argv.
3. Same-UID process operations need no token: kill-by-port, kill-by-pidfile, `launchctl bootout`. Process identity is not a string.
4. **TOCTOU is structural**: the hook exits *before* the agent CLI writes. A `realpath` check at hook time says nothing about the inode at write time; hardlinks are invisible to `realpath` by construction.
5. Indirect execution (git hooks, `npm run`, package managers) is documented as a known limit — correctly so — but git *subcommands* in compound commands are not even scoped correctly today (above).

The honest framing — which `HARDENING.md` circles but doesn't land — is: **this is a tripwire that raises the cost of accidental and lazy bypasses, not a boundary against a competent in-process adversary, who shares your UID and doesn't need your regex's permission.** The docs should say exactly that, in that many words, and close the `.envrc.local`/`dd`/`ln`/git-scoping/port-kill holes anyway, because those are cheap.

For fairness, the things that hold up: the S2 sidecar binds `127.0.0.1` hardcoded (`s2_core.py:1842-1874`); non-loopback `S2_URL` is refused unless explicitly overridden; the MCP client has an https-only host allowlist with redirect refusal; `RC_ALLOW_GUARD_EDIT` genuinely can't be set by the agent mid-session (it's read from the host process env the agent can't touch); keychain-backed operator auth on `bypass-next` and `enable-enforcement` is real. The *layer-2 edit lock* — the part that guards the guards — is the best-engineered piece of the security surface.

One more, because it's the mirror image: the gen-sidecar launcher builds `llama_cpp.server --model … --port …` with **no `--host`** (`gen_sidecar_launcher.py:100,116`) on a local backend it supports (`RC_REASONER_BACKEND=llama`) but does not gate or warn on. llama-cpp's documented default binds `0.0.0.0`. That is an unauthenticated, OpenAI-compatible endpoint on every NIC of the machine for operators who choose that backend — in a project whose tagline is "loopback-only, refuses external NIC." The S2 sidecar gets a hardcoded loopback bind; the LLM sidecar gets a footnote.

---

## 3. The prose promises things the code doesn't do

The audit trail is honest; the product prose isn't. Receipts, all verified against the tree:

| Claim (README) | Reality (code) |
|---|---|
| "`rc audit-history` … **recalibrates thresholds** where you actually make mistakes" (README:142-145) | `cmd_audit_history` (`rc_cli.py:1310-1342`) mines and **prints a table**. Nothing consumes `_commit_miner`'s labels: the in-tree recalibration pipeline (`_supervisor_recalibrate.py`) runs on a different miner (`eval/calibration_corpus.py`) and only on a CUSUM signal. The docstring itself says "feedback loop input **for Phase-4**"; the Phase-4 machinery exists but never ingests this command's output. As written — this command recalibrating thresholds — the claim is false. |
| "`exit-2` blocks **always** end with `Decision ID:`" (README:138-140) | Exactly one of the four pre-guards emits it. `pre_bash_guard._exit` (`:359-363`), `pre_plan_guard`, and `pre_task_guard` write messages and `sys.exit`. A Bash-layer block — Layer 1, the layer the threat model says matters most — shows no ID. Bonus fragility: the footer prints the *session log's last event*, which under concurrent hooks can be a different event than the block being emitted. |
| "no telemetry, no cloud relay, no ongoing network calls" (README:18) | True for the default score path (loopback hardcoded, SSRF guards real, no analytics SDK anywhere). Imprecise as an absolute: `docs/INSTALL.md` documents an opt-in cloud relay for non-Apple-silicon hosts (`RC_REASONER_BACKEND=remote`), and the embedder performs HF hub metadata checks at cold boot with `HF_HUB_OFFLINE` forwarded but never set. The sentence is marketing-shorthand for "no background phone-home," which is true; the exceptions are documented and opt-in. |
| "The shipped `.envrc` sets `RC_PROJECT_INDEX=1` by default" (README:133-134) | The template `rc init` actually renders has it **commented out** under "Opt-in features (default off)" (`direnv.envrc:41`). The line is true only on the author's machine, whose own `.envrc:238` sets it. |
| "Every `RC_*` / `S2_*` env var" documented (CONFIGURATION.md:3) | Dozens of behavior-relevant env vars are undocumented. Concrete examples: `RC_STRUCTURAL_BLOCK`, which defaults on and **hard-blocks** import cycles and duplicate definitions in non-advise modes — a blocking surface missing from the canonical table. Also documented-vs-code default drift (`S2_HEALTH_GRACE_S` 30 in docs, 60 in code). |
| Tier table: "fallback < 2 GiB → `mamba-130m`" (README:469) | Code: fallback window is 0–4 GiB; the MIMO rows in code are absent from the README table (`embedder_tier.py:182-197`). Doc-drift, not functional bug — the loadability probe overrides the nominal table at runtime. |
| Latency: FAQ "p95 ~5 s per Edit on CPU with `mamba-130m`" (USAGE.md:363, BENCHMARKS.md:198) | `S2_HARD_CAP_MS` defaults to **1500** (`pre_edit_guard.py:89`). A p95 of 5 s means at minimum the entire slowest 5% of CPU edits exceed the cap and fall back to symbolic oracles with a stderr notice (`pre_edit_guard.py:274-275`); the median is unknown from a p95. Neither document acknowledges the other's number. |

None of these are subtle. All of them were fixable in the same commits that meticulously fixed regexes. The pattern — forensic honesty about the past, marketing syntax about the present — recurs often enough that it's clearly the house style.

---

## 4. The evaluation: honestly retracted, circularly alive

This is the part research-literate readers will care about most, and it's genuinely two-sided.

**The retraction machinery is excellent.** The 8-cell benchmark table at the heart of the whitepaper was found to contain a byte-identical duplicated row (T5/T7) and an inverted decision rule (P0); it is now boxed `RETRACTED 2026-09-19` in three documents simultaneously, with original numbers preserved and the retracting commit pinned. We recomputed every surviving aggregate from the repo's own tables: costs, tokens, and the sign-test p-value of **1.00** (a literal coin flip, exactly as the README now says) all reproduce to the cent, including the round-5-corrected plan-quality mean of 2.60 (−23.5%), which we verified against the per-task rows (1.0+3.0+5.0+3.0+1.0)/5. Our own reviewer initially misread that table and "found" an arithmetic error in the correction; re-verification killed the finding. The corrected number is right. Baselines are captured by a command that refuses to overwrite existing IDs; run manifests freeze configuration hashes *before* execution; the contaminated pre-registration artifact is preserved as `.INVALIDATED` with the reason committed. This is better provenance hygiene than most published research.

**But nothing load-bearing has ever successfully run.** A census of every standing quantitative claim:

- The headline "Iter-3" table (48 cells, 3 judges — flake 0.92→1.00, plan +0.32, tokens −8.2%) has **no committed backing artifact**; per-task JSONL is described as gitignored (it isn't — only `*.partial.jsonl` is), and it sits in the whitepaper under the heading "(Latest)" directly beneath retraction captions. Even its wall-clock row contradicts the retracted table by ~8 minutes, which the docs acknowledge and then leave standing.
- The pre-registered embedder evaluation — real frozen gates, real refusal-gate test, genuinely creditable *machinery* — has zero valid data: its only run manifest (n_pairs=1, all gates `observed: null`) was invalidated for control contamination.
- The judge-independence gate artifact (`eval/runs/judge_independence_pilot_20260506.json`) records `max` pairwise κ = 0.6998 against `gate_threshold: 0.7` with `gate_pass: true`. The initial reading here — a gate "passing below its own threshold" — was **wrong and is retracted**: the generator (`relabel_grounding_pairs_v3.py:354`) defines the gate as a *ceiling* on inter-judge correlation (`gate_pass = max κ < 0.7`), which is the correct semantics for independence. The residual, fair critique: the margin is 0.0002 on n=100 with no CI, and the artifact is unreadable without its generator — a reader of the repo clone cannot tell pass from fail. Thin evidence, not bad faith.
- The eval CI badge: on pull requests, `eval.yml` sets `RC_LIVE=0`, and `run_suite.py`'s dry-run mode *"only prints the schedule and exits 0"* — the badge job runs **nothing**. Push runs n=2, a stub by construction whose report is all-zero deltas, verdict "inconclusive."
- The coherence threshold `0.09 "95th-pct"` traces to 197 unlabeled events from the tool's own audit log, chosen at p95 *because*, per the research memo, a 1% gate "cannot meaningfully shape agent behaviour" — i.e., tuned to its own fire rate, not to any detection objective.
- The Mahalanobis anomaly signal: executed by our reviewer, not just read. At the shipped default corpus size (n=5, d=768), the leave-one-out threshold yields a realized benign exceedance ~10× *below* nominal (near-inert), while the exact-zero degenerate guard misses near-degenerate corpora (1e-9 jitter → hair-trigger scores of ~1e15 on a 0.001 shift). The honest docstring admission ("nearly inert at small corpus sizes") is to the project's credit; shipping `RC_MAHAL_CORPUS_MIN=5` anyway is not.

**And the operating loop is circular.** Map it: the gate writes decisions to its own audit log; `rc label` shows the labeler the tool's own decision *before* eliciting the label; the labeler is, by the project's own protocol document, the tool's author; labels feed the training set that recalibrates thresholds that fire into the audit log. The north-star "reasoning-efficiency" metric counts the tool's own blocks as "drift caught," adjusts using a **hardcoded 0.43 constant** imported from the very (old-schema, partially retracted) iter-3 study, and reads an override-survival file that — see §5 — no code has ever written. The `audit-history` "ground truth" heuristic labels any commit negative if a *later* commit by **any author**, within 48 h, touching the same files, *mentions* fix/revert/hotfix/patch — in a repo whose actual commit history consists of `fix(remediation): …` chains from coding agents. Commit-message style is being harvested as ground truth. The blind two-labeler protocol that would break the circularity exists in `EVAL_PROTOCOL.md` — as design fiction. It has never run.

The repo's own `AGENTS.md` forbids representing "synthetic scenarios, historical telemetry, or a small weekly run as broad causal evidence." By that standard, every quality claim currently standing in the whitepaper is a hypothesis, and the documents mostly admit it — in hedges — while the headline tables do the advertising.

---

## 5. Engineering hygiene: real tests, dead metrics, deleted caps

**What's genuinely good.** 949 test functions against ~12k source lines, and the sampling is *behavioral*: hooks tested as real subprocesses against in-process stub HTTP sidecars; scoring math tested with real degenerate inputs and determinism assertions; six `unittest.mock` uses in the entire tree against 868 `monkeypatch` uses. Data hygiene in the audit path is thoughtful: source text is never logged (hash + byte counts), env dumps are hashed, redactor failure drops the record rather than leaking. Supply-chain discipline (SHA-pinned checkpoints, mutable-ref rejection, checkpoint allowlist, `trust_remote_code=False`, negative caching on load failure) is enforced, not decorative. The offline story is honest: no backbone → 503 `backbone_unavailable`, not a hang.

**What's broken or brittle, verified:**

- **The dead-metric pipeline.** `audit_log.record_override()` (`audit_log.py:545`) uses `_os`, which is not imported at module scope (only locally, in a different function, at `:639`). Executed: `NameError`. It has **zero call sites** — so it's dead code today — but it is the *sole* writer of `override_links.json`, which `rc reasoning-efficiency` and the override-survival metric consume. A metric in the README's command list is computed from a file that no code path has ever written, and the one function that could write it crashes if called. This is the whole project in one function: rigorous periphery, hollow center.
- **The suite fails as documented.** Running the README's own pre-push gate (`pytest -m "not live and not slow"`) at the pinned commit: the pre-reg freshness gate `test_pre_reg_embedder_manifest_fresh_enough` FAILED under full-suite ordering — the mechanism is an mtime race (a fresh checkout runs `eval/` before `src/`, making `ssm_backbone.py` appear newer than the measurement manifest) — plus three `test_hook_block` tests and one `test_calibration_integration` test **fail in full-suite runs and pass in isolation**. (In the hours between pinning `2146e93` and publication, in-tree remediation commits added an xfail/skip for the freshness test; the order-dependence of the hook-block tests remains.) Order-dependent failures in a repo that advertises its tests as the load-bearing quality signal is its own kind of finding — and the hook-block timeouts under load are consistent with the timing story in §3.
- **Concurrency.** The Phase C corpus path reads and writes `_BASELINES` without `_BASELINES_LOCK` (every sibling accessor takes it), from `/score` handlers running in `asyncio.to_thread` — a session-promotion rewrite racing a reader. And the Ledoit-Wolf fit + LOO (n fits!) + a module import run **inside the global lock**, serializing every session's scoring behind one session's refit.
- **The windowing cost cap was deleted, not fixed.** `diff_windowing.py:299-343` computes a stride-and-sample, then discards it and returns *every* chunk (a dead self-import included). The round-2 blocker (malicious lines silently dropped from embedding) was real; the "fix" removes the cost bound entirely — for operators who opt into `RC_DIFF_WINDOWING=1` (default off), a 50-function file now costs 100 mamba forwards per edit, guaranteeing the 1500 ms cap trip from §3. Correctness restored, operability discarded.
- **Config list drift.** The guarded-path allowlist exists in five copies (three tuples + two regex embeddings), already drifted: the Task guard's list knows about files the Bash guard's list doesn't. One SSRF fix must be applied in four near-verbatim transport duplicates. `s2_core.py` is a 1,897-line monolith spanning dataclasses, session state, four-language call-graph front-ends, tensor math, and the HTTP service; `rc_cli.py` crams ~40 subcommands into 2,061 lines.
- **Packaging.** Shipping top-level packages literally named `src` and `eval` to PyPI is an ambient hazard: any other distribution doing the same collides at install time, uninstall-order-dependent. `from src.s2_core import score_change` as the documented public API (`README:557`) is the tell that this was a repo first and a package never.

---

## 6. The meta-question: is the honesty real?

Six audit rounds in, the repo now contains retractions of **fabricated attestation claims** — the repo's own round-6 audit response explicitly retracts four claims from earlier audit-response documents that were false when checked against the tree, and the retraction notes are actually there (verified). There is a committed history of: "we said we fixed it, we hadn't, here is the git pickaxe that proves we hadn't, and here is the fix."

Read cynically, that's a disqualifying rap sheet. Read carefully, it's the opposite of the usual failure mode: the fabrications were caught *by the project's own audit process*, retracted *in place*, with the evidence of the fabrication *preserved*. The system that caught the lies is the same system that produced them, which is either a paradox or the actual definition of an adversarial process working.

But the pattern across all six rounds is consistent and it's the real verdict of this review: **the project's integrity lives in its artifacts and dies in its prose.** Baselines: immutable. README: claims a calibration loop that prints to stdout. Retractions: forensic. Quick start: installs the wrong product. Threat model: unusually candid. Guard hash store: un-HMAC'd. Tests: 949, behavioral, honest. Failing in suite order at the pinned commit.

The result is a tool that is simultaneously the most and least trustworthy thing in its category. Most, because when it tells you something in a manifest or a retraction note, it's true. Least, because when it tells you something in a README sentence, the base rate from our receipts table is: roughly half of the product-prose claims we suspected needed correction (selection bias acknowledged — reviewers hunted for violations, so this is closer to "half of suspicious claims are violations" than a general unreliability rate).

## 7. What a serious v1 requires

1. **Fix the front door.** Rename the distribution or banner the collision; tag a release; delete or refresh `dist/`. Nothing else matters until a new user can install the thing.
2. **Close the cheap holes and re-word the expensive ones.** `.envrc.local` + copy-tool family into `GUARDED_PATH_FRAGMENTS`/hard-denies; `dd of=`/`ln`; per-segment git allowlist; state-based (not command-shaped) sidecar revival; auth + body cap on `/score` and stop auto-persisting baselines from unauthenticated callers; `--host 127.0.0.1` in the gen launcher; HMAC the guard-hash store; port `_resolve_repo_root` into the revive hook. Then write the sentence the docs are missing: *this is a tripwire, not a sandbox, and here is the adversary it does not stop.*
3. **Make the prose match the code, mechanically.** "Recalibrates" → "prints labels (Phase-4 pending)". "Always" → the actual guard coverage. "Every env var" → generated from code, in CI. A doc-lint test that fails when a README claim string diverges from a code constant would fit this repo's soul perfectly.
4. **Break one circularity.** Strip the tool's own decision from the labeling view and run the two-blind-labeler protocol already specified in `EVAL_PROTOCOL.md` — on real sessions, even n=30. One honest external measurement is worth more than the entire current whitepaper.
5. **Fix the latency story.** Either the neural path fits inside its own 1500 ms cap on CPU, or the README should say what actually happens (symbolic fallback, always) and the ML should stop being described as the product.
6. **Green the gate.** Fix the five order-dependent failures; add `record_override`'s missing writer or delete the metric that reads its phantom file.

## Verdict

`reasoning-core` is a genuinely interesting research artifact wearing a product's README: a System-1/System-2 edit gate with real oracle value, real supply-chain discipline, and an adversarial-audit culture that most labs ten times its size lack — undermined by a distribution that has never shipped, a security model with category-level gaps politely listed as "known limits," an evaluation loop that has never once closed on external ground truth, and a persistent habit of describing Phase 4 in the present tense.

The bones are good. The receipts are immaculate. The front door is bricked, the watchdog sleeps through port-kills, and the north-star metric is normalized by a constant multiplied in from a retracted study. Fix the first two paragraphs of the README and half of this review ages out overnight — which, given this project's track record of actually doing the work in its audit responses, is exactly the kind of finding it knows how to close.

**Recommended posture for a would-be user today:** run it in default advisory mode for the oracles and the audit trail, which are the parts that deliver as described; do not enable enforcement mode and do not believe a README sentence that a code line hasn't co-signed.

---

*Review round 6, 2026-09-24. Independent of rounds 1–5; disputes with those rounds' findings are noted where they occurred. All file:line references are to commit `2146e93`. Two reviewer findings that failed re-verification — the surviving-5 plan-quality arithmetic, and the judge-independence gate reading — are disclosed in the header and §4 rather than silently dropped — the standard this project taught us to apply.*

---

## Self-audit (round 6b): what this review got wrong or weighted unfairly

This section applies the same evidentiary standard to itself that the review applied to the repository. It was produced by two independent auditors (an incorrect-findings hunt and a nitpick/fairness audit) who were explicitly told to assume the review was written by an over-eager adversary. Findings below are classified as **CORRECTED** (factual error), **REWEIGHTED** (technically true but severity/proportionality mis-calibrated), or **DROPPED** (nitpicky/unfair to emphasize). Each carries a file:line receipt against the review.

### CORRECTED

| # | Review claim | Error | Correction |
|---|---|---|---|
| C-1 | §1 bullet: "`dist/` ships a stale, never-published `reasoning_core-0.2.0` wheel." | `dist/` is gitignored (`.gitignore:11`); `git ls-files dist/` returns empty. The wheel is a local build artifact, not part of the repo. Presenting an untracked directory as a repo defect is unfair. | Deleted from §1. The distribution story still collapses via zero tags + PyPI collision + stale docstring admission; no finding depends on `dist/`. |
| C-2 | TL;DR + §3 latency row: "silently falls back to the deterministic oracles" / "times out into symbolic fallback almost always." | `pre_edit_guard.py:274-275` prints `[hybrid-reasoner] sidecar hard cap exceeded ({cap_ms}ms); symbolic fallback engaged.` to stderr on timeout. Fallback is logged, not silent. Also: p95 bounds only the tail; median is unknown from a p95. | Reworded throughout to "falls back with a stderr notice" and "the entire p95 tail … blows the cap." The unreconciled-latency finding stands; the silence claim does not. |
| C-3 | §3 "no ongoing network calls" row: "documenting a code-relay to a cloud LLM API as the standard path on Linux." | `docs/INSTALL.md:177-186` presents the Scaleway backend as **one option** for non-Apple-silicon hardware, under a section header, not as "the standard." The word "standard" does not appear. | Reworded to "documenting an opt-in cloud relay for non-Apple-silicon hosts." The underlying point (the README's absolute claim coexists with a documented opt-in relay) stands; the characterization of the relay as "standard" was mine, not the docs'. |
| C-4 | §2 "__pycache__ planting": "CPython executes attacker bytecode while the file-hash verification stays green." Scope implied the hook script itself is vulnerable. | The dev checkout invokes hooks as direct scripts (`python3 ${CLAUDE_PROJECT_DIR}/src/hooks/pre_edit_guard.py` per `.claude/settings.json`), which runs as `__main__` without consulting `__pycache__`. The **shipped install** uses `-m src.hooks.pre_edit_guard` (per `rc init` templates), which DOES consult `__pycache__`; sibling imports (`audit_log`, `_guard_paths`, etc.) are also importable via cache regardless of invocation form. | Added scope qualifier: the vector applies to the shipped `-m` invocation and all imported siblings; direct-script dev invocations are not affected. Finding stands with narrower applicability. |

### REWEIGHTED

| # | Review claim | Honest grade | Rationale |
|---|---|---|---|
| W-1 | §3 "Decision ID always" treated as MAJOR | MINOR | Only one of four pre-guards emits it, but the bash guard's exit path already shows its reason inline (`pre_bash_guard._exit` at :359-363); `rc explain`/`bypass-next` are edit-flow features. The word "always" is wrong and should be fixed in the README, but the operator impact is limited to bash-layer UX. |
| W-2 | §2 gen-sidecar 0.0.0.0 treated as security-section bullet | MINOR caveat | Opt-in backend (`RC_REASONER_BACKEND=llama`), not default; S2 sidecar loopback correctly credited. Real but niche; belongs as a footnote, not a headline gap. |
| W-3 | §2 `/score` poisoning treated as security-section bullet | MINOR | Single-user local tool; attacker shares UID and localhost access. `/baseline` was authenticated in round-6; residual risk is real but low-impact in the threat model. |
| W-4 | §5 windowing cap deleted treated as major operability loss | MINOR caveat | Default-off feature (`RC_DIFF_WINDOWING=1`); operators who opt in accept the tradeoff. Correctness restored at cost of operability — fair to note, but not load-bearing for default users. |
| W-5 | §2 revive-hook missing repo-root validation buried as bullet 3 | Promote to top of §2 gap list, grade MAJOR | Fires automatically on sidecar death (not opt-in); hooks install into arbitrary target repos so a hostile repo can plant `scripts/start-sidecar.sh`; supervisor has `_resolve_repo_root` but revive doesn't — inconsistency within the same codebase. More severe than gen-sidecar 0.0.0.0. |
| W-6 | §6 "half of the product-prose claims we checked needed a footnote" | Acknowledge selection bias | Denominator is claims *selected for suspicion* by hostile reviewers, not a random sample. "Half of suspicious claims are violations" is closer to a tautology than a base rate. Reframe as "of the N claims we suspected, roughly half needed correction" or drop the statistic entirely. |
| W-7 | §1 closing: "installs a French NLP dataset generator" | Soften tone | Nationality as pejorative modifier undermines credibility. Replace with "unrelated Inria-affiliated package." Factual content unchanged. |

### DROPPED

| # | Review claim | Why dropped |
|---|---|---|
| D-1 | §3 ROADMAP "Plan-B fallback ships behind `RC_USE_MAMBA2_2_7B=1`" | Category-mixing. ROADMAP is explicitly a living planning document; counting aspirational statements alongside user-facing README claims inflates the §3 table. No reader would configure this flag from ROADMAP alone. Moved to a separate "roadmap hygiene" footnote if retained at all. |
| D-2 | §3 env-var census "147 vs 101" | Methodologically shaky: grep catches comments, test-only tokens, string placeholders. The precision implies rigor the method lacks. The underlying concrete examples (`RC_STRUCTURAL_BLOCK` undocumented hard-block, `S2_HEALTH_GRACE_S` drift 30→60) carry the finding entirely without the soft census. Drop the numbers; keep the examples. |
| D-3 | TL;DR "six-or-maybe-seven CLIs" as flagship sentence | Counting nit elevated above security and distribution findings. An undercount in the project's disfavor is minor. Belongs as a one-liner in §3 or §5, not in the opening sentence. |

### What survives fully intact

Of the ~25 distinct findings in the original review, approximately **18 survive hostile re-verification fully intact**, including every load-bearing finding: PyPI quick-start collision, `.envrc.local` copy-tool bypass chain, `record_override` NameError + dead-metric pipeline, suite order-dependence, circular eval loop, Decision ID coverage gap, audit-history print-only, Mahalanobis near-inert at shipped defaults, baseline capture refusal, supply-chain pinning discipline, data hygiene, behavioral test sampling. Three findings were corrected (C-1 through C-4), seven were reweighted, and three were dropped.

The net effect: the review's core thesis — *"the project's integrity lives in its artifacts and dies in its prose"* — is strengthened, not weakened, by these corrections. The false-precision claims and the contemptuous tone were the weakest parts of the document; removing them makes the remaining findings harder to dismiss.

### One thing the review was too lenient about

The revive hook's missing repo-root validation (§2, now promoted per W-5) deserved more prominence from the start. It is the most consequential gap in the security surface because it fires automatically, targets arbitrary repos, and contradicts the supervisor's own stricter check in the same codebase. The original review treated it symmetrically with other bullets; it should have been the lead security finding.

---

*Self-audit round 6b, 2026-09-24. Produced by two independent adversarial auditors targeting the review document itself. Corrections applied inline where possible; this section preserves the audit trail.*
