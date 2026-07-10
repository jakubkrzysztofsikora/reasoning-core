# CHANGELOG — 2026-07-10

## `rc benchmark` — audit-log measurement report

New `rc benchmark` subcommand turns the local audit log into a structured
measurement report.

### Behavior

- Produces a Markdown report from `~/.local/share/reasoning-core/events/`.
- Optional JSON output via `--json PATH` for CI ingestion.
- Metrics:
  - Decision counts (allowed, blocked, warn, shadow_blocked, fail-open,
    allowed_via_override)
  - Events by class (contract, oracle, rule_engine, plan_grounding,
    self_protection, shadow, override, fail_open, other)
  - Scope-creep catches (plan_impl_drift + contract_violation)
  - Retry-after-block proxy
  - False-positive proxy (capped at 1.0)
  - Median / p95 latency and median block latency
  - Token-cost proxy (synthetic week-over-week unit)
  - Override survival ratio
- Supports `--before YYYY-MM-DD` / `--after YYYY-MM-DD` for time-windowed
  comparisons. `--before` is exclusive, `--after` inclusive.
- Override survival is omitted for historical windows because it compares
  against the current working tree.
- `--after` auto-extends `--days` so older windows are not silently truncated.
- A `--before`-only query with the default `--days` emits a stderr note that
  the window is bounded.

### Code changes

- `src/rc_cli.py`
  - New `_classify_severity()`, `_token_cost_proxy()`, `_percentile()`,
    `_compute_benchmark()`, `_fmt_benchmark_markdown()`,
    `_override_survival_ratio()`, `cmd_benchmark()`.
  - New `_git_repo_root()` helper.
  - Refactored `cmd_override_survival()` to share override-survival logic
    via `_override_survival_counts()`.
- `tests/test_rc_cli_benchmark.py` — 12 tests.
- `docs/USAGE.md`, `docs/CONFIGURATION.md`, `docs/ROADMAP.md`, `README.md` —
  updated to describe the new command.

### Verification

- `pytest -m "not live" -q --timeout=60` passes.
- Adversarial review identified and resolved issues around repo-root guard,
  date-window/days interplay, false-positive double-counting, duplicate
  survival logic, table labelling, and non-string timestamps.

---

## Daily benchmark trend service

New `scripts/daily-benchmark.sh` + `scripts/install-daily-benchmark-launchagent.sh`
keep a rolling trend line of `rc benchmark` reports.

### Behavior

- Runs `rc benchmark --days 1` and `rc benchmark --days 7` every day at 06:17
  local time via a launchd LaunchAgent.
- Stores dated reports under `~/.local/share/reasoning-core/benchmarks/YYYY-MM-DD/`.
- Writes `benchmark-day.{json,md}`, `benchmark-week.{json,md}`, and `trend.md`
  with day-over-day deltas when a previous day's JSON exists.
- Symlinks `~/.local/share/reasoning-core/benchmarks/latest` to the most recent
  run for quick inspection.

### Code changes

- `scripts/daily-benchmark.sh` — idempotent runner; safe to invoke by hand.
- `launchd/com.reasoning-core.daily-benchmark.plist` — LaunchAgent template.
- `scripts/install-daily-benchmark-launchagent.sh` — installs, validates, and
  loads the plist.
- `README.md` §"Daily benchmark trend service" — user-facing docs.
- `docs/PILOT_2026_07_10.md` — measurement plan now references the automated
  daily service.

### Verification

- Installed and loaded locally; manual `launchctl start` produces the expected
  dated output directory and symlink.

