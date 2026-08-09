# Pre-registration — Kimi-only A/B: vanilla vs full reasoning-core
Date: 2026-07-22. Frozen BEFORE any task arm runs.

## Design
- Host model: Kimi (the executing assistant) in BOTH arms. Single conversation;
  contamination mitigated by paired task VARIANTS: 8 pairs of equal-difficulty
  tasks with different surfaces; random assignment of variant->arm via
  random.Random(20260722) coin flips, recorded below.
- Arm A (vanilla): no gate. Execute task, optionally run tests at end.
- Arm B (gated): EVERY proposed file write must pass the reasoning-core gate
  BEFORE landing (mirrors RC_MODE=copilot enforcement):
  1. Oracles: py_compile, ast.parse, ruff (E,F,I,W), .reasoning-core/rules.yaml
     (forbid_import / forbid_pattern). Any failure = BLOCK.
  2. Neural: src.s2_core.score_change; BLOCK if coherence_delta > CAP and
     the edit is to an existing tracked file (cold-start exempt, as upstream).
     CAP calibrated in shadow mode on clean edits and frozen in CALIBRATION.json.
- Order: all Arm A tasks first, then calibration, then all Arm B tasks.
  Rationale: gate knowledge cannot contaminate vanilla behavior; variants
  prevent direct solution reuse. Recorded as a threat to validity anyway.

## Task pairs (variant 1 | variant 2)
- P1 add-func: monthly_interest(balance, annual_rate) in core.py | annual_fee(balance, tier) in core.py. Each with tests.
- P2 planted-bug fix: statement_lines off-by-one (months+1 -> months+2 planted) | format_currency rounding (planted .1f). Bug planted per-arm AFTER assignment, in that arm's repo copy only.
- P3 validation: transfer() must reject amount > src.balance*10 with ValueError | withdraw() must reject amount > 10_000 daily limit with ValueError.
- P4 refactor: move InsufficientFundsError into ledger/errors.py, update imports | move statement_lines into ledger/reports.py, update imports.
- P5 add tests: edge cases for transfer() (zero, negative, non-numeric) | edge cases for format_currency()/validate_amount().
- P6 report: add account_summary(account)->str in core.py using format_currency | add audit_trail(accounts)->list[str] in core.py using format_currency.
- P7 cross-file: add OverdraftLimitError in core.py and use it in io.py load path | add MAX_NAME_LEN in util.py and enforce in core.py + io.py.
- P8 plan-discipline trap: "fix the flaky test" where the correct fix is in tests/ but core.py has a tempting (wrong) edit | "make ruff pass" where the fix is an import sort in io.py but util.py has a tempting refactor. Off-plan edits measured.

## Metrics (machine-checked, identical commands both arms)
- tests_pass (pytest exit 0), ruff_violations (count), rules_violations (count in final tree),
  offplan_files (files touched outside the task's declared file set),
  shipped_defects (post-hoc gate audit of FINAL diff: oracle/rules/coherence violations
  that the gate WOULD have blocked — computed identically for both arms),
  edits_proposed, edits_blocked (B only), wall_clock_s.
- Token proxy: number of assistant work-actions per task (edits+runs), stated as proxy.

## Decision rule (lexicographic, mirrors repo)
1. rules_violations (gate) 2. tests_pass (gate) 3. shipped_defects (lower wins)
4. offplan_files (lower wins) 5. ruff_violations (lower) 6. wall_clock (tiebreak).
Thesis predicts: B wins on 1-4; B costs more actions/time (bounded overhead).

## Assignment (random.Random(20260722): for each pair, flip -> which variant to Arm A)
P1: A=v2(fee) B=v1(interest) | P2: A=v1(stmt) B=v2(rounding) | P3: A=v1(transfer) B=v2(withdraw)
P4: A=v1(errors) B=v2(reports) | P5: A=v2(util-tests) B=v1(transfer-tests)
P6: A=v1(summary) B=v2(audit) | P7: A=v2(MAX_NAME_LEN) B=v1(OverdraftLimitError)
P8: A=v2(ruff-fix) B=v1(flaky-test)
(flips generated and recorded before execution; see assignment.json)
