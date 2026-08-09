# reasoning-core Eval Runbook

Operator guide for the SWE-bench Verified eval harness that compares the
reasoning-core hook + S2 sidecar (treatment) against vanilla Claude Code
(control). Methodology lives in
[`docs/EVAL_DESIGN.md`](../docs/EVAL_DESIGN.md); this file is the
how-to-run.

---

## Quickstart (5-task smoke, local venv)

Three commands from a clean checkout with `python3.11+` and a populated
Hugging Face cache:

```bash
pip install -r requirements.txt -r eval/requirements-eval.txt
RC_LIVE=1 S2_FAIL_CLOSED=0 S2_TIMEOUT=30 \
    python3 eval/run_suite.py --n 5 --arms vanilla,treatment --parallel 1
python3 eval/aggregate.py --results-dir eval/results --out eval/results/report --format both
```

Open `eval/results/report.md` for the headline. n=5 is descriptive only;
ship/kill verdicts require n≥50 (see `docs/EVAL_DESIGN.md` §5.1).

> n=2 smoke ≈ **$11**, n=5 smoke ≈ **$26**, n=100 full ≈ **$528**.
> (Token-cost model from `EVAL_DESIGN.md` §6.1.)

---

## Prerequisites

### Local

| Item | Version / Note |
|---|---|
| Python | 3.11+ (3.13 wheels also fine — `tree-sitter-languages` falls back to per-language wheels) |
| OS | macOS arm64 or Linux x86_64 |
| Disk | ~3 GB free (HF cache + repo clones under `$EVAL_SCRATCH_DIR`) |
| RAM | 8 GB usable; Mamba-130m forward pass peaks ~1.2 GB |
| Anthropic API access | `ANTHROPIC_API_KEY` with Opus 4.7 entitlement |
| Hugging Face | Anonymous read access to `state-spaces/mamba-130m-hf` is enough |
| Sidecar build deps | `git`, `curl`, `jq`. macOS gets these via `brew install jq`. |

### CI

