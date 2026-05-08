---
date: 2026-05-08
commit: 98e5a6c2bbe25c90d0a8c7c8897af7bf6ad56a89
branch: main
ticket: (none — internal eval R&D)
status: draft-v2 (incorporates LLM-scientist + AI-engineer review)
---
# Plan v2: SWE-bench Verified Eval Toolkit for Gemini-CLI (Setup A vs Setup B)

## Summary

Build a sibling toolkit at `/Users/jakubsikora/research-gemini-swebench-eval-scripts/` that produces **two pre-registered deliverables**:

1. **Deliverable D1 — Setup A baseline**: vanilla `gemini` CLI (or `google-genai` SDK fallback) on SWE-bench Verified Frontier+MultiFile subset (~135 instances) + 30-instance random Verified holdout. n=3 reps. Standalone leaderboard-style result for gemini.
2. **Deliverable D2 — A vs B replication**: same harness with Setup B = gemini + reasoning-core MCP server (`gemini mcp add reasoning-core …`). Gated on RC-gemini integration shipping. Reports paired-bootstrap Δ on resolved% + 5 BARS dims; **directional concordance only with iter-3, not magnitude-poolable** (4 confounds: model family, task domain, integration, agent loop).

Cross-family judges: **Vibe + Qwen-Coder + Claude (Sonnet 4.6 or Opus 4.7)**. Gemini judge dropped to avoid self-judging conflict (iter-3 L4).

Plan v2 incorporates LLM-scientist + AI-engineer reviews from 2026-05-08. See §"Review fixes applied" for traceability.

## Research References

- iter-3 whitepaper: `thoughts/shared/research/2026-05-08-iter3-eval-whitepaper.md`
- iter-3 prereg + amendment: `iter3-prereg.json`, `iter3-prereg-v2.md`
- Plan v1 + reviews: see commit message and §"Review fixes applied" below
- Web research (2026-05-08): SWE-bench Verified Frontier+MultiFile subset
- Upstream: https://github.com/SWE-bench/SWE-bench (MIT)
- Discriminative subsets: jatinganhotra.dev/blog/swe-agents/2025/06/05/swe-bench-verified-discriminative-subsets.html
- Existing claude toolkit shape: `/Users/jakubsikora/research-claude-code-setup-eval-scripts/`

## Constraints / Decisions Locked In

1. New sibling repo at `/Users/jakubsikora/research-gemini-swebench-eval-scripts/`.
2. Subset = **Frontier (95) + MultiFile (40) + Random Verified holdout (30)** = ~165 unique instances. Holdout is the generalizability check (subset-selection bias mitigation).
3. Reps: **n=3 per instance per setup**. With holdout: ~165 × 2 × 3 = **990 cells** for D2; ~495 cells for D1 alone.
4. **Setup B is stubbed** until reasoning-core MCP server for gemini ships. Stub uses `gemini mcp add reasoning-core <command>` contract, NOT a `rc-gemini` binary. Hard-fail guard: `gemini mcp list` must contain reasoning-core entry AND `REASONING_CORE_*` env set AND server-startup smoke succeeds. ACP mode (`gemini --acp`) under investigation as alternative cleaner sidecar surface.
5. **Use `pip install swebench@<pinned-sha>`** at the toolkit's pyproject.toml level. Do NOT vendor-as-tree (review B3 — breaks relative imports). Pin both the package version AND the upstream git SHA in `SWEBENCH_PIN.txt`.
6. **Agent loop uses `google-genai` Python SDK** for Setup A, NOT `gemini` CLI directly. Reason: per-turn `usageMetadata.cachedContentTokenCount` is only exposed in the SDK; iter-3-comparable token/cache economics is load-bearing. CLI is used only for the smoke-test path (Phase 1) and a thin fallback. Setup A reframed as "vanilla google-genai SDK loop" — weaker but honest framing.
7. **Compute platform = Linux x86 cloud VM** (Hetzner / GCP n2-standard-32 / similar) for Phase 5+6. M-series mac runs Phase 1-4 dev only. Rosetta running x86 SWE-bench images is 3-5× slower; not viable for 990 cells.
8. Disk precondition: ≥500GB free at sweep start. Image-prune hook between batches. Ports per-instance via Docker-compose project naming.
9. Judges: **Vibe + Qwen-Coder + Claude**. Gemini dropped (self-judging). Claude added cross-family.
10. **Two pre-registrations**:
    - `swebench-gemini-prereg-D1.json` (Setup A baseline) — frozen before Phase 5 starts.
    - `swebench-gemini-prereg-D2.json` (A vs B replication) — frozen before Setup B's first cell fires (after RC-gemini integration smoke passes).
