---
date: 2026-09-08
commit: 46c7b832756f73f1cdade53c6a11f3b367914d0e
branch: main
tags: [benchmarking, evaluation, real-usage, telemetry, baselines, reasoning-core]
status: complete
---
# Research: Benchmarking improvements and real-usage evaluation

## Summary

Reasoning-core has two separate measurement products: controlled paired task evaluations under `eval/`, and operational real-usage reports produced from local audit JSONL. The latest local usage snapshot is 2026-09-08, but it is a global operational diagnostic rather than causal evidence about repository quality.

The last committed end-to-end evaluation is `smoke-001`, a two-task stub run with an intentionally inconclusive verdict. The next evaluation should use the pre-registered human-labeled reasoning-quality endpoint, freeze a clean immutable baseline, resolve the current arm-support mismatch, and join controlled outcomes with project/session-scoped telemetry without treating block counts as product effectiveness.

## Files Involved

### Backend

| File | Layer | Purpose |
|------|-------|---------|
| N/A | — | No .NET/C# backend was found; the repository is a local Python/Shell sidecar and CLI. |

### Frontend

| File | Layer | Purpose |
|------|-------|---------|
| N/A | — | No Vue.js/TypeScript frontend was found; the only TypeScript file is part of the repository corpus, not an application UI. |

### Runtime and CLI

| File | Layer | Purpose |
|------|-------|---------|
| `src/hooks/pre_edit_guard.py` | Hook | Reconstructs proposed edits, calls the sidecar, applies gates, and emits audit events. |
| `src/hooks/_dispatch.py` | Gate dispatch | Separates pre-score and post-score enforcement gates. |
| `src/hooks/audit_log.py` | Repository/telemetry | Appends redacted session JSONL events under the local audit root. |
| `src/s2_core.py` | Sidecar service | Exposes `/health`, `/score`, `/metrics`, and `/baseline`; returns an `ImpactReport`. |
| `src/rc_cli.py` | Reporting CLI | Implements `rc benchmark`, `rc baseline`, and `rc reconcile`. |
| `src/baselines.py` | Baseline registry | Creates immutable manifests and compares code/configuration context. |
| `scripts/daily-benchmark.sh` | Scheduler wrapper | Produces dated daily/weekly reports and day-over-day trend output. |
| `launchd/com.reasoning-core.daily-benchmark.plist` | Scheduler | Runs the daily benchmark service on macOS. |

### Evaluation and data

| File | Layer | Purpose |
|------|-------|---------|
| `eval/run_suite.py` | Evaluation runner | Samples tasks, randomizes arm order, freezes run inputs, and invokes the task harness. |
| `eval/run_task.sh` | Evaluation harness | Creates per-task vanilla/treatment runs, executes tests, and records usage/results. |
| `eval/aggregate.py` | Evaluation analysis | Pairs task records and computes metric deltas, intervals, adjusted p-values, and a verdict. |
| `eval/stats.py` | Statistics | Provides paired tests, bootstrap intervals, and multiple-comparison correction. |
| `eval/reconcile_session.py` | Session analysis | Compares edited files with session `gate_edit` audit rows. |
| `src/hooks/_training_set.py` | Label collection | Stores and reports human labels for real-session decisions. |
| `eval/baselines/README.md` | Baseline documentation | Defines immutable baseline and artifact-verification workflow. |
| `eval/baselines/baseline-2026-08-09.json` | Baseline artifact | Stores the active code/configuration context and risk-label schema. |

### Research and documentation

