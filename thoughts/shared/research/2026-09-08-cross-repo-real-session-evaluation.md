---
date: 2026-09-08
commit: 46c7b832756f73f1cdade53c6a11f3b367914d0e
branch: main
window: 2026-06-11..2026-09-08
scope: all local audit sessions and repositories
status: complete
tags: [real-usage, sessions, cross-repo, telemetry, effectiveness, reasoning-core]
---
# Cross-repository real-session evaluation

## Executive Summary

This evaluation uses existing local audit sessions across repositories instead of an artificial pilot. It scans the same date-partitioned JSONL/JSONL.GZ population used by `rc benchmark`, groups events by full `project_dir` and `session_id`, and reports operational and enforcement signals descriptively.

The recorded 90-day snapshot contains approximately 171,652 events across 136 full project paths (95 basename groups) and 2,466 sessions. The instrumentation provides broad coverage, but the data does not yet support a causal claim about reasoning quality: 6,034 events are fail-open, 6,294 contain sidecar-unavailability reasons, and scope signals are mostly warnings. Human outcome labeling was absent when this snapshot was first recorded; the labeling update below starts that missing evidence.

## Labeling update

A later gzip-aware scan was used only to build the first labeling queue. It
contained 36,399 labelable edit/write decisions across 455 sessions and 41
project paths after excluding temporary fixture roots. One representative
decision per session was selected, producing a 40-candidate,
repository-balanced queue across decision buckets, signal sources, hosts, and
months. These later counts are a queue snapshot, not a replacement for the
canonical 171,652-event population above.

Five decisions have now been reviewed and stored in the local training set:

| Label | Positive count |
|---|---:|
| `scope_drift` | 1 |
| `plan_violation` | 0 |
| `structural_regression` | 1 |
| `syntax_type_error` | 0 |
| `test_failure` | 0 |

The seed includes five repositories and five gate contexts. It is not large
enough for a quality estimate, inter-rater agreement, threshold change, or
causal comparison. The immutable comparison manifest is
`baseline-2026-09-08-real-session-eval`.

## Population and Method

| Item | Value |
|---|---:|
| Window | 2026-06-11 through 2026-09-08 |
| Events | 171,652 (recorded `rc benchmark --days 90` snapshot) |
| Full project paths | 136 |
| Repository basename groups | 95 |
| Sessions | 2,466 |
| Hosts | 4 |
| Median events/session | 2 |
| p95 events/session | 378 |
| Largest session | 6,368 events |

The canonical aggregate was generated with:

```bash
rc benchmark --days 90 \
  --json /tmp/reasoning-core-benchmark-90.json \
  --output /tmp/reasoning-core-benchmark-90.md
```

The cross-repository breakdown additionally reads `.jsonl` and `.jsonl.gz`, normalizes legacy decision spellings (`allow`/`allowed`, `block`/`blocked`), and groups by `project_dir` and `session_id`. Event collection and gzip handling are implemented in `src/rc_cli.py:796-824`; audit fields are created in `src/hooks/audit_log.py:190-240`.

The audit directory is live and can change while a rolling-window report is being rerun: a validation rerun on 2026-09-08 returned 171,694 benchmark events. The metrics below intentionally remain tied to the recorded 171,652-event snapshot rather than mixing two populations.

## Executive Metrics

| Metric | Count | Rate/assessment |
|---|---:|---|
| Allowed | 129,648 | 75.5% |
| Blocked | 3,214 | 1.87% |
| Warn | 12,462 | 7.26% |
| Fail-open | 6,034 | 3.52% |
| Scope-creep catches | 9,600 | 5.59% of events |
| Scope-creep blocked | 169 | 1.76% of scope catches |
| Scope-creep warned | 9,431 | 98.24% of scope catches |
| Sidecar-unavailability reasons | 6,294 | 3.67% of events |
| Retry-after-block | 19 | Low observed proxy count |
| Allowed via override | 70 | Mostly one repository |
| Operator overrides | 2 | In `reasoning-core` |
| Operator confirmations | 1 | In `reasoning-core` |
| Median / p95 latency | 7 / 2,321 ms | Operational, not quality |

The benchmark’s token-cost and false-positive values remain synthetic operational proxies. They are not dollar costs, human judgments, or proof that a block was correct; the implementation defines the formulas at `src/rc_cli.py:1319-1360` and `:1460-1497`.

## Session-Level Analysis

### All observed sessions

