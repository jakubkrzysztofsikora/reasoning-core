---
date: 2026-05-07
commit: 73e23ce61b63664ac48bc3eeb7157cbde2ad15c8
branch: main
ticket: iter-3
status: draft-v2
supersedes: draft-v1 (2026-05-07; rejected by reviewers as out-of-scope + confounded)
---

# Plan: Iter-3 — Reasoning-core levers for B's correctness gate + plan-impl coupling

## Summary

Reasoning-core ships three default-off levers + one standalone benchmark. Eval team independently decides whether to enable them in iter-3's Setup B. Levers are reproducible, single-factored, and publishable as coherence-loop primitives independent of any A-vs-B verdict.

## Scope contract

In-scope: **`/Users/jakubsikora/Repos/personal/reasoning-core/`** only.

Out-of-scope (forbidden in this plan):
- `/Users/jakubsikora/eval-setups/B/.envrc` and `settings.local.json` — eval team owns
- `~/.claude/projects/*/memory/*.md` — Claude Code user-memory layer
- Eval framework, judges, prompts, rubric, honesty bonus, Docker, α gate, T7 rotation, T9 reference review, cache cost reporting, grade-coverage gate

Reasoning-core ships levers; the eval team turns them on. This separation dissolves v1's "B hand-engineered for the gate" framing problem.

## Research References

- **Iter-2 whitepaper**: `thoughts/shared/research/2026-05-07-iter2-eval-whitepaper.md`
- **Iter-1 whitepaper**: `thoughts/shared/research/2026-05-05-iter1-eval-whitepaper.md`
- **Subagent analyses (this conversation, 2026-05-07)** — root-cause maps for divergence-only bailout, plan-impl jaccard −0.18, +134s wall-clock
- **Three reviewer passes** (LLM scientist + agentic-AI engineer + AI-newsletter tech reviewer, both v1 and v2 scopes) — actionable findings folded in below

## Decisions captured

| # | Question | Decision |
|---|----------|----------|
| 1 | Behavior change for B's correctness-gate misses | Lever: `RC_BEST_EFFORT_SPEC=1` activates a SessionStart-hook overlay. Default off. Single-factor wording: "never ship DIVERGENCES.md alone — pair with a compilable artifact." |
| 2 | Plan-impl coupling | Lever: `RC_PLAN_GROUNDING={0,1,2}` registered gate in `_dispatch.py`. Default 0. Mode 1 emits stderr advisory + audit event (NOT visible to agent). Mode 2 hard-blocks. |
| 3 | Wall-clock optimization | Deferred to iter-4. |
| 4 | Memory rule rewrite | Out of scope. Removed from plan. |
| 5 | Settings overlay via `settings.local.json` | Out of scope. Replaced with SessionStart-hook script that emits the proper JSON envelope. |

## Reviewer findings folded in

- **Engineer**: SessionStart hook MUST emit `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}` envelope (per `src/hooks/session_resume_inject.py:54-62`). Plain stdout is discarded by Claude Code.
- **Engineer**: Reuse `_emit_audit` (`pre_edit_guard.py:220`) and `audit_log.record_block` (`:308,:424,:469`). Drop invented `_audit_log_warn/_block`.
- **Engineer**: Plan-grounding goes into `_dispatch.py` returning `GateOutcome`; orchestrator translates `stderr_only`/`exit_block` actions per the existing vocabulary (`_dispatch.py:9-26`).
- **Engineer**: Use `os.path.normpath` + suffix match against full ref string, NOT `Path(file_path).name in refs` (too loose).
- **Engineer**: PLAN.md resolution precedence — `RC_RUN_DIR/PLAN.md` → `CLAUDE_PROJECT_DIR/PLAN.md` → `cwd/PLAN.md`.
- **Scientist**: Single-factor the overlay text (drop substitution recipe; just the license-removal sentence).
- **Scientist**: Plan-grounding warn must be audit-only (NOT agent-visible) — confirmed by the reduced scope (eval team can promote to mode 2 if desired).
- **Newsletter**: Add standalone benchmark harness so plan-grounding lever is publishable independent of A-vs-B verdict.
- **Newsletter**: Document `test_plan_path_vocabulary_realistic_corpus` to catch path-extraction regex misses.
- **Newsletter**: Ship `docs/iter3-levers.md` enumerating env vars, defaults, and reproducibility note.

