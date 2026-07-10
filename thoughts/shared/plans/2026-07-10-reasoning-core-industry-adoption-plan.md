---
date: 2026-07-10
status: draft — adversarially reviewed, revision 2
author: reasoning-core strategic planning
adversarial_review:
  - 2026-07-10 — review 1, verdict "RETHINK"; findings addressed in revision 1
  - 2026-07-10 — review 2, verdict "FIX-FIRST"; findings addressed in revision 2
---

# reasoning-core Industry Adoption Plan — Revision 2
## From user-side guardrail to measurable agent-reliability infrastructure

### Important constraints (added after adversarial review)

- **Resource reality:** This is a personal/ side project with at most **1–2 FTEs**. It is not a funded startup. The plan therefore picks **one primary bet** and defers everything else to explicit "if funded" branches.
- **No false enforcement claims:** Gateway integration (Bifrost/LiteLLM) is framed as **observability, policy distribution, and context steering** — not as a replacement for client-side enforcement. Tool execution happens on the developer's laptop; only a local hook can block before disk.
- **Dependency-gated phases:** Calendar months are estimates. Phases do not start until their technical prerequisites are green.
- **True kill criterion exists:** If the primary bet fails, the project stops expanding and returns to being an OSS power-user tool.

### Executive summary

reasoning-core has a durable moat: it is the only local, editor-agnostic, evidence-based enforcement layer for AI coding agents. But its current positioning as a "privacy-first user guardrail" makes it invisible to the AI companies that shape the market. Anthropic, OpenAI, Google, and GitHub will not adopt a third-party kill switch they cannot see, control, or learn from.

This plan changes the positioning without abandoning the local core. The **primary bet** is to make reasoning-core the **neutral measurement substrate** the industry integrates with:

1. **Standardized audit schema** — a format AI companies and researchers can consume.
2. **Defensible, reproducible benchmarks** — cross-agent evals that vendors quote or dispute on merit.
3. **Open-source measurement harness** — `reasoning-core-bench`, not a black-box score.

Only after the measurement substrate is credible do we add:
4. **Gateway observability plugins** — Bifrost/LiteLLM integrations for telemetry and policy context (not enforcement; deferred until customer-funded).
5. **Enterprise control plane** — identity, policy, audit retention (deferred until funded).

The 12-month goal is to become a **cited, neutral benchmark and telemetry standard** for agent reliability. Revenue, if any, comes later from enterprises who want the control plane on top of that standard.

### Current reality

- **Product-market fit is real but narrow.** Power users want local guardrails, and the audit log proves the gate catches drift. The addressable market of developers who will install and maintain a local Python stack is small.
- **AI companies cannot see it.** The zero-telemetry promise blocks the feedback loop that would let Anthropic improve Claude Code from reasoning-core decisions.
- **The value is under-quantified.** Token savings and plan-adherence deltas are reported, but not in a format procurement or product teams can act on.
- **UX is CLI-first.** A blocked edit surfaces as stderr. There is no IDE/agent-native approval flow.
- **No enterprise surface.** There is no multi-user policy, identity, or compliance dashboard.
- **The contract format is project-specific.** `.reasoning-core/rules.yaml` and `PLAN.md` derivation are useful, but not a standard any other tool recognizes.

### Strategic pillars

| Pillar | Problem | Outcome | Priority |
|---|---|---|---|
| **P1 — Open measurement standard** | No neutral way to compare agents on plan adherence and drift | reasoning-core becomes a cited source of truth for agent reliability metrics | Primary |
| **P2 — Reproducible benchmark harness** | Existing evals are vendor-run and opaque | Vendors and researchers run `reasoning-core-bench` themselves | Primary |
| **P3 — Telemetry standard (enterprise self-host)** | AI companies cannot learn from local gates without code leaking | Opt-in, privacy-preserving telemetry format + self-hosted collector | Primary |
| **P4 — Enterprise control plane** | Teams cannot centrally enforce policy or audit agent behavior | Paid tier that CIOs buy (deferred until funded) | Secondary |
| **P5 — Gateway observability** | Enterprises want governance at the network layer, not per agent | Bifrost/LiteLLM plugins for audit + policy context (not enforcement) | Secondary |

### Resource model