| File | Layer | Purpose |
|------|-------|---------|
| `docs/EVAL_PROTOCOL.md` | Protocol | Defines the pre-registered endpoint, five arms, labeling, statistics, and kill criteria. |
| `docs/BENCHMARKS.md` | Results summary | Condenses prior multi-run benchmark results and their limitations. |
| `docs/PILOT_2026_07_10.md` | Usage pilot | Defines an advise-vs-copilot real-usage comparison. |
| `docs/CHANGELOG-2026-07-10.md` | Operations history | Documents the daily benchmark service and report storage. |
| `eval/runs/smoke-001/report.md` | Latest committed run | Records the last committed end-to-end smoke evaluation. |
| `thoughts/shared/research/2026-05-05-iter1-eval-whitepaper.md` | Historical evaluation | Original eight-task Setup A/Setup B comparison. |
| `thoughts/shared/research/2026-05-07-iter2-eval-whitepaper.md` | Historical evaluation | Replication with three tasks and two judges. |
| `thoughts/shared/research/2026-05-08-iter3-eval-whitepaper.md` | Historical evaluation | Iteration 3 evaluation and preregistration context. |
| `thoughts/shared/research/2026-05-23-reasoning-core-effectiveness-audit.md` | Real-usage audit | 19-day local audit review and value/noise separation. |
| `thoughts/shared/research/2026-06-13-reasoning-core-efficiency-infrastructure.md` | Measurement research | Transcripts, audit logs, and `rc` efficiency reporting. |

## Latest Benchmark and Session Data

### Freshest local benchmark snapshot

The newest generated directory is `~/.local/share/reasoning-core/benchmarks/2026-09-08/`. It contains daily, trailing-week, trend, baseline-reference, and baseline-id files.

| Window | Events | Allowed | Blocked | Warn | Fail-open | Scope-creep catches | Scope-creep blocked | Median / p95 latency | Token-cost proxy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-09-08 day | 3,076 | 2,971 | 75 | 26 | 0 | 20 | 0 | 6 / 8 ms | 3,227.5 |
| Trailing 7 days | 10,859 | 9,221 | 145 | 671 | 4 | 388 | 0 | 6 / 1,888 ms | 12,113.0 |

Sources: `~/.local/share/reasoning-core/benchmarks/2026-09-08/benchmark-day.md:5-22` and `benchmark-week.md:5-22`.

The day-over-day report shows the trailing snapshot moving from 2026-09-07 to 2026-09-08 as follows: total events `6,849 → 10,859`, blocked `62 → 145`, warnings `624 → 671`, fail-open `1 → 4`, scope-creep catches `281 → 388`, scope-creep prevented `1 → 0`, median latency `7 → 6 ms`, p95 latency `2,983 → 1,888 ms`, and token-cost proxy `7,908 → 12,113`. These are overlapping rolling windows, not independent cohorts. Source: `~/.local/share/reasoning-core/benchmarks/2026-09-08/trend.md:5-22`.

The reports explicitly describe token cost and false-positive values as operational proxies, not dollar estimates or causal quality measures. The 2026-09-08 daily false-positive proxy is `0.000`; the trailing-week value is `0.138`. No operator override survived in the trailing-week snapshot.

### Freshest repository-specific usage session

The current repository session is recorded at `~/.local/share/reasoning-core/events/2026-09-08/anon-6e6d837e5f2b.jsonl`. The observed file contains 117 events from `2026-09-08T08:04:30Z` through `2026-09-08T08:12:51Z`: one injected `SessionStart` event and 116 allowed `Bash` events, with no blocked or warning decisions. This is useful as a session-level provenance example, but it is not a representative treatment/control sample.

The corresponding persistent session manifest is `~/.local/state/reasoning-core/sessions/06b2f9882d32_e3b0c44298fc.json`; it identifies the repository as Python and records extension distribution and task-spec hashes.

### Active baseline

`baseline-2026-08-09` is the active immutable context baseline. `rc baseline show baseline-2026-08-09` reports:

- captured at `2026-08-09T12:16:31Z`;
- code SHA `5947d66adb957340a8a130fafc86bdb64ecc1036`;
- dirty working tree;
- empty `audit_window_metrics`;
- `RC_MODE=copilot`, `RC_PLAN_GROUNDING=2`, `RC_ORACLE_BLOCK=1`, `RC_RULE_ENGINE=1`;
- `S2_FAIL_CLOSED=1`, `S2_HARD_CAP_MS=1500`, and risk-label schema version `2`.

