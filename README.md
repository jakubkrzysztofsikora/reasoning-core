<!--- Logo --->
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/logo-dark.svg">
    <img alt="reasoning-core logo" src="./docs/logo.svg" width="300">
  </picture>
</p>

<p align="center">
  <strong>reasoning-core</strong>
</p>

<p align="center">
  Stop the agent vibecoding files outside its plan. Save your AI tokens.
  <br/>
  <strong>100% local — loopback only, zero telemetry, your code never leaves your machine.</strong>
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
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT">
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  </a>
  <a href="docs/ROADMAP.md">
    <img src="https://img.shields.io/badge/status-alpha-orange.svg" alt="Status: alpha">
  </a>
</p>

---

## What it does

Claude (or Gemini / Copilot / Vibe) proposes an Edit → a local scorer reads
the diff, checks it against your repo's structural fingerprint, and **blocks
the edit before it lands** if it drifts off-plan, invents helpers you already
have, or violates a coupling/coherence threshold.

Two layers, one local pipe:

- **Neural gate.** A code-embedder (`mamba-130m` default; `codestral-mamba`,
  `bge-code`, `unixcoder-base`, `random-mamba` selectable via `RC_EMBEDDER`)
  scores an 8-dim risk vector per edit (cyclomatic / fan-in / fan-out / depth
  / churn / coupling / cohesion / novelty) plus a chord-distance
  `coherence_delta` on [0, 2]. Three additional project/session dims are
  emitted only when `RC_PROJECT_INDEX=1` and a session baseline is registered.
- **Symbolic gate.** Optional `.reasoning-core/rules.yaml` with
  `forbid_import` / `forbid_pattern` rules — fail-closed by default, evaluated
  alongside the neural risk vector. When the SSM sidecar times out, the hook
  falls back to the symbolic gate and emits `signal_source="symbolic_fallback"`.

Both decisions surface through the same exit-2 pipe with top-3 risk
contributors and a `validate_unified_diff` repair tool for blocked agents.

Everything runs on your machine. Nothing extra leaves it.

## Why you'd want it

On an 8-task eval with 3 runs each, blind-graded by 3 cross-vendor judges:

- **−8.2% tokens averaged across tasks** — best single-task saving **−29%** on PR review.
- **Plan quality 3.62 → 3.94** (1–5 BARS scale) — structured, sound plans on the first run.
- **Stays inside promised files +0.23, uses your repo's existing patterns +0.43** — fewer "no, use the existing util" loops.
- **100% local** — sidecars bind `127.0.0.1` only (loopback, refuses external NIC). Default 130M-param Mamba SSM, ~200MB RAM, sits next to `claude`. No telemetry, no cloud relay — your code stays on your laptop ([engineers asked for exactly this](thoughts/shared/research/2026-06-02-community-pain-points.md#5-demand-for-local--no-cloud-enforcement)).
- **Repo-scoped** via direnv — leaves every other folder on your machine untouched.

Costs (measured with enforcement enabled — Setup B; default installed posture
is `RC_MODE=advise`): **+98s wall-clock per run** (the gate plans before editing),
and code legibility was a tie.

Full numbers in [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md).

---

## What this means if you live in Claude Code / Codex / OpenCode

Think of reasoning-core as a **local, private governance layer** that sits
between you and the AI agent. It doesn't replace Claude, Codex, or OpenCode —
it makes them prove their work before the edit lands in your repo.

**1. The plan is no longer just vibes.**  
`PLAN.md` is compiled into a machine-readable contract. If the agent tries to
edit a file that isn't in the plan, introduce a forbidden import, or violate an
invariant you wrote down, the gate catches it first. No more 3 AM "billing
module refactor" that wasn't in the plan.

**2. Cheap local checks run before the expensive neural model.**  
Execution-grounded oracles — `py_compile`, AST smoke tests, `ruff` on changed
files — fire in milliseconds before the SSM embedder is even asked. Syntax
errors and bad imports get rejected instantly, saving you GPU time and patience.

**3. A Process Reward Model asks, "Does this edit actually match the claim?"**  
The PRM gate scores how well a diff hunk supports the claim in `PLAN.md`. It
starts in shadow mode, collecting evidence across repos and days, and only
promotes itself to a blocking gate once it has proven it would catch real
drift. The system earns the right to block.

**4. It learns from your own git history.**  
`rc audit-history` mines your recent commits and labels a commit **negative**
if it was followed within 48 hours by a fix/revert/hotfix on the same files.
That labeled feedback loop recalibrates thresholds so the gate gets tighter
where you actually make mistakes and looser where you don't.

**5. You get an operator dashboard, not a black box.**  
`rc status`, `rc explain`, `rc benchmark`, `rc reasoning-efficiency`,
`rc override-survival`, and `rc audit-history` turn gate behavior into numbers
you can reason about. You can see whether the gate is helping or just getting
in the way.

**6. It travels across agents.**  
The same contract/oracle/PRM/commit-miner stack runs under Claude Code,
Codex, Gemini, OpenCode, or any other host that speaks the hook protocol. As
the agent market fragments, your safety layer comes with you.

**7. The audit log doesn't lie.**  
An honesty baseline distinguishes verified signals from unverified ones. When
you're debugging why something was blocked — or why something *wasn't* — the
log tells you exactly what the gate actually checked.

Bottom line: fewer broken commits, less silent drift, and a measurable answer
to the question "is this AI actually making me faster, or just busier?"

---

## Install

```bash
# 1. Clone the framework
git clone https://github.com/jakubkrzysztofsikora/reasoning-core.git
cd reasoning-core

# 2. One-time bootstrap (venv + model weights + supervised stack)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
huggingface-cli download state-spaces/mamba-130m-hf
bash scripts/install-supervisor-launchagent.sh   # daemonized: S2 (:8765) + gen (:8766), broker (:8764)

# 3. Enable it in any repo you want gated
cd /path/to/your-repo
bash ~/path/to/reasoning-core/install.sh
```

Step 3 writes `.envrc`, `.claude/settings.local.json`, `.gemini/settings.json`,
`.copilot/*`, and `.vibe/*` in the current repo. It auto-installs `direnv` if
it's missing and runs `direnv allow .`. Re-running is safe — existing files
are left alone.

```bash
claude       # or: gemini / copilot / vibe — hooks fire from any of them
```

### Defaults (Phase 0, honest opt-in)

- **`RC_MODE=advise`** — gate warns and audits, but never blocks. Modes:
  `advise` (warn/audit only), `copilot` (block on contract/oracle/rule
  failures), `autopilot` (block and auto-repair within policy). Flip to
  `copilot` after reviewing a 48-hour shadow report.
- **`RC_SHADOW_MODE=1`** — decisions are logged, not enforced. Kept for
  backward compatibility; `RC_MODE=advise` is the canonical posture.
- **`RC_PLAN_GROUNDING=1`** — warn (stderr-only) when an Edit drifts from
  `PLAN.md`. Set `=0` to silence, `=2` to hard-block.
- **`RC_BEST_EFFORT_SPEC=1`** — SessionStart hook injects the iter-3
  spec overlay.
- **`RC_RULE_ENGINE=1`** — enable `.reasoning-core/rules.yaml` symbolic gate
  (fail-closed; co-emitted with the neural vector).
- **`S2_HARD_CAP_MS=1500`** — hard client-side cap on `/score` POSTs;
  on timeout the hook invokes the symbolic rule engine, lang-lock, and
  plan-grounding gates and emits `signal_source="symbolic_fallback"`.
- **`S2_COHERENCE_THRESHOLD=0.09`** — chord-distance ceiling recalibrated
  to the empirical 95th percentile (audit 2026-06-01 §1.3).

Decisions are always logged to `~/.local/share/reasoning-core/events/`
regardless of mode. See [`docs/CHANGELOG-2026-06-02.md`](docs/CHANGELOG-2026-06-02.md)
(memory-watchdog fix + supervised-stack defaults) and
[`docs/CHANGELOG-2026-06-01.md`](docs/CHANGELOG-2026-06-01.md)
for migration notes and
[`thoughts/shared/research/2026-06-01-reasoning-core-1000pct-improvements.md`](thoughts/shared/research/2026-06-01-reasoning-core-1000pct-improvements.md)
§13 for the implementation status, verification observations, and the
queued follow-ups (PRM training, Phase-2-dim verification, periodic
positive-label re-mining).

Run `rc benchmark` for a one-command Markdown report from your local audit
log, or `rc reasoning-efficiency` to see today's composite north-star score.

## Multi-machine deployment

The default install writes an **advise-mode** `.envrc` (log/warn only). To
promote a repo to **copilot** enforcement on this or another machine, append
the enforcement block to that repo's `.envrc.local`:

```bash
# >>> reasoning-core enforcement pilot (2026-07-10) >>>
# Activate intentionally with: direnv reload
# Promotes the repo from advise (log/warn) to copilot (block) and turns
# plan-grounding drift into a hard block (RC_PLAN_GROUNDING=2).
# .envrc.local is gitignored and machine-specific — do not commit it.
export RC_MODE=copilot
export RC_SHADOW_MODE=0
export RC_PLAN_BLOCK=1
export RC_PLAN_GROUNDING=2
export RC_ORACLE_BLOCK=1
export RC_RULE_ENGINE=1
export S2_FAIL_CLOSED=1
# <<< reasoning-core enforcement pilot (2026-07-10) <<<
```

`.envrc.local` is **gitignored** and loaded last by `.envrc`, so
machine-specific overrides such as `RC_EMBEDDER`, `S2_DEVICE`, or
`S2_MEM_LIMIT_GB` stay intact. After editing, activate the change with:

```bash
direnv reload
```

To replicate the same posture on another machine, pull `reasoning-core`,
re-run `install.sh` in each gated repo (or copy the block into its
`.envrc.local`), and restart the sidecar/supervisor. The enforcement block
never needs to be committed.

## Uninstall

```bash
cd /path/to/your-repo
bash ~/path/to/reasoning-core/uninstall.sh
```

Removes only what `install.sh` created (tracked via
`.reasoning-core/install.manifest`). The shared clone, the sidecar, and your
Python deps are untouched.

---

## Supported CLIs

Same sidecar, four hosts:

| CLI | Gate enforcement | Tier |
|---|---|---|
| Claude Code | Runtime PreToolUse hook | Tier 1 |
| Gemini CLI (≥0.37.1) | Runtime PreToolUse hook | Tier 1 |
| GitHub Copilot CLI (≥1.0.29) | MCP tool `gate_edit` + post-turn audit | Tier 2 |
| Mistral Vibe CLI (≥2.9.4) | MCP tool + `post-agent-turn` hook | Tier 2 |

The MCP server (`hybrid-reasoner`) exposes two tools across all hosts:
`gate_edit` (the synthetic PreToolUse gate for Tier-2 hosts) and
`validate_unified_diff` (structural patch validator + best-effort repair for
agents emitting malformed diffs).

Tier-2 means the gate runs at the model layer — the LLM is instructed to
call `gate_edit` before every write. Under context pressure it sometimes
skips the call; for mission-critical work prefer Claude or Gemini. Detail:
[`docs/CLI_PARITY.md`](docs/CLI_PARITY.md).

---

## Configure

The generated `.envrc` exposes the knobs you'll touch first:

```bash
export RC_MODE=advise       # advise | copilot | autopilot
export RC_SHADOW_MODE=1     # legacy: 0 = enforce, 1 = log-only
export S2_FAIL_CLOSED=0     # 1 = block if sidecar down, 0 = fail open
export RC_PLAN_GROUNDING=1  # 0 = off, 1 = warn, 2 = hard block
export RC_BEST_EFFORT_SPEC=1# SessionStart iter-3 spec overlay
export RC_RULE_ENGINE=1     # enable .reasoning-core/rules.yaml symbolic gate
# export RC_EMBEDDER=mamba-130m       # mamba-130m | codestral-mamba | bge-code | unixcoder-base | random-mamba
# export RC_PROJECT_INDEX=1           # enable project_fan_in / project_coupling dims
# export RC_DIFF_AUDIT=1              # post-turn unified-diff structural audit
```

Per-machine overrides → `.envrc.local` (gitignored). Full env-var table:
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

The `rc` CLI (`export PATH="$RC_REPO/bin:$PATH"`) handles diagnostics and
single-shot bypasses:

```bash
rc status                   # sidecar health + threshold posture
rc explain                  # why the last edit was blocked
rc bypass-next              # arm one bypass for the next Edit/Write
rc confirm-next             # confirm the next block was correct (audit ground-truth)
rc enable-enforcement       # first-run wizard: scaffold PLAN.md and flip to copilot
rc score-pr                 # score a PR's changed files (CI dry-run / local check)
rc benchmark                # one-command benchmark report from the audit log
rc reasoning-efficiency     # composite north-star metric from audit log
rc override-survival        # how many operator overrides survived to git HEAD
rc audit-history            # label recent commits for calibration feedback
```

`rc bypass-next` and `rc confirm-next` emit explicit `operator_override`
and `operator_confirmed` audit events so the shadow report can measure
false positives. `rc enable-enforcement` walks you through a 48-hour
shadow review, scaffolds `PLAN.md` from `README.md`, and switches the repo
to `RC_MODE=copilot`.

---

## Documentation

- [**docs/CI_INTEGRATION.md**](docs/CI_INTEGRATION.md) — GitHub Actions and Azure DevOps PR scoring
- [**docs/INSTALL.md**](docs/INSTALL.md) — manual install, global-everywhere setup, Scaleway-hosted critic, Cato VPN, supervisor/launchd, embedder backends, troubleshooting
- [**docs/USAGE.md**](docs/USAGE.md) — `rc` CLI, hook layers, rule engine, diff audit, shadow mode, bypass/kill switches, FAQ
- [**docs/CONFIGURATION.md**](docs/CONFIGURATION.md) — every `RC_*` and `S2_*` env var
- [**docs/HOW_IT_WORKS.md**](docs/HOW_IT_WORKS.md) — System 1 + System 2 architecture, 8-dim risk vector, chord-distance scoring, rule engine wiring
- [**docs/BENCHMARKS.md**](docs/BENCHMARKS.md) — full eval numbers, per-task verdicts, caveats
- [**docs/ROADMAP.md**](docs/ROADMAP.md) — what's shipped, what's open
- [**docs/ARCHITECTURE.md**](docs/ARCHITECTURE.md) — deep technical dive
- [**docs/HARDENING.md**](docs/HARDENING.md) — threat model
- [**docs/CLI_PARITY.md**](docs/CLI_PARITY.md) — per-host gaps and production caveats

---

## Use it from code

```python
from src.s2_core import score_change

before = "def f():\n    if not x: return\n    return x.lower()\n"
after  = "def f():\n    return x.lower()\n"
r = score_change("/repo/util.py", before, after)
print(r.architectural_impact_score, r.regression_detected, r.file_kind)
```

```bash
curl -fsS -X POST http://127.0.0.1:8765/score \
  -H 'content-type: application/json' \
  -d '{"path":"/repo/util.py","before_src":"...","after_src":"..."}' | jq
```

---

## Contributing

Yes please. Spec first (update [`docs/PLAN.md`](docs/PLAN.md)), add a kanban
entry, self-verify (`pytest -m "not live"` green, `bash -n scripts/*.sh`
clean, `python3 -c "import json; json.load(open('.claude/settings.json'))"`
passes). Don't break the public contracts: HTTP `/score`, `ImpactReport` JSON,
MCP tool signatures.

---

## Acknowledgements + License

- [Mamba](https://huggingface.co/state-spaces/mamba-130m-hf) — Albert Gu & Tri Dao.
- [Mamba-Codestral](https://huggingface.co/mistralai/Mamba-Codestral-7B-v0.1) — Mistral AI.
- [Tree-sitter](https://tree-sitter.github.io/) — Max Brunsfeld.
- [Model Context Protocol](https://modelcontextprotocol.io/) — Anthropic.
- [FastMCP](https://github.com/jlowin/fastmcp) — Jeremiah Lowin.
- [direnv](https://direnv.net) — repo-scoped env without leaking into other shells.

[MIT](LICENSE) © Jakub Sikora — use it, fork it, ship something better.