- **Core team:** 1 engineer (founder/maintainer), part-time.
- **Extended:** 1 designer/researcher for benchmark protocol + paper (if needed).
- **Funding assumption:** None for primary track. Enterprise/control-plane work only begins if ≥1 pilot customer commits to funding it or if a grant/sponsorship is secured.
- **Hiring gate:** No SaaS/multi-tenant build until $10K MRR or equivalent grant is committed.

### Dependency gates (phases do not start until these are green)

1. **Gate 0 (before any adoption work):** Technical upgrade plan `2026-07-09-reasoning-core-game-changer-upgrade.md` delivers:
   - Working contract compiler (`_plan_contract.py`).
   - Working execution oracles (`_oracles.py`) passing a 100-edit labeled eval.
   - Honest README/defaults alignment.
   If Gate 0 is not green, this adoption plan is paused.

2. **Gate 1 (before benchmark publication):** Audit schema v4 + `reasoning-core-bench` produces reproducible results on a powered set of SWE-bench Verified tasks with a frozen contract protocol and independent contract adjudication.

3. **Gate 2 (before enterprise/collector track):** ≥1 enterprise pilot signs a letter of intent or paid contract for the control plane or collector. Phase 3 does not start without this.

---

## Phase 1 — Measurement foundations (estimated months 1–3)

### Goal
Make reasoning-core auditable and measurable without breaking the local-only promise. **This phase intentionally has only two deliverables.** Telemetry client work is deferred to Phase 3.

### Deliverables

1. **Standardized audit event schema v4**
   - Define `reasoning-core/audit-event@v4` JSON Schema.
   - Include `gate_id`, `signal_source`, `decision_id`, `latency_ms`, `rc_mode`, `contract_clause_id`, `oracle_annotation_ids`, `prm_score`, `recovery_hint_type`, `block_layer`.
   - Upload payloads use **opaque IDs** for contract clauses and oracle annotations, never the underlying plan/repo text. The local log may retain the full text under `privacy_level=full`.
   - Add `privacy_level` field: `redacted` (default), `aggregate`, or `full` (operator opt-in; affects local log only).
   - Document in `docs/AUDIT_SCHEMA.md`.
   - Backwards compatibility shim for v3 readers.

2. **`rc benchmark` command**
   - One-command benchmark runner producing a Markdown report from the local audit log.
   - Metrics: blocked edits by severity class, override survival, token cost proxy, median latency, false-positive proxy, scope-creep catches.
   - Comparison mode: `rc benchmark --before YYYY-MM-DD --after YYYY-MM-DD`.
   - Output aimed at engineering managers: counts and survival ratios. Avoid counterfactual claims like "regressions prevented."

### What is explicitly NOT in Phase 1

- No telemetry client or collector.
- No public community metrics dashboard.
- No cross-agent comparisons from telemetry data.
- No cloud SaaS.

### Success criteria
- Audit schema v4 shipped, documented, and validated against a stub collector in CI.
- `rc benchmark` merged and tested on local logs.
- README claims reviewed and corrected.

### Kill criteria
- If `rc benchmark` cannot produce a credible proxy of value, pivot to pure technical metrics (block counts, latency, override survival).

---

## Phase 2 — Benchmark authority (estimated months 4–6, after Gate 1)

### Goal
Make reasoning-core the neutral measurement harness that AI companies quote or dispute on merit.

### Deliverables

1. **`reasoning-core-bench` harness**
   - Open-source evaluation harness under `eval/bench/`.
   - Runs SWE-bench Verified-style tasks with standardized contract extraction.
   - Supports Claude Code, Codex CLI, Gemini CLI, OpenCode, and Vibe via adapters.
   - Measures: Clean Task Success, plan adherence, scope-creep rate, token cost, retry rate, time-to-first-valid-edit.
   - Produces a structured report with full logs so results are reproducible.

2. **Frozen contract protocol + independent adjudication**
   - Define how contracts are derived from issue text before the agent runs.
   - Apply the same contract symmetrically to control and treatment.
   - Contracts are human-reviewed or derived by a published third-party rubric.
   - Report inter-rater agreement (κ) on a sampled subset.
   - Document in `docs/BENCH_PROTOCOL.md`.
   - Pre-register the protocol publicly before running the main eval.

3. **First public benchmark report**
   - Compare ≥3 agents on plan adherence and drift.
   - Self-funded; no vendor sponsorships from graded agents.
   - Publish methodology, raw aggregates, and known limitations.
   - Goal: get vendors to reference or dispute the numbers publicly.

