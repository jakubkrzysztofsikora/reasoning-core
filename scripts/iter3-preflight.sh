#!/usr/bin/env bash
# iter3-preflight.sh — operator-runnable wiring smoke for iter-3 eval.
#
# Independent of the eval framework. Runs the 4 wiring checks from the
# iter-3 pre-mortem (sweep round-5) plus a Docker probe. Catches:
#   - SessionStart hook unregistered → silent no-op (failure mode #4)
#   - Plan-grounding gate broken → silent no-op (failure mode #5)
#   - Missing PLAN.md → silent evaporation (failure mode #7)
#   - Docker absent → T1/E1 will be Docker-blind (failure mode #1)
#
# Use BEFORE kicking off a 48-run iter-3 sweep to catch silent-no-result
# wiring failures while only 0 runs are sunk-cost (vs surfacing them
# post-sweep).
#
# Exit codes:
#   0 — all checks pass; iter-3 wiring is GO
#   1 — one or more wiring checks failed
#   2 — Docker absent (warning, not fatal — eval can still proceed for
#       non-cross-system tasks; T1/E1 will produce divergence-only)

set -u  # treat unset vars as errors; do NOT use -e (we want to continue past failures)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0
WARN=0

color_pass() { printf "\033[32mPASS\033[0m"; }
color_fail() { printf "\033[31mFAIL\033[0m"; }
color_warn() { printf "\033[33mWARN\033[0m"; }

check() {
  local name="$1" cmd="$2"
  if eval "$cmd" >/dev/null 2>&1; then
    printf "  [%s] %s\n" "$(color_pass)" "$name"
    PASS=$((PASS+1))
  else
    printf "  [%s] %s\n" "$(color_fail)" "$name"
    FAIL=$((FAIL+1))
  fi
}

warn_check() {
  local name="$1" cmd="$2"
  if eval "$cmd" >/dev/null 2>&1; then
    printf "  [%s] %s\n" "$(color_pass)" "$name"
    PASS=$((PASS+1))
  else
    printf "  [%s] %s\n" "$(color_warn)" "$name"
    WARN=$((WARN+1))
  fi
}

echo "iter-3 wiring preflight (run from anywhere; uses repo at $REPO_ROOT)"
echo

echo "Section 1 — Code wiring (must pass for iter-3 to function)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Check 1: hook script exists
check "session_start_best_effort.py present" \
  "test -f '$REPO_ROOT/src/hooks/session_start_best_effort.py'"

# Check 2: pre_edit_guard hosts plan-grounding
check "pre_edit_guard.py wires gate_plan_grounding" \
  "grep -q 'gate_plan_grounding' '$REPO_ROOT/src/hooks/pre_edit_guard.py'"

# Check 3: gate exists in dispatch
check "_dispatch.gate_plan_grounding defined" \
  "grep -q '^def gate_plan_grounding' '$REPO_ROOT/src/hooks/_dispatch.py'"

# Check 4: B-side fires injected receipt
RC_BEST_EFFORT_SPEC=1 RC_AUDIT_ROOT="$TMP/audit-b" python3 \
  "$REPO_ROOT/src/hooks/session_start_best_effort.py" >/dev/null 2>&1
B_INJECTED=$(find "$TMP/audit-b" -name "*.jsonl" -exec cat {} \; 2>/dev/null \
             | python3 -c "import sys,json; n=sum(1 for l in sys.stdin if l.strip() and json.loads(l).get('signal_source')=='best_effort_spec' and json.loads(l).get('decision')=='injected'); print(n)" 2>/dev/null || echo 0)
check "Setup B audit shows decision=injected (got $B_INJECTED, expect 1)" \
  "test '$B_INJECTED' -eq 1"

# Check 5: A-side (env unset) fires skipped receipt
RC_AUDIT_ROOT="$TMP/audit-a" python3 \
  "$REPO_ROOT/src/hooks/session_start_best_effort.py" >/dev/null 2>&1
A_SKIPPED=$(find "$TMP/audit-a" -name "*.jsonl" -exec cat {} \; 2>/dev/null \
            | python3 -c "import sys,json; n=sum(1 for l in sys.stdin if l.strip() and json.loads(l).get('signal_source')=='best_effort_spec' and json.loads(l).get('decision')=='skipped'); print(n)" 2>/dev/null || echo 0)
check "Setup A audit shows decision=skipped (got $A_SKIPPED, expect 1)" \
  "test '$A_SKIPPED' -eq 1"

