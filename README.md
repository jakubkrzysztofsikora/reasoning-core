<!--- Logo --->
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/logo-dark.svg">
    <img alt="reasoning-core logo" src="./docs/logo.svg" width="300">
  </picture>
</p>

<p align="center">
  <strong>reasoning-core</strong> — local pre-write containment harness & structural scoring for AI coding agents.
</p>

<p align="center">
  Deterministic repository policy enforces hard blocks; a local Mamba-3 SSM provides advisory structural scoring; local Laya provides multi-file intake triage.
  <br/>
  100% loopback-only. Zero telemetry. No cloud relay.
</p>

<p align="center">
  <a href="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/lint-and-test.yml">
    <img src="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/lint-and-test.yml/badge.svg" alt="lint-and-test">
  </a>
  <a href="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/eval.yml">
    <img src="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/eval.yml/badge.svg" alt="eval">
  </a>
  <a href="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/reasoning-core-pr-score.yml">
    <img src="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/reasoning-core-pr-score.yml/badge.svg" alt="PR score">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT">
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  </a>
</p>

---

## Why reasoning-core?

AI coding agents (Claude Code, OpenAI Codex, Gemini, Copilot) are remarkably fast at writing code, but on large, complex codebases (~200k+ LOC) they frequently fall into the **System 1 hallucination trap**:
- Inventing non-existent function arguments or invalid schema defaults.
- Injecting duplicate function definitions that shadow existing implementations.
- Silently violating repository architecture, forbidden imports, and style contracts.

**reasoning-core** acts as a local **pre-write containment gate**: it intercepts every proposed file edit *before it lands on disk*, running deterministic policy checks (AST, syntax, lint, custom repo rules) and advisory neural scoring via a local **Mamba-3 state-space model**.

When paired with **Laya** (a local multi-file intake & triage engine), agents gain both broad architectural focus and hard guardrails:

```
                  ┌────────────────────────────────────────────────────────┐
                  │                    Task Brief                          │
                  └──────────────────────────┬─────────────────────────────┘
                                             │
                       [System 1: Laya Fast Intake Triage]
                                             │
                                             ▼
                                  Scoper & Triage Hint
                                             │
                                             ▼
                                 AI Coding Agent (Codex)
                                             │
                                   Proposes File Edit
                                             │
                                             ▼
                  ┌────────────────────────────────────────────────────────┐
                  │          [System 2: Reasoning Core Gate]               │
                  │  • Fast Deterministic Checks (py_compile, ruff, AST)   │
                  │  • Repo Rules (.reasoning-core/rules.yaml)             │
                  │  • Mamba-3 SSM 8-Dimension Structural Risk Vector      │
                  └──────────────────────────┬─────────────────────────────┘
                                             │
                             ┌───────────────┴───────────────┐
                             │                               │
                       [Pass / Clean]               [Blocked / Regression]
                             │                               │
                             ▼                               ▼
                       Lands on Disk                Decision ID & Reason
                                                    (Agent self-corrects)
```

### Proven Benchmark Results

On real-world multi-module architectural tasks in large codebases (FeatureBench Sphinx row 99, ~200k LOC):

| Arm | Test Pass Rate | Failure Mode |
|---|:---:|---|
| **Vanilla Agent** (Codex alone) | **0%** (0/10) | Hallucinated illegal default keys into CLI parser; crashed all tests |
| **Laya alone** | **0%** (0/10) | Fast triage, but produced identical hallucinated schema defaults |
| **Reasoning Core alone** | **0%** (0/10) | Explored safely but timed out without finishing patch |
| **Reasoning Core + Laya** | **70% (7/10)** | **Blocked duplicate function collisions & hallucinated options; agent self-corrected to compliant architecture** |

> In confirmatory evaluations on disposable worktrees, the harness reduced observer-detected invalid policy writes by **>10×** compared with vanilla Claude Code (rate-ratio upper 95% CI: 0.0295; 150 pairs). See [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md) and [`eval/featurebench_four_arm/README.md`](eval/featurebench_four_arm/README.md).

---

## Quick Path to First Success (RC + Laya)

