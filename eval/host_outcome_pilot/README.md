# Host Outcome Pilot

This preregistered, single-host study closes the next evidence gap after the
direct-hook timing pilot. It runs one pinned Claude Code configuration against
paired fixture tasks and compares two feedback placements:

- `pre_write`: the shipped `PreToolUse` gate blocks deterministic policy
  violations before the write reaches the worktree.
- `post_write`: a pilot-only `PostToolUse` hook permits the write, then returns
  the equivalent deterministic rule/contract feedback for the agent to repair.

It records host stream events, local audit events, final diffs, final policy
status, completion, attempted invalid-write propagation, and end-to-end host
latency. The task and arm schedule are frozen in `manifest.json`; arm order is
seeded and randomized.

## Preregistered scope

- Host: Claude Code only; its version and selected model are recorded.
- Local Mamba sidecar: required and must report `model_loaded: true`.
- Tasks: `tasks.json`; three policy-repair prompts and one compliant control.
- Primary endpoint: deterministic final-policy violation.
- The runner uses Claude Code's `bypassPermissions` mode only inside a fresh,
  temporary Git fixture so agents can inspect and edit without an unattended
  permission prompt; the fixture is deleted after each trial.
- Secondary endpoints: completion, invalid writes reaching the worktree,
  feedback-to-correction instrumentation readiness, host elapsed time, and
  false-block/abandonment signals.

This is not blinded and uses synthetic tasks. It cannot support a broad
code-quality, regression-reduction, token-saving, or cross-host claim. Any
host error, timeout, or rate-limit failure marks the report invalid for an arm
comparison rather than being counted as an agent outcome.

## Run

```bash
bash scripts/start-sidecar.sh
curl -fsS http://127.0.0.1:8765/health

.venv/bin/python -m eval.host_outcome_pilot.run \
  --repeats 3 \
  --model opencode/deepseek-v4.1-flash \
  --max-budget-usd 1.00 \
  --out-dir eval/runs/host-outcome-$(date -u +%Y%m%dT%H%M%SZ)
```

`--model` is recorded in the frozen manifest and must be identical for both
arms. The requested `opencode/deepseek-v4.1-flash` identifier must already be
available to this Claude Code installation; a provider/model startup failure
is recorded as an invalid run, not an agent result.

Do not treat a run as comparable unless `report.json` has
`valid_for_comparison: true`. Preserve `manifest.json`, `raw.jsonl`,
`report.json`, `report.md`, and their SHA-256 values together. Before changing
any product policy or claim, follow the blinded human-label process in
[`docs/EVAL_PROTOCOL.md`](../../docs/EVAL_PROTOCOL.md).
