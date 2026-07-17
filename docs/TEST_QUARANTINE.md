# Test quarantine

Last updated: 2026-07-16

## Quarantined tests

These tests are valid but require a running SSM embedder or generative sidecar,
which can take minutes to load on CPU. They are marked `slow` and run in a
separate CI job with a longer timeout, not in the fast unit-test lane.

| File | Reason | Owner | Target fix date |
|---|---|---|---|
| `tests/test_baseline_drift.py` | Loads real SSM sidecar; times out on CPU | reasoning-core maintainer | 2026-07-30 |
| `tests/test_pre_plan_guard.py` | Loads generative sidecar; times out on CPU | reasoning-core maintainer | 2026-07-30 |

## Running quarantined tests

```bash
pytest -m "slow" --timeout=600 tests/test_baseline_drift.py tests/test_pre_plan_guard.py
```

## Running the fast suite

```bash
pytest -m "not live and not slow" --timeout=60
```
