---
date: 2026-09-08
commit: 46c7b832756f73f1cdade53c6a11f3b367914d0e
branch: main
status: complete
tags: [value-proposition, real-usage, evaluation, observability, reliability, agent-quality, tooling]
---
# Research: Value promise versus observed evidence and evaluation improvements

## Summary

The repository currently substantiates a local, provenance-rich audit and guardrail system: it evaluates proposed edits, emits deterministic and model-assisted signals, can block or warn on selected risks when enforcement is enabled, and records audit data. It does **not yet substantiate** the stronger product claims that the system improves real coding quality, reduces harmful edits, lowers cost, or generalizes uniformly across repositories.

There is also an explicit documentation-to-defaults gap. The README and whitepaper present a structural reasoning gate and quantified benchmark results, while shipped templates default to `RC_MODE=advise`, `RC_SHADOW_MODE=1`, `RC_PLAN_BLOCK=0`, and `S2_FAIL_CLOSED=0`; the repository's own audit documents call this contradiction out. The main measurement problem is therefore accompanied by a product-contract problem: users cannot tell which behavior and evidence the headline promise actually refers to.

The 90-day real-session review found broad operational coverage but no verified human outcome labels, material sidecar-availability noise, warning-heavy scope enforcement, and strong volume skew. Established agent-evaluation practice recommends combining deterministic outcome graders, run/trace/thread evaluation, calibrated human review, versioned production-derived datasets, and explicit reliability SLOs. The highest-value next step is therefore to reconcile the public contract and build an outcome-linked evaluation loop, not another threshold or model-backend change.

## Scope and evidence

This report compares the repository's observable behavior with the value implied by its guardrail and benchmarking artifacts. It uses:

- The cross-repository retrospective in `thoughts/shared/research/2026-09-08-cross-repo-real-session-evaluation.md`.
- The earlier audit and improvement studies in `thoughts/shared/research/`, especially the 2026-05-23, 2026-06-01, 2026-06-02, and 2026-06-13 documents.
- The current audit and benchmark implementation in `src/hooks/audit_log.py`, `src/rc_cli.py`, `scripts/monitor-effectiveness.py`, `src/hooks/_training_set.py`, and `eval/reconcile_session.py`.
- The explicit product and evaluation claims in `README.md:14-18,44-52,96-109,134-141`, `docs/BENCHMARKS.md:7-45`, `docs/whitepaper/sections/abstract.tex`, and `docs/whitepaper/sections/conclusion.tex`.
- The first-party gap and run-status documents `docs/AUDIT_GAP_2026_07_10.md`, `docs/EVAL_RESULTS.md`, and `eval/runs/smoke-001/`.
- The immutable context record `baseline-2026-08-09`. The baseline policy states that audit and operational metrics are diagnostics until joined to blinded human outcome labels (`eval/baselines/README.md:1-17`). The baseline is also marked dirty and has no audit-window quality metrics, so it is not treated as a product-quality control.
- Current public guidance and documentation from Anthropic, LangChain, OpenTelemetry, Promptfoo, Phoenix, Langfuse, Braintrust, Google SRE, Prometheus, Envoy, and OWASP. Source links are listed in [External research](#external-research).

No application code or enforcement configuration was changed.

## Executive conclusion: what value is proven today?

### Defensible promise today

> Reasoning Core is a local, auditable guardrail that applies deterministic and model-assisted checks to proposed code changes, can block or warn on selected risks, and preserves enough provenance to investigate what happened.

This is supported by:

- The README's narrower explicit posture: audit and warn by default, with opt-in enforcement (`README.md:14-16,138-141`).
- Structured event provenance, redaction, and append-only audit paths in `src/hooks/audit_log.py:190-318`.
- Date-window and compressed-log aggregation in `src/rc_cli.py:796-824`.
- Distinct signal sources and benchmark metrics in `src/rc_cli.py:1309-1497`.
- Session reconciliation and label-storage paths in `eval/reconcile_session.py:35-104` and `src/hooks/_training_set.py:111-244`.
- A local-only data boundary in the retrospective evaluation.

### Claims that remain unproven

The current evidence does not justify promising that Reasoning Core:

- Delivers the whitepaper's headline `−8.2%` token, `+0.32` plan-quality, `+0.20` implementation-quality, and `100%` pass-rate results as current general-usage outcomes (`docs/whitepaper/sections/abstract.tex`, `docs/whitepaper/sections/conclusion.tex`).
- Reduces harmful or unnecessary edits in real work.
- Improves final code correctness or test outcomes.
- Lowers token or wall-clock cost in general usage.
- Has a known false-positive rate.
- Works equally well across repositories, hosts, and workflows.
- Maintains a reliable fail-closed safety boundary under sidecar failure.
- Enables the full advertised risk vector in ordinary installed usage. The extra session/project dimensions are gated on baseline/session state (`src/s2_core.py:1013,1047-1063`), and the analyzer found no shipped path that reliably establishes that state for normal sessions.

Those claims require outcome linkage and labeled comparisons. Block, warn, retry, override, latency, and token-proxy counts are exposure or operational metrics; they are not correctness labels. The committed end-to-end smoke run is explicitly `inconclusive`, with zero paired deltas because both arms received an identical stub response (`eval/runs/smoke-001/`, `docs/EVAL_RESULTS.md:1-144`); it cannot corroborate the whitepaper numbers.

## Promise-to-evidence scorecard

| Promised value | What the observed data supports | Status | Main gap |
|---|---|---|---|
| Headline benchmark improvement | The whitepaper and benchmark docs report token, plan-quality, implementation-quality, and pass-rate gains. | **Not currently corroborated** | `docs/BENCHMARKS.md:7-15` says the headline was graded on `risk_labels_version=1`, while the current schema is version 2; the only committed end-to-end run is inconclusive and CI live runs have been inconclusive when the Claude CLI is unavailable. |
| Publicly described enforcement posture matches installed behavior | README documents audit/warn plus opt-in enforcement. | **Contract inconsistent across artifacts** | Installed templates default to advise/shadow/non-blocking and `S2_FAIL_CLOSED=0` (`.envrc:83-85`, `install.sh:158-160`), while `docs/AUDIT_GAP_2026_07_10.md` explicitly records that installed defaults contradict marketed defaults. |
| Prevent regressions and harmful edits | The system emits policy, plan, parse, structural, oracle, and neural signals and blocks some events. | **Not demonstrated** | No verified link from a decision to a harmful downstream edit, failed test, reverted commit, or human judgment. |
| Protect scope and plan intent | 9,600 scope signals were recorded in the 90-day snapshot. | **Detection demonstrated; protection unproven** | 9,431 were warnings and only 169 were blocked; there is no outcome label showing whether warnings were useful or noisy. |
| Fail safely when the reasoning service is unavailable | The baseline configuration sets `S2_FAIL_CLOSED=1`; the system also records failure reasons. | **Contract at risk** | 6,034 fail-open events and 6,294 sidecar-unavailability reasons were observed. The policy for each gate and exception path is not visible as a single auditable contract. |
| Add little interactive friction | The retrospective reported median latency of 7 ms and p95 latency of 2,321 ms. | **Typical path looks fast; tail unproven** | Averages and percentiles are not tied to user-visible interruption, retries, or task completion. There is no explicit latency SLO/error budget. |
| Generalize across real repositories | The snapshot covers about 2,466 sessions and 136 full project paths, with 95 basename groups. | **Coverage demonstrated; generalization unproven** | `lustro` contributes about 37.4% of events; `reasoning-core` is self-observation; `/fake/repo` is fixture traffic. |
| Explain why a decision happened | Event provenance includes session, project, tool, reason, gate, signal source, configuration hash, and latency. | **Mostly demonstrated** | The current effectiveness monitor scans only uncompressed history, and decision records are not consistently joined to downstream outcomes. |
| Learn continuously from real usage | Label collection and reconciliation code exists. | **Pipeline exists; feedback loop inactive** | No default real-session `training_set.jsonl` was present, so no verified human-labeled quality set was available. |
| Provide the advertised complete risk vector | README describes an eight-dimension neural risk vector and additional session/project dimensions. | **Partially realized** | Session/project dimensions are gated by baseline and session state; the extra dimensions are disclosed as rarely hit and are not demonstrated as a stable production signal. |
| Preserve local control and privacy | The reviewed data was local and the reporting flow is local-first. | **Substantially demonstrated** | Any future hosted observability or annotation tool would require an explicit data-boundary decision. |

## What the historical research adds

The previous studies are consistent with the current retrospective:

1. **The strongest unresolved question is downstream value.** The 2026-05-23 audit found that most blocks appeared to be self-protection or reliability noise; its claims about genuine value-adds, token savings, and wall-clock impact were not independently verifiable.
2. **Operational reliability has repeatedly been a confounder.** The same audit reported sidecar reliability and tail-latency concerns. The 2026-06-02 monitor also found hard-cap events likely related to launchd/plist configuration drift.
3. **Controlled benchmark gains are not real-usage causal evidence.** Iterations from 2026-05-05 through 2026-05-08 were sensitive to task design, correctness gates, judge agreement, and infrastructure. They show that the harness can distinguish setups, not that the product improves general repository work.
4. **The measurement architecture is ahead of the labeling architecture.** The repository has event collection, rotation, gzip-aware aggregation, reconciliation, retry markers, baselines, and label code, but not enough blinded outcome labels to estimate intervention precision, recall, or harmful-edit reduction.
5. **The current composite efficiency metrics should remain diagnostic.** The 2026-06-13 research describes a formula incorporating drift, false drift, gate wall-clock time, repo idiom delta, and availability, but human outcomes, commit survival, and operator judgments are not yet incorporated.

## Issues and established solutions

### 1. Events are not outcomes

**Observed issue:** A block or warning records what the guard did, not whether the proposed change would have harmed the repository or whether the intervention helped the user.

**Established solution:** Treat the complete execution as the unit of evaluation:

- **Run level:** Was one tool call or decision valid?
- **Trace/session level:** Did the whole edit episode produce the intended repository state?
- **Thread/conversation level:** Did the multi-turn interaction resolve the task?
- **Outcome level:** Did tests pass, did the commit survive, and did a reviewer accept the result?

Anthropic explicitly separates transcript/trajectory from the final environment outcome. LangChain similarly recommends run, trace, and thread evaluation rather than final-output-only scoring. For this repository, the minimum trace should link `decision_id`, `session_id`, project path, file/diff identity, final repository state, tests, retry/override behavior, and human label.

### 2. No labeled ground truth

**Observed issue:** The current corpus has no verified default human-label set.

**Established solution:** Start with a small, stratified production-derived set rather than an artificial pilot:

- Sample 20-50 real sessions first.
- Balance positive and negative cases: a scope signal that should occur and a similar case where it should not; a valid block and a benign edit; available and unavailable sidecar paths.
- Use blinded review and a short rubric: harmful edit, plan violation, scope drift, structural regression, syntax/type failure, test failure, unnecessary interruption, and reviewer confidence.
- Add a reference solution or verified final state where possible.
- Use double review for a calibration subset and record agreement.

Anthropic recommends starting with real failures and manual checks, writing unambiguous tasks, balancing cases, and isolating trial environments. Braintrust and LangSmith document annotation queues and human review as the ground-truth path for calibrating automated evaluators.

### 3. Availability and enforcement semantics are mixed

**Observed issue:** Fail-open and sidecar-unavailability events are currently visible, but they are mixed with quality signals when calculating broad rates. This can make an unavailable evaluator look like a weak evaluator or hide a safety bypass inside a quality metric.

**Established solution:**

- Define separate SLIs for **decision availability**, **enforcement outcome**, **latency**, and **validated quality**.
- Emit an explicit decision state such as `allowed`, `blocked`, `warned`, `unavailable`, `timed_out`, `fallback`, or `operator_override`.
- Define a documented policy per gate for unavailable evaluation. OWASP recommends failing securely for security controls; availability-preserving fail-open behavior must be explicit, bounded, and visible.
- Use health/readiness checks, bounded retries, circuit breaking, and a clear fallback path. Envoy's circuit-breaking guidance is a useful model for limiting pending requests, active requests, and retry amplification.
- Use histograms and SLO/error-budget reporting rather than only daily averages. Google SRE recommends explicit SLIs/SLOs; Prometheus recommends histogram-based distributions for latency and SLO analysis.

### 4. Pooled metrics are volume-skewed

**Observed issue:** A few repositories dominate event volume, while many sessions are tiny. A global block or warning rate is not the experience of a typical repository.

**Established solution:** Publish three views:

1. Pooled event totals for capacity and reliability.
2. Per-session distributions for user exposure.
3. Repository-balanced medians and intervals for generalization.

Keep fixture paths, home-directory/bootstrap sessions, the `reasoning-core` development repository, and unrelated product repositories in separate cohorts. Do not use repository pooling as a causal quality claim.

### 5. Automatic judges are not calibrated

**Observed issue:** The repository has neural signals and proxy metrics, but no measured agreement against domain labels.

**Established solution:**

- Use deterministic graders for parse, schema, language, test, structural, and exact policy checks.
- Use model-based graders only for semantic questions that cannot be checked deterministically.
- Prefer binary pass/fail for objective rubrics and pairwise comparison when choosing between versions.
- Calibrate on at least 20 labeled examples, track agreement and disagreement, and refresh the calibration set as behavior changes.
- Route uncertain, high-impact, negative, and judge-disagreement cases to human review.

LangChain's evaluation guidance explicitly recommends code-based graders for objective checks, LLM judges for semantic checks, human review for ambiguity, pairwise comparison for version choice, and continuous calibration. This supports the repository's policy that neural scoring should remain advisory unless corroborated by an independent deterministic signal.

### 6. Production traces are not becoming regression tests

**Observed issue:** The codebase has reconciliation and label paths, but no active production-to-dataset loop.

**Established solution:** Every validated production failure should become a versioned regression example:

1. Select a real trace.
2. Redact sensitive content and freeze the relevant repository state.
3. Record the expected outcome or reference solution.
4. Add deterministic graders first.
5. Add a semantic rubric only where deterministic checks are insufficient.
6. Run the case before and after changes.
7. Retain the original trace, label, dataset version, configuration hash, and run manifest.

This is the common lifecycle described by LangSmith, LangChain, Braintrust, Langfuse, and Promptfoo: production traces surface unknown failures; curated datasets prevent recurrence.

### 7. Historical monitoring is incomplete

**Observed issue:** `scripts/monitor-effectiveness.py:29-44` scans `*.jsonl`, while historical audit data also contains compressed `*.jsonl.gz`. The monitor therefore undercounts retained history if used as the sole evaluator.

**Established solution:** Reuse the gzip-aware reader and semantics from `rc benchmark` (`src/rc_cli.py:796-824`) or make the monitor call a shared reader. Add a parser-version and file-inventory manifest so reruns can be reproduced and differences from live writes are explicit.

## Tool and solution landscape

The tools below solve different layers. They should not be treated as interchangeable.

| Tool or pattern | Strong fit | Weak fit / tradeoff | Recommendation for this repository |
|---|---|---|---|
| Existing `rc benchmark` + `rc label` + `rc reconcile` | Local privacy, current provenance, low migration cost | Limited review UX and dataset lifecycle | **Keep as source of truth for the first labeled evaluation.** |
| OpenTelemetry GenAI semantic conventions | Portable span/event vocabulary and backend choice | Convention is evolving; it does not provide an evaluator or review UI | **Adopt a compatibility mapping**, not a wholesale rewrite. Preserve `decision_id`, gate, signal source, and outcome fields as custom attributes. |
| Promptfoo | Local, provider-agnostic, declarative regression tests, deterministic assertions, CI, red teaming | Primarily an eval runner; not the system of record for production traces or human review | **Use for the offline regression/security layer** once a small real-session dataset exists. |
| Arize Phoenix | Self-hosted OTLP tracing, sessions, annotations, metrics, and evaluations | Additional service/storage operations | **Good privacy-preserving observability pilot** if the local review workflow becomes the bottleneck. |
| Langfuse | Self-hosted tracing, scores, datasets, experiments, dashboards, and prompt/eval lifecycle | Adds ClickHouse/Postgres/Redis/object-storage operational surface depending on deployment | **Strong alternative to Phoenix** when dataset and experiment UX matters more than minimal infrastructure. |
| LangSmith | Mature offline/online evaluation lifecycle, annotation queues, trace-to-dataset workflows, thread evaluation | Hosted workflow and vendor/data-residency considerations | Use only after an explicit decision that reviewed traces may leave the local boundary. |
| Braintrust / W&B Weave / MLflow | Integrated experiments, production traces, human review, and dataset management | More platform adoption and possible hosted/governance constraints | Consider for a larger team or multi-project evaluation program, not as the first fix. |
| Prometheus/Grafana + OpenTelemetry metrics | SLOs, latency distributions, availability, alerts, trace correlation | Operational metrics do not answer whether a block was correct | Use for sidecar health and latency; keep quality labels separate. |
| Envoy-style circuit breaking and bounded retries | Prevents retry amplification and overload during dependency failure | Requires explicit policy and instrumentation | Apply the pattern locally to sidecar request handling, even without adopting Envoy. |
| Human review queue or Label Studio | Structured, assignable, multi-reviewer annotation | Another workflow and data store | Prefer the existing local `rc label` path initially; adopt a review UI only if sampling volume demands it. |

## Recommended improvement plan

### P0 — make the value claim measurable

1. **Reconcile the public contract with shipped defaults.** Choose one explicit posture:
   - Keep audit/warn plus opt-in enforcement, and rewrite README/whitepaper/submission language to say exactly that; or
   - Make enforcement the shipped default, then run a separately validated safety and usability evaluation before making that change.
   Do not leave `README.md`, `docs/whitepaper/`, `.envrc`, `install.sh`, and `docs/AUDIT_GAP_2026_07_10.md` describing materially different products.
2. **Retire or requalify stale headline numbers.** Re-run the benchmark with the current `risk_labels_version=2`, a non-stub agent, pinned artifacts, and the preregistered graders before presenting the `−8.2%`, `+0.32`, `+0.20`, or `100%` figures as current evidence. Until then, label them historical/synthetic.
3. **Publish a value contract.** Change product language and reports to distinguish:
   - Guardrail exposure: what fired.
   - Reliability: whether the sidecar answered and within the latency budget.
   - Validated quality: whether the intervention correlated with a human- or test-verified outcome.
4. **Freeze an evaluation snapshot.** Record the event-file inventory, date window, parser version, configuration hashes, baseline ID, and cohort exclusions.
5. **Create a 20-50 session labeled seed set.** Sample across repositories, session sizes, decision states, signal sources, scope outcomes, and availability failures. Use blinded double review for a subset.
6. **Join decisions to outcomes.** Implement the smallest reliable linkage from `decision_id`/`session_id` to diff, commit, test result, retry, override, and final reviewer label. Leave unlinked events in exposure denominators only.
7. **Separate availability from quality denominators.** Do not score an unavailable sidecar as a correct or incorrect reasoning decision.

### P1 — build the evaluation loop

6. **Add three evaluation levels.**
   - Run/event: deterministic policy, parse, language, and tool checks.
   - Session/trace: final repository state, tests, scope adherence, and user-visible friction.
   - Thread/conversation: whether the multi-turn task was resolved without repeated failure.
7. **Use deterministic outcome graders first.** Tests, static analysis, diff invariants, plan checks, syntax/type checks, and repository state should outrank neural scores.
8. **Calibrate semantic judges.** Use binary or pairwise rubrics, human agreement, disagreement routing, and versioned judge prompts/configuration.
9. **Promote validated failures to a regression set.** Keep capability cases separate from near-100%-pass regression cases. Run the regression set on every enforcement/model/configuration change.
10. **Make the historical evaluator gzip-aware.** Share one reader and one normalization path between `rc benchmark`, the monitor, and future reports.

### P2 — make reliability explicit

11. **Define sidecar SLIs/SLOs.** At minimum: availability, fail-open/unavailable rate, p95/p99 decision latency, timeout rate, retry amplification, and operator override rate. Set targets only after measuring stable cohorts; do not change enforcement thresholds from the current retrospective alone.
12. **Instrument failure modes as first-class outcomes.** Distinguish connection refusal, hard-cap timeout, request timeout, malformed response, fallback, and explicit fail-open policy.
13. **Add health/readiness and bounded retry/circuit-breaker behavior.** Prevent a sick sidecar from creating a retry storm or silently degrading enforcement.
14. **Connect metrics to traces.** OpenTelemetry metrics/histograms and trace IDs should let an availability alert open the exact session and decision records that caused it.

### P3 — choose tooling only where it removes a demonstrated bottleneck

15. Start with the existing local pipeline and a Promptfoo-compatible export for CI regression/red-team tests.
16. If trace exploration or annotation becomes the bottleneck, pilot one self-hosted backend—Phoenix or Langfuse—against redacted data and compare reviewer throughput, storage cost, and join fidelity.
17. Defer hosted LangSmith, Braintrust, Weave, or MLflow adoption until data residency, ownership, and multi-repository governance are explicit requirements.

## Proposed evaluation endpoints

The next report should expose separate endpoints rather than one composite “effectiveness” number:

### Operational reliability

- Sidecar decision availability.
- Fail-open/unavailable rate, by repository, host, day, and gate.
- p50/p95/p99 decision latency.
- Timeout and retry amplification.
- User-visible retry, override, and confirmation rates.

### Validated quality

- Harmful-edit rate among labeled sessions.
- Scope-drift detection precision and recall.
- Plan-violation detection precision and recall.
- Structural/syntax/test regression rate.
- False-interruption rate for warnings and blocks.
- Human-review agreement and judge agreement.

### Outcome and efficiency

- Task success at session level.
- Pass@1 for first-attempt success where one successful attempt is sufficient.
- Pass^k for consistency-sensitive workflows.
- Cost and latency per successful task, not per event.
- Repository-balanced and pooled views, always reported separately.

These endpoints should not be collapsed into a single quality score until their correlations with human-valued outcomes are established.

## Decision

The next investment should be **measurement validity and reliability**, not a new reasoning backend or more aggressive enforcement:

1. Keep the local audit and reconciliation path.
2. Label a stratified sample of existing sessions.
3. Link decisions to final repository/test outcomes.
4. Establish availability and latency SLOs.
5. Build a small production-derived regression set.
6. Add Promptfoo for deterministic CI/red-team execution.
7. Add Phoenix or Langfuse only if the local review experience is demonstrably insufficient.

Until this loop exists, the honest product position is: **“Reasoning Core provides auditable, selective guardrails and operational evidence.”** The stronger position—**“Reasoning Core improves coding quality and reduces regressions in real work”**—should remain a hypothesis.

## Open questions

1. Which event cohorts are genuine repository work versus fixtures, bootstrap runs, or home-directory sessions?
2. Can `decision_id`, `session_id`, file/diff identity, commits, and tests be joined reliably across all repositories?
3. Which gates are security-relevant enough to require fail-closed behavior, and which may explicitly fail open for availability?
4. What is the minimum reviewer rubric that two domain experts can apply consistently?
5. Should the first review workflow remain in `rc label`, or is a local Phoenix/Langfuse/Label Studio pilot needed?
6. Which 20-50 sessions should seed the initial regression dataset, and how will future sampling remain repository-balanced?
7. What user-facing latency and availability SLOs correspond to the promised low-friction experience?

## External research

Primary references consulted on 2026-09-08:

- Anthropic, “Demystifying evals for AI agents”: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
  - Tasks, trials, graders, transcripts, outcomes, capability versus regression evals, balanced datasets, stable environments, deterministic graders, and pass@k/pass^k.
- LangChain, “Evaluating AI Agents at the Run, Trace, and Thread Level”: https://www.langchain.com/resources/agent-evals
  - Run/trace/thread evaluation, offline/online lifecycle, human annotation queues, judge calibration, pairwise/binary scoring, and production trace-to-dataset workflows.
- LangSmith, “Evaluation concepts”: https://docs.langchain.com/langsmith/evaluation-concepts
  - Curated examples, offline versus online evaluation, runs/threads, human/code/LLM/pairwise evaluators, and feedback.
- OpenTelemetry GenAI semantic conventions: https://github.com/open-telemetry/semantic-conventions-genai
  - Portable GenAI spans, metrics, events, MCP/provider conventions, and reference implementations.
- Promptfoo introduction: https://www.promptfoo.dev/docs/intro/
  - Local, provider-agnostic, declarative evaluation, CI integration, assertions, and feedback loops.
- Promptfoo red teaming: https://www.promptfoo.dev/docs/red-team/
  - Systematic adversarial probes, deterministic/model-graded analysis, and CI/CD monitoring.
- OpenAI, “Moving from OpenAI Evals to Promptfoo”: https://developers.openai.com/cookbook/examples/evaluation/moving-from-openai-evals-to-promptfoo
  - Portable configuration and CI-oriented regression workflow.
- Arize Phoenix tracing: https://arize.com/docs/phoenix/tracing/llm-traces
  - OpenTelemetry traces, sessions, annotations, latency/token/error inspection, and trace evaluations.
- Langfuse observability: https://langfuse.com/docs/observability/overview
  - Self-hosted tracing, scores, datasets, experiments, dashboards, and asynchronous trace delivery.
- Braintrust human review: https://www.braintrust.dev/docs/annotate/human-review
  - Structured human scores, blind review, conditional routing, corrections, and production-to-dataset curation.
- Google SRE, “Service Level Objectives”: https://sre.google/sre-book/service-level-objectives/
  - SLIs, SLOs, availability, latency, error budgets, and explicit service expectations.
- OWASP, “Fail securely”: https://owasp.org/www-community/Fail_securely
  - Security controls should default to the disallow path on exceptions; fail-open behavior must be deliberate and context-specific.
- Envoy, “Circuit breaking”: https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/circuit_breaking
  - Bounded connections, pending requests, active requests, and retry budgets to prevent dependency overload.
- Prometheus, “Histograms and summaries”: https://prometheus.io/docs/practices/histograms/
  - Distribution metrics and histogram-based latency/SLO analysis.