# Check 6: A-side has ZERO injected receipts (symmetry guarantee)
A_INJECTED=$(find "$TMP/audit-a" -name "*.jsonl" -exec cat {} \; 2>/dev/null \
             | python3 -c "import sys,json; n=sum(1 for l in sys.stdin if l.strip() and json.loads(l).get('signal_source')=='best_effort_spec' and json.loads(l).get('decision')=='injected'); print(n)" 2>/dev/null || echo 0)
check "Setup A has zero decision=injected (got $A_INJECTED, expect 0)" \
  "test '$A_INJECTED' -eq 0"

# Check 7: plan-grounding fires on drift edit
mkdir -p "$TMP/run-dir/src"
echo '- `src/foo.py` — 100 LOC' > "$TMP/run-dir/PLAN.md"
echo "x = 1" > "$TMP/run-dir/src/drift.py"
PAYLOAD=$(python3 -c "import json; print(json.dumps({'tool_name':'Edit','tool_input':{'file_path':'$TMP/run-dir/src/drift.py','old_string':'x = 1\n','new_string':'x = 2\n'}}))")
echo "$PAYLOAD" | RC_PLAN_GROUNDING=1 RC_RUN_DIR="$TMP/run-dir" \
  RC_AUDIT_ROOT="$TMP/audit-pg" python3 \
  "$REPO_ROOT/src/hooks/pre_edit_guard.py" >/dev/null 2>&1
PG_WARNS=$(find "$TMP/audit-pg" -name "*.jsonl" -exec cat {} \; 2>/dev/null \
           | python3 -c "import sys,json; n=sum(1 for l in sys.stdin if l.strip() and json.loads(l).get('signal_source')=='plan_grounding' and json.loads(l).get('decision')=='warn'); print(n)" 2>/dev/null || echo 0)
check "plan_grounding warn fires on drift (got $PG_WARNS, expect ≥1)" \
  "test '$PG_WARNS' -ge 1"

# Check 8: plan-grounding emits audit_only when PLAN.md missing
rm -f "$TMP/run-dir/PLAN.md"
echo "$PAYLOAD" | RC_PLAN_GROUNDING=1 RC_RUN_DIR="$TMP/run-dir" \
  RC_AUDIT_ROOT="$TMP/audit-pg2" python3 \
  "$REPO_ROOT/src/hooks/pre_edit_guard.py" >/dev/null 2>&1
PG_AUDIT_ONLY=$(find "$TMP/audit-pg2" -name "*.jsonl" -exec cat {} \; 2>/dev/null \
                | python3 -c "import sys,json; n=sum(1 for l in sys.stdin if l.strip() and json.loads(l).get('signal_source')=='plan_grounding' and json.loads(l).get('decision')=='audit_only' and json.loads(l).get('reason')=='no_plan_md'); print(n)" 2>/dev/null || echo 0)
check "plan_grounding audit_only on missing PLAN.md (got $PG_AUDIT_ONLY, expect ≥1)" \
  "test '$PG_AUDIT_ONLY' -ge 1"

echo
echo "Section 2 — Operational dependencies (warnings only)"

# Check 9: docker available (T1/E1 cross-system tasks need it)
warn_check "docker available (T1/E1 cross-system tasks need it)" \
  "command -v docker && docker ps"

# Check 10: scaleway secret reachable (RC_GEN_API_KEY for Scaleway critic)
warn_check "scw CLI installed (RC_GEN_API_KEY auto-populates from scw)" \
  "command -v scw"

# Check 11: sidecar reachable (S2_PORT 8765)
warn_check "sidecar /health responds (S2 reasoning sidecar)" \
  "curl -fsS --max-time 2 http://127.0.0.1:8765/health"

echo
echo "Summary: $PASS pass, $FAIL fail, $WARN warn"
echo

if [[ "$FAIL" -gt 0 ]]; then
  echo "RESULT: NO-GO — $FAIL wiring check(s) failed. Iter-3 will produce silent null results."
  exit 1
fi
if [[ "$WARN" -gt 0 ]]; then
  echo "RESULT: GO with caveats — wiring is correct, but $WARN dependency warning(s):"
  echo "        Docker absent → T1, E1 will produce DIVERGENCES.md only (cannot clear gate)"
  echo "        scw absent    → Scaleway critic falls back; CDGS still runs heuristic-only"
  echo "        sidecar down  → S2 score path will fail-closed if S2_FAIL_CLOSED=1"
  exit 2
fi

echo "RESULT: GO — all wiring + dependencies clean. Safe to kick off iter-3 sweep."
exit 0
