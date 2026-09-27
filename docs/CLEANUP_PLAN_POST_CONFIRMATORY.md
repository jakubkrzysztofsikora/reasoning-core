# Post-Confirmatory Cleanup & Documentation Plan

**Created:** 2026-09-18
**Trigger:** Confirmatory v2 evaluation passed (`scoped_ten_x_claim_passed: True`)
**Run:** `eval/runs/ten-x-confirmatory-v2-direct-claude-20260917T211830Z/`

---

## Goal

Wire all documentation, READMEs, and cross-references to reflect the completed
confirmatory result (v2). Ensure a reader landing on any doc page can trace:
what was claimed → how it was tested → what was found → what the limitations
are. Remove stale "pending" / "no confirmatory result" language. v2 is the
evidence; v1 is a footnote (corpus prompt defect, superseded).

---

## 1. Documents to Update

### 1.1 `docs/EVAL_10X_PROTOCOL.md`
**Current state:** Says "No confirmatory result has been collected."
**Changes:**
- [ ] Update Status section: record that the confirmatory claim passed on v2 corpus
- [ ] Add a "Confirmatory Result" subsection with:
  - Corpus: `tasks_confirmatory_v2.json` (SHA `4879ce7f…`)
  - Lane: Claude Code 2.1.274 + direct OAuth claude-sonnet-4-5 + Edit/Write only
  - N=150 pairs, 300 executions, all valid
  - Control: 133/150 escapes (88.7%), Treatment: 0/150 (0%)
  - Rate-ratio upper 95% CI: 0.0295 (threshold <0.1) ✅
  - Completion guardrail: treatment +80pp vs control (lower bound +0.646) ✅
  - Operational: 0 failures ✅
  - `scoped_ten_x_claim_passed: True`