## Phase 1: Helper extraction (`_plan_paths.py`)

Goal: a single source of truth for plan-vocabulary path extraction, consumed by both `pre_plan_guard` and the new plan-grounding gate.

### Changes

#### File: `src/hooks/_plan_paths.py` (NEW, ~50 LOC)

- **What**: Expose `distinct_file_paths(content: str) -> set[str]`. Reuse the regex logic currently in `pre_plan_guard._extract_files_with_loc` (`pre_plan_guard.py:107-118`) and `_count_distinct_file_paths` (`:121-126`).
- **Where**: New module.
- **Rationale**: v1 plan invented a non-existent `return_set` kwarg on `_count_distinct_file_paths`. Engineer flagged this as a P1 bug. Extracting the shared helper resolves both the signature mismatch and the cross-hook private-import smell.
- **Code sketch**:
  ```python
  """Plan-vocabulary path extraction shared between pre_plan_guard and
  the plan-grounding gate.
  """
  from __future__ import annotations
  import re
  from typing import Iterable

  _FILE_LINE_RE = re.compile(
      r"`?(?P<path>[A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,8})`?\s*[:#]?\s*(?:line\s*)?(?P<loc>\d+)?",
  )
  _BARE_PATH_RE = re.compile(r"`([A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,8})`")

  def distinct_file_paths(content: str) -> set[str]:
      paths: set[str] = set()
      for m in _FILE_LINE_RE.finditer(content):
          p = m.group("path") or ""
          if p and "." in p:
              paths.add(p)
      paths.update(_BARE_PATH_RE.findall(content))
      return paths
  ```

#### File: `src/hooks/pre_plan_guard.py`

- **What**: Refactor `_count_distinct_file_paths` to consume the new helper.
- **Where**: `pre_plan_guard.py:121-126`.
- **Rationale**: Single source of truth. No behavior change.
- **Code sketch**:
  ```python
  from _plan_paths import distinct_file_paths

  def _count_distinct_file_paths(content: str) -> int:
      return len(distinct_file_paths(content))
  ```

### Success Criteria

#### Automated Verification

- [ ] `pytest tests/test_plan_paths.py -v` passes — covers annotated `path:LOC`, bare backticked paths, and `set[str]` return.
- [ ] **Parity test**: `len(distinct_file_paths(content)) == _count_distinct_file_paths(content)` over a corpus of 5 real PLAN.md examples checked into `tests/fixtures/plans/`.
- [ ] No regression: `pytest tests/test_lang_invariants.py tests/test_calibration.py -q`.

### Dependencies

- Requires: nothing
- Blocks: Phase 3 (plan-grounding gate consumes this)

## Phase 2: SessionStart-hook overlay (`session_start_best_effort.py`)

Goal: env-gated mechanism for the eval team (or any caller) to inject a single-sentence system-context overlay. Default off. Single-factored: only the license-removal sentence ships in v2; substitution-recipe wording deferred per scientist's iter-4 ablation ask.

### Changes

#### File: `src/hooks/session_start_best_effort.py` (NEW, ~40 LOC)