The `.github/workflows/eval.yml` workflow (owned by Track D — see
[CI Invocation](#ci-invocation)) runs on `ubuntu-latest`. The
prerequisites collapse to "Docker can pull the image baked by
`eval/Dockerfile`".

---

## Environment variables

Required vs. optional, with defaults the harness uses if you do not set
them. Anything marked **required** has no safe default — set it in the
shell that launches `claude` or the eval harness.

### Sidecar / hook (`S2_*`)

| Var | Default | Required | Effect |
|---|---|---|---|
| `S2_FAIL_CLOSED` | `1` | optional | `1` = sidecar down → hook blocks (exit 2). `0` = fail-open. **Eval default `0`** to avoid spurious task failures from CPU latency. |
| `S2_TIMEOUT` | `60` | optional | Per-`/score` HTTP timeout in seconds. Eval uses `30`. CPU Mamba ≈ 3 s, so 30 s is comfortable. |
| `S2_DEVICE` | `cpu` | optional | `cuda` to opt into GPU inference if available. |
| `S2_SSM_CHECKPOINT` | `state-spaces/mamba-130m-hf` | optional | Override the HF repo id (e.g. swap to a SlideMamba release later). |
| `S2_PORT` | `8765` | optional | Loopback bind only. Eval workers each get their own port (`8765 + worker_id`). |

### reasoning-core overrides (`RC_*`)

| Var | Default | Required | Effect |
|---|---|---|---|
| `RC_LIVE` | `0` | required for live runs | `1` lets `run_suite.py` actually call Anthropic + the sidecar. With `0`, the harness exits 0 in dry-run mode (used by CI lint). |
| `RC_ALLOW_GUARD_EDIT` | unset | optional | If `1`, the L1 + L2 guard hooks let edits to `.claude/settings.json` and `src/hooks/*` through. **Never set this inside an eval container.** |
| `RC_PLAN_BLOCK` | `0` | optional | Hard-block plan-time guard violations (default is warn-only). |
| `RC_MAMBA_REVISION` | `main` | optional (build) | Picked up by `eval/scripts/prefetch_mamba.sh` to pin the HF revision baked into the image. |
| `RC_MAMBA_SHA256` | (pinned) | required for image rebuild | sha256 of `model.safetensors`. Empty value aborts the Docker build (fail-loud). |

### Anthropic (`ANTHROPIC_*`)

| Var | Default | Required | Effect |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | — | **required** | Live runs fail without it. |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | optional | Override for the local y-router (`http://127.0.0.1:8787`). The eval design (`§4.1`) calls Anthropic directly, **not** the router, so leave this unset for fidelity. |
| `ANTHROPIC_MODEL` | `claude-opus-4-7` | optional | Pinned by the harness; do not override unless you know what you are doing. |

### Hugging Face (`HF_*`)

| Var | Default | Required | Effect |
|---|---|---|---|
| `HF_HOME` | `~/.cache/huggingface` (local), `/root/.cache/huggingface` (image) | optional | Where the prebaked Mamba checkpoint lives. The Dockerfile pins it; local runs inherit your home. |
| `HF_HUB_OFFLINE` | `0` | optional | Set `1` to forbid network HF reads at runtime. The image ships warm so this is safe in CI once the prefetch step succeeded. |
| `TRANSFORMERS_OFFLINE` | `0` | optional | Same intent as `HF_HUB_OFFLINE` for the `transformers` loader. |

### Eval scratch / output

| Var | Default | Effect |
|---|---|---|
| `EVAL_SCRATCH_DIR` | `/tmp/reasoning-core-eval` | Per-task repo clones land here. Removed after each task. |
| `EVAL_RESULTS_DIR` | `eval/results` | Per-task `per_task_metrics.json`, `hook_events.jsonl`, etc. |
| `EVAL_REPORT_DIR` | `eval/results/` | `aggregate.py` writes `report.json` and `report.md` here. |

---

## Local invocation

### Option A — venv

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r eval/requirements-eval.txt

# pre-warm the sidecar's Mamba weights once so the first /score call is fast
python3 -c "from src.ssm_backbone import get_backbone; get_backbone()"

export ANTHROPIC_API_KEY=sk-ant-...
export RC_LIVE=1 S2_FAIL_CLOSED=0 S2_TIMEOUT=30

python3 eval/run_suite.py --n 5 --arms vanilla,treatment --parallel 1
python3 eval/aggregate.py --results-dir eval/results --out eval/results/report --format both
```

### Option B — Docker

```bash
docker build -f eval/Dockerfile -t reasoning-core-eval:dev .

docker run --rm \
    -e ANTHROPIC_API_KEY \
    -e RC_LIVE=1 -e S2_FAIL_CLOSED=0 -e S2_TIMEOUT=30 \
    -v "$PWD/eval/results:/app/eval/results" \
    reasoning-core-eval:dev \
    -lc 'python3 eval/run_suite.py --n 5 --arms vanilla,treatment --parallel 1 \
         && python3 eval/aggregate.py --results-dir eval/results --out eval/results/report --format both'
```

The image bakes the Mamba checkpoint, so the first `/score` request is
warm. The host-mounted `eval/results` makes the report artifacts visible
outside the container.

---

## CI invocation

Track D owns `.github/workflows/eval.yml`. The contract this Dockerfile
honours:

1. The workflow `setup` step builds (or pulls from GHCR) the image
   produced by `eval/Dockerfile`. Tag scheme: `reasoning-core-eval:<sha>`.
2. `lint-and-test` runs on every push (Python 3.11 host, no Docker
   needed for that job).
3. `eval-smoke` runs on every push to `main`, `n=5`, 30-min budget,
   inside the eval image. Sticky-comments the headline metrics on the
   commit SHA.
4. `eval-full` is `workflow_dispatch` only, `n=100`, 6-hour budget,
   protected by the `eval-full-approved` environment. Manual approval
   required.
5. `gh workflow run eval.yml -f n_tasks=10` invokes the full path with
   a smaller task count for rehearsal.

The image's `ENTRYPOINT ["/bin/bash"]` is what lets the workflow pass
`-lc 'python3 eval/run_suite.py ...'` directly. Do not change it without
coordinating with Track D.

---

## Output artifacts

Per-task (under `eval/results/<task_id>/<arm>/`):

| File | Contents |
|---|---|
| `per_task_metrics.json` | All scalar metrics from `EVAL_DESIGN.md` §3 (RR, ResR, AED, CycΔ, FIOΔ, latency, tokens, novelty) |
| `claude_transcript.jsonl` | One JSON line per Claude API response — `usage`, `content`, `tool_use`, `tool_result` blocks |
| `hook_events.jsonl` (treatment) | One line per PreToolUse fire: `{task_id, file_path, tool_name, exit_code, hook_duration_ms, sidecar_response, timestamp_iso}` |
| `sidecar.log` (treatment) | uvicorn stdout/stderr including FastAPI access log (per-request latency) |
| `test_results.json` | `gold_tests_passing`, `newly_failing_tests[]`, `total_tests_run`, `duration_seconds`, `pre_existing_failures[]` |
| `patch.diff` | Final unified diff Claude submitted |

Aggregated (under `eval/results/`):

| File | Contents |
|---|---|
| `report.json` | Cross-arm summary: per-metric paired diffs, BCa 95 % CI, raw + Holm-corrected p-values, decision table |
| `report.md` | Human-readable rendering of `report.json` |

### Audit-log retention

Per-session JSONL events from the runtime hooks land at
`/tmp/rc-events/<YYYY-MM-DD>/<session_id>.jsonl`. Rotation policy:

- one file per session, appended only — never rewritten;
- one directory per UTC date, rotates at midnight;
- the eval image keeps the last **7 days** under `/tmp/rc-events/`
  (the scratch tmpfs is wiped on container exit, so this is purely for
  intra-run debugging);
- on the host running a long bench, prune with
  `find /tmp/rc-events -mtime +7 -delete`.

---

## Cost & runtime model

Numbers reproduced from `EVAL_DESIGN.md` §6.1; refer there for the
derivation. The 4-worker parallel column is what the eval harness uses
when `--parallel 4`.

| Scope | n (paired tasks) | Total Claude runs | Est. tokens | Est. cost (Opus 4.7, 70/30 in/out) | Sequential wall-clock | 4-worker parallel |
|---|---|---|---|---|---|---|
| n=2 smoke | 2 | 4 | 320 k | **~$11** | ~40 min | ~12 min |
| n=5 smoke | 5 | 10 | 800 k | **~$26** | ~1 h 40 min | ~30 min |
| n=100 full | 100 | 200 | 16 M | **~$528** | ~33 h | **~9 h** |

Per-run wall-clock: ~8 min control, ~12 min treatment (15 tool calls
× ~3 s sidecar latency, fits inside the 35 s hook timeout).

---

## Troubleshooting

Five highest-likelihood failure modes and the diagnostic step that tells
you which it is.

### 1. Sidecar fails to boot — `127.0.0.1:8765/health` never returns 200

```bash
# Is anything bound on 8765 already?
lsof -iTCP:8765 -sTCP:LISTEN
# Boot the sidecar manually and watch the log
bash scripts/start-sidecar.sh
tail -f /tmp/rc-sidecar.log
# Confirm the Mamba load actually completes (~30 s on CPU)
curl -fsS http://127.0.0.1:8765/health | jq .
```

If `model_loaded:false` persists, check `HF_HOME` is set and the
checkpoint cache is populated; otherwise the loader is silently
re-downloading.

### 2. Hugging Face 403 / network gating during prefetch

Symptom: `eval/scripts/prefetch_mamba.sh` exits non-zero with a 403 or
sha256 mismatch.

```bash
# Confirm cache state
ls -lah ~/.cache/huggingface/hub/models--state-spaces--mamba-130m-hf/snapshots/*/
# Force a re-download bypassing the cache
HF_HUB_OFFLINE=0 huggingface-cli download state-spaces/mamba-130m-hf --quiet
# Recompute the sha to compare against RC_MAMBA_SHA256
sha256sum ~/.cache/huggingface/hub/models--state-spaces--mamba-130m-hf/snapshots/*/model.safetensors
```

A corporate egress proxy (Cato/Zscaler) is the most common cause — set
`HTTPS_PROXY` or run prefetch from a host that already has the cache,
then mount it into the build context with `--build-arg HF_HOME=...`.

### 3. Anthropic 401 / 429 mid-run

```bash
# Smoke the credential
curl -sS https://api.anthropic.com/v1/messages \
    -H "x-api-key: $ANTHROPIC_API_KEY" \
    -H "anthropic-version: 2023-06-01" \
    -H "content-type: application/json" \
    -d '{"model":"claude-opus-4-7","max_tokens":4,"messages":[{"role":"user","content":"hi"}]}' | jq .
# Inspect the per-task transcript for the offending status line
grep -E '"status_code":' eval/results/<task_id>/<arm>/claude_transcript.jsonl | tail -5
```

429s mean the token-bucket is exhausted; `--parallel 1` and retry. The
harness coding rule is "do not drop tasks" (`EVAL_DESIGN.md` §5.5);
retried tasks are re-run end-to-end so their wall-clock is recorded.

### 4. Hook fail-open spike — treatment regression rate suspiciously close to control

```bash
# Count timeouts vs successful blocks across the whole run
jq -s '[.[] | select(.exit_reason=="timeout")] | length' \
    eval/results/*/treatment/hook_events.jsonl
jq -s '[.[] | select(.exit_code==2)] | length' \
    eval/results/*/treatment/hook_events.jsonl
```

If timeouts > 5 % of treatment tool calls, re-run with `S2_TIMEOUT=60`
(see `EVAL_DESIGN.md` §6.4 threats-to-validity row 3).

### 5. pytest segfault inside the cloned repo (test environment error)

```bash
# Was the failure pre-existing? Compare base vs. patched test results
jq '.pre_existing_failures, .newly_failing_tests' \
    eval/results/<task_id>/<arm>/test_results.json
# Drop into the scratch repo and reproduce
cd /tmp/reasoning-core-eval/<task_id>/<arm>/repo
python -m pytest -q -x <failing_test_id> 2>&1 | head -50
```

Per `EVAL_DESIGN.md` §5.5 the task is **kept** in the denominator with
`RR=0.5` (conservative imputation) — do not delete the directory and do
not re-run. The aggregator records `test_error=true`.

---

## Comparison-mode operator notes

`aggregate.py` emits a decision block in `report.md` that classifies
each metric as PASS / FAIL against the `EVAL_DESIGN.md` §7 thresholds.
What the wording means:

- **"Treatment beats control" — significant**: Holm-corrected p < 0.05
  AND the paired difference goes the way the criterion expects (e.g.
  `R_c − R_t ≥ 0.15` for regression rate). The hook earns a vote toward
  ship.
- **"No significant difference"**: |paired diff| inside the BCa 95 % CI
  that straddles zero. The hook neither helps nor hurts on this metric.
  At small n (≤ 5) this is the *expected* outcome — Holm correction over
  10 metrics destroys power, so do not over-interpret.
- **"Treatment worse than control" — significant**: Holm-corrected p <
  0.05 AND the diff goes against the criterion. If this lands on RR or
  ResR specifically, it triggers the `EVAL_DESIGN.md` §7 kill criteria
  and the hook is **not shipped**.

The ship decision (per `EVAL_DESIGN.md` §7) requires **all five** of:
RR Δ ≥ 0.15, ResR no worse than −5 pp, FPBR ≤ 0.10, latency ratio ≤ 1.5,
BRR ≥ 0.60. Any single FAIL among the five → opt-in default; any kill
criterion triggered → disable.

Smoke runs at n ≤ 5 are descriptive only. Use them to gate "the harness
itself is healthy" (every per-task file present, no schema drift, no
sidecar timeouts > 5 %). Real ship/kill votes come from n ≥ 50 and
ideally the full n=100.
# Evaluation protocol note

`run_suite.py` freezes its task selection, seed, arm configuration, dataset
digest, code SHA, and aggregate-report digest in `run_manifest.json`. The
continuing weekly monitoring design uses five paired arms (`vanilla`,
`advisory_shadow`, `deterministic_only`, `plan_grounding_only`, and
`full_copilot`) with 20 tasks. These runs are regression monitoring, not proof
of broad performance claims; use two arm-blind labelers and report reliability
before interpreting differences.
