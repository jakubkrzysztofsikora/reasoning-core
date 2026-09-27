# Positioning, Trust, And 10x Validation

This document is a product-positioning and evidence plan, not a performance
claim. It separates verified product facts from analysis and treats all outcome
claims as hypotheses until they are measured on labeled real sessions.

## Positioning in one sentence

**reasoning-core is the local Mamba SSM scoring and deterministic-policy layer
between an AI coding agent and a repository.**

It evaluates structural change before supported hosts write, enforces explicit
repository policy when an operator enables it, and keeps the decision,
override, and verification evidence local.

The default Mamba SSM is part of every supported score request. Its structural
signals -- including novelty, coupling, and coherence -- are an indispensable
second opinion. Under the default policy, an uncorroborated SSM regression is
an auditable warning; configured deterministic policy is the standalone
hard-block source. Direct neural enforcement is an explicit, repository-level
choice (`RC_NEURAL_CORROBORATED=0`), not a general quality claim.

## Ideal user and job

The primary user is a staff engineer, technical lead, or developer-platform
owner supporting a team that already uses coding agents on an established,
valuable repository.

They have concrete boundaries that cannot live only in a prompt:

- A layer must not import or directly call a protected subsystem.
- A planned change must stay within approved files or phases.
- An agent must not bypass a repository's deterministic checks.
- An exception needs an attributable operator override and evidence trail.

They do not need another generic PR commenter. They need a local, observe-first
control point in the agent's edit loop, with explicit rules they can inspect
before requiring enforcement.

## Claim-evidence matrix

| Candidate claim | Evidence today | Status | Safe public wording |
|---|---|---|---|
| Local pre-write scoring | Runtime hooks exist for Claude, Codex, Gemini, Kimi, Pi, and Antigravity; Copilot and Vibe use a model-mediated MCP path. | Implemented, host-dependent | "Evaluates proposed edits before writes on runtime-hook hosts; Copilot and Vibe use an MCP-mediated gate." |
| Default SSM scoring | `mamba-130m` is the default backend and the sidecar embeds supported edits. | Implemented | "Every supported score request uses the local default Mamba SSM structural scorer." |
| Deterministic enforcement | Rules, contracts, language locks, and configured oracles can deny in enforcement mode. | Implemented, opt-in | "Enforce explicit repo rules, plan contracts, and configured deterministic checks." |
| Neural hard blocks | Uncorroborated SSM regressions are downgraded when `RC_NEURAL_CORROBORATED=1` (the default). | Configurable, not default | "Use structural risk to prioritize review; enable direct neural enforcement only after repository-specific validation." |
| Local evidence trail | Decision IDs, audit events, overrides, verification receipts, and episodes are written locally. | Implemented | "Keep local decisions, overrides, and verification receipts." |
| Fewer regressions, faster delivery, or lower spend | Historical study is one codebase with an older risk-label schema; current smoke evidence is inconclusive. | Unproven | Do not make an outcome promise. |
| "Up to 29% fewer tokens" | A single historical cache-heavy task, not re-evaluated on the current schema. | Historical only | Do not use in product copy or repository metadata. |
| Auto-repair | `autopilot` currently has the same enforcement posture as `copilot`; repair is not shipped. | Not shipped | Do not claim automatic repair. |

Source of implementation truth: `README.md`, `docs/CLI_PARITY.md`,
`docs/CONFIGURATION.md`, `src/hooks/pre_edit_guard.py`, and `src/mcp_gate.py`.
Evidence limitations: [`BENCHMARKS.md`](BENCHMARKS.md),
[`EVAL_RESULTS.md`](EVAL_RESULTS.md), and
[`eval/baselines/README.md`](../eval/baselines/README.md).

## Competitive map

