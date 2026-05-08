---
date: 2026-05-08
commit: 98e5a6c2bbe25c90d0a8c7c8897af7bf6ad56a89
branch: main
ticket: (none — internal eval R&D)
status: draft
---
# Plan: SWE-bench Verified Eval Toolkit for Gemini-CLI (Setup A vs Setup B)

## Summary

Build a sibling toolkit at `/Users/jakubsikora/research-gemini-swebench-eval-scripts/` that runs **SWE-bench Verified — Frontier + MultiFile subset (~135 instances)** comparing **Setup A (vanilla `gemini` CLI)** against **Setup B (`gemini` CLI + reasoning-core sidecar, stubbed for now)**, n=3 reps, all 3 judges + SWE-bench's native test-patch grader. Output is decision-comparable to the iter-3 Circit toolkit so we can publish a combined external-validity supplement to the iter-3 whitepaper.

## Research References

- `thoughts/shared/research/2026-05-08-iter3-eval-whitepaper.md` — internal eval result (Setup B wins on rubric quality, internal n=8 tasks)
- Web research (2026-05-08, "online research subagent"): SWE-bench Verified Frontier+MultiFile recommended for harness-config discrimination; documented 22-pt swing between scaffolds on identical model
- Upstream: https://github.com/SWE-bench/SWE-bench (MIT, Docker harness)
- Discriminative subset definitions: jatinganhotra.dev/blog/swe-agents/2025/06/05/swe-bench-verified-discriminative-subsets.html
- Existing toolkit patterns to mirror: `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/{spawner,judge_runner,decision,reporter,run_schema,prereg_validation}.py`

## Constraints / Decisions Locked In

1. **New sibling repo** at `/Users/jakubsikora/research-gemini-swebench-eval-scripts/`. No subdir of claude toolkit, no fork of upstream.
2. **Subset**: Frontier (95) + MultiFile (40) = 135 instances, deduped to ~120 unique.
3. **Reps**: n=3 per instance per setup. Total cells = 135 × 2 × 3 = **810 cells**.
4. **Setup B is stubbed** until reasoning-core gets a gemini-CLI integration. Stub matches claude-side pattern: `~/eval-setups-gemini/B/.envrc` + `~/eval-setups-gemini/B/gemini.json` (gemini-equivalent of `settings.local.json`). Setup B Phase 4 spawn is a TODO that fails loudly until RC-gemini lands.
5. **Vendor SWE-bench harness** (do not git-submodule). Pin to a known SHA. Keep upstream code untouched in `vendor/swebench/`; our overlay calls into it via Python imports.
6. **gemini CLI** at `/opt/homebrew/bin/gemini` (v0.37.1 verified present). Headless mode: `gemini -p "<prompt>" --json` (TBD: confirm exact flags in Phase 1).
7. **Scoring**: SWE-bench's own `swebench.harness.run_evaluation` (test patches against fail-to-pass + pass-to-pass) is **primary**. Circit-style 3-judge BARS rubric is **secondary**, computed on the patch + agent transcript so we have rubric-comparability with iter-3.
8. **Decision rule**: SWE-bench resolved% as the gate (not lex), then BARS rubric tiers as iter-3-comparable secondary signal.

---

## Phase 1: Scaffold + vendor SWE-bench harness + gemini-cli smoke test

### Changes

#### File: `/Users/jakubsikora/research-gemini-swebench-eval-scripts/README.md`
- **What**: New repo README mirroring claude toolkit's, but for gemini.
- **Where**: New file.
- **Rationale**: Matches the existing eval-scripts pattern; documents env requirements (gemini CLI on PATH, docker, scaleway scw key for Qwen-Coder grader, vibe + gemini CLIs for cross-judging).

#### File: `/Users/jakubsikora/research-gemini-swebench-eval-scripts/pyproject.toml`
- **What**: Minimal `pyproject.toml` (Python 3.11+, stdlib + numpy + datasets + docker SDK).
- **Where**: New file.
- **Rationale**: Match upstream SWE-bench dependency set; add datasets (HF) and docker-py if not transitively pulled.

