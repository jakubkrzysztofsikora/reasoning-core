#!/usr/bin/env bash
# eval/run_task.sh -- single-task harness for the reasoning-core eval.
#
# Usage:
#   eval/run_task.sh <task_id> <arm> [<out_dir>]
#       arm in {vanilla, treatment}
#
# Behaviour:
#   * Looks up <task_id> in eval/datasets/swe_bench_verified_python_subset.json.
#   * Clones the target repo at base_commit into
#     /tmp/rc-eval-<task_id>-<arm>/repo (configurable via $EVAL_SCRATCH_DIR).
#   * vanilla arm: launches `claude` with no project hooks (settings.json
#     renamed out of the way).
#   * treatment arm: copies .claude/settings.json from the reasoning-core
#     repo into the workdir and exports S2_FAIL_CLOSED=1 / S2_TIMEOUT=60.
#   * Both arms launch with the same prompt (problem statement), max-turns,
#     seed, and model.
#   * Hard time-box: 15 minutes (override via $RC_TASK_TIMEOUT_S).
#   * Writes <out_dir>/<task_id>.<arm>.json with per-task metrics.
#
# Test-mode (offline / unit tests): when $RC_EVAL_STUB_CLAUDE=1, the
# `claude` invocation is replaced by a stub that copies a recorded patch
# from $RC_EVAL_STUB_PATCH_DIR/<task_id>.patch into the working tree and
# emits synthetic transcript metadata. This makes the harness exercisable
# without the live binary and lets test_eval_aggregate use real fixtures.
#
# When $RC_LIVE != "1" the script enters dry-run mode: prints the resolved
# task metadata and the would-be commands, exits 0. CI uses this by default.

set -euo pipefail

usage() {
    echo "usage: $0 <task_id> <arm> [<out_dir>]" >&2
    echo "  arm in {vanilla, treatment}" >&2
    exit 2
}