The best-case scenario combines **Laya** for intake triage and **Reasoning Core** for bounded execution containment. Here is the 5-minute setup to run your first bounded task.

### 1. Install reasoning-core

> ⚠️ **Important:** Install from this tagged GitHub repository. The `reasoning-core` name on PyPI belongs to an unrelated project.

```bash
python3 -m pip install 'reasoning-core[full] @ git+https://github.com/jakubkrzysztofsikora/reasoning-core.git@v0.3.0'
```

### 2. Verify Services

Reasoning Core runs a local sidecar daemon on `127.0.0.1:8765` with a portable Mamba-3 SISO 893M model (runs on Apple Silicon MPS or CPU):

```bash
# Check sidecar status
rc status
```

Start the local Laya server on `127.0.0.1:8000`:

```bash
uv pip install 'laya[serve]==0.3.20'
LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_DEVICE=mps LAYA_MODELS=english laya-serve
```
*(On Linux/Windows without MPS, use `LAYA_DEVICE=cpu`)*

### 3. Run a Bounded Autonomous Task

Create a simple bounded task file outside your repository, e.g. `task.json`:

```json
{
  "prompt": "Update src/service.py so normalize_name trims whitespace and lowercases input.",
  "triage_brief": "Python string-normalization helper.",
  "allowed_paths": ["src/service.py"],
  "checks": [["python", "-m", "pytest", "-q", "tests/test_service.py"]]
}
```

Now run the bounded autonomous runner:

```bash
rc autonomous --host codex --model gpt-6-sol \
  --repo /path/to/your-repo \
  --task /path/to/task.json \
  --out ~/.local/share/reasoning-core/runs/task-001
```

**What happens:**
1. **Laya scopes the workspace**: Routes the triage brief and identifies the candidate files.
2. **Codex attempts edits**: Any invalid syntax, illegal imports, or structural duplicates are intercepted by Reasoning Core's pre-patch gate.
3. **Agent self-corrects**: When an edit is blocked, Codex receives the exact reason and reformulates the code to comply with repository rules.
4. **Verified output**: A verified `final.patch` and detailed `report.json` are written to the output directory without mutating your source checkout.

---

## Day-to-Day Interactive Coding

To protect your day-to-day workflow with your favorite CLI agent (Claude Code, OpenAI Codex, Gemini CLI, Copilot):

```bash
# 1. Wire hooks into your project repository
cd /path/to/your-repo
rc init

# 2. Run your preferred agent — hooks fire automatically on every edit
claude       # or: codex / gemini / copilot / kimi / vibe / pi
```

### What You See When an Edit is Blocked

```
[reasoning-core] BLOCKED: duplicate definition (get_parser)
  file: sphinx/cmd/build.py
  line: 45
  reason: Function `get_parser` collides with existing definition in sphinx/cmd/quickstart.py:571

[hybrid-reasoner] Decision ID: 92644989594e
  Inspect:  rc explain 92644989594e
  Override: rc bypass-next
```

- **Auditable**: Every block generates a unique `Decision ID`.
- **Explainable**: Run `rc explain <id>` to see the exact rule or structural vector that triggered.
- **Operator Override**: Need to force an edit? Run `rc bypass-next` to allow a single bypass.

---

## Key Features

- **Instant Pre-Execution Oracles (< 5 ms)**:
  - `py_compile`: Diff syntax validation.
  - `ruff`: Linting, imports, and style enforcement.
  - `ast.parse`: AST consistency smoke checks.
  - `.reasoning-core/rules.yaml`: Project-specific forbidden imports and regex patterns.
- **Mamba-3 Structural Scoring (8-Dimension Vector)**:
  - Neural analysis of `cyclomatic`, `fan_in`, `fan_out`, `depth`, `churn`, `coupling`, `cohesion`, and `novelty`.
  - Advisory by default; flags high-risk architecture shifts without false-positive hard blocks.
- **Multi-Host Parity**:
  - **Tier 1 (Runtime Hook)**: Claude Code, OpenAI Codex, Gemini CLI, Moonshot Kimi, Pi, Antigravity.
  - **Tier 2 (Model-Mediated MCP Gate)**: GitHub Copilot CLI, Mistral Vibe.