| Session property | Sessions | Share |
|---|---:|---:|
| Any blocked event | 616 | 24.98% |
| Any warning | 349 | 14.15% |
| Any fail-open | 279 | 11.31% |
| Any scope signal | 304 | 12.33% |
| No block, warning, or fail-open | 1,751 | 70.99% |

These categories overlap. A session with a fail-open and a block is counted in both categories.

### Substantive sessions

Because the median session has only two events, a second view uses sessions with at least ten events (`n=637`):

| Session property | Sessions | Share |
|---|---:|---:|
| Any blocked event | 372 | 58.40% |
| Any warning | 327 | 51.33% |
| Any fail-open | 277 | 43.49% |
| Any scope signal | 302 | 47.41% |

This is exposure, not failure rate. A session containing a warning does not establish that the warning was correct or harmful.

## Cross-Repository Results

The highest-volume repository, `lustro`, contributes about 37.4% of all events. Volume-weighted global metrics therefore do not represent a typical repository. The following rollup shows the largest populations and selected operational outliers; rates are within each repository.

| Repository/path basename | Events | Sessions | Blocked | Warn | Fail-open | Scope catches | Scope blocked |
|---|---:|---:|---:|---:|---:|---:|---:|
| `lustro` | 64,255 | 366 | 0.45% | 5.75% | 0.19% | 1,626 | 0 |
| `circit-cowork` | 12,353 | 48 | 0.72% | 12.92% | 12.84% | 1,596 | 0 |
| `circit-hive-mind` | 8,840 | 50 | 1.91% | 6.81% | 3.19% | 641 | 57 |
| `circit-app` | 8,650 | 41 | 2.34% | 10.38% | 8.16% | 763 | 0 |
| `ArtificialWolvesFinal` | 8,388 | 91 | 0.36% | 11.66% | 0.24% | 972 | 0 |
| `sovereign-agent-setup` | 6,578 | 366 | 1.31% | 7.92% | 1.26% | 458 | 0 |
| `reasoning-core` | 5,136 | 330 | 33.04% | 4.11% | 3.76% | 228 | 110 |
| `ai-control-room` | 4,588 | 32 | 0.44% | 13.62% | 9.96% | 625 | 0 |
| `nieruchomosci-mcp` | 3,768 | 67 | 1.01% | 0.00% | 21.28% | 0 | 0 |
| `status-service` | 2,021 | 8 | 0.89% | 30.78% | 0.00% | 458 | 0 |

### Outlier handling

- `/fake/repo` produced 917 events and 397 blocks. It is treated as a synthetic/fixture path, not a production repository.
- `reasoning-core` has unusually high blocking because the current measurement and development work occur inside it; it should be reported separately from unrelated product repositories.
- `lustro`, `circit-cowork`, and `circit-app` dominate volume or availability noise, so their values should not be generalized to all repositories.

## What Went Well

### Broad provenance coverage

Events carry `session_id`, `project_dir`, `decision_id`, host, tool, reason, gate, signal source, and configuration hash. The schema is assembled in `src/hooks/audit_log.py:190-240`, and the append path is redacted and append-only at `src/hooks/audit_log.py:242-318`.

### Cross-repo operational visibility

The audit root contains enough volume to compare repositories, sessions, signal sources, latency, and failure modes without creating synthetic tasks. `rc benchmark` already supports date windows, JSON output, Markdown output, and compressed historical logs through `src/rc_cli.py:155-207` and `:796-824`.

### Deterministic signals are identifiable

The retained data separates sources such as `plan_grounding`, `symbolic_fallback`, `structural`, `mcp_gate`, `oracle`, and `lang_lock`. This allows policy/plan/parse/structural signals to be analyzed separately from the three observed `neural` events rather than treating all decisions as equivalent.

## What Went Wrong

### Sidecar availability is a major confounder

There are 6,034 fail-open decisions and 6,294 sidecar-unavailability reasons. The most common causes are 3,000 ms hard-cap timeouts, request timeouts, and connection-refused errors. These are reliability outcomes, not evidence that the reasoning gate was correct or incorrect.

### Scope detection is mostly advisory

Of 9,600 scope signals, 9,431 were warnings and only 169 were blocked. Without human labels or a downstream outcome, the data cannot distinguish useful early advice from false positives, ignored advice, or benign plan drift.

### The existing effectiveness monitor undercounts history

`scripts/monitor-effectiveness.py:29-44` scans only `*.jsonl`, while retained history is also compressed. On this machine it read 4,816 rows for the 89-day invocation, versus roughly 171k rows from the gzip-aware `rc benchmark` scan. It is therefore unsuitable as the sole all-repository historical evaluator without a gzip-aware path.