if [[ $# -lt 2 ]]; then
    usage
fi

TASK_ID="$1"
ARM="$2"
OUT_DIR="${3:-${EVAL_OUT_DIR:-eval/results}}"

if [[ "$ARM" != "vanilla" && "$ARM" != "treatment" ]]; then
    echo "ERROR: arm must be 'vanilla' or 'treatment'; got '$ARM'" >&2
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATASET="${EVAL_DATASET:-$REPO_ROOT/eval/datasets/swe_bench_verified_python_subset.json}"
SCRATCH_BASE="${EVAL_SCRATCH_DIR:-/tmp/rc-eval}"
WORKDIR="$SCRATCH_BASE-$TASK_ID-$ARM"
TIMEOUT_S="${RC_TASK_TIMEOUT_S:-900}"
SYSTEM_PROMPT="${EVAL_SYSTEM_PROMPT:-$REPO_ROOT/eval/prompts/system_prompt.txt}"
MODEL="${RC_EVAL_MODEL:-claude-opus-4-7}"
MAX_TURNS="${RC_EVAL_MAX_TURNS:-50}"
SEED="${RC_EVAL_SEED:-42}"

mkdir -p "$OUT_DIR"

# --- Resolve task metadata -------------------------------------------------
if [[ ! -f "$DATASET" ]]; then
    echo "ERROR: dataset $DATASET missing" >&2
    exit 3
fi

# Extract fields with python (jq may not be on the PATH).
META_JSON="$(python3 - "$DATASET" "$TASK_ID" <<'PY'
import json, sys
ds_path, task_id = sys.argv[1], sys.argv[2]
with open(ds_path, "r", encoding="utf-8") as fh:
    ds = json.load(fh)
for t in ds.get("tasks", []):
    if t.get("instance_id") == task_id:
        print(json.dumps(t))
        sys.exit(0)
print(json.dumps({}))
sys.exit(0)
PY
)"

if [[ "$META_JSON" == "{}" ]]; then
    echo "ERROR: task_id '$TASK_ID' not found in $DATASET" >&2
    exit 4
fi

REPO="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("repo",""))' "$META_JSON")"
BASE_COMMIT="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("base_commit",""))' "$META_JSON")"
DIFFICULTY="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("difficulty",""))' "$META_JSON")"

# --- Dry run? --------------------------------------------------------------
if [[ "${RC_LIVE:-0}" != "1" ]]; then
    cat <<EOF
[run_task.sh dry-run]
task_id    : $TASK_ID
arm        : $ARM
repo       : $REPO
base_commit: $BASE_COMMIT
difficulty : $DIFFICULTY
workdir    : $WORKDIR
out_file   : $OUT_DIR/$TASK_ID.$ARM.json
timeout_s  : $TIMEOUT_S
model      : $MODEL
max_turns  : $MAX_TURNS
seed       : $SEED
EOF
    if [[ "$ARM" == "treatment" ]]; then
        echo "settings   : $REPO_ROOT/.claude/settings.json (will be copied)"
        echo "env        : S2_FAIL_CLOSED=1 S2_TIMEOUT=60"
    else
        echo "settings   : (none -- vanilla arm renames .claude/settings.json out)"
    fi
    exit 0
fi

# --- Live run --------------------------------------------------------------
START_T="$(date +%s)"

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

# Clone target repo at the task base commit. SWE-bench tasks reference real
# upstream GitHub repos.
if [[ -z "$REPO" || -z "$BASE_COMMIT" ]]; then
    echo "ERROR: missing repo/base_commit in metadata" >&2
    exit 5
fi

git clone --quiet "https://github.com/$REPO.git" "$WORKDIR/repo"
git -C "$WORKDIR/repo" checkout --quiet "$BASE_COMMIT"

# Set up project settings per arm.
PROJECT_DIR="$WORKDIR/repo"
if [[ "$ARM" == "treatment" ]]; then
    mkdir -p "$PROJECT_DIR/.claude"
    cp "$REPO_ROOT/.claude/settings.json" "$PROJECT_DIR/.claude/settings.json"
    # Ensure the hooks reference the *reasoning-core* repo, not the cloned
    # target's PROJECT_DIR (which has no `src/`).
    export CLAUDE_PROJECT_DIR="$REPO_ROOT"
    export S2_FAIL_CLOSED=1
    export S2_TIMEOUT=60
else
    # Vanilla: ensure no settings.json bleeds in.
    rm -rf "$PROJECT_DIR/.claude"
    export CLAUDE_PROJECT_DIR="$PROJECT_DIR"
    unset S2_FAIL_CLOSED || true
fi

# Compose problem statement file from fail_to_pass.
PROBLEM_FILE="$WORKDIR/PROBLEM.md"
python3 - "$META_JSON" "$PROBLEM_FILE" <<'PY'
import json, sys
meta = json.loads(sys.argv[1])
out = sys.argv[2]
ftp = meta.get("fail_to_pass") or []
ptp = meta.get("pass_to_pass") or []
with open(out, "w", encoding="utf-8") as fh:
    fh.write(f"# Task: {meta.get('instance_id')}\n\n")
    fh.write(f"Repo: {meta.get('repo')}\n")
    fh.write(f"Base commit: {meta.get('base_commit')}\n\n")
    fh.write("## Failing tests (must pass after your patch)\n\n")
    for t in ftp:
        fh.write(f"- {t}\n")
    fh.write("\n## Currently-passing tests (must NOT regress)\n\n")
    for t in ptp:
        fh.write(f"- {t}\n")
PY

# Launch Claude (or the test stub).
TRANSCRIPT="$WORKDIR/claude_transcript.jsonl"
HOOK_EVENTS="$WORKDIR/hook_events.jsonl"
EXIT_CODE=0
TIMED_OUT="false"

invoke_claude() {
    if [[ "${RC_EVAL_STUB_CLAUDE:-0}" == "1" ]]; then
        # Stub: apply a recorded patch and emit synthetic transcript.
        local patch_dir="${RC_EVAL_STUB_PATCH_DIR:-$REPO_ROOT/eval/fixtures/stub_patches}"
        local patch_file="$patch_dir/$TASK_ID.patch"
        if [[ -f "$patch_file" ]]; then
            git -C "$PROJECT_DIR" apply --reject --whitespace=fix "$patch_file" || true
        fi
        cat > "$TRANSCRIPT" <<JSON
{"type":"stub","task_id":"$TASK_ID","arm":"$ARM","usage":{"input_tokens":1000,"output_tokens":500}}
JSON
        return 0
    fi

    if ! command -v claude >/dev/null 2>&1; then
        echo "ERROR: claude CLI not on PATH; set RC_EVAL_STUB_CLAUDE=1 for offline runs." >&2
        return 127
    fi

    local args=(
        --model "$MODEL"
        --max-turns "$MAX_TURNS"
        --append-system-prompt "@$SYSTEM_PROMPT"
    )
    if [[ "$ARM" == "vanilla" ]]; then
        args+=(--no-hooks)
    fi
    # Run in workdir so PROJECT_DIR / settings.json resolve correctly.
    (
        cd "$PROJECT_DIR"
        cat "$PROBLEM_FILE" | timeout --kill-after=30s "$TIMEOUT_S" \
            claude "${args[@]}" \
            >"$TRANSCRIPT" 2>"$WORKDIR/claude.stderr.log"
    )
}

set +e
invoke_claude
EXIT_CODE=$?
set -e
if [[ $EXIT_CODE -eq 124 ]]; then
    TIMED_OUT="true"
fi

END_T="$(date +%s)"
WALL_CLOCK=$(( END_T - START_T ))

# Run pytest harness.
TEST_RESULTS="$WORKDIR/test_results.json"
python3 - "$WORKDIR" "$TEST_RESULTS" "$META_JSON" <<'PY'
import json, os, subprocess, sys
workdir, out_path, meta_json = sys.argv[1], sys.argv[2], sys.argv[3]
meta = json.loads(meta_json)
ftp = meta.get("fail_to_pass") or []
result = {
    "gold_tests_passing": False,
    "newly_failing_tests": [],
    "pre_existing_failures": [],
    "total_tests_run": 0,
    "duration_seconds": 0,
}
repo_dir = os.path.join(workdir, "repo")
if not ftp:
    # Without a target test list there is nothing to assert; treat as resolved=False.
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    sys.exit(0)
try:
    proc = subprocess.run(
        ["python3", "-m", "pytest", "--tb=no", "-q", "--timeout=60", *ftp],
        cwd=repo_dir,
        timeout=600,
        capture_output=True,
        text=True,
    )
    result["gold_tests_passing"] = (proc.returncode == 0)
    result["total_tests_run"] = len(ftp)
except Exception as exc:
    result["pytest_error"] = str(exc)
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(result, fh)
PY

# Extract token usage from transcript if present.
USAGE_JSON="$(python3 - "$TRANSCRIPT" <<'PY'
import json, sys
total_in = 0; total_out = 0
try:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            usage = obj.get("usage") if isinstance(obj, dict) else None
            if isinstance(usage, dict):
                total_in += int(usage.get("input_tokens", 0) or 0)
                total_out += int(usage.get("output_tokens", 0) or 0)
except FileNotFoundError:
    pass
print(json.dumps({"input_tokens": total_in, "output_tokens": total_out}))
PY
)"