- **What**: SessionStart hook script that emits the proper JSON envelope when `RC_BEST_EFFORT_SPEC=1`.
- **Where**: New module. Pattern matches `src/hooks/session_resume_inject.py:54-62` (`_emit_additional_context`).
- **Rationale**: Engineer caught that plain stdout is NOT consumed by Claude Code as system context — only the `hookSpecificOutput` JSON envelope is. v1 plan's "prints overlay text on stdout" wording would have been a no-op.
- **Single-factored overlay text** (scientist iter-4 ask): only the license-removal sentence. No substitution recipe.
- **Code sketch**:
  ```python
  """SessionStart-hook overlay (RC_BEST_EFFORT_SPEC).

  When `RC_BEST_EFFORT_SPEC=1`, injects a single-sentence system-context
  overlay via the `hookSpecificOutput.additionalContext` envelope. Default
  off — env var unset emits nothing and exits 0.

  The wording is deliberately minimal (single-factor for iter-4 attribution):
  it removes the agent's implicit license to ship DIVERGENCES.md alone, but
  does NOT prescribe a substitution recipe. If iter-3 sees correctness-gate
  movement, iter-4 can ablate the substitution-recipe variant separately.
  """
  from __future__ import annotations
  import json
  import os
  import sys

  _OVERLAY = (
      "When task-required infrastructure is unavailable, never ship a "
      "DIVERGENCES.md alone — always pair it with the closest compilable "
      "artifact the contract permits."
  )

  def main() -> None:
      if os.environ.get("RC_BEST_EFFORT_SPEC") != "1":
          sys.exit(0)
      out = {
          "hookSpecificOutput": {
              "hookEventName": "SessionStart",
              "additionalContext": _OVERLAY,
          }
      }
      sys.stdout.write(json.dumps(out))
      sys.exit(0)

  if __name__ == "__main__":
      main()
  ```

### Success Criteria

#### Automated Verification

- [ ] `pytest tests/test_session_start_best_effort.py -v` — assertions: (a) `RC_BEST_EFFORT_SPEC` unset → no stdout, exit 0; (b) `=1` → JSON envelope with `hookSpecificOutput.hookEventName == "SessionStart"`; (c) `additionalContext` equals exactly `_OVERLAY` (no drift).
- [ ] Smoke run: `RC_BEST_EFFORT_SPEC=1 python3 src/hooks/session_start_best_effort.py | python3 -m json.tool`.

### Dependencies

- Requires: nothing
- Blocks: nothing in RC scope. Eval team registers this in their own `settings.local.json:hooks.SessionStart` array, format identical to existing entries at `settings.local.json:91-103`.

## Phase 3: Plan-grounding gate (`gate_plan_grounding`)

Goal: warn-only audit signal for plan-impl drift, audit-visible to the eval aggregator but NOT to the agent (path-stuffing failure mode neutralized).

### Changes

#### File: `src/hooks/_dispatch.py`

- **What**: Add `gate_plan_grounding(*, file_path: str) -> GateOutcome`.
- **Where**: New function after `gate_lang_lock` (`_dispatch.py` — locate by searching for `def gate_lang_lock`).
- **Rationale**: Engineer confirmed the GateOutcome `action` vocabulary already includes `stderr_only` (warn) and `exit_block` (hard) — exactly what plan-grounding needs. No new sentinel required.
- **Code sketch**:
  ```python
  def gate_plan_grounding(*, file_path: str) -> GateOutcome:
      """Warn (mode=1) or block (mode=2) when an edit targets a file
      not referenced in the run's PLAN.md. Default OFF (mode unset/0).

      Resolution precedence: RC_RUN_DIR > CLAUDE_PROJECT_DIR > cwd.
      Path-match: os.path.normpath + suffix-match against full ref strings.
      PLAN.md itself is always allowed (basename match).
      """
      mode = os.environ.get("RC_PLAN_GROUNDING", "0").strip()
      if mode not in ("1", "2"):
          return GateOutcome()  # action="pass"
      if not file_path:
          return GateOutcome()
      basename = os.path.basename(file_path).lower()
      if basename == "plan.md" or basename.endswith(".plan.md"):
          return GateOutcome()
      plan_path = _resolve_plan_path()
      if plan_path is None:
          return GateOutcome(reason="no_plan_md")
      try:
          from _plan_paths import distinct_file_paths
          plan_text = plan_path.read_text(encoding="utf-8", errors="replace")
          refs = distinct_file_paths(plan_text)
      except (OSError, ImportError):
          return GateOutcome(reason="plan_unreadable")
      norm = os.path.normpath(file_path)
      if any(norm.endswith(os.path.normpath(r)) for r in refs):
          return GateOutcome(reason="in_plan")
      audit_extra = {
          "plan_path": str(plan_path),
          "plan_refs_count": len(refs),
      }
      if mode == "1":
          return GateOutcome(
              action="stderr_only",
              stderr=f"[reasoning-core] WARN: edit drifts from plan — {file_path} not in PLAN.md\n",
              decision="warn",
              reason="plan_impl_drift",
              signal_source="plan_grounding",
              audit_extra=audit_extra,
          )
      return GateOutcome(
          action="exit_block",
          code=2,
          stderr=(
              f"[reasoning-core] BLOCKED: plan_impl_drift — {file_path} not in PLAN.md.\n"
              f"  Update PLAN.md to include this file, or set RC_PLAN_GROUNDING=1 for warn-only.\n"
          ),
          decision="blocked",
          reason="plan_impl_drift",
          signal_source="plan_grounding",
          audit_extra=audit_extra,
      )

  def _resolve_plan_path() -> "Optional[Path]":
      from pathlib import Path
      for env_key in ("RC_RUN_DIR", "CLAUDE_PROJECT_DIR"):
          base = os.environ.get(env_key)
          if base:
              p = Path(base) / "PLAN.md"
              if p.exists():
                  return p
      p = Path.cwd() / "PLAN.md"
      return p if p.exists() else None
  ```

