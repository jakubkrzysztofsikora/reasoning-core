#!/usr/bin/env bash
# test-prototype.sh
#
# End-to-end smoke for the hybrid reasoning core.
#
# Steps:
#   (a) boot the S2 sidecar in the background
#   (b) wait for /health to report model_loaded:true (60s budget)
#   (c) seed fixtures for Python, JavaScript, C#, SQL
#   (d) submit a deliberately bad Python refactor; assert hook exit == 2
#   (e) submit a benign JS rename and a benign C# method-name change;
#       assert hook exit == 0 for both
#   (f) submit a benign SQL CREATE PROCEDURE addition; assert exit == 0
#   (g) submit a .rb payload; assert exit == 0 with stderr containing
#       "unsupported_language"
#   (h) tear down the sidecar
#   (i) print PASS or FAIL: <reason> and exit accordingly
#
# Owner: engineer-3 (RC-008).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_SCRIPT="$REPO_ROOT/src/hooks/pre_edit_guard.py"
SIDECAR_HEALTH="http://127.0.0.1:8765/health"

TMP_DIR="$(mktemp -d -t reasoning-core-prototype-XXXXXX)"
SIDE_LOG="$TMP_DIR/sidecar.log"
SIDE_PID=""

cleanup() {
    local exit_code=$?
    if [[ -n "$SIDE_PID" ]] && kill -0 "$SIDE_PID" 2>/dev/null; then
        kill "$SIDE_PID" 2>/dev/null || true
        # give it a moment, then force.
        for _ in 1 2 3 4 5; do
            if ! kill -0 "$SIDE_PID" 2>/dev/null; then break; fi
            sleep 0.2
        done
        if kill -0 "$SIDE_PID" 2>/dev/null; then
            kill -9 "$SIDE_PID" 2>/dev/null || true
        fi
    fi
    rm -rf "$TMP_DIR" 2>/dev/null || true
    exit "$exit_code"
}
trap cleanup EXIT INT TERM

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

require_bin() {
    command -v "$1" >/dev/null 2>&1 || fail "missing required binary: $1"
}
require_bin python3
require_bin curl
require_bin jq

# ---------------------------------------------------------------------------
# (a) Boot sidecar
# ---------------------------------------------------------------------------
echo "[prototype] booting sidecar..."
if [[ -x "$REPO_ROOT/scripts/start-sidecar.sh" ]]; then
    ( cd "$REPO_ROOT" && bash scripts/start-sidecar.sh ) >"$SIDE_LOG" 2>&1 &
else
    ( cd "$REPO_ROOT" && python3 -m src.s2_core ) >"$SIDE_LOG" 2>&1 &
fi
SIDE_PID=$!

# ---------------------------------------------------------------------------
# (b) Wait for /health (model_loaded:true). 60s budget.
# ---------------------------------------------------------------------------
echo "[prototype] waiting for /health (60s budget, pid=$SIDE_PID)..."
ready=0
for i in $(seq 1 60); do
    if ! kill -0 "$SIDE_PID" 2>/dev/null; then
        echo "[prototype] sidecar exited early; log tail:" >&2
        tail -n 80 "$SIDE_LOG" >&2 || true
        fail "sidecar process died before /health came up"
    fi
    if curl -fsS --max-time 2 "$SIDECAR_HEALTH" 2>/dev/null \
        | jq -e '.model_loaded == true' >/dev/null 2>&1; then
        ready=1
        echo "[prototype] sidecar ready after ${i}s"
        break
    fi
    sleep 1
done
if [[ "$ready" -ne 1 ]]; then
    echo "[prototype] last sidecar log lines:" >&2
    tail -n 80 "$SIDE_LOG" >&2 || true
    fail "sidecar /health did not report model_loaded:true within 60s"
fi

# ---------------------------------------------------------------------------
# (c) Seed fixtures
# ---------------------------------------------------------------------------
PY_FILE="$TMP_DIR/sample.py"
JS_FILE="$TMP_DIR/sample.js"
CS_FILE="$TMP_DIR/Sample.cs"
SQL_FILE="$TMP_DIR/sample.sql"
RB_FILE="$TMP_DIR/sample.rb"

cat >"$PY_FILE" <<'PY'
def f(n):
    if not n:
        return 0
    return n + f(n - 1)
PY

cat >"$JS_FILE" <<'JS'
function greet(name) {
    return "hello " + name;
}
JS

cat >"$CS_FILE" <<'CS'
public class Sample {
    public int Add(int a, int b) {
        return a + b;
    }
}
CS

cat >"$SQL_FILE" <<'SQL'
CREATE PROCEDURE list_users()
BEGIN
    SELECT id, email FROM users;
END;
SQL

cat >"$RB_FILE" <<'RB'
def greet(name)
  "hello #{name}"
end
RB