# Compose the per-task metrics file.
python3 - <<PY
import json, os, sys
meta = $META_JSON
usage = $USAGE_JSON
test_results_path = "$TEST_RESULTS"
try:
    with open(test_results_path, "r", encoding="utf-8") as fh:
        tr = json.load(fh)
except Exception:
    tr = {}
out = {
    "task_id": "$TASK_ID",
    "arm": "$ARM",
    "repo": meta.get("repo"),
    "base_commit": meta.get("base_commit"),
    "resolved": bool(tr.get("gold_tests_passing", False)),
    "regression_introduced": bool(tr.get("newly_failing_tests")),
    "newly_failing_tests": tr.get("newly_failing_tests", []),
    "pre_existing_failures": tr.get("pre_existing_failures", []),
    "tokens_in": int(usage.get("input_tokens", 0)),
    "tokens_out": int(usage.get("output_tokens", 0)),
    "wall_clock_s": $WALL_CLOCK,
    "timeout": $([[ "$TIMED_OUT" == "true" ]] && echo True || echo False),
    "claude_exit_code": $EXIT_CODE,
}
with open("$OUT_DIR/$TASK_ID.$ARM.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=2)
PY

echo "[run_task.sh] wrote $OUT_DIR/$TASK_ID.$ARM.json"
exit 0