The committed manifest is `eval/baselines/baseline-2026-08-09.json:13-61`. It is a configuration/context record, not a quality score. Its referenced local artifact directory exists but contains no visible files in the current filesystem scan.

### Last controlled evaluation

The latest committed end-to-end run is `eval/runs/smoke-001/`. It used two tasks, two arms, four total runs, 311 audit events, and a stub that returned identical patches for both arms. Every paired metric difference was zero and the report verdict was `inconclusive`, not evidence of treatment effectiveness. Sources: `eval/runs/smoke-001/report.md:1-18` and `:31-47`.

The latest committed judge/grounding artifacts are from 2026-05-06, including `eval/runs/qwen_kappa_gate.json`, `eval/runs/qwen_grounding_v3_20260506.json`, and `eval/runs/judge_independence_pilot_20260506.json`. The v3 grounding artifact records `n=131`, κ `0.8025`, accuracy `0.9008`, and `gate_pass=true`; this is judge/grounding reliability evidence, not a current end-to-end product-effect estimate.

## Data Flow

### Real-usage path

1. The edit hook receives `Edit`, `Write`, or `MultiEdit`, reconstructs the proposed source, and builds the sidecar request at `src/hooks/pre_edit_guard.py:226-305`.
2. The loopback sidecar exposes `/score`; endpoint wiring is at `src/s2_core.py:1230-1316`, and the `ImpactReport` shape is at `src/s2_core.py:81-116`.
3. Structural deltas, embedding/coherence distance, session drift, project dimensions, and regression conditions are assembled at `src/s2_core.py:968-980` and `:1005-1097`.
4. Pre-score/post-score gate dispatch is separated in `src/hooks/_dispatch.py:59-61` and `:368-370`. Mode handling and symbolic fallback are at `src/hooks/pre_edit_guard.py:366-416`.
5. The hook emits decision, reason, latency, risk-vector, signal-source, gate, session, and project fields at `src/hooks/pre_edit_guard.py:528-598`. Common metadata and configuration hash are added by `src/hooks/audit_log.py:190-240`.
6. `audit_log.append_event()` redacts secret-shaped values, creates a UTC date directory, appends one JSON object per line, and rotates old files at `src/hooks/audit_log.py:242-318`. The default root is defined at `src/hooks/audit_log.py:66-73`.
7. `rc benchmark` is registered at `src/rc_cli.py:1753-1763`; `cmd_benchmark()` writes JSON/Markdown at `src/rc_cli.py:155-207`. Event scanning reads dated `.jsonl` and `.jsonl.gz` files at `src/rc_cli.py:796-824`.
8. `_compute_benchmark()` counts decisions, severity classes, signal sources, scope-creep outcomes, latency, and operational proxies at `src/rc_cli.py:1374-1488`. Severity classification is at `:1309-1343`, token-cost proxy at `:1346-1360`, and false-positive proxy at `:1474-1484`.
9. `scripts/daily-benchmark.sh:23-120` runs `rc benchmark --days 1` and `--days 7`, stores the active baseline reference, writes trend deltas, and updates the `latest` symlink. The macOS schedule is in `launchd/com.reasoning-core.daily-benchmark.plist:15-25`.

### Controlled-evaluation path

1. `eval/run_suite.py:48-75` freezes dataset hash, task IDs, seed, arm schedule, arm configuration, code SHA, and raw-result path in `run_manifest.json`.
2. Task selection and per-pair arm randomization are implemented at `eval/run_suite.py:89-129`. The harness invokes `eval/run_task.sh`, which clones a base repository, configures an arm, runs the agent and tests, extracts transcript token usage, and writes per-task JSON at `eval/run_task.sh:72-324`.
3. `eval/aggregate.py:110-131` pairs treatment and vanilla records by task ID. It summarizes ten metrics, computes BCa bootstrap intervals, paired Wilcoxon p-values, and Holm-adjusted p-values at `eval/aggregate.py:196-206` and `:254-270`.
4. The legacy aggregator verdict checks regression-rate improvement, resolved-rate non-inferiority, and latency ratio at `eval/aggregate.py:143-190`; possible results are `ship`, `kill`, or `inconclusive`.
5. The pre-registered protocol uses a different primary endpoint: human-labeled scope drift, plan violation, or structural regression. It specifies five arms, blinded labelers, κ ≥ 0.70, paired-proportions statistics, and operational kill criteria in `docs/EVAL_PROTOCOL.md:1-115`.
6. Baseline capture/show/compare is implemented at `src/rc_cli.py:67-107` and `src/baselines.py:57-149`. The registry refuses to overwrite an existing baseline ID and can verify referenced artifact hashes.