#### File: `src/hooks/pre_edit_guard.py`

- **What**: Hand-wire `gate_plan_grounding` after `gate_lang_lock` and before `_extract_changes`.
- **Where**: Immediately before line 445 (the line containing `pairs = _extract_changes(...)`).
- **Rationale**: Earliest stable point with `file_path` resolved (line 295), lang-lock cleared (line 443), and SSM POST not yet fired. Orchestrator owns side-effects per existing pattern.
- **Code sketch**:
  ```python
  # Iter-3 plan-grounding gate (audit-only by default; off when RC_PLAN_GROUNDING unset)
  pg_outcome = _dispatch.gate_plan_grounding(file_path=file_path)
  if pg_outcome.action == "stderr_only":
      sys.stderr.write(pg_outcome.stderr)
      _emit_audit(
          tool_name=tool_name,
          decision=pg_outcome.decision,
          file_path=file_path,
          started=started,
          reason=pg_outcome.reason,
          signal_source=pg_outcome.signal_source,
          extra=pg_outcome.audit_extra,
      )
  elif pg_outcome.action == "exit_block":
      audit_log.record_block(file_path)
      _emit_audit(
          tool_name=tool_name,
          decision=pg_outcome.decision,
          file_path=file_path,
          started=started,
          reason=pg_outcome.reason,
          signal_source=pg_outcome.signal_source,
          extra=pg_outcome.audit_extra,
      )
      _exit(pg_outcome.code, pg_outcome.stderr)
  pairs = _extract_changes(tool_name, tool_input)
  ```

### Success Criteria

#### Automated Verification

- [ ] `pytest tests/test_plan_grounding.py -v` covers: (a) mode unset → `action="pass"`, (b) mode=1 + drift → `action="stderr_only"` + `signal_source="plan_grounding"`, (c) mode=2 + drift → `action="exit_block"` + code=2, (d) PLAN.md itself always passes, (e) no PLAN.md exists → pass with `reason="no_plan_md"`, (f) suffix-match correctness on real plan vocabulary.
- [ ] `tests/test_hook_block.py` integration: subprocess-style test wires `RC_PLAN_GROUNDING=2` end-to-end through `pre_edit_guard.py`, confirms exit code 2 + audit-log block recorded.
- [ ] **`test_plan_path_vocabulary_realistic_corpus`** (newsletter ask): assert that for each PLAN.md fixture in `tests/fixtures/plans/`, the gate's path-extraction regex captures every file path the agent actually edits in the matching run trace. False-positive rate < 5%.

### Dependencies

- Requires: Phase 1 (`_plan_paths.distinct_file_paths`)
- Blocks: nothing in RC scope

## Phase 4: Standalone benchmark harness (`tests/test_plan_grounding_corpus.py`)

Goal: make plan-grounding publishable as a coherence-loop primitive independent of any A-vs-B verdict (newsletter requirement).

### Changes

#### File: `tests/fixtures/plans/` (NEW directory)