#### File: `/Users/jakubsikora/research-gemini-swebench-eval-scripts/vendor/swebench/`
- **What**: Vendored copy of upstream `swebench/` package at pinned SHA.
- **Where**: New tree; copy from `git clone https://github.com/SWE-bench/SWE-bench.git --depth 1` into `vendor/swebench/` (no .git).
- **Rationale**: Per-decision: vendor not submodule. Pin SHA in `vendor/SWEBENCH_PIN.txt` for reproducibility.
- **Code sketch**:
  ```sh
  git clone --depth 1 https://github.com/SWE-bench/SWE-bench.git /tmp/swe-upstream
  (cd /tmp/swe-upstream && git rev-parse HEAD) > vendor/SWEBENCH_PIN.txt
  cp -r /tmp/swe-upstream/swebench vendor/swebench
  cp /tmp/swe-upstream/LICENSE vendor/swebench-LICENSE.txt
  ```

#### File: `/Users/jakubsikora/research-gemini-swebench-eval-scripts/eval/__init__.py`
- **What**: Package marker.
- **Where**: New file.
- **Rationale**: Mirror `research-claude-code-setup-eval-scripts/eval/__init__.py`.

#### File: `/Users/jakubsikora/research-gemini-swebench-eval-scripts/eval/gemini_runner.py`
- **What**: Wrapper around `gemini` CLI for headless single-prompt invocation. Returns `{stdout, stderr, returncode, tokens, duration_ms}`.
- **Where**: New file.
- **Rationale**: Equivalent of claude toolkit's `spawner.spawn_one` but for gemini CLI. Single function, no retry yet (Phase 5).
- **Code sketch**:
  ```python
  def run_gemini_once(prompt: str, env: dict[str, str] | None,
                     timeout_seconds: int, cwd: Path) -> RunResult:
      cmd = ["gemini", "-p", prompt, "--json", "--max-thinking-tokens", "31999"]
      # exact flags TBD in smoke test below
      proc = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=timeout_seconds, env=env, cwd=cwd)
      return RunResult(stdout=proc.stdout, stderr=proc.stderr,
                       returncode=proc.returncode, ...)
  ```

#### File: `/Users/jakubsikora/research-gemini-swebench-eval-scripts/scripts/smoke-gemini-cli.sh`
- **What**: 1-call smoke test verifying `gemini -p` accepts a prompt and returns JSON.
- **Where**: New file.
- **Rationale**: Equivalent to claude toolkit's `preflight_claude_session` (round-9 I3). Catches out-of-credits, auth-expired, wrong-model BEFORE burning sweep cells.

### Success Criteria

#### Automated Verification
- [ ] `python3 -m unittest discover tests -v` passes (empty test suite OK at this phase).
- [ ] `python3 -c "from eval.gemini_runner import run_gemini_once; r = run_gemini_once('say hi', None, 30, Path('.')); assert r.returncode == 0"` succeeds.
- [ ] `bash scripts/smoke-gemini-cli.sh` exits 0 and prints token usage.
- [ ] `vendor/SWEBENCH_PIN.txt` contains a 40-char SHA.

#### Manual Verification
- [ ] `gemini -p "say hi"` JSON output structure documented in `gemini_runner.py` docstring.
- [ ] Vendor SWE-bench layout matches upstream README's expected structure (`swebench/harness/run_evaluation.py` exists, importable).

### Dependencies
- Requires: nothing
- Blocks: Phase 2, 3

---

## Phase 2: Setup A & Setup B definitions (B stubbed)

### Changes