## Existing Patterns

1. **Frozen paired run manifests.** `eval/run_suite.py:48-75` hashes the dataset, records task IDs and seed, randomizes arm order, captures `RC_*/S2_*` configuration, and writes the manifest before execution. This is the strongest existing template for a comparable next evaluation.
2. **Paired outcome analysis.** `eval/aggregate.py:110-206` joins treatment/control records by task, computes per-task deltas, and applies bootstrap/Wilcoxon/Holm analysis. `tests/test_eval_aggregate.py:1-89` covers complete/incomplete pairs and inconclusive outcomes.
3. **Immutable context baselines.** `src/baselines.py:57-149` captures code state, configuration hash, guard hash, embedder, risk schema, and artifact references; `eval/baselines/README.md:1-18` documents the workflow.
4. **Operational telemetry snapshots.** `src/rc_cli.py:796-824` and `:1309-1497` stream local JSONL audit data into daily/weekly metrics. `scripts/daily-benchmark.sh:23-120` adds retention-independent dated snapshots and day-over-day trend output.
5. **Distributed human-label collection.** `src/hooks/_training_set.py:111-244` supports `rc label`, random unlabeled sampling, and `rc label-stats`; the protocol defines the target label categories and storage in `docs/EVAL_PROTOCOL.md:76-115`.
6. **Session reconciliation.** `eval/reconcile_session.py:35-104` and the `rc reconcile` path compare working-tree edits with session `gate_edit` rows, providing a provenance check for real usage.

## Existing Research: What Is New

There is no `thoughts/shared/decisions/` directory in the repository. Existing research already covers:

- Iteration 1–3 controlled benchmark design and judge reliability (`thoughts/shared/research/2026-05-05-iter1-eval-whitepaper.md`, `2026-05-07-iter2-eval-whitepaper.md`, `2026-05-08-iter3-eval-whitepaper.md`);
- real-usage effectiveness and noise audits (`2026-05-23-reasoning-core-effectiveness-audit.md`, `2026-06-01-reasoning-core-1000pct-improvements.md`, `2026-06-02-effectiveness-monitoring.md`);
- local measurement infrastructure (`2026-06-13-reasoning-core-efficiency-infrastructure.md`);
- a 48-hour advise-vs-copilot pilot (`docs/PILOT_2026_07_10.md` and `docs/CHANGELOG-2026-07-10.md`).

This report adds the first consolidated view of the current 2026-09-08 local benchmark/session artifacts against that prior research. The new findings are the latest rolling metrics, the latest repository-specific session provenance, the unchanged 2026-08-09 context baseline, and the continued gap between operational telemetry and a recent causal end-to-end evaluation.

## Architecture Notes

- The repository has no application Controller/Service/Repository or Vue Store/API layers; its boundaries are hook → loopback sidecar → append-only audit log → CLI/reporting and offline evaluation.
- `rc benchmark --days 7` scans the configured audit root globally. It is not automatically restricted to the current repository, so global usage counts must not be presented as repository-specific outcomes.
- The daily benchmark, controlled task evaluation, and baseline registry are separate data products. No current report joins all three into one causal quality result.
- The legacy aggregator and the pre-registered protocol use different primary endpoints and decision rules. The legacy runner supports a broader arm list than the current `eval/run_task.sh` shell harness, which currently accepts only `vanilla` and `treatment` (`eval/run_suite.py:166`, `eval/run_task.sh:35-48`).
- The active baseline is dirty and has no audit-window quality metrics. A new comparison requires a clean, uniquely identified baseline and frozen configuration before execution.
- The real-session label path is implemented, but no default `~/.local/share/reasoning-core/training_set.jsonl` was visible during the scan. The protocol’s label quotas therefore cannot be assumed satisfied.