- **What**: 5–8 frozen PLAN.md + edit-trace pairs drawn from real iter-2 runs (anonymized if necessary).
- **Where**: Each fixture: `<task_id>/PLAN.md` and `<task_id>/edits.jsonl` (one JSON line per edit, fields: `file_path`, `expected_in_plan` boolean ground truth).
- **Rationale**: Frozen corpus lets the gate's precision/recall be measured reproducibly without re-running an eval.

#### File: `tests/test_plan_grounding_corpus.py` (NEW)

- **What**: Iterate over all fixtures; for each edit, call `gate_plan_grounding`; compare decision against `expected_in_plan` ground truth; assert precision ≥ 0.9 and recall ≥ 0.8 across the corpus.
- **Rationale**: This test runs in CI and gates merges. Any regression in the path-extraction regex or matching logic surfaces immediately, independent of any external eval.
- **Code sketch**:
  ```python
  def test_corpus_precision_recall():
      tp = fp = fn = 0
      for fix_dir in (REPO / "tests/fixtures/plans").iterdir():
          os.environ["RC_RUN_DIR"] = str(fix_dir)
          os.environ["RC_PLAN_GROUNDING"] = "1"
          for line in (fix_dir / "edits.jsonl").read_text().splitlines():
              rec = json.loads(line)
              outcome = _dispatch.gate_plan_grounding(file_path=rec["file_path"])
              gate_says_in_plan = (outcome.action == "pass")
              truth = rec["expected_in_plan"]
              if gate_says_in_plan and truth: tp += 1
              elif gate_says_in_plan and not truth: fp += 1
              elif not gate_says_in_plan and truth: fn += 1
      precision = tp / max(1, tp + fp)
      recall = tp / max(1, tp + fn)
      assert precision >= 0.90, f"precision {precision} < 0.90"
      assert recall >= 0.80, f"recall {recall} < 0.80"
  ```

### Success Criteria

#### Automated Verification

- [ ] `pytest tests/test_plan_grounding_corpus.py -v` passes with precision ≥ 0.90, recall ≥ 0.80.
- [ ] Corpus has ≥ 5 fixtures spanning 3+ task types from iter-2.

### Dependencies

- Requires: Phases 1 + 3
- Blocks: nothing

## Phase 5: Documentation (`docs/iter3-levers.md`)

Goal: reproducibility hygiene per newsletter requirement. Anyone reading the iter-3 results can attribute changes to specific levers and replicate.

### Changes

#### File: `docs/iter3-levers.md` (NEW, ~80 lines)

- **What**: Enumerate the three iter-3 levers, defaults, semantics, opt-in instructions.
- **Sections**:
  1. **Lever spec** — table of (env var, default, mode values, what each mode does, file:line of implementation).
  2. **Reproducibility note** — exact env-var values to reproduce iter-3 Setup B if levers were enabled. Cite reasoning-core git SHA at iter-3 freeze.
  3. **Falsifying ablation (iter-4)** — single-factor variants enumerated for the next iteration: (a) overlay text minimal vs full substitution recipe; (b) plan-grounding mode 1 vs 2; (c) no levers vs all levers.
  4. **Out-of-scope** — explicit list of what reasoning-core did NOT change in iter-3 (eval prompts, judges, rubric, Docker, α gate, settings.local.json, memory).
- **Rationale**: Without this doc, the iter-3 result (whichever way it goes) is not attributable. With it, the lever code is publishable as a standalone artifact.

### Success Criteria

#### Automated Verification

- [ ] `docs/iter3-levers.md` exists and references all three env vars by exact name.
- [ ] Markdown lint clean (if a linter is in CI).

#### Manual Verification

- [ ] An iter-4 reviewer reading the doc + the code can re-run iter-3 Setup B without consulting any out-of-scope file.

### Dependencies