#### File: `~/eval-setups-gemini/setups.yaml`
- **What**: New setups registry, gemini equivalent of `~/eval-setups/setups.yaml`.
- **Where**: New file (different from claude's `~/eval-setups/`).
- **Rationale**: Keep gemini setups separate from claude setups; one toolkit per registry.
- **Code sketch**:
  ```yaml
  setups:
    A:
      envrc:    ~/eval-setups-gemini/A/.envrc
      gemini_config: ~/eval-setups-gemini/A/gemini.json
      notes: "Setup A — vanilla gemini CLI, no sidecar"
    B:
      envrc:    ~/eval-setups-gemini/B/.envrc
      gemini_config: ~/eval-setups-gemini/B/gemini.json
      notes: "Setup B — gemini CLI + reasoning-core sidecar (TODO: RC-gemini integration)"
  ```

#### File: `~/eval-setups-gemini/A/.envrc`
- **What**: Vanilla A envrc — no special vars; just sets `GEMINI_API_KEY` (or relies on `gemini /login`) and clears any RC env leak.
- **Where**: New file.
- **Rationale**: Matches claude A pattern.
- **Code sketch**:
  ```sh
  # Setup A — vanilla gemini
  unset REASONING_CORE_SESSION_DIR
  unset REASONING_CORE_PROFILE
  # GEMINI_API_KEY assumed to be set by user via `gemini /login`
  ```

#### File: `~/eval-setups-gemini/A/gemini.json`
- **What**: Vanilla minimal gemini config (gemini-equivalent of Claude's `settings.local.json`).
- **Where**: New file.
- **Rationale**: Match claude pattern; needs research in Phase 1 to determine actual gemini config schema.

#### File: `~/eval-setups-gemini/B/.envrc`
- **What**: Setup B envrc — sets RC env vars (matching claude B's pattern) so when RC-gemini lands, it Just Works.
- **Where**: New file.
- **Rationale**: Build the shape now; wire when RC-gemini compat ships.
- **Code sketch**:
  ```sh
  # Setup B — gemini + reasoning-core sidecar (TODO: pending RC-gemini integration)
  export REASONING_CORE_PROFILE=gemini-default
  export REASONING_CORE_SESSION_DIR="$HOME/.local/state/reasoning-core/sessions"
  # When RC-gemini lands, the wrapper will inject planning hooks here.
  ```

#### File: `~/eval-setups-gemini/B/gemini.json`
- **What**: Setup B gemini config with narrowed tool allowlist + RC orchestrator MCP slots reserved.
- **Where**: New file (stub).
- **Rationale**: Stub now so spawner pre-flight passes; populate when RC-gemini ships.

#### File: `eval/setups.py`
- **What**: Loader equivalent to claude toolkit's `spawner.load_setups`. Validates that A's and B's configs differ (per-spawner pre-flight).
- **Where**: New file.
- **Rationale**: Refuses to run if A and B configs are byte-identical (real pitfall in iter-1).

#### File: `eval/setup_b_guard.py`
- **What**: Hard-fail check for Setup B: if `REASONING_CORE_PROFILE` is set but `which rc-gemini` returns nothing, error with clear message.
- **Where**: New file.
- **Rationale**: Until RC-gemini ships, Setup B sweeps must abort, not silently fall back to vanilla. Avoids iter-2 v2 collapse pattern (mistral leftover config bypass).
- **Code sketch**:
  ```python
  def assert_setup_b_ready() -> None:
      if not shutil.which("rc-gemini"):
          raise RuntimeError(
              "Setup B requires reasoning-core gemini integration (`rc-gemini`). "
              "Not yet implemented. Run only Setup A until RC-gemini lands.")
  ```

### Success Criteria

#### Automated Verification
- [ ] `python3 -c "from eval.setups import load_setups; print(load_setups())"` lists A + B with correct paths.
- [ ] `diff -q ~/eval-setups-gemini/A/.envrc ~/eval-setups-gemini/B/.envrc` returns non-empty.
- [ ] `diff -q ~/eval-setups-gemini/A/gemini.json ~/eval-setups-gemini/B/gemini.json` returns non-empty.
- [ ] `python3 -c "from eval.setup_b_guard import assert_setup_b_ready; assert_setup_b_ready()"` exits non-zero with the TODO message (until RC-gemini ships).

#### Manual Verification
- [ ] Setup A's `gemini.json` schema confirmed against gemini CLI v0.37.1 docs.
- [ ] Setup B's `.envrc` env var names match what RC-gemini integration is expected to read (placeholder; revisit when RC-gemini design is firm).

### Dependencies
- Requires: Phase 1
- Blocks: Phase 5

---

## Phase 3: Subset selection + dataset prep

### Changes

#### File: `eval/subset.py`
- **What**: Loads SWE-bench Verified from HuggingFace (`princeton-nlp/SWE-bench_Verified`), filters to Frontier (95) + MultiFile (40) instances. Frontier IDs and MultiFile IDs are loaded from a checked-in JSON manifest.
- **Where**: New file.
- **Rationale**: Subset list is from external research (jatinganhotra.dev). Pin the IDs in `vendor/subset_manifest.json` for reproducibility.

#### File: `vendor/subset_manifest.json`
- **What**: Static list of 95 Frontier + 40 MultiFile instance IDs. Source: jatinganhotra.dev discriminative-subsets blog post + manual cross-check.
- **Where**: New file.
- **Rationale**: Subset definitions might shift upstream; pin them.
- **Code sketch**:
  ```json
  {
    "source": "jatinganhotra.dev/blog/swe-agents/2025/06/05/swe-bench-verified-discriminative-subsets.html",
    "fetched_at": "2026-05-08",
    "frontier": ["sympy__sympy-12345", "..." /* 95 IDs */],
    "multifile": ["scikit-learn__scikit-learn-67890", "..." /* 40 IDs */]
  }
  ```

#### File: `eval/dataset_prep.py`
- **What**: Per-instance scaffolding: clone the repo at the SHA, apply the test patch (NOT the gold patch), set up the worktree, install deps via `swebench`'s setup helpers.
- **Where**: New file.
- **Rationale**: SWE-bench instances need pre-built Docker images (Epoch AI registry) OR per-instance setup. Vendor SWE-bench harness handles this; we wrap.

### Success Criteria

#### Automated Verification
- [ ] `python3 -c "from eval.subset import load_subset; s=load_subset(); assert len(s)==135"` passes.
- [ ] `python3 -c "from eval.dataset_prep import prepare_one; prepare_one('sympy__sympy-12345')"` produces a docker image or worktree.

#### Manual Verification
- [ ] Subset manifest IDs cross-checked against blog post.
- [ ] Sample instance loadable from HuggingFace.

### Dependencies
- Requires: Phase 1
- Blocks: Phase 4, 5

---

## Phase 4: SWE-bench-shaped prompt + agent loop for gemini

### Changes

#### File: `eval/prompt_template.py`
- **What**: Build the per-instance prompt that gemini will see. Standard SWE-bench format: problem statement + repo snapshot pointer + "produce a unified diff" instruction.
- **Where**: New file.
- **Rationale**: Match the prompt format upstream agents (SWE-agent, OpenHands, etc.) use, so our results are leaderboard-comparable.

#### File: `eval/agent_loop.py`
- **What**: Drive gemini through a multi-turn agent loop: problem → plan → tool calls (read/edit files in worktree) → final patch. Captures transcript + tool calls + tokens.
- **Where**: New file.
- **Rationale**: SWE-bench is multi-turn. gemini-cli's headless mode supports tool use via MCP; plumb file-read/file-edit tools.
- **Code sketch**:
  ```python
  def run_one_instance(instance_id: str, setup: SetupSpec, worktree: Path,
                      timeout_seconds: int) -> InstanceResult:
      env = load_envrc(setup.envrc)
      prompt = build_prompt(instance_id, worktree)
      # Multi-turn: gemini -p with file-system tools enabled
      result = run_gemini_with_tools(prompt, env, worktree, timeout_seconds)
      patch = extract_patch(result.transcript)
      return InstanceResult(instance_id, patch, result.tokens, result.duration_ms,
                            result.transcript, result.tool_calls)
  ```

#### File: `eval/patch_extractor.py`
- **What**: Pull the final unified diff out of gemini's response. Handle markdown fences, multiple diff blocks, etc.
- **Where**: New file.
- **Rationale**: Patches must be in the format SWE-bench grader expects (`*.diff`).

### Success Criteria

#### Automated Verification
- [ ] `python3 -c "from eval.agent_loop import run_one_instance; ..."` produces a non-empty patch on a known-trivial instance.
- [ ] `python3 -m unittest tests.test_patch_extractor` passes (covers markdown-fence + multi-diff cases).

#### Manual Verification
- [ ] Sample patch on instance `sympy__sympy-12345` (or trivial fixture) inspected by hand and confirmed valid unified diff.
- [ ] gemini's tool-use schema in v0.37.1 matches `agent_loop.py`'s assumptions (revisit if it doesn't).

### Dependencies
- Requires: Phase 1, 3
- Blocks: Phase 5

---

## Phase 5: Sweep orchestration

### Changes

#### File: `eval/sweep.py`
- **What**: Top-level loop: for each setup × instance × rep, call `agent_loop.run_one_instance`, save artifacts, run SWE-bench grader, capture pass/fail.
- **Where**: New file.
- **Rationale**: Equivalent of claude toolkit's `iter3-full-sweep.sh` but Python (cleaner for 810 cells with retry + parallelism).

#### File: `scripts/run-full-sweep.sh`
- **What**: Bash wrapper that calls `python3 -m eval.sweep` with the right args, redirects log, captures pid.
- **Where**: New file.
- **Rationale**: Mirrors `iter3-full-sweep.sh` pattern (caffeinate, nohup, pid file).

#### File: `eval/parallel_runner.py`
- **What**: Run N workers in parallel (default N=4 on a 32-core mac, configurable). Per-instance cells are independent (each has own Docker image), so parallelism is safe — UNLIKE the claude/Circit case where docker port-8081 collided.
- **Where**: New file.
- **Rationale**: SWE-bench grader is per-instance Docker; no shared state; parallelize aggressively. Estimated: 810 cells / 4 workers × 2min/cell = ~7h wall.

#### File: `eval/retry.py`
- **What**: Retry-on-rate-limit / transient signature detection (gemini-API-specific signatures). Mirrors claude `_is_rate_limited_or_transient`.
- **Where**: New file.
- **Rationale**: gemini API has its own 429 / 503 patterns; pre-build the signature list now.

### Success Criteria

#### Automated Verification
- [ ] Dry-run on 1 instance × 1 setup × 1 rep completes end-to-end (prompt → patch → grade).
- [ ] Dry-run on 5 instances × 2 setups × 1 rep with --workers=2 completes in <15 min.
- [ ] Pid file at `/tmp/swe-gemini-sweep.pid` exists during sweep; cleared on exit.
- [ ] Sweep aborts loud if Setup B's `setup_b_guard.assert_setup_b_ready()` fails.

#### Manual Verification
- [ ] Watch first 5 cells live (transcript tail). Confirm gemini emits patches in expected format.
- [ ] Confirm SWE-bench grader docker image cache is populated (avoids re-pull per cell).

### Dependencies
- Requires: Phase 1, 2, 3, 4
- Blocks: Phase 6, 7

---

## Phase 6: Scoring + Circit-style rubric overlay

### Changes

#### File: `eval/swebench_grader.py`
- **What**: Wrapper around `vendor/swebench/harness/run_evaluation.py`. Per cell: run grader against the cell's patch; record `resolved` (true/false), `fail_to_pass`, `pass_to_pass`, test logs.
- **Where**: New file.
- **Rationale**: SWE-bench's own primary grader. Test-patch validation is the gold standard.

#### File: `eval/judge_runner.py`
- **What**: Port of claude toolkit's `judge_runner.py`. Runs Gemini + Vibe + Qwen-Coder over the patch + transcript with the same 5 BARS dims (`repo_fit`, `cleanliness`, `correctness_determinism`, `plan_signal`, `diff_discipline`).
- **Where**: New file (copy + adapt).
- **Rationale**: Direct comparability to iter-3 results. Same rubric, same judges, different agent + different tasks → external validity supplement.

#### File: `eval/aggregate.py`
- **What**: Compute per-arm: resolved%, paired-bootstrap CI on resolved%, BARS dim means, wall-clock, tokens (incl. cache).
- **Where**: New file (mirror claude toolkit's `aggregate.py`).
- **Rationale**: Same statistical apparatus as iter-3 → side-by-side reporting.

### Success Criteria

#### Automated Verification
- [ ] `python3 -m eval.swebench_grader --cell <path>` returns `resolved: bool`.
- [ ] `python3 -m eval.judge_runner --cell <path> --judges gemini,vibe,qwen-coder` produces 3 grade JSONs per artifact.
- [ ] `python3 -m eval.aggregate --eval-dir <path> --out report.json` produces a report comparable in shape to claude `REPORT.md`.

#### Manual Verification
- [ ] Grader output cross-checked against SWE-bench Verified leaderboard for a known-resolved instance.
- [ ] Judge BARS scores look reasonable on 3 sample cells (no parser bugs).

### Dependencies
- Requires: Phase 5
- Blocks: Phase 7

---

## Phase 7: Reporter + decision rule + freeze

### Changes

#### File: `eval/decision.py`
- **What**: Decision rule for SWE-bench: gate = resolved% (no minimum threshold; we want raw delta), tiebreaks = BARS impl quality, plan quality, total tokens, wall-clock.
- **Where**: New file.
- **Rationale**: SWE-bench is binary (resolved/not), not a flake-rate gate like Circit. Adapt the lex order accordingly.

#### File: `eval/reporter.py`
- **What**: Render `REPORT.md` with: per-arm resolved% + 95% CI, paired delta (B−A), per-task pass/fail breakdown, BARS rubric table (mirror iter-3 format), token + cache + wall stats.
- **Where**: New file.
- **Rationale**: Side-by-side with iter-3 for the supplement.

#### File: `eval/freeze.py`
- **What**: Snapshot the eval dir into a frozen manifest (mirror claude toolkit's `freeze.py`).
- **Where**: New file.

### Success Criteria

#### Automated Verification
- [ ] `python3 -m eval.cli decide --report report.json` emits `decision.json` with `winner: "A"|"B"|null` + reasoning.
- [ ] `python3 -m eval.cli freeze --label swebench-gemini-iter1 --eval-dirs <path>` produces a frozen manifest.
- [ ] Final REPORT.md has same column structure as iter-3 REPORT.md (n_runs, locked, rotated, impl_q, plan_q, main_tokens, $, wall_s) plus SWE-bench-specific columns (resolved%, fail_to_pass, pass_to_pass).

#### Manual Verification
- [ ] Compare REPORT.md column-by-column against iter-3 — ready for side-by-side appendix in a v2 whitepaper.
- [ ] Decision rule produces an answer (no `inconclusive` short-circuit at this phase).

### Dependencies
- Requires: Phase 6
- Blocks: nothing

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| gemini CLI v0.37.1 headless mode lacks the tool-use APIs we need | Med | High | Phase 1 smoke test catches it. If true, pivot to gemini API directly via Python `google-genai` SDK instead of CLI; Setup A semantics adjust accordingly. |
| RC-gemini never lands → toolkit ships with only Setup A working | Med | Med | Setup B is stubbed; toolkit produces useful Setup A-only data immediately. Setup B can be wired later without refactor. |
| SWE-bench Docker images won't pull on Mac (M-series ARM compatibility) | Low | High | Epoch AI registry publishes ARM images. Verify in Phase 1 by pulling 1 image. Fall back to running grader on a Linux VM if needed. |
| Subset manifest IDs drift from upstream | Low | Med | Pinned in `vendor/subset_manifest.json`; we own the snapshot. |
| 810 cells exceed 8h wall-clock budget on M-series mac with 4 workers | Med | Med | Parallel workers configurable; can drop to n=1 (270 cells) without methodology hit (SWE-bench convention). |
| gemini API rate limits cause cascading failures | Med | Med | Phase 5 builds retry-on-429 from day 1; mirror claude toolkit's `_is_rate_limited_or_transient`. |
| BARS rubric calibration drift between gemini-eval and iter-3 (judges scoring different agent styles differently) | Med | Med | Document explicitly in REPORT; judge α gate per round-9 I5; if α<0.6, fall back to descriptive-only (same pattern as iter-3-prereg-v2). |
| Patch extractor mishandles gemini's diff format | High | Med | Phase 4 has unittest fixtures. First 5 live cells manually verified. |

## Rollback Strategy

Toolkit is a sibling repo. To roll back:

1. `rm -rf /Users/jakubsikora/research-gemini-swebench-eval-scripts/`
2. `rm -rf ~/eval-setups-gemini/`
3. `rm /tmp/swe-gemini-sweep.*`

No production systems touched. No git history modified outside the new repo. Vendor SWE-bench code is pinned; deletion is clean.

If a sweep is in progress and needs aborting mid-run:

1. `kill $(cat /tmp/swe-gemini-sweep.pid)`
2. `rm -rf <eval_dir>` (the in-progress data)
3. Worktrees can be left intact (per-instance Docker; nothing leaks to host).

## File Ownership Summary

| File | Phase | Change Type |
|---|---|---|
| `research-gemini-swebench-eval-scripts/README.md` | 1 | Create |
| `research-gemini-swebench-eval-scripts/pyproject.toml` | 1 | Create |
| `research-gemini-swebench-eval-scripts/vendor/swebench/` | 1 | Create (vendor) |
| `research-gemini-swebench-eval-scripts/vendor/SWEBENCH_PIN.txt` | 1 | Create |
| `research-gemini-swebench-eval-scripts/eval/__init__.py` | 1 | Create |
| `research-gemini-swebench-eval-scripts/eval/gemini_runner.py` | 1 | Create |
| `research-gemini-swebench-eval-scripts/scripts/smoke-gemini-cli.sh` | 1 | Create |
| `~/eval-setups-gemini/setups.yaml` | 2 | Create |
| `~/eval-setups-gemini/A/.envrc` | 2 | Create |
| `~/eval-setups-gemini/A/gemini.json` | 2 | Create |
| `~/eval-setups-gemini/B/.envrc` | 2 | Create (stub) |
| `~/eval-setups-gemini/B/gemini.json` | 2 | Create (stub) |
| `research-gemini-swebench-eval-scripts/eval/setups.py` | 2 | Create |
| `research-gemini-swebench-eval-scripts/eval/setup_b_guard.py` | 2 | Create |
| `research-gemini-swebench-eval-scripts/eval/subset.py` | 3 | Create |
| `research-gemini-swebench-eval-scripts/vendor/subset_manifest.json` | 3 | Create |
| `research-gemini-swebench-eval-scripts/eval/dataset_prep.py` | 3 | Create |
| `research-gemini-swebench-eval-scripts/eval/prompt_template.py` | 4 | Create |
| `research-gemini-swebench-eval-scripts/eval/agent_loop.py` | 4 | Create |
| `research-gemini-swebench-eval-scripts/eval/patch_extractor.py` | 4 | Create |
| `research-gemini-swebench-eval-scripts/eval/sweep.py` | 5 | Create |
| `research-gemini-swebench-eval-scripts/eval/parallel_runner.py` | 5 | Create |
| `research-gemini-swebench-eval-scripts/eval/retry.py` | 5 | Create |
| `research-gemini-swebench-eval-scripts/scripts/run-full-sweep.sh` | 5 | Create |
| `research-gemini-swebench-eval-scripts/eval/swebench_grader.py` | 6 | Create |
| `research-gemini-swebench-eval-scripts/eval/judge_runner.py` | 6 | Create (port) |
| `research-gemini-swebench-eval-scripts/eval/aggregate.py` | 6 | Create (port) |
| `research-gemini-swebench-eval-scripts/eval/decision.py` | 7 | Create |
| `research-gemini-swebench-eval-scripts/eval/reporter.py` | 7 | Create |
| `research-gemini-swebench-eval-scripts/eval/freeze.py` | 7 | Create (port) |

## Open Questions / Flagged Uncertainties

1. **gemini CLI v0.37.1 tool-use protocol**: I do not have first-hand verification of how the gemini CLI exposes file-read/file-edit tools to the model in headless mode. Phase 1's smoke test must establish this. If the CLI lacks tool-use, Phase 4's `agent_loop.py` will need to use the gemini Python SDK directly and the "Setup A = vanilla gemini CLI" framing weakens (becomes "Setup A = vanilla gemini SDK call").
2. **Setup A's `gemini.json` schema**: unknown until we read gemini CLI docs; placeholder for now.
3. **RC-gemini integration shape**: I'm assuming it mirrors RC-claude (env vars + wrapper). If the actual design differs, Setup B's `.envrc` and `gemini.json` need updating.
4. **Subset manifest source**: jatinganhotra.dev blog is one researcher's curated subset. Worth cross-checking against the upstream `experiments/` repo for canonical IDs before sweep.
5. **Cost budget**: Top-3 researcher estimate was $582 batch for 6 SWE-bench Verified Frontier+MultiFile runs (n=1). At n=3, that's ~$1,746 — but we'd be on gemini API not Anthropic, so re-estimate against Google's pricing in Phase 1.
6. **Pricing volatility**: Anthropic and Google prices both moved in late 2025; confirm with current public pricing pages before locking budget.
