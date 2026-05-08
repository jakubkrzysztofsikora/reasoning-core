---
date: 2026-05-06
commit: 6a921ce
branch: main
ticket: system-2-loop-closure
status: draft
revision: v6 (round-5 nits: epsilon split for promotion #5(b); minimum-n floor; multi-session durability note; circit-app inlined snapshots + 50% augmentation cap)
---
# Plan: Closing the System-2 Loop (v6)

## Summary

Iter-2 ships an honest one-shot gate. This plan upgrades it to an active
collaborator across six phases:

0. **Refactor lift** — split `pre_edit_guard.py` (758 LOC) into
   `_guard_paths.py` + `_dispatch.py`; promote `_files_touched` to public
   `src/git_utils.files_touched`. Now folded into Week 1.
1. **TTFV + Drift Visualization + one-line installer + 50-commit Proof-of-Utility**
2. **Hybrid symbolic gating (ADR injection)** — Python-only v1, 2 rule types,
   stdlib `ast` (no tree-sitter dep yet)
3. **S-C-R loop** — per-dim Pareto-dominance, semantic safety net (pyright/ruff
   with missing-binary fallback), stderr truncation contract
3.5. **v3 cross-family kappa dataset** (NEW in v3 — separated from sequencing
     row to make ownership explicit)
3.6. **30-pair ground-truth corpus** (NEW in v3 — labeled by 2 raters with
     pre-registered protocol)
4. **Mamba-3 watch + Plan-B Mamba-2-2.7B fallback** (HOLD on Mamba-3 — no HF
   checkpoint as of 2026-05)

Default posture: dark for every phase.

## v3 changelog (round-2 review fixes)

Round-2 surfaced 4 new blockers + 9 new tractable issues on top of v2.
v3 incorporates all:

CRITICAL (auditor REVISE-FIRST):
- **Phase 3.5 NEW**: v3 cross-family kappa dataset given its own phase with
  explicit owner, file paths, judge model IDs, labeling protocol, n. No
  longer hidden in a "Week 6+" sequencing line.
- **Phase 3.6 NEW**: 30-pair post-incident ground-truth labeling task gets
  pre-registered protocol (matches the rigor of the 2-rater operator-review).
- **Phase 0 sequencing**: folded into Week 1 (Day 0-1 of week 1, NOT a
  pre-week zero-day). Sequencing table updated.
- **Prompt-injection mitigation reworded**: Pareto + safety-net is the
  primary defense; "input scrub" relegated to defense-in-depth.

HIGH:
- **Pareto epsilon now per-dim relative** (1% of orig + 1e-3 floor; LLM-sci):
  `np.all(new_rv <= np.maximum(orig_rv * 1.01, orig_rv + 1e-3))`. Honors
  mixed-magnitude dims (fan_in deltas O(1) vs novelty deltas O(0.01)).
- **n=100 corpus + CI clarity**: precision >=0.90 *point estimate* with
  Wilson 95% CI lower bound reported; promotion to `deny` requires CI
  lower >= 0.85 (instead of 0.90, which would need n>=350).
- **Cross-family judge independence MEASURED not asserted**: Phase 3.5
  pre-registers pairwise kappa <0.7 between any two judges as the
  independence test; if violated, swap one judge.
- **2-rater n bumped from 5 to 30 paired ratings**: stable Cohen's kappa
  needs ~30; protocol now requires 30 SCR-accepted repairs across the
  shadow window (which is itself bumped from 50 SCR triggers to a
  threshold that yields 30 acceptances).
- **Safety-net missing-binary fallback**: if `pyright` / `ruff` not on
  PATH, treat as advisory pass (logged) — better than blocking legitimate
  repairs because of an ops gap.
- **Indent detection via editorconfig first**: `propose_repair` reads
  `.editorconfig` if present; majority-vote only as fallback.
- **`rc explain <id>` added to File Ownership Summary** + Phase 1 subparser
  list (was implicit in audit-log Phase from iter-2; now explicit because
  Phase 3 stderr trailer points operators at it).
- **Corpus version pin location specified**: `rules.yaml` top-level field
  `corpus_version: v1`; engine constant `_REQUIRED_CORPUS_VERSION = "v1"`;
  mismatch -> `_exit(2)` with explicit message.

MEDIUM:
- **Phase 0 LOC target relaxed to <550** (math: 758-230 extracted ≈ 528)
- **`--no-backbone` auto-fallback emits stderr banner**: `[RC] Backbone
  unavailable - running structural-only. Run 'rc prefetch-mamba' for full
  novelty signal.`
- **`RC_RULE_ENGINE_STRICT` polarity flipped**: now `RC_RULE_ENGINE_LENIENT=1`
  (=disable strict mode), keeping the natural `RC_*_ENABLED=1` polarity.
- **JSON-not-JSONC sentinel mechanism**: instead of in-file comments,
  `npx reasoning-core init` writes a separate `~/.claude/settings.local.reasoning-core.json`
  managed file + a one-line `"include"` in the operator's settings.local.json.
  Idempotent: rewriting only the managed file leaves operator's file alone.
- **TTFV CI runs BOTH variants explicitly**: matrix in eval.yml runs
  `--variant=cold` AND `--variant=warm` jobs.
- **`pytest --collect-only` labeled "import-graph proxy"**: success criterion
  no longer claims it as a behavioral check; it's an import-break gate
  only.
- **GUARDED_PATHS auto-discovery NEW (Phase 0a)**: tuple now built at
  import time from `glob('src/hooks/*.py')` + `glob('src/_supervisor*.py')`
  + explicit list of cross-cutting files. Eliminates manual-curation smell.
- **TTFV smoke test repo "vue" replaced**: per recent commit `e13bdad`
  Vue routes through HTML grammar; vue is no longer a meaningful 3rd test.
  Replace with `httpx` (Python, growing repo with structural drift events).

LOW:
- **Phase 0 atomicity**: single commit for `_files_touched -> files_touched`
  rename + `eval/calibration_corpus.py` call-site update; deprecation shim
  not needed (Phase 0 is a cohesive PR).
- **npm publishing config**: explicit rows for `.npmignore`,
  `package-lock.json`, semver / version-pin policy, `npm publish` workflow,
  auth/2FA documented.
- **`COH_DELTA_EPSILON` chicken-and-egg**: Phase 3 ships with epsilon=0.05
  bootstrap; Phase 3.5 corpus produces the calibrated value; engine reads
  whichever is latest. Bootstrap path explicit.

## Research References

- 4-subagent round-1 codebase analyses (SCR insertion, Mamba-3 feasibility,
  symbolic gating, TTFV+viz)
- 4-subagent round-1 plan reviews (LLM-sci, agent-harness, senior-dev, auditor)
- 4-subagent round-2 plan reviews (verifying v2, surfacing v3 needs)
- Iter-2 plan tracker rows 41-86

## Falsifiable success criteria (pre-registered, v3)

- **SCR**: in shadow, >=30% of regression_detected blocks produce a repair
  that passes both per-dim Pareto-dominance AND semantic safety net (ruff
  clean OR pyright clean OR safety-net unavailable -> advisory pass);
  0 repaired-then-merged commits show post-hoc regression on labeled-corpus
  FPR within 2 weeks. Cross-family judge re-fit kappa >=0.6 with CI lower
  >=0.5 before SCR promotes from advisory-only.
- **ADR**: dogfood rules catch >=3 historical bug classes; n>=100 per rule
  type required for promotion; precision >=0.90 *point estimate* with
  Wilson 95% CI lower bound >= 0.85; recall >=0.80 lower bound reported.
- **TTFV**: `scripts/ttfv-smoke.sh` exits 0 in <900s on 3 third-party repos
  (django, requests, httpx). Both `--variant=cold` (`--no-backbone`) and
  `--variant=warm` run as a CI matrix.
- **Mamba-3**: AND-conjunctive: HF checkpoint exists AND AIS distribution
  shift on 200-edit benign corpus is < +/-0.05 vs Mamba-130m baseline AND
  re-fit calibration kappa >= 0.6 on v3 cross-family dataset (Phase 3.5).

---

## Phase 0: Refactor lift (Day 0-1 of Week 1; ~1 day)

### Phase 0a: Split pre_edit_guard.py + auto-discover GUARDED_PATHS

#### File: `src/hooks/_guard_paths.py` (new, ~100 LOC)
- v3 fix: build GUARDED_PATHS at import time:
  ```python
  GUARDED_PATHS: tuple[str, ...] = tuple(sorted({
      *(f"/{p.relative_to(REPO_ROOT)}" for p in REPO_ROOT.glob("src/hooks/*.py")),
      *(f"/{p.relative_to(REPO_ROOT)}" for p in REPO_ROOT.glob("src/_supervisor*.py")),
      *_EXPLICIT_GUARDED,  # rules.yaml, settings.json, sidecar core, etc.
  }))
  ```
- `_path_is_guarded(file_path)` matcher
- Helper for override env vars (`RC_ALLOW_GUARD_EDIT`, `RC_ALLOW_RULES_EDIT`,
  `RC_ALLOW_SUBAGENT_GUARD_EDIT`)

#### File: `src/hooks/_dispatch.py` (new, ~150 LOC)
- Per-feature gate chain: lang-lock → mock-detector → drift-gate →
  calibration-gate → (Phase 2) rule-engine → (Phase 3) SCR
- Each gate returns `(action, report_update)` where action ∈
  {"continue", "block", "advisory"}

### Phase 0b: Promote `_files_touched` to public

#### File: `src/git_utils.py` (new, ~80 LOC)
- `files_touched(repo_root: Path, sha: str) -> list[FileChange]`
- Move amend / merge / cross-file-revert filter helpers
- `cat_file_at_sha(repo_root, sha, path)` for `before_src` / `after_src`

#### File: `eval/calibration_corpus.py`
- Replace 3 internal call sites (lines 110, 111, 124) with
  `from src.git_utils import files_touched`
- Delete the private `_files_touched`
- v3: single commit (atomic; no deprecation shim needed)

### Phase 0 Success Criteria

#### Automated
- [ ] `pytest -q -m "not live"` count unchanged
- [ ] `pre_edit_guard.py` LOC drops below 550 (v3-relaxed from 500)
- [ ] `from src.git_utils import files_touched` works from `eval/` and
      from `src/rc_audit_history.py`
- [ ] CI stays green
- [ ] Auto-discovered GUARDED_PATHS picks up new hook helpers without manual edit

#### Manual
- [ ] No behavior change visible to operator (golden-edit smoke clean)

### Dependencies
- Requires: nothing
- Blocks: Phase 1a, Phase 2, Phase 3

---

## Phase 1: TTFV + Drift Visualization + Installer + Proof-of-Utility (Week 1; ~3-4 days after Phase 0)

### Phase 1a: rc audit-history retroactive replay

#### File: `src/rc_audit_history.py` (new, ~250 LOC)
- Reuses `src.git_utils.files_touched`
- Two execution modes: in-process + `--via-sidecar`
- `--no-backbone` shortcut: AIS reported as `"partial"` (not numeric);
  novelty unavailable; structural dims only
- v3 default behavior: when no sidecar AND no cached weights, `--no-backbone`
  auto-engages AND emits stderr banner:
  `[RC] Backbone unavailable - running structural-only. Run 'rc prefetch-mamba' for full novelty signal.`

#### File: `src/rc_cli.py`
- Register `audit-history` subparser
- v3: also register `prefetch-mamba` subparser (fetches weights without
  starting sidecar; surfaces the action the banner suggests)

### Phase 1b: rc viz Mermaid drift dashboard

#### File: `src/rc_viz.py` (new, ~200 LOC)
- Mermaid xychart-beta sparkline + per-day heatmap
- **Audit schema additions: NONE** (uses existing fields).
- v3 explicit: Phase 3d uses existing `signal_source` column with new
  *value* "scr_iter"; no new column.
- GHE compatibility: `--mermaid-version 8` flag emits markdown-table fallback

#### File: `src/rc_cli.py`
- Register `viz` subparser

### Phase 1c: TTFV smoke test (CI gate, both variants)

#### File: `scripts/ttfv-smoke.sh` (new, ~50 LOC)
- v3 explicit CI matrix:
  ```yaml
  strategy:
    matrix:
      variant: [cold, warm]
  ```
- Variant=cold: `--no-backbone` path; <900s wall
- Variant=warm: full path with cached weights; <900s wall
- Test repos: django, requests, **httpx** (was vue; per `e13bdad` Vue
  routes through HTML, no longer a meaningful 3rd test)
- CI: nightly cron in `.github/workflows/eval.yml`, NOT per-PR

### Phase 1d: One-line installer (npx reasoning-core init)

#### File: `npm/reasoning-core-init/package.json` (new)
#### File: `npm/reasoning-core-init/.npmignore` (new) — ship only `bin/`, `README.md`, `package.json`
#### File: `npm/reasoning-core-init/package-lock.json` (committed for reproducible installs)
#### File: `npm/reasoning-core-init/bin/init.js` (new, ~150 LOC)
#### File: `.github/workflows/npm-publish.yml` (new) — trigger on tag push,
  uses `NPM_TOKEN` secret + 2FA-enforced npm account
#### File: `npm/reasoning-core-init/README.md` (new) — install + usage + uninstall

#### v3 idempotency mechanism (JSON-safe; replaces sentinel-comment approach)
- Installer writes `~/.claude/settings.local.reasoning-core.json` (managed file)
- Adds (or verifies present) a single JSON-safe `"$reasoning_core_managed": true`
  marker key in the operator's existing `~/.claude/settings.local.json` plus a
  one-line `"include": "settings.local.reasoning-core.json"` reference.
- On re-run, only the managed file is rewritten; operator's file is touched
  only to add the include reference if missing.
- Uninstall removes the marker + include + managed file.

#### Distribution policy
- Semver: `0.1.0` initial; minor bumps for new hook events; major bumps for
  breaking settings.local.json schema changes
- 2FA on npm account; `npm publish --access public` via tag-triggered workflow
- No hand-publish (workflow is the only path)

### Phase 1e: 50-commit Proof-of-Utility audit (extends 1a)

#### Extension to `src/rc_audit_history.py`
- New `--mode proof-of-utility` flag; `--commits 50` default in this mode
- Marketing-grade markdown: SUMMARY block (would-block / would-warn /
  allowed counts + percentages); top-N regressions; privacy-redacted
  (no full file contents)

### Phase 1f: rc explain (v3 NEW, surfaced by SCR stderr trailer)

#### File: `src/rc_cli.py`
- Register `explain` subparser: `rc explain <decision_id>`
- Reads main JSONL audit log + scr-iter ring buffer; reconstructs the
  full decision (orig risk vector, all SCR iter candidates, accepted repair,
  Pareto verdict, safety-net result)
- Output: structured markdown block

### Phase 1 Success Criteria

#### Automated
- [ ] `pytest -q -m "not live"` passes; +8 new tests (audit-history, viz,
      installer-idempotency, ttfv-smoke matrix, explain)
- [ ] `bash scripts/ttfv-smoke.sh --variant=cold` exits 0 in <900s
- [ ] `bash scripts/ttfv-smoke.sh --variant=warm` exits 0 in <900s
- [ ] Both variants run in CI matrix (verified by green eval-smoke nightly)
- [ ] `npx reasoning-core init` exits 0 on macOS + Linux (CI matrix)
- [ ] Re-running `npx reasoning-core init` does NOT double-register hooks
      (managed file rewritten, operator's settings.local.json untouched
      after first install)
- [ ] `--no-backbone` auto-engages emits the stderr banner (regex test)
- [ ] `rc explain <decision_id>` reconstructs SCR + main rows correctly
- [ ] CI workflow adds nightly `ttfv-smoke` job

#### Manual (with pre-registered protocol)
- [ ] Cold-box demo on macOS: 2 raters independently time `npx reasoning-core
      init` -> open proof-of-utility report; both report <15min wall-clock;
      Cohen's kappa >=0.8 on a 10-step rubric
- [ ] Proof-of-utility report on django shows >=3 historical commits with
      flagged risk-vector regressions

### Dependencies (v3)
- Hard requires: Phase 0
- Sequencing preference (NOT a hard dep): land before Phase 2-3

---

## Phase 2: Hybrid Symbolic Gating (Python-only v1; ~5 days)

### Phase 2a: Rule engine core

#### File: `.reasoning-core/rules.schema.yaml` (new, git-tracked)
- v1 rule types: `forbid_import` + `forbid_pattern`
- v1 languages: Python only via stdlib `ast`
- Severity: `deny | warn | shadow`
- Top-level field `corpus_version: v1` (v3 NEW per senior-dev N1)

#### File: `src/hooks/_rule_engine.py` (new, ~700 LOC realistic)
- `load_rules(project_root)` (mtime-cached)
- `evaluate_edit(file_path, before_src, after_src, lang, rules) -> list[RuleHit]`
- Python AST walker: `extract_imports` via stdlib `ast`
- v3 NEW: `_REQUIRED_CORPUS_VERSION = "v1"`; if `rules.yaml.corpus_version`
  doesn't match → `_exit(2)` with explicit message
- Performance budget: <=5 ms/rule, hard cap 50 rules

#### File: `src/hooks/_dispatch.py` (Phase 0 extension)
- Rule-engine slot in chain after mock-detector

#### File: `src/hooks/_guard_paths.py` (Phase 0 auto-discovery)
- Adds `/.reasoning-core/rules.yaml` + `/.reasoning-core/rules.schema.yaml`
  to the explicit list (auto-discovery only covers src/hooks/ and
  src/_supervisor*.py)
- New env var `RC_ALLOW_RULES_EDIT=1` operator override

#### Schema-error posture (v3 polarity-fixed)
- v3: default = strict (rules.yaml schema error → `_exit(2)` with
  "rules.yaml invalid: <msg>"; hook fail-closed because operator's intent
  is to enforce)
- `RC_RULE_ENGINE_LENIENT=1` (NEW, replaces `RC_RULE_ENGINE_STRICT`) reverts
  to fail-open with `_warn_once` (escape hatch matching natural
  `RC_*_ENABLED=1` polarity)
- v3 audit row when LENIENT=1 is honored: `signal_source="rule_engine_lenient"`
  emitted on every edit so the silent-fail-open posture is observable

### Phase 2b: Dogfood rules + corpus eval

#### File: `.reasoning-core/rules.yaml` (new, git-tracked, NOT in .gitignore)
- v1 ships 2 dogfood rules:
  1. `forbid_import`: src/hooks/** MUST NOT import src/sidecar_supervisor
  2. `forbid_pattern`: no subprocess.run(..., shell=True) anywhere in src/
- Top-level: `corpus_version: v1`

#### File: `eval/datasets/rule_corpus/v1/` (new, versioned)
#### File: `eval/datasets/rule_corpus/build.py` (new, ~100 LOC)
- Generator: hand-curated 100 paired (clean, violating) snippets per rule type
- Re-runnable (deterministic seed=42); produces stable corpus at v1
- v3 spec: required precision >=0.90 *point estimate* with Wilson 95% CI
  lower bound >= 0.85; recall >=0.80 lower bound reported

### Phase 2c: Magic-comment + CLI integration

- Per-rule bypass: `# rc:skip-rule:<id>` honored ONLY when the comment
  exists in `before_src`. If the comment appears in `after_src` but did
  NOT exist in `before_src` (i.e., the agent added it in this edit), DENY
  the bypass and treat as a violation. Operator-authored bypasses
  (which existed before the edit) are honored; agent-self-introduced
  bypasses are rejected.
- New CLI: `rc rules explain <file>` and `rc rules eval`

### Phase 2 Success Criteria

#### Automated
- [ ] `tests/test_rule_engine.py` (flat tests/ dir): table-driven
- [ ] `RC_RULE_ENGINE=1` + violating edit → exit 2; clean → exit 0
- [ ] Editing `.reasoning-core/rules.yaml` without `RC_ALLOW_RULES_EDIT=1`
      is denied
- [ ] Schema error in rules.yaml → exit 2 with explicit message
- [ ] `RC_RULE_ENGINE_LENIENT=1` + schema error → warn-only, hook continues,
      audit row emitted
- [ ] `corpus_version` mismatch in rules.yaml → exit 2
- [ ] Per-rule precision >=0.90 / recall >=0.80 on rule_corpus/v1/, with
      Wilson 95% CI lower bound >= 0.85 (precision) reported. **Note**: at n=100, this requires k>=92/100 observed (k=90 yields Wilson lower ~0.825, just below the gate). Promotion criterion is explicit, not silently wrong
- [ ] Per-edit rule-engine wall-time <=50ms p99 (warm grammar)

#### Manual
- [ ] >=1 dogfood rule fires on a real historical commit
- [ ] `rc rules explain src/s2_core.py` produces a readable list

### Dependencies
- Hard requires: Phase 0
- Sequencing preference: TTFV proven first

---

## Phase 3: S-C-R loop with per-dim Pareto + semantic safety net (~5-7 days)

### Phase 3a: Generative repair head with normalization

#### File: `src/gen_client.py`
- New `propose_repair(file_path, before_src, after_src, risk_vector,
  human_summary, *, model=None, budget_ms=4000, max_tokens=2048) -> Optional[str]`
- v3 normalization (per AH N2 + senior-dev #5):
  1. Read `.editorconfig` for the file's directory; honor `indent_style` /
     `indent_size` / `end_of_line` if present
  2. Else detect indent from before_src (majority vote across non-blank lines)
  3. Detect line-ending policy: if before_src is mixed LF+CRLF, error out
     (refuse the file; log telemetry for follow-up). Pure-LF or pure-CRLF
     is preserved.
  4. Detect final-newline policy from before_src
  5. Strip code fences, validate non-empty
  6. Apply normalized indent / line-ending / final-newline to candidate
  7. Validate len ratio in [0.3, 3.0]; LOG telemetry on rejection
- Auth: existing Bearer auth

### Phase 3b: SCR loop helper

#### File: `src/hooks/_scr_loop.py` (new, ~300 LOC)
- `try_scr_repair(*, file_path, before_src, after_src, initial_report,
  max_iters=None, wall_budget_ms=8000) -> RepairResult`
- Default max_iters: **1** (per senior-dev H4); `RC_SCR_DEEP=1` opens 2-3

#### v3 acceptance rule (per-dim Pareto, mixed-magnitude safe; LLM-sci):
```python
TOLERANCE = np.maximum(orig_rv * 0.01, 1e-3)  # 1% relative + 1e-3 floor
pareto_ok = np.all(new_rv <= orig_rv + TOLERANCE)
coh_ok    = coh_new <= coh_orig + COH_DELTA_EPSILON
sem_ok    = semantic_safety_net(file_path, candidate)  # Phase 3d
accepted  = (not new_report.regression_detected) and pareto_ok and coh_ok and sem_ok
```

#### COH_DELTA_EPSILON bootstrap (v3):
- Phase 3 ships with epsilon = 0.05 (bootstrap; matches existing risk-table
  defaults)
- Phase 3.5 corpus produces a calibrated value: 1-sigma of `coherence_delta`
  on the v3 cross-family benign-edit subset
- Engine reads `eval/runs/coh_delta_epsilon.json` (mtime-cached); falls
  back to 0.05 if file missing
- v3 explicit: SCR shadow-window data does NOT auto-promote epsilon —
  Phase 3.5 must produce it independently to break the chicken-and-egg

#### Other safeguards (carry-over from v2):
- Anti-oscillation: hash each candidate; abort on repeat
- Cold-start skip: skip when initial_report.cold_start is True
- Kappa-gate guard: requires sentinel gate_pass=True AND model_id matches
  RC_GEN_MODEL AND v3 cross-family validation present

#### File: `src/hooks/_dispatch.py` (Phase 0 extension)
- SCR slot in chain after the regression-detected branch
- Pre-emptive checks: shadow-mode and calibration-only anomaly skip SCR

### Phase 3c: kappa-sentinel `model_id` field

#### File: `eval/qwen_grounding_eval.py`
- Include `model_id` (read from `RC_GEN_MODEL`) in result JSON + sentinel
- Backwards-compat: missing model_id → fail-open (skip SCR)

#### File: `src/hooks/_plan_quality.py::_kappa_gate_passed`
- Extend signature: `_kappa_gate_passed(model_id: Optional[str] = None) -> bool`

### Phase 3d: Semantic safety net + stderr truncation + telemetry

#### Semantic safety net (per LLM-sci C3 + senior-dev N3 + AH N1)
- For Python: `ruff check --quiet <tmp>` AND `pyright --outputjson <tmp>`
- Wall budget for safety net: **3s**, included in the 8s SCR wall budget
- v3 latency budget tracked as separate p99 metric (success criterion below)
- v3 missing-binary fallback (per senior-dev N3): if `pyright` or `ruff`
  not on PATH, treat as advisory pass (logged with
  `signal_source="safety_net_unavailable"`); log + continue rather than
  block legitimate repairs
- v3 honest naming: `pytest --collect-only` is **not** in safety net;
  it's an "import-graph proxy" gate run BEFORE the safety net (per LLM-sci),
  acknowledged not to catch behavioral regressions

#### Stderr truncation contract (v2 carry-over)
- `MAX_STDERR_BYTES = 8192`
- Full repair text + risk-vector deltas written to **main JSONL** keyed by
  `decision_id` (NOT scr-iter ring buffer; v3 explicit per AH partial
  on round-2 #1)
- scr-iter ring buffer holds REJECTED iter candidates only (excluded
  from FPR analytics)
- Stderr trailer: `[truncated; full repair at audit:<decision_id> -- run 'rc explain <id>']`

#### v3 implementation specs (per AH/SD round-2):
- `S2_TIMEOUT=12s` scoped to **the SCR call only** via urllib.request
  `timeout=12` kwarg (NOT signal.alarm — process-global signals are unsafe
  in PreToolUse hook)
- Stderr magic-prefix: `[RC-SCR-REPAIR <decision_id>]\n` (mandatory header)
- Audit-row inflation mitigation: scr-iter rows go to
  `~/.local/share/reasoning-core/events/<date>/scr-iter.jsonl` with own
  500MB cap. Rotation logic owned by `src/hooks/_scr_loop.py::_rotate_ring_buffer(path, max_bytes=500*1024*1024)`
  invoked on every SCR-iter write (cheap mtime+size check). When cap hit,
  rename to `.1` and start fresh; oldest .1 evicted. **Multi-session
  durability**: this ring is advisory-grade only — two concurrent Claude
  Code sessions on the same repo MAY interleave a few rows during rotation
  (acceptable since rejected SCR iters are excluded from FPR analytics
  and never auth-bearing). Main JSONL audit log is NOT subject to this
  caveat; only the scr-iter ring buffer. Concurrent-rotation
  race: low likelihood given PreToolUse hook serialization (one hook
  process at a time per session), but use `os.replace` (atomic) and
  catch FileNotFoundError on the rename in case a sibling already rotated.

### Phase 3 promotion criteria (v3 tightened)
SCR exits advisory-only and starts auto-applying repairs ONLY when ALL hold:
1. Phase 3.5 v3 cross-family judge dataset shipped (devstral + llama-3.3-70b
   + non-coder mistral-small majority vote)
2. Cross-family judge pairwise kappa < 0.7 (independence test)
3. Re-fit kappa >= 0.6 with 95% CI lower bound >= 0.5 on v3 dataset
4. Phase 3.6 30-pair operator-review ground-truth subset shipped
5. **Phase 3.6 qualification eval**: run SCR against the 30 `buggy_after`
   inputs from Phase 3.6; >=80% of SCR-accepted repairs satisfy BOTH
   (a) Pareto-dominant vs `buggy_after` on risk-vector (i.e., SCR genuinely
       improved on the bug), AND
   (b) Pareto-non-worse vs `fixed_after` on risk-vector with the SAME per-dim
       tolerance Phase 3b uses: `TOLERANCE = np.maximum(fixed_after_rv * 0.01,
       1e-3)`. Coherence_delta separately bounded by COH_DELTA_EPSILON
       (the calibrated scalar from Phase 3.5d).
       Round-5 fix: original wording overloaded `COH_DELTA_EPSILON` (a coherence
       scalar) onto per-dim risk-vector comparison — category error. Now matches
       Phase 3b acceptance rule split (per-dim TOLERANCE + scalar epsilon).
   AND pass the semantic safety net.
   Senior-dev round-4 fix: original wording demanded SCR Pareto-DOMINATE
   `fixed_after`, which is unachievable since `fixed_after` is the human
   gold reference. Corrected to non-worse vs fixed_after + dominant vs
   buggy_after — auto-apply gate is now reachable.
   Pre-flight: before promotion criterion #5 is locked, verify >=25/30
   `fixed_after` Pareto-dominate `buggy_after` under current sidecar (auditor
   tracking item); if <25, reduce promotion threshold from >=80% to >=70%.
   **Round-5 fix (LLM-sci minimum-n floor)**: require >=20 SCR-accepted of the
   30 corpus pairs before computing the promotion ratio. With <20 accepted,
   the 80%-of-N statistic is too noisy at small N; instead, extend the
   shadow window until >=20 acceptances OR formally fail promotion.
6. Shadow window: 30 SCR-accepted repairs (was 50 SCR triggers; bumped per
   LLM-sci to support stable Cohen's kappa between 2 raters)

### Phase 3 Success Criteria

#### Automated
- [ ] `tests/test_scr_loop.py`: per-dim Pareto rejection (mixed-magnitude
      regression), accepts dominant improvements, respects budget,
      oscillation aborts, fails open on kappa-gate=false / model_id
      mismatch / backend unreachable
- [ ] `tests/test_scr_anti_regression.py`: known-bad before/after, mock
      `propose_repair` returns *different but worse on novelty (1e-2)*,
      SCR rejects (proves per-dim relative tolerance works)
- [ ] `tests/test_scr_safety_net.py`: ruff-failing → reject; pyright-failing
      → reject; `ruff` missing-binary → advisory pass with audit row;
      `pyright` missing-binary → advisory pass
- [ ] `tests/test_scr_normalization.py`: editorconfig wins; majority-vote
      fallback works; mixed LF+CRLF before_src → repair refused with telemetry
- [ ] `tests/test_scr_stderr_truncation.py`: 16KB repair → 8KB clip with
      audit:<id> trailer; `rc explain <id>` retrieves full text from main JSONL
- [ ] Hot-path p99 <=11s with `RC_SCR_ENABLED=1` (1 iter default)
- [ ] Safety-net p99 <=3s (separately measured; pyright cold-start excluded
      via warm mode)
- [ ] No Bearer-token leak in stderr (regex-grep test)
- [ ] Cross-version kappa-sentinel test: v1-format sentinel (no model_id)
      read by v3 SCR → fail-open, no crash

#### Manual (pre-registered 2-rater protocol, v3 with adequate n)
- [ ] On 30 SCR-accepted repairs across the shadow window: 2 raters
      independently label each as "better / same / worse"; Cohen's kappa
      >=0.7 between raters (n=30 supports stable kappa); >=80% labeled
      "better" by both raters

### Dependencies (v3)
- Hard requires: Phase 0; Phase 3.5 corpus before promotion from advisory-only;
  Phase 3.6 ground-truth labeling before auto-apply
- Sequencing preference: land after Phase 1-2 for telemetry
- Tracker rows: closes #57 (re-introduce score_with_iteration with consumer)

---

## Phase 3.5: v3 cross-family kappa dataset (NEW in v3; ~3-4 days)

Per round-2 auditor: this was the biggest gap — referenced as gating
SCR auto-apply, Mamba-3 cutover, COH_DELTA_EPSILON calibration, but had
no phase, no owner, no file paths. Now explicit.

### Phase 3.5a: Judge selection + independence test

#### Pre-registered judge model IDs (Scaleway-hosted)
- **J1**: `devstral-2-123b-instruct-2512` (coder family — same as v2 used)
- **J2**: `llama-3.3-70b-instruct` (general / Meta, NOT coder-tuned)
- **J3**: `mistral-small-3.2-24b-instruct-2506` (general / Mistral, NOT
  coder-tuned, smaller)
- v3 NEW: pre-registered independence test — pairwise Cohen's kappa
  between J1/J2/J3 must be **< 0.7** on a 50-pair pilot subset; if
  violated, swap one judge

### Phase 3.5b: Dataset construction

#### File: `eval/datasets/grounding_pairs_v3.jsonl` (new)
- Source: same 200-pair raw corpus that produced v2
- Re-label using majority-vote across J1/J2/J3 (with the rubric prompt
  from `_GROUNDING_PROMPT`)
- Keep only pairs where 2 of 3 judges agree → "high-confidence v3 set"
- Expected size: ~150-180 pairs (judge agreement higher than single-judge
  filter in v2 since 2-of-3 is more permissive than 1-of-1 strict match)
- **Pre-approved cost cap**: 200 pairs x 3 judges x ~3s/call = ~30 min
  wall on Scaleway; estimated $5-10 in API spend. Operator-approved budget
  ceiling: $20. Phase 3.5 must abort + report if real cost > $20.
  **Owner**: `eval/relabel_grounding_pairs_v3.py` tracks cumulative API
  calls; on each batch, computes estimated cost (calls * avg-tokens *
  Scaleway-price-per-1k); raises SystemExit when threshold crossed.

#### File: `eval/relabel_grounding_pairs_v3.py` (new, ~150 LOC)
- Parameterized over judge list; default = pre-registered J1/J2/J3
- Writes per-judge intermediate + majority-vote final
- Idempotent (resumable via existing `.partial.jsonl` mechanism)

### Phase 3.5c: Re-fit + sentinel

#### File: `eval/qwen_grounding_eval.py`
- Run against `grounding_pairs_v3.jsonl`; produce
  `eval/runs/qwen_grounding_v3_<date>.json`
- Sentinel update: `eval/runs/qwen_kappa_gate.json` carries
  `{"gate_pass", "kappa", "model_id", "dataset_version": "v3"}`

### Phase 3.5d: COH_DELTA_EPSILON calibration

#### File: `eval/runs/coh_delta_epsilon.json` (NEW)
- Compute 1-sigma of `coherence_delta` on the 200-edit benign-edit subset
  (subset of v3 dataset where teacher_label=0 / non-regression)
- Write `{"epsilon": <1sigma>, "n": <count>, "computed_at": <ts>}`
- SCR loop reads this; falls back to 0.05 bootstrap if missing

### Phase 3.5 Success Criteria

#### Automated
- [ ] `eval/datasets/grounding_pairs_v3.jsonl` shipped (≥150 pairs)
- [ ] Pairwise judge kappa < 0.7 (independence verified) on a 50-pair pilot
- [ ] Re-fit kappa >= 0.6 with 95% CI lower bound >= 0.5 on v3
- [ ] `coh_delta_epsilon.json` present with explicit `n` and `epsilon`
- [ ] Sentinel `dataset_version: v3`

### Dependencies
- Hard requires: nothing (independent of Phase 0-3 code; just needs Scaleway
  + scw cli)
- Blocks: SCR auto-apply (Phase 3 promotion criteria), Mamba-3 trigger
  condition (c)

---

## Phase 3.6: 30-pair post-incident ground-truth corpus (NEW in v3; ~2 days)

Per round-2 auditor: Phase 3 promotion criterion `>=80% of SCR-accepted
repairs match post-incident ground truth on 30-pair subset` had no source.
Now: explicit corpus with pre-registered labeling protocol.

### Phase 3.6a: Source selection

#### File: `eval/datasets/scr_ground_truth_v1/` (new)
- Source: 30 historical repos commits where `git log --grep="fix\|revert"`
  shows a buggy commit followed by a "what should have shipped" fix commit
  in this repo's own history
- Constraint: each pair must have a sidecar-scored before/after
  triggering `regression_detected=True` on the buggy commit
- Hand-curated: jakub picks 30 with documented "what went wrong" trail
- **Fallback if <30 available in this repo's history** (senior-dev round-3 V1
  flag — repo is young): augment from `circit-app` git history (>=1k commits,
  multi-year, jakub has commit access) keeping same selection criteria.
  Pre-flight check at start of Phase 3.6a: count viable pairs in this repo;
  if <30, draw remainder from circit-app.
  **Augmentation cap (auditor round-5)**: <=50% of corpus (i.e., <=15/30
  pairs) may come from circit-app, so the qualification eval stays anchored
  to reasoning-core domain conventions. If a literal interpretation would
  require >15 from circit-app, fail the pre-flight and fall back to
  threshold reduction (>=70%) instead.
  **CI portability**: corpus rows store inlined `{before, buggy_after,
  fixed_after}` snapshots; SHAs are provenance only. CI does NOT clone
  circit-app — only the operator's local pre-flight needs that access.
- Each pair: `{commit_sha_buggy, commit_sha_fixed, file, before, buggy_after,
  fixed_after, ground_truth_label}`

### Phase 3.6b: 2-rater labeling protocol (pre-registered)

#### Protocol
- Rater 1: jakub (operator)
- Rater 2: choose ONE of (in priority order):
  (a) jakub's named peer reviewer X (pre-committed, no budget)
  (b) Prolific / Mechanical-Turk labeling task with $200 budget cap (24h SLA)
  (c) 2nd reasoning-core maintainer when team grows past 1 (deferred)
  Deadline: rater 2 must be confirmed by end of Week 3 or Phase 3.6
  qualification slips one week
- Each rater independently labels each of the 30 pairs:
  `{"buggy", "fixed", "neutral"}` for whether `fixed_after` semantically
  matches the post-incident "what should have shipped"
- Inter-rater Cohen's kappa >=0.7 required; if below, retrospective
  arbitration session + relabel
- Final label = majority vote (or arbitration result)

#### File: `eval/datasets/scr_ground_truth_v1/protocol.md` (new, ~50 LOC)
- Pre-registers the protocol BEFORE labeling begins

### Phase 3.6 Success Criteria

#### Automated
- [ ] `eval/datasets/scr_ground_truth_v1/pairs.jsonl` shipped (n=30)
- [ ] `protocol.md` shipped + signed by both raters before labeling

#### Manual
- [ ] 2-rater Cohen's kappa >= 0.7 on labels
- [ ] All 30 pairs have `ground_truth_label` set

### Dependencies
- Hard requires: nothing (operates on this repo's git history)
- Blocks: SCR auto-apply (promotion criterion #5)

---

## Phase 4: Mamba-3 watch + Plan-B Mamba-2-2.7B (~1-2 days)

(Unchanged from v2; sections kept brief for context.)

### Phase 4a: Plan-B fallback

#### File: `src/ssm_backbone.py`
- Extend DEFAULT_CHECKPOINT fallback chain to include `state-spaces/mamba2-2.7b`
- Trigger: env `RC_USE_MAMBA2_2_7B=1`

#### File: `eval/scripts/prefetch_mamba.sh`
- Honor `RC_MAMBA_REPO=state-spaces/mamba2-2.7b` + `RC_MAMBA_SHA256` env

### Phase 4b: Mamba-3 watch tracker (AND-conjunctive criteria)

#### File: `thoughts/shared/plans/2026-05-06-iter2-100pct-eval-plan.md`
- Tracker row #87: ALL THREE must hold:
  (a) HF checkpoint published, (b) AIS distribution shift <+/-0.05 vs
  Mamba-130m on 200-edit benign corpus, (c) re-fit kappa >=0.6 on Phase
  3.5 v3 cross-family dataset

### Phase 4 Success Criteria
- [ ] `RC_USE_MAMBA2_2_7B=1 ./scripts/start-sidecar.sh` boots on CI Linux
- [ ] No regression on `pytest -q -m "not live"` with Plan-B path inactive

---

## Risk Assessment (v3)

(Adds rows for v3-specific concerns; v2 rows preserved.)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SCR Pareto-worse repair slips through | Med | High | v3 per-dim relative tolerance: `np.maximum(orig*0.01, 1e-3)` |
| SCR semantically broken repair pasted by agent | Med | Critical | v3 safety net (ruff/pyright); missing-binary → advisory pass + audit |
| **Prompt injection via `before_src`** (input-side, NOT output) | Med | High | **v3 PRIMARY DEFENSE: Pareto + safety-net gate (output-side validation cannot be defeated by injected instructions). Defense-in-depth: input scrub for `# IGNORE PRIOR` etc. is supplemental.** |
| kappa=0.74 contamination silently inherited | Med | Critical | v3 promotion: Phase 3.5 cross-family + judge independence test + kappa>=0.6 CI lower>=0.5 BEFORE auto-apply |
| Stderr truncation clips repair mid-code | High | Critical | MAX_STDERR_BYTES=8192; full text in MAIN audit JSONL (NOT scr-iter ring); trailer points to `rc explain <id>` |
| rules.yaml typo silently disables enforcement | High | Critical | Schema error → exit 2 + explicit message; `RC_RULE_ENGINE_LENIENT=1` escape hatch with audit row on every honored fail-open |
| Corpus version pin missing | Med | High | v3: `corpus_version: v1` in rules.yaml + `_REQUIRED_CORPUS_VERSION` engine constant; mismatch → exit 2 |
| pre_edit_guard.py too large to test | High | High | v2/v3 Phase 0a: split into _guard_paths + _dispatch; auto-discovered GUARDED_PATHS |
| `_files_touched` private layering rot | Med | Med | v2 Phase 0b: promote to src/git_utils.files_touched (single atomic commit) |
| SCR loop adds latency that trips PreToolUse timeout | Med | High | max_iters=1 default; RC_SCR_DEEP=1 opens 2-3; S2_TIMEOUT=12s scoped to SCR call (urllib `timeout=12` kwarg, NOT signal.alarm) |
| Pyright cold-start eats safety-net 3s budget | Med | Med | v3 separate p99 metric; missing-binary fallback to advisory; pyright warm-cache strategy in start-sidecar.sh (deferred but tracked) |
| Rule corpus n=100 + CI ambiguity | Med | Med | v3: Wilson 95% CI lower bound reported; promotion requires CI-lower >= 0.85 (n=100 sufficient) |
| 2-rater kappa with n=5 underpowered | High | High | v3: bumped to n=30 SCR-accepted repairs (matches stable Cohen's kappa needs) |
| Cross-family judge collinearity (J1+J2+J3 all transformer-decoder) | Med | High | v3: pre-registered pairwise kappa < 0.7 independence test; if violated, swap |
| `--no-backbone` auto-fallback hides core value | Med | High | v3: stderr banner `[RC] Backbone unavailable - run 'rc prefetch-mamba'`; CLI shows partial AIS not silent zero |
| settings.local.json sentinel-comment idempotency | High | Med | v3: separate managed file `~/.claude/settings.local.reasoning-core.json` + JSON-safe `"$reasoning_core_managed": true` marker |
| Indent flips on mixed legacy files | Med | Med | v3: `.editorconfig` first; majority-vote fallback; mixed LF+CRLF in before_src → refuse repair with telemetry |
| Audit-row inflation hits 5GB cap | Med | Med | scr-iter rows in separate ring buffer; main JSONL keeps only the 1 ACCEPTED repair text per blocked edit |
| `# rc:skip-rule` agent-introduced bypass | Med | High | Provenance check `comment in after AND comment not in before` → deny |
| Bypass-knob inflation (5 NEW knobs) | Med | Med | v3: `RC_RULE_ENGINE_LENIENT` polarity matches existing `RC_*_ENABLED` convention; `rc config show` deferred to v4 (open question) |
| TTFV smoke vue is no longer Vue-routed | Low | Low | v3: replaced with httpx (Python, growing repo with structural drift events) |
| npm publishing security | Low | High | v3: 2FA-enforced npm account, tag-triggered workflow only, NPM_TOKEN in GH secret |

## Rollback Strategy

| Phase | Kill switch |
|---|---|
| Phase 0 | revert commit (refactor; no env knob) |
| Phase 1 | n/a — opt-in CLIs |
| Phase 2 | `unset RC_RULE_ENGINE` |
| Phase 3 | `unset RC_SCR_ENABLED` |
| Phase 3.5 | n/a — dataset-only |
| Phase 3.6 | n/a — dataset-only |
| Phase 4 | `unset RC_USE_MAMBA2_2_7B` |

Phase 2 corpus versioned (`rule_corpus/v1/`) + engine pins via
`_REQUIRED_CORPUS_VERSION`; mismatch on rollback → exit 2 forces atomic flip.

## File Ownership Summary (v3)

| File | Phase | Change Type |
|---|---|---|
| `src/hooks/_guard_paths.py` | 0a | Create (auto-discovery via glob) |
| `src/hooks/_dispatch.py` | 0a | Create |
| `src/git_utils.py` | 0b | Create |
| `src/hooks/pre_edit_guard.py` | 0a | Modify (slim to orchestrator; <550 LOC) |
| `eval/calibration_corpus.py` | 0b | Modify (use git_utils.files_touched) |
| `src/rc_audit_history.py` | 1a/1e | Create |
| `src/rc_viz.py` | 1b | Create |
| `src/rc_cli.py` | 1a/1b/1f/2c | Modify (add audit-history, viz, explain, prefetch-mamba subparsers) |
| `scripts/ttfv-smoke.sh` | 1c | Create (matrix: cold + warm) |
| `.github/workflows/eval.yml` | 1c | Modify (nightly ttfv-smoke matrix job) |
| `.github/workflows/npm-publish.yml` | 1d | Create (tag-triggered, 2FA-enforced) |
| `npm/reasoning-core-init/package.json` | 1d | Create |
| `npm/reasoning-core-init/.npmignore` | 1d | Create |
| `npm/reasoning-core-init/package-lock.json` | 1d | Create (committed) |
| `npm/reasoning-core-init/bin/init.js` | 1d | Create (writes managed file + marker; no in-file comments) |
| `npm/reasoning-core-init/README.md` | 1d | Create |
| `.reasoning-core/rules.schema.yaml` | 2a | Create (git-tracked; corpus_version field) |
| `.reasoning-core/rules.yaml` | 2b | Create (git-tracked; corpus_version: v1) |
| `src/hooks/_rule_engine.py` | 2a | Create (~700 LOC; corpus version mismatch → exit 2) |
| `eval/datasets/rule_corpus/v1/` | 2b | Create (versioned) |
| `eval/datasets/rule_corpus/build.py` | 2b | Create (deterministic generator) |
| `tests/test_rule_engine.py` | 2 | Create (flat tests/, not tests/hooks/) |
| `tests/test_rc_explain.py` | 1f | Create (verifies main JSONL + scr-iter ring reconstruction) |
| `src/gen_client.py` | 3a | Modify (propose_repair + .editorconfig-aware normalization) |
| `src/hooks/_scr_loop.py` | 3b | Create (per-dim Pareto + safety net) |
| `eval/qwen_grounding_eval.py` | 3c/3.5c | Modify (add model_id + dataset_version fields) |
| `src/hooks/_plan_quality.py` | 3c | Modify (kappa_gate_passed signature) |
| `tests/test_scr_loop.py` | 3 | Create |
| `tests/test_scr_anti_regression.py` | 3 | Create |
| `tests/test_scr_safety_net.py` | 3 | Create |
| `tests/test_scr_normalization.py` | 3 | Create |
| `tests/test_scr_stderr_truncation.py` | 3 | Create |
| **`eval/datasets/grounding_pairs_v3.jsonl`** | **3.5b** | **Create (~150 pairs majority-vote)** |
| **`eval/relabel_grounding_pairs_v3.py`** | **3.5b** | **Create (~150 LOC)** |
| **`eval/runs/coh_delta_epsilon.json`** | **3.5d** | **Create (calibrated 1-sigma)** |
| **`eval/datasets/scr_ground_truth_v1/pairs.jsonl`** | **3.6a** | **Create (n=30)** |
| **`eval/datasets/scr_ground_truth_v1/protocol.md`** | **3.6a** | **Create (pre-registered protocol)** |
| `src/ssm_backbone.py` | 4a | Modify |
| `eval/scripts/prefetch_mamba.sh` | 4a | Modify |
| `thoughts/shared/plans/2026-05-06-iter2-100pct-eval-plan.md` | 4b | Modify (tracker row #87) |

## Sequencing (v3 with Phase 0 folded)

```
Week 1 Days 1-2: Phase 0 (refactor lift)            # blocks everything else
Week 1 Days 3-5: Phase 1 (TTFV + viz + installer + proof-of-utility)
Week 2:          Phase 2 (Python-only rule engine)
Week 3:          Phase 3 a-c (SCR loop)
Week 3-4:        Phase 3.5 (v3 cross-family dataset; runs concurrent)
Week 3-4:        Phase 3.6 (30-pair ground-truth corpus; runs concurrent)
Week 4-5:        Phase 3 shadow window (30 SCR-accepted repairs)
Week 5:          Phase 4 (Mamba-2 Plan B)
Week 6+:         Iter-3 eval (gated on Phases 3.5 + 3.6 + shadow)
```

Total: 5-6 weeks. Phase 0 folded into Week 1 (no separate "Day 0"). Phase
3.5 + 3.6 run concurrent with Phase 3 implementation since they're
data-only (no shared code path).

## Open Questions (v3)

1. **`rc config show` for env-knob discoverability**: with 5 new knobs on
   top of existing 23, is a discoverability CLI worth adding to Phase 1?
   (Operator UX vs scope creep.)
2. **Post-Edit hook for SCR-applied detection**: stderr trailer suggests
   the agent retrieve via `rc explain <id>`. Do we ALSO want a PostToolUse
   hook that diffs the actual edit against the suggested repair to record
   `applied_scr_repair=true`?
3. **`rc viz` web dashboard escape hatch**: forcing-function before adding
   FastAPI? ">=5 distinct operator requests in tracker" pre-registered.
4. **DECIDED (v4)**: Phase 3.5 budget capped at $20 (inline in 3.5b);
   operator pre-approval recorded in plan body.

## Pre-registered acceptance for plan as a whole (v3)

This plan is **architecture, not ablation**. Success measured by iter-3
eval (when it runs), gated on:
- Phase 0-3 + 3.5 + 3.6 shipped
- Green CI for >=3 consecutive commits on main
- Phase 3 shadow window cleared with cross-family kappa >=0.6 (CI lower >=0.5)
- Each phase has its own promotion gate

No phase blocks iter-2.