- Requires: Phases 1, 2, 3, 4
- Blocks: iter-3 freeze

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Path-extraction regex misses agent's file-path style → spurious warns + audit noise | Medium | Low | `test_plan_path_vocabulary_realistic_corpus` catches at CI time. Default mode 1 (audit-only) means no agent-visible disruption. |
| SessionStart overlay JSON envelope rejected by Claude Code (schema mismatch) | Low | Medium | Pattern copied from production-validated `session_resume_inject.py:54-62`. Test asserts envelope shape exactly. |
| Eval team enables levers in iter-3 but doesn't document which set was active → unattributable result | Medium | High | `docs/iter3-levers.md` provides the reproducibility doc; eval team's frozen manifest will pin the env-var values. RC team sends the doc to eval team at iter-3 freeze. |
| Plan-grounding gate adds latency to every edit | Low | Low | In-process file read + regex + set lookup. <5ms per edit (no network). |
| Trivial-stub failure mode — agent ships `Assert.True(true)` to satisfy "pair with compilable artifact" | Medium (eval-team scope) | Medium | RC cannot mitigate (rubric/honesty bonus / rotated tests own this). RC docs note the failure mode and recommend eval team add an assertion-count or symbol-coverage check. |
| Gate's suffix-match `norm.endswith(...)` over-permits (e.g. `utils.py` matches any nested `utils.py`) | Medium | Low | Document tradeoff in code comment; tighten in iter-4 if CI corpus shows precision < 0.90. |

## Rollback Strategy

Each phase is independently revertable:

- **Phase 1**: revert `pre_plan_guard.py` refactor; delete `_plan_paths.py`. Behavior identical.
- **Phase 2**: delete `session_start_best_effort.py`. No callers in RC code (eval team's settings registers it; if absent, eval team's setup falls through harmlessly).
- **Phase 3**: revert `pre_edit_guard.py` 3-line wiring; remove `gate_plan_grounding` + `_resolve_plan_path` from `_dispatch.py`. Default-off env var means no behavior change before revert.
- **Phase 4**: delete `tests/test_plan_grounding_corpus.py` and fixtures.
- **Phase 5**: delete `docs/iter3-levers.md`.

Per-phase commits enforce granular rollback.

## File Ownership Summary

| File | Phase | Change Type |
|------|-------|-------------|
| `src/hooks/_plan_paths.py` | 1 | Create |
| `src/hooks/pre_plan_guard.py` | 1 | Modify (refactor `_count_distinct_file_paths`) |
| `src/hooks/session_start_best_effort.py` | 2 | Create |
| `src/hooks/_dispatch.py` | 3 | Modify (add `gate_plan_grounding` + `_resolve_plan_path`) |
| `src/hooks/pre_edit_guard.py` | 3 | Modify (hand-wire gate before `_extract_changes` at :444) |
| `tests/test_plan_paths.py` | 1 | Create |
| `tests/test_session_start_best_effort.py` | 2 | Create |
| `tests/test_plan_grounding.py` | 3 | Create |
| `tests/test_hook_block.py` | 3 | Modify (add `RC_PLAN_GROUNDING=2` integration case) |
| `tests/fixtures/plans/<task_id>/PLAN.md` | 4 | Create (5–8 fixtures) |
| `tests/fixtures/plans/<task_id>/edits.jsonl` | 4 | Create (5–8 fixtures) |
| `tests/test_plan_grounding_corpus.py` | 4 | Create |
| `docs/iter3-levers.md` | 5 | Create |

## Out of scope (deferred)

Per the iter-2 whitepaper §13 and the reasoning-core scope contract:

**Owned by eval team** (RC cannot ship):
- Docker provisioning for T1, E1
- Inter-rater α work (third judge, BARS anchor strengthening)
- Honesty bonus design / rubric split between honesty and correctness gate
- T7 rotation, T9 reference-review embedding
- Cache cost reporting (cache_read/write as primary metrics)
- Grade-coverage gate
- Setup A/B label stability, dry-run judge sanity probe
- Modifications to `eval-setups/B/.envrc` or `settings.local.json`
- Modifications to `~/.claude/projects/.../memory/*.md`

**Deferred to iter-4** (RC scope, but not iter-3):
- Wall-clock optimization (PLAN.md content cache, extension exemption, S2_TIMEOUT lowering — ~25s/run easy wins identified)
- Substitution-recipe expansion of the SessionStart overlay (single-factor ablation)
- Plan-grounding mode 2 default-on (after corpus precision/recall stabilizes)
- Mock-detector finer-grained guards under best-effort context