| Category | Verified examples | What it does well | reasoning-core's distinct job |
|---|---|---|---|
| Static application security | [Semgrep](https://github.com/semgrep/semgrep), [CodeQL](https://github.com/github/codeql-action) | Finds security and correctness patterns; integrates with IDEs, commits, CI, and PRs. Semgrep also exposes an MCP server for coding assistants. | Do not compete as universal SAST. Apply local agent-session policy and structural scoring before a supported agent writes. |
| Code quality / quality gates | [SonarQube](https://github.com/SonarSource/sonarqube) | Mature cross-language code quality, security, PR/branch analysis, and centralized quality gates. | Move selected repository-policy feedback into the local agent edit loop; complement, rather than replace, CI quality gates. |
| AI PR review | [Qodo](https://www.qodo.ai/), [Greptile](https://www.greptile.com/), [PR-Agent](https://github.com/qodo-ai/pr-agent) | Codebase-context PR review, summaries, suggestions, and centralized team workflows. | Intervene earlier, attach decisions to edit attempts, and work local-first instead of waiting for a PR. |
| Agent coding loop | [Aider](https://github.com/Aider-AI/aider) | Fast agent-side lint/test feedback and repair loops. | Add repository-owned rules, plan/file contracts, and decision/override provenance around a host's edit operation. |
| Agent configuration security | [AgentShield](https://github.com/affaan-m/agentshield), [Snyk Agent Scan](https://github.com/snyk/agent-scan) | Scan agent configuration, MCP servers, tools, skills, permissions, and common prompt-injection or exfiltration risks. | Secure reasoning-core's own host integration and give adopters a complementary control. This is not reasoning-core's product substitute. |
| Software supply chain | [OpenSSF Scorecard](https://github.com/ossf/scorecard), [OSV-Scanner](https://github.com/google/osv-scanner), [SafeDep Vet](https://github.com/safedep/vet), [SLSA Generator](https://github.com/slsa-framework/slsa-github-generator) | Surface project hygiene, dependency vulnerabilities, known malware intelligence, and build provenance. | Establish trust in the project distribution and releases; this is separate from assessing whether an individual agent edit is acceptable. |

This differentiation is about *when and where* a control operates, not a claim
that the SSM universally detects defects better than established scanners.

## Trust and malicious-logic research

No scanner or badge can truthfully certify that a repository "contains no
malicious logic." Static analysis has blind spots; dependency and model
artifacts can change; a green scan reflects only a defined rule set at a point
in time. Do not use a badge that makes that claim.

The defensible alternative is a layered, externally inspectable security
posture: publish what was checked, who produced a release, and the scope and
limits of each check.

### Open and free tools evaluated

| Tool | License / access | Relevant scope | Badge or artifact | Limitation |
|---|---|---|---|---|
| [OpenSSF Scorecard](https://github.com/ossf/scorecard) | Apache-2.0; free CLI and GitHub Action | Repository security practices: dangerous workflows, token permissions, dependency pinning, branch protection, SAST, signed releases, and more. | Public score and embeddable OpenSSF Scorecard badge. | Measures security practices, not absence of malicious source logic. |
| [GitHub CodeQL](https://github.com/github/codeql-action) | MIT action; GitHub code scanning is free for public repositories. | Static security analysis of supported languages. | Direct GitHub Actions workflow badge plus Code Scanning results/SARIF. | A passing workflow is not a code-safety proof; coverage depends on languages and queries. |
| [OSV-Scanner](https://github.com/google/osv-scanner) | Apache-2.0; free. | Known vulnerabilities in manifests, lockfiles, SBOMs, and source dependency usage. | CI result/SARIF artifact; no independent universal project badge. | Detects known dependency vulnerabilities, not malicious first-party logic or all malware. |
| [Semgrep Community Edition](https://github.com/semgrep/semgrep) | LGPL-2.1; local CLI is free. | Custom static rules, including repo-specific suspicious patterns. | CI workflow badge/SARIF; can be called by agents through its MCP server. | Rule coverage and quality are operator-defined; not a certification service. |
| [AgentShield](https://github.com/affaan-m/agentshield) | MIT; CLI and GitHub Action. | Claude/Codex and related agent configs, MCP settings, hooks, skills, permissions, and injection vectors. | GitHub Action report, score/grade, SARIF, and evidence-pack outputs. | Its own benchmark documents that it does not connect to live MCP servers; it examines configuration-side evidence. |
| [Snyk Agent Scan](https://github.com/snyk/agent-scan) | Apache-2.0 repository. | AI agents, MCP servers, and agent skills. | CLI/CI findings. | Confirm the feature and service terms for the chosen version; do not imply all scanning is offline or free. |
| [SafeDep Vet](https://github.com/safedep/vet) | Apache-2.0 code; open-source usage available. | Dependencies, SBOMs, GitHub Actions, malware-query integration, and AI-tool discovery. | CI/SARIF; Vet itself displays Scorecard, SLSA, and CodeQL badges. | Real-time malware intelligence is backed by SafeDep Cloud; verify the selected feature's data and pricing path. |
| [SLSA GitHub Generator](https://github.com/slsa-framework/slsa-github-generator) | Apache-2.0; free. | Build provenance for release artifacts. | Signed provenance attestation and SLSA level badge when requirements are met. | Proves how an artifact was built, not that source behavior is benign. |

Research method: public repository metadata was queried through `gh`; primary
READMEs and action metadata were inspected locally after shallow clones; the
OpenSSF Scorecard public site was inspected with the Playwright CLI. The
observations above describe the inspected source state, not a security audit of
those third-party tools.

### Recommended trust baseline for reasoning-core

1. Add the OpenSSF Scorecard GitHub Action, publish its results, and add the
   official Scorecard badge only after a successful public score exists.
2. Add CodeQL for the repository's supported first-party languages and publish
   its workflow badge. Treat alerts as triage work, not a marketing score.
3. Add OSV-Scanner and GitHub dependency review for pull requests; upload SARIF
   where the repository's GitHub plan supports code scanning.
4. Run AgentShield in CI against reasoning-core's agent host configuration,
   MCP configuration, skills, hooks, and permissions. Fail only on reviewed,
   high-confidence policy categories at first.
5. Generate provenance for distributed release artifacts before advertising
   installs beyond source checkout. Add a SLSA badge only when a verified level
   is actually achieved.
6. Publish `SECURITY.md`, a threat model, scope statements, pinned action SHAs,
   and reproducible verification commands.

Suggested README wording after these controls are live:

> Security posture: automated supply-chain, dependency, static-analysis, and
> agent-configuration checks run in CI. Their reports describe the checks run
> for a revision; they do not certify that the project is free of every security
> defect or malicious behavior.

Use the official Scorecard badge format, not a custom green
"malware-free" badge:

```md
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/jakubkrzysztofsikora/reasoning-core/badge)](https://securityscorecards.dev/viewer/?uri=github.com/jakubkrzysztofsikora/reasoning-core)
```

The endpoint did not return a project result during this research, so this badge
must not be added until the Scorecard workflow has run and the public viewer
resolves.

## Measured 10x hypothesis

There is no evidence that reasoning-core is "10x better" overall than Semgrep,
SonarQube, Qodo, Greptile, or other tools. Do not claim that.

The testable workflow hypothesis is narrower:

> For explicitly configured repository-policy violations on Tier-1 hosts,
> reasoning-core reduces the time from an agent's violating edit attempt to
> actionable feedback by at least 10x relative to a post-write CI/PR-review
> control path, without unacceptable false blocks.

### Required definitions

| Term | Definition |
|---|---|
| Tier 1 | A host with runtime interception: Claude, Codex, Gemini, Kimi, Pi, or Antigravity. |
| Policy violation | A pre-labeled rule, contract, language-lock, or deterministic-oracle failure. Do not use model-judged "bad code" as the primary endpoint. |
| Feedback latency | Wall-clock time from the host's proposed write to the first actionable, attributable feedback. |
| Resolution latency | Wall-clock time from the violating proposal to a corrected edit that passes the same deterministic check. |
| False block | A block that an independent maintainer review labels as policy-compliant and that survives review without a required correction. |
| Override survival | An override whose resulting change remains in the default branch without a related revert/fix during the preregistered observation window. |

### Experiment design

| Item | Treatment | Control |
|---|---|---|
| Host | Same Tier-1 host, model, repository revision, task prompt, and agent settings. | Same. |
| Intervention | reasoning-core with a preregistered deterministic rule/contract/oracle set and required Mamba SSM scoring. | Existing post-write lint/test/CI/PR-review feedback path; no pre-write rc enforcement. |
| Tasks | At least 30 realistic tasks across at least 3 repositories, stratified by rule/contract/oracle category. | The matched paired task. |
| Randomization | Randomize treatment/control order within matched task pairs; use a fixed task revision and record seeds/settings. | Same. |
| Blinding | Blind maintainers reviewing final diffs and false-block labels to arm where operationally possible. | Same. |
| Data | Capture decision IDs, timestamps, rules fired, host, edit retries, overrides, deterministic receipts, PR/CI timestamps, and final review disposition. | Capture comparable write, CI, PR, and review timestamps. |

### Preregistered success and safety criteria

The 10x message is permitted only if all conditions hold:

1. Median treatment feedback latency is at least 10x lower than the matched
   control latency, with a paired bootstrap 95% confidence interval for the
   ratio wholly above 10x.
2. Treatment does not reduce locked deterministic test pass rate by more than
   2 percentage points, with a confidence interval that excludes a larger loss.
3. Independent false-block rate is at most 5%, and the upper 95% confidence
   bound is at most 10%.
4. Median resolution latency is not worse than control by more than 10%.
5. Results are reported separately for each host. Copilot/Vibe MCP-mediated
   results must never be pooled into the Tier-1 interception claim.
6. The Mamba SSM's role is reported separately: its advisory warnings,
   deterministic corroboration rate, direct-neural-enforcement rate (if any),
   and override outcomes. No neural causal claim follows from aggregate wins.

If criterion 1 succeeds but any safety criterion fails, publish the latency
result only with the failing safety result and do not make a 10x headline.

### Public reporting template

```text
Scope: <repositories, dates, host versions, task count>
Pre-write policy categories: <rules/contracts/oracles>
Median feedback latency: treatment <x>s; control <y>s; ratio <y/x>x
Resolution latency: treatment <x>s; control <y>s
Locked checks passed: treatment <x>/<n>; control <y>/<n>
Independent false-block rate: <x>/<n>
Overrides: <count>; survival after <window>: <x>/<n>
SSM signals: warnings <n>; deterministic corroboration <n>; direct blocks <n>
Limitations: <representativeness, missing links, host-specific caveats>
```

## Product copy boundaries

Use:

> Local-first Mamba SSM scoring and deterministic guardrails for AI coding
> agents. Evaluate structural change, enforce explicit repository policy before
> supported agents write, and keep an auditable local record of decisions,
> overrides, and verification.

Avoid:

- "Saves X% of tokens" without a current, replicated, task-representative study.
- "Fixes agent mess" or "auto-repairs" until a shipped repair loop meets a
  separately published evaluation standard.
- "Prevents regressions" or "malware-free"; these imply a guarantee that the
  product and security controls cannot support.
- "Works before every write" without the Tier-2 MCP caveat.