### Human outcome labels were initially absent

`~/.local/share/reasoning-core/training_set.jsonl` was not present when the
canonical snapshot was created. The label collection implementation exists in
`src/hooks/_training_set.py:111-244`; the current update has begun a verified
set, but it remains far below the 10-positive-per-label target and still needs
independent second labels before confirmatory use.

### Global aggregation is volume-skewed

One repository contributes more than one-third of all events, while many sessions contain only one or two records. A single global block/warn rate is therefore not a representative “real usage” quality score.

## Real-Session Evaluation Plan

This replaces the artificial pilot as the primary next step:

1. **Freeze the population snapshot.** Store the exact window, event-file inventory, parser version, and configuration hashes. Re-run the gzip-aware cross-repo aggregation from the same snapshot.
2. **Stratify instead of volume-weighting.** Report per-repository and per-session metrics, plus unweighted repository medians. Keep `/fake/repo`, the `reasoning-core` development repo, and home-directory/root paths in separate cohorts.
3. **Prioritize availability first.** Analyze fail-open and sidecar-unavailable events by repository, day, host, and configured timeout before interpreting gate effectiveness.
4. **Sample existing decisions for labels.** Draw a stratified sample across repositories, session sizes, decisions, signal sources, and scope reasons. Label final outcomes using `scope_drift`, `plan_violation`, `structural_regression`, `syntax_type_error`, and `test_failure`; do not infer correctness from the decision alone.
5. **Link outcomes where possible.** Join `decision_id`, `session_id`, and `file_path` to `rc reconcile`, commit/test evidence, and operator labels. Keep unlabeled events in the denominator only for exposure metrics.
6. **Use two primary descriptive endpoints.**
   - **Operational reliability:** fail-open/unavailable rate, p95 latency, and blocked/warn exposure by session.
   - **Validated quality:** labeled harmful-edit rate among sampled decisions, with inter-rater agreement.
7. **Report both pooled and balanced views.** Publish global totals, per-repository distributions, and a repository-balanced median/interval. Do not use the pooled total as a causal product claim.

## Recommended Immediate Actions

1. Build the gzip-aware cross-repo evaluator as a report/analysis tool, reusing `rc benchmark` semantics rather than modifying enforcement behavior.
2. Investigate the six highest availability-noise cohorts, beginning with `nieruchomosci-mcp`, `circit-cowork`, `circit-app`, and `ai-control-room`.
3. Exclude `/fake/repo` from production summaries and separate `reasoning-core` self-observation from unrelated repositories.
4. Create a local, stratified labeling queue from existing events before changing thresholds, model backends, or enforcement defaults.
5. Treat current results as a retrospective operational baseline. Do not claim reasoning-core improved real coding quality until labeled downstream outcomes exist.

## External Dependencies and Data Boundaries

- Data source: local `~/.local/share/reasoning-core/events`, including rotated `.jsonl.gz`.
- Reporting command: `rc benchmark`.
- Session reconciliation: `rc reconcile` and `eval/reconcile_session.py`.
- Label collection: `rc label`, `rc label --random`, and `rc label-stats`.
- No remote telemetry or external data source was used.

## Open Questions

1. Which event cohorts are real repository sessions versus fixtures, bootstrap runs, or home-directory tool sessions?
2. Can existing commits/tests be reliably joined to individual `decision_id` values across all repositories?
3. Should the monitor be updated to read compressed historical files, or should all-repository analysis live in a new reporting command?
4. Which repository-balanced sampling policy should govern human labels?
5. How should sidecar-unavailability events be excluded from quality denominators while remaining visible in reliability metrics?
6. Are the high-blocking `reasoning-core` and `/fake/repo` cohorts expected test traffic, or do they expose a real enforcement/configuration problem?

## Appendix: Existing Code Paths

- Audit event schema and redaction: `src/hooks/audit_log.py:190-318`
- Global event scan: `src/rc_cli.py:796-824`
- Benchmark metrics and proxies: `src/rc_cli.py:1309-1497`
- Benchmark command/output: `src/rc_cli.py:155-207`, `:1572-1641`
- Daily/weekly report wrapper: `scripts/daily-benchmark.sh:23-120`
- Existing effectiveness monitor: `scripts/monitor-effectiveness.py:29-144`
- Real-session label storage: `src/hooks/_training_set.py:111-244`
- Session reconciliation: `eval/reconcile_session.py:35-104`