4. **Integration with existing eval platforms**
   - Export results in SWE-bench compatible JSONL.
   - Add a GitHub Action that runs `reasoning-core-bench` on PRs and posts a comment.

### What is explicitly NOT in Phase 2

- No certification program. No paid badges. Neutrality is the product.
- No cross-agent leaderboard funded by vendors.

### Success criteria
- First public benchmark report published with ≥3 agents compared.
- ≥3 external researchers or vendors run the harness and report back (bug reports count).
- Reproducibility: a third party can run the same harness and get comparable results.

### Kill criteria
- If benchmark cannot be made statistically defensible (power, frozen contracts, reproducibility), stop publishing comparative claims and focus on single-agent improvement measurement.
- If no external engagement after the first report, deprioritize benchmark track and return to OSS tool improvements.

### Budget and sample size
- Compute budget: allocate $1,000–2,000 for API costs and reruns.
- Task-count floor derived from a power calculation after the pilot; publish the calculation in the protocol.

---

## Phase 3 — Enterprise telemetry and gateway observability (estimated months 7–12, after Gate 2)

### Goal
Validate enterprise demand with a self-hosted collector + gateway observability plugins before building a SaaS.

### Prerequisites

- Gate 2 satisfied: ≥1 enterprise pilot signs an LOI or paid contract.
- Written employer IP clearance obtained before any Bifrost/LiteLLM plugin work begins.

### Deliverables

1. **Open-source telemetry client (`src/telemetry/`)**
   - `rc telemetry enable --endpoint https://your-collector.internal` uploads redacted/aggregate events.
   - Default: **disabled**. No telemetry without explicit operator action.
   - Upload only opaque IDs and counts that cannot reconstruct source code.
   - Never upload file paths, source snippets, or plan text.

2. **`agent-policy.yaml` schema**
   - Portable policy schema that works locally and in enterprise deployments.
   - Supports inheritance: org policy → team policy → repo policy.
   - Versioned policies.

3. **Enterprise self-hosted collector v1**
   - Paid/closed-source collector that receives redacted telemetry from `rc telemetry`.
   - Single-tenant; runs in the customer's infrastructure.
   - Dashboard: blocks, overrides, survival, policy violations.
   - Export to S3/Splunk/SIEM.
   - No SSO/SAML yet; API-key auth only.
   - **Note:** The collector is a commodity ingestion endpoint. The paid value is the policy control plane and approval workflows added in Phase 4.

4. **Gateway observability plugins (Bifrost + LiteLLM)**
   - Build plugins that run at the gateway layer for **observability and policy context injection only**.
   - The plugin:
     - Detects `tool_use` intent.
     - Emits audit events to the self-hosted collector.
     - Injects policy context into the system message so the model knows the enforceable scope.
     - Cannot block execution (tool execution is client-side).
   - Two reference implementations:
     - Bifrost sidecar pattern (developed in the reasoning-core repo against public Bifrost docs).
     - LiteLLM guardrail/callback.

### What the gateway plugin does NOT do

- It does **not** block `Edit`/`Write` before disk. Only the local hook can do that.
- It does **not** run execution oracles (no repo access at the gateway).
- It is **not** marketed as "one integration, every agent, no hooks."

### Deployment model for gateway plugin

- Gateway runs on customer infrastructure alongside Bifrost/LiteLLM.
- reasoning-core sidecar runs on the same host (or as a sidecar container) so policy scoring remains local.
- Telemetry flows to the self-hosted collector.

### Success criteria
- ≥1 enterprise pilot running the self-hosted collector + gateway plugin.
- Telemetry schema adopted by ≥1 external tool or research project.
- Gateway plugin proven not to break streaming or client behavior.

### Kill criteria
- If zero enterprise pilots by the end of Phase 3, do not build SaaS/multi-tenant. Keep the collector as a consulting/custom offering only.

---

## Phase 4 — Enterprise control plane (estimated months 13–18, after Gate 2 + Phase 3 validation)

### Goal
Build the paid SaaS/self-hosted product for teams.

### Prerequisites

- Gate 2 satisfied and Phase 3 pilot has validated collector + gateway plugin.
- Core team has funding to hire or contract for security/backend work.

### Deliverables