## Prepared Next Evaluation

The next evaluation should be prepared as a staged, pre-registered run rather than inferred from the daily block totals:

1. **Freeze context.** Clean or explicitly record the working tree, capture a new immutable baseline ID, verify it, pin the agent/model/judge versions, and record the dataset and configuration hashes. Do not overwrite `baseline-2026-08-09`.
2. **Resolve the harness mismatch.** The protocol calls for five arms (A–E) with pilot `n=20` per arm and full `n=100` per arm (`docs/EVAL_PROTOCOL.md:41-64`), but the current shell harness accepts only vanilla/treatment. A five-arm confirmatory run is blocked until the arm contract is made consistent; a two-arm run should be treated as a rehearsal or explicitly re-registered design.
3. **Collect labels before confirmation.** Populate and verify the distributed training set for `scope_drift`, `plan_violation`, `structural_regression`, `syntax_type_error`, and `test_failure`; require two blinded labelers and κ ≥ 0.70 before using the confirmatory set (`docs/EVAL_PROTOCOL.md:7-38`, `:76-115`).
4. **Run the pilot with hard operational gates.** Capture manifests, transcripts, hook events, sidecar logs, tests, per-task metrics, and human labels. Apply the protocol’s abort conditions: treatment aborts over 10%, labeled FPBR over 25%, or p95 block latency over 5 seconds (`docs/EVAL_PROTOCOL.md:118-154`).
5. **Use the validated endpoint.** Make reasoning-quality failure rate (any scope drift, plan violation, or structural regression) primary; report SWE-bench clean success and regression as secondary. Keep deterministic policy/plan/parse/lint/structural checks distinct from neural advisory scores.
6. **Join real usage carefully.** Attribute audit rows by `project_dir`, `session_id`, and `decision_id`; report repository-specific cohorts separately from the global daily snapshot. Use `rc reconcile` and label links to validate that observed edits have provenance.
7. **Compare only after preregistration.** Use the immutable run manifest, paired analysis, human-label agreement, and the decision table together. Treat the daily operational trend as context and diagnostics, not as proof that a model/backend or threshold caused an outcome.

## External Dependencies

- macOS `launchd` schedules the daily benchmark service.
- The sidecar is a local loopback HTTP service with `/score`, `/health`, `/metrics`, and `/baseline` endpoints.
- Controlled tasks use the SWE-bench Verified Python subset and the configured Claude/task harness.
- Historical grounding reliability artifacts use external judge-model outputs (Qwen, Devstral, Llama, Gemma, and Mistral variants) stored as local evaluation artifacts.
- The active baseline records the `mlx` reasoner backend, `mamba-130m` embedder, Python 3.14.6, and macOS arm64 environment; these are context fields, not independent quality evidence.

## Open Questions

1. Should the next confirmatory comparison be the full five-arm protocol or a newly registered two-arm design while the shell harness remains limited?
2. Which exact repository/session cohort should be used for real-usage comparison, given that current `rc benchmark` aggregates the global audit root?
3. Where is the missing default `training_set.jsonl`, and have the protocol’s per-label quotas actually been met?
4. Should the legacy `eval/aggregate.py` decision block be retired or explicitly labeled as a smoke/rehearsal path so it cannot be confused with the pre-registered human-label endpoint?
5. Why does the active baseline reference an existing but empty local artifact directory, and which raw audit/run artifacts must be retained and hash-verified for the next comparison?
6. Which agent, reasoner backend, embedder revision, and judge set should be pinned for the next run so changes are attributable?