- [ ] One-sentence note: an earlier corpus (v1) had a prompt design defect that eliminated control pressure; the corrected corpus is v2
- [ ] Update the "Fixed Evaluation Lane" table if any values changed (they didn't — still 2.1.274)
- [ ] Add cross-reference to the v2 baseline and run artifacts

### 1.2 `eval/ten_x_pilot/CONFIRMATORY_PLAN.md`
**Current state:** Written as a forward-looking plan ("before a confirmatory run is frozen…")
**Changes:**
- [ ] Add a "Status" header: "COMPLETED — see Results section"
- [ ] Add a "Results" section linking to the analysis artifact and summarizing outcomes
- [ ] Brief note that v1 was superseded due to a prompt defect (one sentence)
- [ ] Update any "must do before freeze" language to past tense where completed
- [ ] Retain all preregistered design decisions as-is (they're the record)

### 1.3 `eval/ten_x_pilot/README.md`
**Current state:** References `tasks_confirmatory_v1.json` as the candidate corpus; describes the pipeline in present tense
**Changes:**
- [ ] Reference `tasks_confirmatory_v2.json` as the frozen confirmatory corpus
- [ ] One-line note: v1 superseded (prompt defect)
- [ ] Update CLI examples to use v2 artifacts and SHAs
- [ ] Add a "Results" section with the confirmatory outcome summary
- [ ] Update the evidence boundary: confirmatory evidence now exists for the scoped claim
- [ ] Document the tension-probe step as part of the corpus development workflow

### 1.4 `eval/host_enforcement_qualification/README.md`
**Current state:** Already up-to-date through 2.1.274 requalification
**Changes:**
- [ ] Minor: add a note that this qualification was used for the confirmatory v2 collection
- [ ] No structural changes needed

### 1.5 `docs/EVAL_RESULTS.md`
**Current state:** Contains only the old smoke-001 toolkit smoke test
**Changes:**
- [ ] Add a new top-level section: "Confirmatory Containment Evaluation (2026-09-18)"
- [ ] Include the full results table, scope, and limitations
- [ ] Cross-link to the protocol, plan, and run artifacts
- [ ] Retain the smoke-001 section as historical toolkit validation

### 1.6 `README.md` (top-level)
**Current state:** Evidence status says "current real-session quality evidence is being built"
**Changes:**
- [ ] Update Evidence Status paragraph to note the confirmatory containment result
- [ ] Add a brief "Evaluation" section (2-3 sentences) linking to EVAL_10X_PROTOCOL.md and EVAL_RESULTS.md
- [ ] Keep the existing benchmark disclaimer intact (those are separate historical results)
- [ ] Do NOT overclaim: the result is scoped to observer-detected invalid writes on disposable worktrees, Edit/Write only, one model, one host version

### 1.7 `eval/README.md`
**Current state:** Describes the SWE-bench harness and quickstart
**Changes:**
- [ ] Add a section on the 10x containment evaluation harness pointing to `eval/ten_x_pilot/`
- [ ] Note that the containment eval uses a different runner (`eval/ten_x_pilot/run.py`) than the SWE-bench suite (`eval/run_suite.py`)

### 1.8 `eval/baselines/README.md`
**Current state:** Documents baseline conventions
**Changes:**
- [ ] Add entries for the three new baselines:
  - `baseline-2026-09-15-host-enforcement-qualification` (repaired schema)
  - `baseline-2026-09-15-ten-x-confirmatory-corpus-v1` (v1 field failure record)
  - `baseline-2026-09-17-ten-x-confirmatory-corpus-v2` (frozen confirmatory corpus)
- [ ] Note the v1→v2 iteration rationale

---

## 2. Files to Create

### 2.1 `eval/runs/ten-x-confirmatory-v2-direct-claude-20260917T211830Z/README.md`
- Run-specific README documenting exact pins, timeline, v1 context, and how to reproduce
- Link to analysis, report, corpus, baselines

### 2.2 ~~v1 run README~~ — NOT NEEDED
- v1 is a superseded artifact; its `report.json` is self-documenting
- No dedicated README; one-line mentions in docs where relevant suffice

---

## 3. Files to Clean Up / Archive

### 3.1 Superseded artifacts
These remain on disk (immutable record). Docs mention them only as "superseded":
- `eval/runs/ten-x-feasibility-direct-claude-v1-20260915T093605Z/` — invalid (fixture reuse)
- `eval/runs/observer-seeded-validation-20260915T105141Z.json` — superseded by T121000Z
- `eval/baselines/baseline-2026-09-15-ten-x-confirmatory-corpus-v1.json` — superseded by v2

### 3.2 Temporary review artifacts
These live in `/tmp/rc-reviews/` and are ephemeral by design. No action needed
(they don't survive reboot). The retained artifacts are in `eval/runs/`.

### 3.3 Generator script
- `eval/ten_x_pilot/generate_confirmatory_v2.py` — keep as part of the corpus provenance chain

---

## 4. Cross-Reference Audit

Verify every internal link resolves after updates:

| From | To | Status |
|------|----|--------|
| README.md | docs/BENCHMARKS.md | ✅ exists |
| README.md | docs/EVAL_10X_PROTOCOL.md | ❌ not linked yet |
| docs/EVAL_10X_PROTOCOL.md | eval/ten_x_pilot/CONFIRMATORY_PLAN.md | check |
| eval/ten_x_pilot/README.md | docs/EVAL_10X_PROTOCOL.md | check |
| eval/ten_x_pilot/CONFIRMATORY_PLAN.md | docs/EVAL_10X_PROTOCOL.md | check |
| docs/EVAL_RESULTS.md | docs/EVAL_10X_PROTOCOL.md | ❌ not linked yet |
| eval/README.md | eval/ten_x_pilot/ | ❌ not linked yet |
| eval/baselines/README.md | individual baselines | check |

---

## 5. Test Updates

- [ ] Verify `tests/test_ten_x_pilot.py` still passes (24 tests)
- [ ] Verify `tests/test_host_enforcement_qualification.py` still passes
- [ ] Verify `py_compile` clean on all modified Python files
- [ ] Verify `git diff --check` clean (no trailing whitespace)

---

## 6. Execution Order

1. Write v2 run README (§2.1) — anchor for other docs to link to
2. Update EVAL_10X_PROTOCOL.md (§1.1) — the authority document
3. Update CONFIRMATORY_PLAN.md (§1.2) — close the loop on the plan
4. Update eval/ten_x_pilot/README.md (§1.3) — operator-facing docs
5. Update docs/EVAL_RESULTS.md (§1.5) — results page
6. Update README.md (§1.6) — top-level landing
7. Update eval/README.md and eval/baselines/README.md (§1.7, §1.8)
8. Cross-reference audit (§4)
9. Tests and lint (§5)
10. Adversarial review of the documentation changes

---

## 7. Principles

- **v2 is the evidence.** All docs lead with v2. v1 is a one-sentence footnote where relevant.
- **Scope discipline:** Every mention of the 10x result includes the scope qualifier (observer-detected, disposable worktrees, Edit/Write only, one model/host).
- **No overclaim:** The result does not establish filesystem-wide containment, code quality improvement, speed benefit, or Mamba causal efficacy.
- **Immutable artifacts:** Run directories and baselines are append-only. Docs describe them; they don't replace them.
- **Traceability:** A reader should be able to go from any doc → protocol → plan → corpus → raw data → analysis → conclusion.
