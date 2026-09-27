# Feedback-Timing Pilot

This is an 80/20 mechanism study for one narrow claim: whether a configured,
deterministic reasoning-core policy can return actionable pre-write feedback
sooner than the selected post-write check for the same edit.

It invokes the hook directly, so it does not measure a host's hook-delivery
latency. It also does not measure agent task success, code quality, regression
reduction, token use, or general superiority over other scanners. It must not
be used to make those claims.

## What it runs

`probes.json` defines five expected deterministic violations and two compliant
negative controls. Every probe is run in a fresh temporary Git repository.

- **Treatment:** invokes `src/hooks/pre_edit_guard.py` in `RC_MODE=copilot`
  with plan contracts and the rule engine enabled. The default local Mamba SSM
  remains on the score path, with `RC_NEURAL_CORROBORATED=1`.
- **Control:** writes the same change, then runs the probe's named local check.
  Rule and contract probes use `check_policy.py`; this intentionally measures
  feedback timing, not whether a generic linter has equivalent policy coverage.

The output includes raw trials, frozen probe/configuration input, JSON results,
and a Markdown report.

## Claimable run

Start the sidecar and wait until its `/health` response says
`"model_loaded": true`. Use one runtime-hook host configuration and record its
version separately; do not combine Copilot or Vibe MCP-mediated results with
Tier-1 results.

```bash
bash scripts/start-sidecar.sh
# Wait for: {"model_loaded": true, ...}
curl -fsS http://127.0.0.1:8765/health

.venv/bin/python -m eval.timing_pilot.run \
  --repeats 3 \
  --out-dir eval/runs/timing-pilot-$(date -u +%Y%m%dT%H%M%SZ)
```

A claimable report needs all expected outcomes, zero negative-control blocks,
and zero hook/sidecar fail-opens. Report the ratio by policy category, not only
as an aggregate. A "10x faster feedback" statement is allowed only when the
preregistered median control/treatment ratio is at least 10x on the declared
scope. This fixture's local controls may be faster than the Mamba-backed hook;
that is a valid result, not a reason to omit it.

## Smoke run

When the model is unavailable, this command verifies fixture and reporting
wiring only:

```bash
.venv/bin/python -m eval.timing_pilot.run \
  --repeats 1 \
  --allow-symbolic-fallback \
  --out-dir /tmp/rc-timing-pilot-smoke
```

The report is explicitly marked `symbolic_fallback: true` and cannot support an
SSM, product-performance, or 10x claim.

## Next step

If this mechanism study is reliable, run the existing blinded outcome protocol
in [`docs/EVAL_PROTOCOL.md`](../../docs/EVAL_PROTOCOL.md) before making claims
about regressions or code quality.