1. **reasoning-core Cloud**
   - Optional SaaS or on-prem control plane.
   - Features:
     - Central policy registry.
     - Team/org boundaries with SAML/SCIM.
     - Audit log ingestion from local and gateway installs.
     - Dashboard, alerting, compliance export.
   - Source-code upload is never required; full audit logs can be stored in customer cloud.

2. **Approval workflows**
   - `rc request-override <decision-id>` routes to an approver.
   - Audit trail records approver identity.

3. **Compliance features**
   - Retention policies.
   - SOC 2 Type I readiness assessment.
   - Immutable audit log option.

### Success criteria
- ≥3 paying enterprise customers or ≥$30K MRR.
- SOC 2 Type I readiness assessment complete.

### Kill criteria
- If <3 paying customers by month 18 after Gate 2, archive the SaaS track. reasoning-core remains an OSS tool + consulting.

---

## True kill criterion for the whole project

If, after Phase 2, **both** of the following are true:
- No paid enterprise pilot or LOI.
- No external researcher/vendor has run or referenced `reasoning-core-bench`.

Then: stop all expansion work. reasoning-core reverts to being an open-source power-user guardrail. No enterprise track, no gateway plugins, no SaaS.

If the technical upgrade plan's core features (contract compiler + oracles) regress or fail to pass Gate 0 at any point, the adoption plan is paused immediately, independent of the kill criterion above.

---

## Commercial model

| Phase | Revenue assumption | Offer |
|---|---|---|
| 1–2 | $0 | Open-source core, schema, benchmark harness |
| 3 | Pilot revenue or consulting | Self-hosted collector + gateway plugin (collector is commodity; paid value is policy + approval workflow in Phase 4) |
| 4 | $30K MRR target | SaaS/self-hosted control plane with SSO/SCIM + approval workflows |

No venture funding is assumed. Enterprise work only begins with customer commitment.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Gateway integration overpromises enforcement | Explicitly scope it to observability + context injection; never claim it replaces local hooks |
| Resource overrun | One primary bet (benchmark/measurement); Phase 3+ require Gate 2 (customer commitment) |
| Technical prerequisites slip | Dependency gates; phases do not start until prerequisites are green |
| Telemetry violates privacy promise | Default off, opaque IDs in upload payloads, self-hosted collector, never upload code |
| Benchmark credibility attacked | No vendor sponsorships, pre-registered protocol, independent contract adjudication, reproducible logs |
| Metric defined by instrument vendor | Use independent adjudication layer; publish inter-rater κ; separate contract extraction from scoring |
| AI companies build the same features | Stay ahead on local-first, cross-agent, open-standard positioning |
| Free core cannibalizes paid collector | Collector is a commodity; paid value is policy control plane + approval workflows |
| Fail-open at gateway contradicts compliance | Loud degradation mode: alert + audit event on every fail-open; configurable per-policy |
| Agents bypass the gateway | Gateway plugin is for telemetry/context; enforcement still requires local hooks |
| IP contamination from employer's POC | No gateway plugin work until written employer IP clearance is obtained |

---

## Dependencies and prerequisites

1. Technical upgrade plan (`2026-07-09-reasoning-core-game-changer-upgrade.md`) delivers contract compiler, execution oracles, and honest defaults.
2. Clear open-source license (MIT) and contributor guidelines.
3. Public website and documentation hub.
4. Written employer IP clearance before any Bifrost/LiteLLM plugin commercialization.
5. Legal review of telemetry terms before any enterprise deployment.
6. Funding or customer commitment before Phase 3.

---

## Immediate next steps (this week)

1. Confirm Gate 0 status: are contract compiler + oracles green?
2. Draft audit schema v4 and open a PR.
3. Create `docs/FOR_RESEARCHERS_AND_VENDORS.md` explaining the schema and benchmark harness.
4. Obtain written employer IP clearance before starting gateway plugin work.
5. Add the true kill criterion to the project README or ROADMAP.

---

## Conclusion

reasoning-core should not try to become a feature inside Claude Code. It should become the **neutral measurement and policy substrate** that the AI coding-agent industry integrates with — starting with an open audit schema and a reproducible benchmark harness. Gateway plugins (Bifrost/LiteLLM) accelerate enterprise adoption by providing observability and policy context at the network layer, but enforcement remains local, preserving the privacy promise. Enterprise paid features are built only after the measurement substrate is credible and a customer has committed to fund them.