- **100% Local & Private**:
  - Sidecars bind exclusively to `127.0.0.1` and refuse external interfaces.
  - Zero telemetry, zero tracking, zero remote relay.

---

## CLI Reference

| Command | Purpose |
|---|---|
| `rc status` | Show sidecar health, active embedder, and enforcement posture |
| `rc init` | Wire hooks into the current repo and start background sidecar |
| `rc autonomous` | Run bounded agent coding with local Laya intake triage |
| `rc explain <id>` | Inspect why a specific edit was blocked |
| `rc bypass-next` | Arm an authenticated one-time bypass for the next edit |
| `rc enable-enforcement` | Switch from advisory mode to deterministic hard-blocking (`--hard`) |
| `rc disable-enforcement` | Revert to advisory-only mode |
| `rc doctor` | Verify agent-hook wiring and audit logs |
| `rc upgrade` | Pull latest updates and idempotently verify hook integrity |

---

## Configuration

By default, `rc init` runs in **advisory mode** (`RC_MODE=advise`): it audits and warns without blocking your edits. To enable hard blocking on deterministic rule violations, run `rc enable-enforcement --hard`.

Key environment variables (configured via `.envrc` or `.envrc.local`):

```bash
export RC_MODE=advise              # advise | copilot (deterministic hard blocks)
export RC_EMBEDDER=mamba3-siso-893m # mamba3-siso-893m | unixcoder-base | mamba-130m
export S2_HARD_CAP_MS=1500         # neural scoring timeout before falling back to symbolic checks
export RC_RULE_ENGINE=1            # enforce .reasoning-core/rules.yaml
```

The system automatically selects the best embedder model that fits your machine's available RAM:
- **≥ 8 GiB RAM**: `mamba3-siso-893m` (Mamba-3 SISO 893M, runs on MPS or CPU).
- **2–8 GiB RAM**: `unixcoder-base` (lightweight transformer fallback).
- **< 2 GiB RAM**: `mamba-130m` (legacy small footprint).

See [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for all options.

---

## Scientific Rigor & Audit History

This repository is built with strict reproducibility and attestation standards:
- **Immutable Baselines**: Every architectural and threshold modification is preceded and followed by an immutable baseline recording (e.g. `rc baseline compare`).
- **Hostile Audits**: Thoroughly vetted against multiple adversarial audit rounds covering shell escapes, race conditions, and statistical calibration. All findings and remediations are transparently documented:
  - [`docs/AUDIT_RESPONSE_2026_09_19.md`](docs/AUDIT_RESPONSE_2026_09_19.md)
  - [`docs/AUDIT_RESPONSE_2026_09_22.md`](docs/AUDIT_RESPONSE_2026_09_22.md)
  - [`docs/AUDIT_RESPONSE_2026_09_22_ROUND2.md`](docs/AUDIT_RESPONSE_2026_09_22_ROUND2.md)
  - [`docs/HARDENING.md`](docs/HARDENING.md)
- **Honest Evidence Reporting**: Historical synthetic claims have been formally retracted in [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md). Neural signals remain strictly advisory unless corroborated by deterministic checks.

---

## Documentation Index

- [Installation & Platform Setup](docs/INSTALL.md)
- [How It Works (System 1 + System 2)](docs/HOW_IT_WORKS.md)
- [Bounded Autonomous Harness](docs/AUTONOMOUS_HARNESS.md)
- [Laya Local Setup & Evaluation](docs/LAYA_LOCAL_EVAL.md)
- [Configuration & Environment Variables](docs/CONFIGURATION.md)
- [CLI Host Parity Guide](docs/CLI_PARITY.md)
- [Threat Model & Hardening](docs/HARDENING.md)
- [Benchmark Results & Evaluations](docs/BENCHMARKS.md)
- [FeatureBench 4-Arm Evaluation Protocol](eval/featurebench_four_arm/README.md)

---

## License

[MIT](LICENSE) © Jakub Sikora.
