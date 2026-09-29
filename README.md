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

If you use AI coding agents (Claude Code, OpenAI Codex, Gemini CLI, Cursor, Copilot) on real repositories, you already know the **System 1 hallucination trap**:
- The agent hallucinates illegal parameters or non-existent schema defaults.
- It invents duplicate function definitions that shadow existing architecture.
- It silently breaks repository conventions, forbidden imports, and architectural contracts.

On large codebases (~200k+ LOC), unguided agents fail almost entirely on multi-module tasks because they lack architectural guardrails.

**reasoning-core** turns your existing AI agent into a reliable, disciplined pair programmer:
1. **Pre-Write Containment Gate**: Intercepts every proposed edit *before it lands on disk*. Fast deterministic checks (`py_compile`, `ruff`, `ast.parse`, and your `.reasoning-core/rules.yaml`) block broken code instantly.
2. **Structural Mamba-3 SSM**: Evaluates an 8-dimension risk vector (coupling, cohesion, cyclomatic complexity, churn, novelty) to flag architectural regressions.
3. **Local Laya Intake Triage**: Helps your agent quickly scope and triage relevant files across sprawling multi-module repositories.

### Proven Benchmark Results

On real-world multi-module architectural tasks in large codebases (FeatureBench Sphinx row 99, ~200k LOC):

| Arm | Test Pass Rate | What Happened |
|---|:---:|---|
| **Vanilla Agent** (Codex alone) | **0%** (0/10) | Hallucinated illegal default keys into CLI parser; crashed all tests |
| **Laya alone** | **0%** (0/10) | Fast triage, but produced identical hallucinated schema defaults |
| **Reasoning Core alone** | **0%** (0/10) | Explored safely but timed out without finishing patch |
| **Reasoning Core + Laya** | **70% (7/10)** | **Blocked duplicate function collisions & hallucinated options; agent self-corrected to compliant architecture** |

> In confirmatory evaluations on disposable worktrees, the harness reduced observer-detected invalid policy writes by **>10×** compared with vanilla Claude Code (rate-ratio upper 95% CI: 0.0295; 150 pairs). See [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md) and [`eval/featurebench_four_arm/README.md`](eval/featurebench_four_arm/README.md).

---

## Quick Path to First Success (With Your Existing Agent Setup)

You do **not** need to change how you work or learn a new coding interface. You wire Reasoning Core directly into your existing project repository and use your existing AI agent CLI.

### 1. Install reasoning-core

> ⚠️ **Important:** Install from this tagged GitHub repository. The `reasoning-core` name on PyPI belongs to an unrelated project.

```bash
python3 -m pip install 'reasoning-core[full] @ git+https://github.com/jakubkrzysztofsikora/reasoning-core.git@v0.3.0'
```

### 2. Wire into Your Existing Project Repository

Run one command inside the repo you want protected:

```bash
cd /path/to/your-project
rc init
```

**What `rc init` does automatically:**
- Detects your installed agents and wires pre-tool interception hooks into `.claude/`, `.codex/`, `.gemini/`, `.copilot/`, etc.
- Launches the local Reasoning Core sidecar daemon (`127.0.0.1:8765`) running Mamba-3 SISO.
- Creates `.reasoning-core/rules.yaml` where you can define custom forbidden imports or repo rules.

### 3. (Best Case) Start Local Laya for Multi-File Triage

For large codebases where agents easily lose context, run the lightweight local Laya server in a background terminal:

```bash
uv pip install 'laya[serve]==0.3.20'
LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_DEVICE=mps LAYA_MODELS=english laya-serve
```
*(On Linux or systems without Apple Silicon MPS, set `LAYA_DEVICE=cpu`)*

### 4. Experience Your First Success

Launch your agent in your repository as you normally do:

```bash
claude      # or: codex / gemini / copilot / kimi / vibe / pi
```

Ask your agent to perform a multi-file refactor or implement a feature across modules:

> *"Refactor our CLI command options in `src/cli.py` to support `--format` and update downstream parser calls."*

#### What Happens in Real Time:

1. **The Trap**: The agent attempts a typical "System 1" hallucination—such as defining a duplicate `get_parser()` helper that already exists elsewhere in the project, or introducing a syntax/type mismatch.
2. **The Catch**: Reasoning Core's pre-write hook intercepts the edit **before it touches your disk**:
   ```
   [reasoning-core] BLOCKED: duplicate definition (get_parser)
     file: src/cli.py:45
     reason: Function `get_parser` collides with existing definition in src/core/parser.py:120

   [hybrid-reasoner] Decision ID: 92644989594e
     Inspect:  rc explain 92644989594e
     Override: rc bypass-next
   ```
3. **The Self-Correction**: Because the agent receives immediate, deterministic feedback about why the edit was illegal, it imports the existing parser instead of recreating it.
4. **The Result**: Clean, compliant code that respects your architecture and passes tests on the first run.

---

## How It Works

```
                                 Your Existing Agent
                          (Claude Code / Codex / Gemini)
                                         │
                             Proposes File Edit / Write
                                         │
                                         ▼
              ┌────────────────────────────────────────────────────────┐
              │          [Reasoning Core Pre-Write Gate]               │
              │  1. Fast Oracles (<5ms): py_compile, ruff, ast.parse   │
              │  2. Policy Engine: .reasoning-core/rules.yaml          │
              │  3. Neural Scorer: Mamba-3 SISO 8-Dim Structural Risk  │
              └──────────────────────────┬─────────────────────────────┘
                                         │
                         ┌───────────────┴───────────────┐
                         │                               │
                   [Pass / Clean]               [Blocked / Violation]
                         │                               │
                         ▼                               ▼
                   Lands on Disk                Decision ID & Feedback
                                                (Agent self-corrects)
```

- **Instant Pre-Execution Oracles (< 5 ms)**: Catches syntax errors, lint failures, and forbidden imports with zero API cost.
- **Mamba-3 Structural Scoring (8-Dimension Vector)**: Evaluates `cyclomatic`, `fan_in`, `fan_out`, `depth`, `churn`, `coupling`, `cohesion`, and `novelty`. Advisory by default to keep agents moving while catching high-risk regressions.
- **Operator In The Loop**: Need to override a block? Run `rc bypass-next` from another terminal to arm a single bypass. Run `rc explain <id>` to inspect full decision details.
- **100% Local & Private**: Binds strictly to `127.0.0.1`. No telemetry, no cloud relay.

---

## Supported Agent Hosts

| Host CLI | Hook Surface | Integration Tier |
|---|---|:---:|
| **Claude Code**, **OpenAI Codex**, **Gemini CLI**, **Moonshot Kimi**, **Pi**, **Antigravity** | Runtime hook (pre-tool interception before write) | **Tier 1** |
| **GitHub Copilot CLI**, **Mistral Vibe** | Model-mediated MCP gate (`gate_edit`) | **Tier 2** |

See [`docs/CLI_PARITY.md`](docs/CLI_PARITY.md) for host-specific details.

---

## Everyday CLI Commands

| Command | Purpose |
|---|---|
| `rc status` | Check sidecar health, active embedder, and enforcement posture |
| `rc init` | Wire hooks into the current repo and start background sidecar |
| `rc explain <id>` | Inspect why a specific edit was blocked |
| `rc bypass-next` | Arm an authenticated one-time bypass for the next edit |
| `rc enable-enforcement` | Switch from advisory mode to deterministic hard-blocking (`--hard`) |
| `rc disable-enforcement` | Revert to advisory-only mode |
| `rc doctor` | Verify agent-hook wiring and audit logs |
| `rc upgrade` | Pull latest updates and idempotently verify hook integrity |
| `rc autonomous` | (Optional) Headless runner for CI and bounded task evaluations |

---

## Configuration & Modes

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
- **Immutable Baselines**: Every architectural and threshold modification is preceded and followed by an immutable baseline recording (`rc baseline compare`).
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
- [Configuration & Environment Variables](docs/CONFIGURATION.md)
- [CLI Host Parity Guide](docs/CLI_PARITY.md)
- [Bounded Autonomous Harness](docs/AUTONOMOUS_HARNESS.md)
- [Laya Local Setup & Evaluation](docs/LAYA_LOCAL_EVAL.md)
- [Threat Model & Hardening](docs/HARDENING.md)
- [Benchmark Results & Evaluations](docs/BENCHMARKS.md)
- [FeatureBench 4-Arm Evaluation Protocol](eval/featurebench_four_arm/README.md)

---

## License

[MIT](LICENSE) © Jakub Sikora.