# Helper: feed a synthetic Claude PreToolUse payload to the hook.
# Accepts: file_path, before_src (read from disk), after_src.
# Captures stdout/stderr/exit-code.
HOOK_STDERR_FILE="$TMP_DIR/hook.stderr"

run_hook() {
    local file_path="$1"
    local after_src_file="$2"

    local payload
    payload=$(jq -n \
        --arg path "$file_path" \
        --arg after "$(cat "$after_src_file")" \
        --arg before "$(cat "$file_path")" \
        '{
            tool_name: "Edit",
            tool_input: {
                file_path: $path,
                old_string: $before,
                new_string: $after
            }
        }')

    local rc=0
    : >"$HOOK_STDERR_FILE"
    set +e
    printf '%s' "$payload" | python3 "$HOOK_SCRIPT" >/dev/null 2>"$HOOK_STDERR_FILE"
    rc=$?
    set -e
    echo "$rc"
}

# ---------------------------------------------------------------------------
# (d) Bad Python refactor: drop guard + unbounded recursion. Expect exit 2.
# ---------------------------------------------------------------------------
PY_BAD="$TMP_DIR/sample.bad.py"
cat >"$PY_BAD" <<'PY'
def f(n):
    return n + f(n + 1)
PY

echo "[prototype] (d) bad Python refactor — expect exit 2"
rc=$(run_hook "$PY_FILE" "$PY_BAD")
if [[ "$rc" -ne 2 ]]; then
    echo "[prototype] hook stderr was:" >&2
    cat "$HOOK_STDERR_FILE" >&2 || true
    fail "(d) bad Python refactor: expected exit 2, got $rc"
fi

# ---------------------------------------------------------------------------
# (e) Benign JS rename + benign C# method-name change. Expect exit 0.
# ---------------------------------------------------------------------------
JS_GOOD="$TMP_DIR/sample.good.js"
cat >"$JS_GOOD" <<'JS'
function greetUser(name) {
    return "hello " + name;
}
JS

echo "[prototype] (e1) benign JS rename — expect exit 0"
rc=$(run_hook "$JS_FILE" "$JS_GOOD")
if [[ "$rc" -ne 0 ]]; then
    cat "$HOOK_STDERR_FILE" >&2 || true
    fail "(e1) benign JS rename: expected exit 0, got $rc"
fi

CS_GOOD="$TMP_DIR/Sample.good.cs"
cat >"$CS_GOOD" <<'CS'
public class Sample {
    public int Sum(int a, int b) {
        return a + b;
    }
}
CS

echo "[prototype] (e2) benign C# method rename — expect exit 0"
rc=$(run_hook "$CS_FILE" "$CS_GOOD")
if [[ "$rc" -ne 0 ]]; then
    cat "$HOOK_STDERR_FILE" >&2 || true
    fail "(e2) benign C# rename: expected exit 0, got $rc"
fi

# ---------------------------------------------------------------------------
# (f) Benign SQL CREATE PROCEDURE addition. Expect exit 0.
# ---------------------------------------------------------------------------
SQL_GOOD="$TMP_DIR/sample.good.sql"
cat >"$SQL_GOOD" <<'SQL'
CREATE PROCEDURE list_users()
BEGIN
    SELECT id, email FROM users;
END;

CREATE PROCEDURE list_admins()
BEGIN
    SELECT id, email FROM users WHERE is_admin = 1;
END;
SQL

echo "[prototype] (f) benign SQL CREATE PROCEDURE addition — expect exit 0"
rc=$(run_hook "$SQL_FILE" "$SQL_GOOD")
if [[ "$rc" -ne 0 ]]; then
    cat "$HOOK_STDERR_FILE" >&2 || true
    fail "(f) benign SQL addition: expected exit 0, got $rc"
fi

# ---------------------------------------------------------------------------
# (g) .rb payload. Expect exit 0 with stderr containing unsupported_language.
# ---------------------------------------------------------------------------
RB_GOOD="$TMP_DIR/sample.good.rb"
cat >"$RB_GOOD" <<'RB'
def greet(name)
  "hi #{name}"
end
RB

echo "[prototype] (g) .rb payload — expect exit 0 + unsupported_language note"
rc=$(run_hook "$RB_FILE" "$RB_GOOD")
if [[ "$rc" -ne 0 ]]; then
    cat "$HOOK_STDERR_FILE" >&2 || true
    fail "(g) .rb payload: expected exit 0, got $rc"
fi
if ! grep -q "unsupported_language" "$HOOK_STDERR_FILE"; then
    echo "[prototype] hook stderr was:" >&2
    cat "$HOOK_STDERR_FILE" >&2 || true
    fail "(g) .rb payload: stderr missing 'unsupported_language' note"
fi

# ---------------------------------------------------------------------------
# (h) Teardown handled by trap. (i) Print PASS.
# ---------------------------------------------------------------------------
echo "PASS"
exit 0