11. Decision rule: **resolved%** primary, **paired-bootstrap CI on Δ** primary inferential, BARS rubric 5-dim secondary with **BH-FDR q≤0.10** correction. NO ≥0.90 gate (review B4: would null both arms at SWE-bench's 30-60% baseline). Use observed gemini SWE-bench Verified leaderboard% as descriptive baseline, no pass/fail threshold.

## Review Fixes Applied (v1 → v2)

### Engineering BLOCKING
- E1 (gemini CLI flags fabricated) → §6 SDK pivot; CLI flags rewritten in Phase 1 to `-o json --yolo --include-directories <wt>` only for smoke.
- E2 (Setup B guard contract) → guard now checks `gemini mcp list` + env + MCP-server startup, not `rc-gemini` binary. ACP mode flagged for investigation in Phase 1.
- E3 (SWE-bench vendoring) → `pip install swebench@<pin>` not vendor-tree.
- E4 (Docker on M-series) → Linux x86 cloud VM for Phase 5+6. Mac is dev-only.
- E5 (Disk budget) → ≥500GB precondition, image-prune hook.
- E6 (Token/cache parity from CLI impossible) → SDK pivot; CLI is fallback only.

### Scientific BLOCKING
- S1 (No prereg) → Phase 0 added; D1 + D2 prereg files frozen pre-sweep.
- S2 (Setup-A-only conflated with external validity) → two distinct deliverables D1 + D2.
- S3 (Gemini self-judging) → Gemini dropped, Claude added.
- S4 (Gate threshold undefined / would null) → no ≥0.90 gate; descriptive at resolved% with paired-bootstrap CI.
- S5 (n=3 underpowered for rubric; cost mis-anchored) → MDE redo in Phase 0; cost re-derived from gemini API price card.

### Nice-to-have
- N1 (subset bias) → 30-instance random Verified holdout added.
- N2 (multiple comparisons) → BH-FDR q≤0.10 in prereg.
- N3 (iter labeling) → "swebench-iter1" own line; cross-referenced from claude-iter-4 prereg as supplement-track-A. NOT iter-4.
- N4 (cross-agent comparability claim too strong) → directional concordance only; magnitude not poolable. Stated explicitly in prereg.
- N5 (patch extractor) → dual extractor (regex + SDK-extracted JSON) with agreement check.

---

## Phase 0: Pre-registration

### Changes

#### File: `thoughts/shared/research/swebench-gemini-prereg-D1.json`
- **What**: Frozen pre-registration for Deliverable D1 (Setup A baseline only).
- **Where**: New file in reasoning-core repo (matches iter3-prereg.json location).
- **Rationale**: review S1 — no Phase 1 code lands without a frozen prereg.
- **Required fields** (mirror iter3-prereg.json structure):
  - `frozen_at_commit_sha` (set when committed)
  - `subset_manifest_sha` (sha256 of `vendor/subset_manifest.json`)
  - `swebench_pin` (vendored package version + upstream SHA)
  - `gemini_sdk_pin` (`google-genai` wheel version + hash)
  - `gemini_model_id` (e.g. `gemini-2.5-pro` — pinned)
  - `judges` = `[vibe, qwen-coder, claude]` with model IDs pinned
  - `inferential_scope_D1` = `[resolved_percent, resolved_percent_holdout]`
  - `descriptive_scope_D1` = `[main_tokens_total, cache_read_total, cache_write_total, wall_clock_s, dollars]`
  - `mde_table` — paired-bootstrap MDE on resolved% Δ at α=0.05, n=3, ~165 instances. Worked example with assumed within-arm SD from iter-3 (0.06-0.18 on pass-rate).
  - `cost_estimate` — derived from gemini API price card (input ~$1.25/Mtok, output ~$10/Mtok for gemini-2.5-pro at time of writing — verify against current page) for ~495 cells.
  - `kill_switch.branch_A` — if gemini SDK token-capture fails repeatability check (Phase 1.6), abort and ship D1 as descriptive-only on resolved%.
  - `amendment_protocol` — same as iter3 (documented diff via prereg-v2.md).
  - `multiple_comparisons` = `BH-FDR q<=0.10`.

#### File: `thoughts/shared/research/swebench-gemini-prereg-D2.json`
- **What**: Frozen pre-registration for Deliverable D2 (A vs B replication).
- **Where**: New file. Frozen LATER, after RC-gemini integration smoke passes.
- **Rationale**: D2 must NOT freeze until Setup B is real. Review S2 — separate prereg keeps deliverables decoupled.
- **Required fields** (delta vs D1):
  - `setup_b_definition` — exact `gemini mcp add` command + `.envrc` + `gemini.json` SHAs.
  - `inferential_scope_D2` = `[resolved_percent_delta_BminusA, 5_BARS_dim_deltas (BH-FDR corrected)]`.
  - `descriptive_scope_D2` = `[token_delta, cache_delta, wall_delta, dollars_delta]`.
  - `external_validity_claim` — explicitly: "directional concordance with iter-3 only; magnitude NOT poolable". Cite the 4 confounds (model family, task domain, integration shape, agent-loop architecture).
  - `mde_table` — for both resolved% Δ AND each BARS dim Δ. The latter is the iter-3 weak-signal (≈0.07 MDE on flake; ≈0.79-1.17 on rubric dims under lmer-cluster-bootstrap).
  - `kill_switch.branch_A` — if Setup B's MCP server fails to start in pre-flight, abort D2 entirely.
  - `judges` — same 3 as D1.

#### File: `thoughts/shared/research/swebench-iter1-meta.md`
- **What**: Cross-references swebench-iter1 to claude-iter-3 + claude-iter-4 prereg as "supplement-track-A". Documents iter-labeling decision.
- **Where**: New file.
- **Rationale**: review N3 — naming clarity.

### Success Criteria

#### Automated Verification
- [ ] `python3 -m eval.cli validate-prereg --prereg swebench-gemini-prereg-D1.json --check-audit-trail` passes (port from claude toolkit).
- [ ] `git log --format="%H %s" thoughts/shared/research/swebench-gemini-prereg-D1.json | head -1` returns a commit SHA.
- [ ] D1 prereg's `frozen_at_commit_sha` equals the commit SHA from the previous bullet.

#### Manual Verification
- [ ] D1 prereg reviewed by ops + science (sign-off in commit message body).
- [ ] D2 prereg drafted but UNFROZEN until Phase 8 (RC-gemini smoke pass).
- [ ] Cost estimate cited with date-stamped link to gemini pricing page.

### Dependencies
- Requires: nothing
- Blocks: Phase 1, all subsequent phases (no code without prereg).

---

## Phase 1: Scaffold + SDK smoke + gemini-CLI capability check

### Changes

#### File: `/Users/jakubsikora/research-gemini-swebench-eval-scripts/README.md`
- **What**: Repo README. Document: env requirements (gemini CLI v0.37.1, `google-genai` SDK pinned, docker, vibe + claude + scaleway-qwen-coder for judges, ≥500GB disk for sweep host, x86 Linux for Phase 5+6).
- **Rationale**: Match claude-toolkit README pattern; add explicit hardware requirements (review E4).

#### File: `/Users/jakubsikora/research-gemini-swebench-eval-scripts/pyproject.toml`
- **What**: Pinned deps. `google-genai==<pin>`, `swebench==<pin>`, `datasets==<pin>`, `numpy`, `urllib3` (stdlib otherwise).
- **Rationale**: review E3 (pip-installed swebench, not vendor-tree).
- **Code sketch**:
  ```toml
  [project]
  name = "research-gemini-swebench-eval-scripts"
  requires-python = ">=3.11"
  dependencies = [
    "google-genai==<pin>",  # set in Phase 1.1
    "swebench==<pin>",
    "datasets>=3.0",
    "numpy>=1.26",
  ]
  ```

#### File: `vendor/SWEBENCH_PIN.txt`
- **What**: Pinned upstream SHA + pypi version of swebench.
- **Rationale**: reproducibility.

#### File: `eval/__init__.py`, `eval/cli.py`
- **What**: Package skeleton + CLI entry point.

#### File: `eval/gemini_runner.py`
- **What**: SDK-based agent runner. Uses `google-genai` SDK for the agent loop. Captures `usageMetadata` per turn (input, output, cached, total).
- **Rationale**: review E6 — token/cache parity from CLI impossible. SDK pivot.
- **Code sketch**:
  ```python
  from google import genai
  from google.genai import types

  def run_gemini_sdk(prompt: str, tools: list, env: dict[str, str],
                    timeout_seconds: int, model_id: str) -> RunResult:
      client = genai.Client(api_key=env["GEMINI_API_KEY"])
      messages = [types.Content(role="user", parts=[types.Part.from_text(prompt)])]
      total_in = total_out = total_cached = 0
      transcript = []
      for _turn in range(MAX_TURNS):
          resp = client.models.generate_content(
              model=model_id, contents=messages, config=types.GenerateContentConfig(tools=tools))
          um = resp.usage_metadata
          total_in += um.prompt_token_count or 0
          total_out += um.candidates_token_count or 0
          total_cached += um.cached_content_token_count or 0
          transcript.append(resp.model_dump())
          if not resp.function_calls:
              break
          # execute tools, append results to messages
          ...
      return RunResult(transcript=transcript, tokens={
          "input": total_in, "output": total_out, "cached": total_cached,
          "total": total_in + total_out
      }, ...)
  ```

#### File: `scripts/smoke-gemini-sdk.sh`
- **What**: 1-call SDK smoke. Verifies SDK installed, key works, token usage emitted, cached_content_token_count populated.
- **Rationale**: pre-flight before any sweep cell. Equivalent to claude `preflight_claude_session`.

#### File: `scripts/smoke-gemini-cli.sh`
- **What**: Document what `gemini --help` actually exposes (v0.37.1). Verify `-o json --yolo --include-directories` works for headless tool-use.
- **Rationale**: keep CLI as fallback path; document for future. Review E1.

#### File: `eval/cli_capabilities.json`
- **What**: Capability snapshot of `gemini` CLI v0.37.1 — flags supported, MCP add command, ACP mode availability.
- **Rationale**: future-proofing; if v0.38 changes flags, snapshot tells us why old smokes broke.

### Success Criteria

#### Automated Verification
- [ ] `pip install -e .` succeeds with all pins.
- [ ] `python3 -c "from google import genai; c=genai.Client(); r=c.models.generate_content(model='gemini-2.5-pro', contents='say hi'); print(r.usage_metadata)"` returns a `usage_metadata` object with NON-NULL `cached_content_token_count` after a 2nd call (cache must demonstrably work).
- [ ] `bash scripts/smoke-gemini-sdk.sh` exits 0 + asserts `cached_content_token_count > 0` on second run.
- [ ] `bash scripts/smoke-gemini-cli.sh` exits 0 with documented flag list.
- [ ] `python3 -c "import swebench.harness.run_evaluation; print(swebench.harness.run_evaluation.__file__)"` succeeds (pip-installed import works).
- [ ] `df -h /` reports ≥500GB free.

#### Manual Verification
- [ ] CLI capability JSON cross-checked against `gemini --help` and `gemini mcp --help`.
- [ ] SDK token cache hit confirmed by repeating identical 8k-token prompt and observing `cached_content_token_count` jump on call 2.
- [ ] gemini API price card link archived to `docs/gemini-pricing-2026-05-08.pdf` (immutable record for cost estimate in prereg).

### Dependencies
- Requires: Phase 0
- Blocks: Phase 2+

### Kill switch (Phase 1 specific)
If `cached_content_token_count` is never populated (SDK doesn't expose it for our use pattern), pivot D1 prereg to `descriptive-only on cache` with documented amendment, OR escalate to operator decision before Phase 2.

---

## Phase 2: Setup A & Setup B definitions (B stubbed via MCP-server contract)

### Changes

#### File: `~/eval-setups-gemini/setups.yaml`
- **What**: Setups registry. Each setup has `envrc`, `gemini_config` (gemini.json equivalent), and **`mcp_servers`** (new field for B).
- **Rationale**: review E2 — Setup B is MCP server registration, not binary.
- **Code sketch**:
  ```yaml
  setups:
    A:
      envrc: ~/eval-setups-gemini/A/.envrc
      gemini_config: ~/eval-setups-gemini/A/gemini.json
      mcp_servers: []
      notes: "Setup A — vanilla google-genai SDK loop, no MCP, no sidecar"
    B:
      envrc: ~/eval-setups-gemini/B/.envrc
      gemini_config: ~/eval-setups-gemini/B/gemini.json
      mcp_servers:
        - name: reasoning-core
          command: <TODO: rc-gemini-mcp-server command, set when RC-gemini ships>
      notes: "Setup B — gemini SDK + reasoning-core MCP server. STUBBED."
  ```

#### File: `~/eval-setups-gemini/A/.envrc`
- **What**: Vanilla A. Sets `GEMINI_API_KEY`, clears any RC env.
- **Code sketch**:
  ```sh
  unset REASONING_CORE_PROFILE REASONING_CORE_SESSION_DIR
  # GEMINI_API_KEY assumed pre-set
  ```

#### File: `~/eval-setups-gemini/A/gemini.json`
- **What**: Vanilla minimal config. (gemini CLI config schema — research in Phase 1.)

#### File: `~/eval-setups-gemini/B/.envrc`
- **What**: Sets `REASONING_CORE_PROFILE=gemini-default`, `REASONING_CORE_SESSION_DIR`. Other vars TBD per RC-gemini design.

#### File: `~/eval-setups-gemini/B/gemini.json`
- **What**: B-specific config. Will reference reasoning-core MCP server once registered via `gemini mcp add`.

#### File: `eval/setups.py`
- **What**: Loader + diff-check (refuse to run if A and B configs are byte-identical).

#### File: `eval/setup_b_guard.py`
- **What**: Three-part guard for Setup B readiness:
  1. `gemini mcp list` contains `reasoning-core` entry.
  2. `REASONING_CORE_*` env vars are set.
  3. The registered MCP server actually starts (timeout 10s).
- **Rationale**: review E2 — correct contract.
- **Code sketch**:
  ```python
  def assert_setup_b_ready() -> None:
      r = subprocess.run(["gemini", "mcp", "list"], capture_output=True, text=True, timeout=10)
      if "reasoning-core" not in r.stdout:
          raise RuntimeError("Setup B: gemini mcp list does not contain 'reasoning-core'. "
                             "Run `gemini mcp add reasoning-core <command>` first.")
      if not os.getenv("REASONING_CORE_PROFILE"):
          raise RuntimeError("Setup B: REASONING_CORE_PROFILE env var not set.")
      # Smoke: try to start the MCP server with a 10s timeout.
      probe = subprocess.run(["gemini", "mcp", "list", "--health"], ...)
      if probe.returncode != 0:
          raise RuntimeError(f"Setup B: MCP server failed to start: {probe.stderr}")
  ```

### Success Criteria

#### Automated Verification
- [ ] `eval.setups.load_setups()` returns A + B with non-empty differing configs.
- [ ] `assert_setup_b_ready()` exits non-zero (Setup B is stubbed; expected to fail until RC-gemini lands).
- [ ] D1 sweep (Phase 5) MUST run with only Setup A and explicitly skip Setup B.

### Dependencies
- Requires: Phase 1
- Blocks: Phase 5 (D1 sweep), Phase 8 (D2 sweep)

---

## Phase 3: Subset selection + dataset prep

### Changes

#### File: `vendor/subset_manifest.json`
- **What**: 95 Frontier + 40 MultiFile + 30 random Verified holdout instance IDs. Source: jatinganhotra.dev cross-checked against upstream `experiments/` repo.
- **Rationale**: review N1 — random holdout for generalizability.
- **Schema**:
  ```json
  {
    "source_curated": "jatinganhotra.dev/blog/swe-agents/2025/06/05/...",
    "source_holdout": "random sample(seed=20260508) from princeton-nlp/SWE-bench_Verified test split, excluding curated set",
    "fetched_at": "2026-05-08",
    "frontier": [...95 IDs...],
    "multifile": [...40 IDs...],
    "holdout": [...30 IDs...]
  }
  ```

#### File: `eval/subset.py`
- **What**: Loads manifest, validates against HF dataset, returns deduped instance list.

#### File: `eval/dataset_prep.py`
- **What**: For each instance: pull pre-built Docker image (Epoch AI registry on Linux x86; document Mac as best-effort), apply test_patch (NOT gold patch), prepare worktree.
- **Rationale**: review N3 (test/gold leak).
- **Field allowlist for prompt** (review N3):
  ```python
  PROMPT_ALLOWED_FIELDS = {"problem_statement", "hints_text", "repo", "base_commit", "test_patch"}
  PROMPT_FORBIDDEN_FIELDS = {"patch", "gold_patch", "fail_to_pass", "pass_to_pass"}
  ```
- **Unit test asserts** that no FORBIDDEN field substring leaks into rendered prompt.

### Success Criteria

#### Automated Verification
- [ ] `len(load_subset()) == 165` (95+40+30).
- [ ] `prepare_one(<random instance ID>)` produces a worktree with test_patch applied AND no gold patch present (`grep -r '<gold-patch-known-string>' <wt>` returns nothing).
- [ ] On Linux x86 host: `docker pull <epoch-registry>/<one-instance>` succeeds.
- [ ] Unit test `tests/test_no_gold_leak.py` passes.

#### Manual Verification
- [ ] Subset IDs reviewed by ops.
- [ ] 5 random instances spot-checked: image pulls, test_patch applies cleanly.

### Dependencies
- Requires: Phase 1
- Blocks: Phase 4, 5

---

## Phase 4: Prompt template + agent loop + dual patch extractor

### Changes

#### File: `eval/prompt_template.py`
- **What**: SWE-bench standard prompt format (problem statement → repo pointer → "produce unified diff"). Field allowlist enforced.

#### File: `eval/agent_loop.py`
- **What**: Multi-turn SDK loop. Tools: file-read, file-write, run-shell (sandboxed in worktree). Captures transcript per turn + tokens per turn.
- **Rationale**: review E6 — SDK loop, not CLI.

#### File: `eval/patch_extractor.py`
- **What**: **Dual-extractor** with agreement check (review N5):
  - Path 1: regex extraction of `diff --git ... ` blocks from transcript.
  - Path 2: secondary SDK call asking gemini to extract its own patch as JSON.
  - If both agree → `extracted_patch`. If disagree → `cell_aborted_extractor_disagreement` (do NOT mark as `resolved=false`).
- **Fixtures** (review N5): ≥10 cases covering markdown fences, multi-diff, missing newline, CRLF, paths with spaces, binary diffs, empty-context hunks, `b/` prefix omission, multiple files, single-line diffs.
- **Validates** with `git apply --check` against the worktree before scoring.

### Success Criteria

#### Automated Verification
- [ ] `tests/test_patch_extractor.py` passes with all 10+ fixtures.
- [ ] `tests/test_no_gold_leak.py` (Phase 3) still passes.
- [ ] Dual-extractor agreement rate on Phase 4 dev set ≥95% (else extractor needs more fuzzing).

#### Manual Verification
- [ ] First 5 live cells: extracted patches manually inspected, all valid unified diffs.

### Dependencies
- Requires: Phase 1, 3
- Blocks: Phase 5

---

## Phase 5: D1 sweep orchestration (Setup A only)

### Changes

#### File: `eval/sweep.py`
- **What**: Top-level loop over instance × setup × rep. For D1, setup=A only.

#### File: `eval/parallel_runner.py`
- **What**: N=4 workers, configurable. Per-instance Docker projects via `COMPOSE_PROJECT_NAME=swe-eval-${instance}-${rep}` (no port collision since we control project naming).

#### File: `eval/retry.py`
- **What**: Gemini-specific transient signatures (review N4):
  ```python
  GEMINI_TRANSIENT = [
    "RESOURCE_EXHAUSTED", "429 Too Many Requests", "quota",
    "503 The model is overloaded", "DEADLINE_EXCEEDED", "INTERNAL", "UNAVAILABLE",
    "code: 8", "code: 13", "code: 14",
    "Auth error: token expired", "gaxios error 401",
  ]
  ```

#### File: `scripts/run-d1-sweep.sh`
- **What**: Bash wrapper. nohup + caffeinate (Mac dev) OR systemd-run (Linux prod). pid file at `/tmp/swe-gemini-d1-sweep.pid`.

#### File: `scripts/preflight-host.sh`
- **What**: Pre-sweep host check (review E5):
  - `df -h /` ≥ 500GB free
  - `docker info` returns valid output
  - SDK key valid (`smoke-gemini-sdk.sh` exits 0)
  - All judges available (vibe + qwen-coder reachable + claude key set)

#### File: `scripts/cleanup-images.sh`
- **What**: Image-prune hook between batches.

### Success Criteria

#### Automated Verification
- [ ] Dry-run on 1 instance × Setup A × 1 rep completes end-to-end.
- [ ] Dry-run on 5 instances × A × 1 rep with 2 workers completes in <15 min on Linux x86.
- [ ] Pre-flight host check passes; sweep aborts loud if host doesn't meet ≥500GB.

#### Manual Verification
- [ ] First 5 cells live tail confirms gemini emits valid patches.
- [ ] Image prune hook fires between batches; disk doesn't blow past 90%.

### Dependencies
- Requires: Phase 1, 2, 3, 4 + frozen D1 prereg.
- Blocks: Phase 6, 7

---

## Phase 6: Scoring (SWE-bench grader + 3-judge BARS overlay)

### Changes

#### File: `eval/swebench_grader.py`
- **What**: Wraps `swebench.harness.run_evaluation` via subprocess + predictions.json (review E3 — that IS the API). Captures `resolved`, `fail_to_pass`, `pass_to_pass`, test logs per instance.
- **Code sketch**:
  ```python
  def grade_cell(eval_dir: Path, predictions_path: Path) -> dict:
      cmd = ["python3", "-m", "swebench.harness.run_evaluation",
             "--predictions_path", str(predictions_path),
             "--max_workers", "1",
             "--run_id", eval_dir.name,
             "--cache_level", "instance"]
      proc = subprocess.run(cmd, capture_output=True, ...)
      # parse output JSON
      ...
  ```

#### File: `eval/judge_runner.py`
- **What**: Port from claude toolkit. Judges = `[vibe, qwen-coder, claude]`. Same 5 BARS dims.
- **Code sketch**:
  ```python
  JUDGE_HTTP_CONFIGS = {
    "qwen-coder": {...same as claude...},
    "claude": {"url": "https://api.anthropic.com/v1/messages",
               "model": "claude-sonnet-4-6",  # cross-family substitute for gemini
               "api_key_resolver": "anthropic_api_key", ...},
  }
  ```
- **Rationale**: review S3 — Claude added cross-family.

#### File: `eval/aggregate.py`
- **What**: Per-arm: resolved% mean + 95% CI (paired-bootstrap), BARS dim means, token + cache + wall sums. BH-FDR correction over 5 BARS dims. (Review N2.)

### Success Criteria

#### Automated Verification
- [ ] `swebench_grader.grade_cell` returns valid dict with `resolved` field on a known-resolved instance.
- [ ] `judge_runner` produces 3 grade JSONs per artifact (skip Gemini judge entirely).
- [ ] Aggregate produces report-shape comparable to claude REPORT.md plus SWE-bench columns (`resolved%`, `fail_to_pass`, `pass_to_pass`).

#### Manual Verification
- [ ] Grader output cross-checked against SWE-bench Verified leaderboard for 3 instances.
- [ ] BH-FDR-corrected q-values present in aggregate output.

### Dependencies
- Requires: Phase 5
- Blocks: Phase 7

---

## Phase 7: D1 reporter + decision + freeze

### Changes

#### File: `eval/decision.py`
- **What**: D1 decision = descriptive (no gate). resolved% with paired-bootstrap CI. BARS rubric tiers reported but no winner declared (D1 is single-arm baseline).

#### File: `eval/reporter.py`
- **What**: REPORT.md mirrors iter-3 column structure for comparability + adds SWE-bench-specific columns. Holdout subset reported separately.

#### File: `eval/freeze.py`
- **What**: Snapshot manifest. **Pins ALL toolchains** (review N5):
  - gemini SDK wheel version + hash
  - swebench package version + upstream SHA
  - Docker image digests for every used image
  - vibe + claude judge CLI versions / API model IDs
  - scaleway endpoint + qwen-coder model tag
  - python wheel hashes for all deps
  - prereg D1 commit SHA

### Success Criteria

#### Automated Verification
- [ ] `decide` emits decision.json with descriptive verdict (no winner since D1 is A-only).
- [ ] `freeze --label swebench-iter1-D1-frozen` produces full manifest.
- [ ] Manifest contains every tool pin from §"Constraints 11".

#### Manual Verification
- [ ] REPORT.md reviewed; ready to ship as standalone D1 result.

### Dependencies
- Requires: Phase 6
- Blocks: Phase 8 (gates D2 freeze on D1 ship)

---

## Phase 8: D2 prereg freeze + Setup B integration smoke

### Changes

(All of Phase 8 is gated on RC-gemini integration shipping. If RC-gemini doesn't ship, Phase 8 doesn't fire and toolkit ends at D1.)

#### File: `thoughts/shared/research/swebench-gemini-prereg-D2.json`
- **What**: D2 prereg is FROZEN here, not Phase 0. Reason: Setup B's `mcp_servers.command`, `.envrc`, and `gemini.json` SHAs must be real, not stubs.

#### File: `~/eval-setups-gemini/B/{.envrc,gemini.json}`
- **What**: Real values, not stubs.

#### File: `scripts/smoke-setup-b.sh`
- **What**: 1-call MCP-server smoke. Invokes `assert_setup_b_ready()` + runs 1 trivial instance with Setup B end-to-end.

### Success Criteria
- [ ] `assert_setup_b_ready()` exits 0.
- [ ] D2 prereg's `frozen_at_commit_sha` matches commit.
- [ ] 1 trivial instance × Setup B × 1 rep produces a valid patch + transcript.

### Dependencies
- Requires: Phase 7 + RC-gemini integration shipping externally.
- Blocks: Phase 9

---

## Phase 9: D2 sweep (A vs B) + scoring + reporter

### Changes

#### File: `scripts/run-d2-sweep.sh`
- **What**: Same harness as D1 but with Setup A AND B. n=3 reps. ~990 cells.

#### File: `eval/decision.py` (extended)
- **What**: D2 decision rule: paired-bootstrap CI on resolved% Δ + 5 BARS Δ (BH-FDR). Decision per pre-reg lex order.

#### File: `eval/reporter.py` (extended)
- **What**: REPORT v2 with A vs B columns + per-task breakdown for SWE-bench instances.

### Success Criteria
- [ ] Sweep completes 990 cells.
- [ ] decide emits A vs B verdict.
- [ ] freeze label `swebench-iter1-D2-frozen`.

### Dependencies
- Requires: Phase 8
- Blocks: nothing (terminal phase)

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `cached_content_token_count` not populated by SDK for our use pattern | Med | High | Phase 1 kill-switch; pivot D1 to descriptive-only on cache. |
| RC-gemini never ships | Med | Med | D1 ships standalone; D2 indefinitely deferred. |
| SWE-bench Docker images x86-only on Mac | High | High | Linux x86 cloud VM mandatory for Phase 5+6. |
| `swebench` pypi version drifts; pinned version yanked | Low | Med | Mirror to internal pypi + checksum. |
| Subset manifest IDs invalid against current HF dataset | Low | Med | Phase 3 validates against `princeton-nlp/SWE-bench_Verified` test split at toolkit init. |
| 990 cells exceed 24h on x86-32 box | Med | Med | Drop to n=1 (330 cells) under documented amendment, OR increase parallelism to 8 workers. |
| gemini API rate limits cascade | Med | Med | Phase 5 retry signatures (N4); 30-min retry budget per cell. |
| Patch-extractor disagreement rate >5% | Med | Med | Cells flagged `cell_aborted_extractor_disagreement` excluded from resolved% denominator with documented count. |
| BH-FDR over-corrects → no significant Δs detected | Low | Low | Pre-registered; descriptive results still publishable. |
| Cross-agent comparability claim challenged in review | Med | Low | Prereg explicitly states "directional concordance only, not magnitude-poolable" — defended in advance. |
| Cost overrun (gemini API) | Low | Med | Phase 0 cost estimate is hard cap; abort if 80% spent before sweep ends. |
| Anthropic-judge cost (Claude added) overruns | Low | Low | Use Sonnet 4.6 not Opus for judge passes; ~$0.05/cell × 990 = $50. |

## Rollback Strategy

Toolkit is sibling repo. To roll back:
1. `rm -rf /Users/jakubsikora/research-gemini-swebench-eval-scripts/`
2. `rm -rf ~/eval-setups-gemini/`
3. `rm /tmp/swe-gemini-*.{pid,log,stdout}`
4. `git revert <prereg-D1-commit> <prereg-D2-commit>` (in reasoning-core repo)

In-progress sweep abort:
1. `kill $(cat /tmp/swe-gemini-d1-sweep.pid)` (or `d2`)
2. `rm -rf <eval_dir>`
3. `bash scripts/cleanup-images.sh` to reclaim Docker disk.

## File Ownership Summary

| File | Phase | Change Type |
|---|---|---|
| `thoughts/shared/research/swebench-gemini-prereg-D1.json` | 0 | Create (frozen) |
| `thoughts/shared/research/swebench-gemini-prereg-D2.json` | 0/8 | Create (frozen at Phase 8) |
| `thoughts/shared/research/swebench-iter1-meta.md` | 0 | Create |
| `research-gemini-swebench-eval-scripts/README.md` | 1 | Create |
| `research-gemini-swebench-eval-scripts/pyproject.toml` | 1 | Create |
| `research-gemini-swebench-eval-scripts/vendor/SWEBENCH_PIN.txt` | 1 | Create |
| `research-gemini-swebench-eval-scripts/eval/{__init__.py,cli.py,gemini_runner.py}` | 1 | Create |
| `research-gemini-swebench-eval-scripts/eval/cli_capabilities.json` | 1 | Create |
| `research-gemini-swebench-eval-scripts/scripts/{smoke-gemini-sdk.sh,smoke-gemini-cli.sh}` | 1 | Create |
| `research-gemini-swebench-eval-scripts/docs/gemini-pricing-2026-05-08.pdf` | 1 | Create (archived) |
| `~/eval-setups-gemini/{setups.yaml,A/.envrc,A/gemini.json,B/.envrc,B/gemini.json}` | 2 | Create (B stubbed) |
| `research-gemini-swebench-eval-scripts/eval/{setups.py,setup_b_guard.py}` | 2 | Create |
| `research-gemini-swebench-eval-scripts/vendor/subset_manifest.json` | 3 | Create |
| `research-gemini-swebench-eval-scripts/eval/{subset.py,dataset_prep.py}` | 3 | Create |
| `research-gemini-swebench-eval-scripts/tests/test_no_gold_leak.py` | 3 | Create |
| `research-gemini-swebench-eval-scripts/eval/{prompt_template.py,agent_loop.py,patch_extractor.py}` | 4 | Create |
| `research-gemini-swebench-eval-scripts/tests/test_patch_extractor.py` | 4 | Create (≥10 fixtures) |
| `research-gemini-swebench-eval-scripts/eval/{sweep.py,parallel_runner.py,retry.py}` | 5 | Create |
| `research-gemini-swebench-eval-scripts/scripts/{run-d1-sweep.sh,preflight-host.sh,cleanup-images.sh}` | 5 | Create |
| `research-gemini-swebench-eval-scripts/eval/{swebench_grader.py,judge_runner.py,aggregate.py}` | 6 | Create |
| `research-gemini-swebench-eval-scripts/eval/{decision.py,reporter.py,freeze.py}` | 7 | Create |
| `research-gemini-swebench-eval-scripts/scripts/{smoke-setup-b.sh,run-d2-sweep.sh}` | 8/9 | Create |

## Open Questions (still unresolved after v2)

1. **gemini SDK cache hit reliability** — Phase 1 kill-switch covers this but the actual % hit rate matters for cost. Resolved in Phase 1.
2. **Linux x86 host availability** — need to provision before Phase 5. Hetzner CCX33 / GCP n2-standard-32 / similar. Owner: ops.
3. **RC-gemini integration design** — currently external dependency. If RC team ships ACP-mode integration instead of MCP-server, prereg D2 needs to reflect that (amendment protocol).
4. **Claude judge cost** — adding Claude pushes judge-side cost up by ~$50 for 990 cells. Acceptable but not trivial.
5. **Subset manifest cross-check against `experiments/`** — needs ops time in Phase 3 to verify the 95+40 IDs are still canonical.
6. **`swebench` package vs upstream SHA divergence** — pypi may lag; Phase 1 must record both pip version AND upstream SHA, with assertion they match.
