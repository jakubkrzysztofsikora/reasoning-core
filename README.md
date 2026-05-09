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
  Stop the agent vibecoding files outside its plan. Save your AI tokens. 100% locally.
</p>

<p align="center">
  <a href="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/lint-and-test.yml">
    <img src="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/lint-and-test.yml/badge.svg" alt="lint-and-test">
  </a>
  <a href="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/eval.yml">
    <img src="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/eval.yml/badge.svg" alt="eval">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT">
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  </a>
  <a href="#roadmap">
    <img src="https://img.shields.io/badge/status-alpha-orange.svg" alt="Status: alpha">
  </a>
  <a href="https://huggingface.co/state-spaces/mamba-130m-hf">
    <img src="https://img.shields.io/badge/SSM-mamba--130m-purple.svg" alt="Mamba 130M">
  </a>
  <a href="https://modelcontextprotocol.io/">
    <img src="https://img.shields.io/badge/MCP-FastMCP-3b82f6.svg" alt="MCP">
  </a>
</p>

---

## TL;DR

```bash
git clone https://github.com/jakubkrzysztofsikora/reasoning-core.git
cd reasoning-core && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && huggingface-cli download state-spaces/mamba-130m-hf
direnv allow .
bash scripts/install-supervisor-launchagent.sh   # macOS — KeepAlive sidecar
# or, ad-hoc, no launchd:
# bash scripts/start-sidecar.sh
export PATH="$PWD/bin:$PATH"                     # `rc` admin shim
claude   # every Edit/Write Claude proposes is now scored before it lands
```

Also works headless on **Gemini CLI**, **GitHub Copilot CLI**, and **Mistral Vibe CLI** — same sidecar, per-host install scripts ([§5c–5e](#5c-enable-for-gemini-cli)).

After that, every change Claude proposes goes through a structural-regression scorer.
The gate ships in **shadow mode** by default (`RC_SHADOW_MODE=1`) — decisions are logged
to the audit trail without enforcing, so you can observe what *would* be blocked on your
codebase before flipping it on. Repo-scoped via direnv — leaves every other folder
untouched.

---

## Table of contents

- [What you get out of the box](#what-you-get-out-of-the-box)
  - [More details](#more-details)
  - [Where it doesn't help ](#where-it-doesnt-help)
- [Why this exists](#why-this-exists)
- [The solution: System 1 + System 2](#the-solution-system-1--system-2)
- [Run it locally (6 steps, no global side-effects)](#run-it-locally-6-steps-no-global-side-effects)
- [Multi-CLI support — Gemini / Copilot / Vibe](#5c-enable-for-gemini-cli)
- [How it works under the hood](#how-it-works-under-the-hood)
- [Hook layers](#hook-layers)
- [CLI](#cli)
- [Supervisor & launchd (macOS)](#supervisor--launchd-macos)
- [Shadow mode & kill switches](#shadow-mode--kill-switches)
- [Evaluation harness](#evaluation-harness)
- [Benchmarks — iteration 1 (draft)](#benchmarks--iteration-1-draft)
- [Scoring math](#scoring-math)
- [Configuration](#configuration)
- [Usage from code](#usage-from-code)
- [Project layout](#project-layout)
- [FAQ / troubleshooting](#faq--troubleshooting)
- [Testing](#testing)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Acknowledgements + License](#acknowledgements--license)

---

## What you get out of the box

- **Up to ~29% fewer tokens per task.** On the PR-review task in our 8-task eval, the sidecar pulled 724k cache-read tokens vs 1.02M for vanilla Claude Code — a 29% saving on that single task. Auth-abandonment task came in at 27% lower. At Anthropic's public cache-read price ($0.30/MTok), if you save ~300k cache-read tokens per task that's ~$0.09/task. 100 tasks/month ≈ $9 saved. 1,000 ≈ $90. 10,000 ≈ $900. Benchmark tests averaged across all 8 tasks saved 8.2%, while being more token-effecient on every task.

- **The agent doesn't go vibecoding off-plan or inventing patterns your repo already has.** Two measured wins drive this: the agent stays inside the files it promised to touch (+0.23 on a 1–5 scale, 4.09 → 4.31), and plans use your repo's existing helpers and naming conventions instead of inventing fresh ones (+0.43, 3.71 → 4.14). In practice: less PR-review thrash, less rework, fewer "no, use the existing util" loops with the  agent.                                                          

- **Plans you don't have to keep reviewing and amending.** Plan quality went from 3.62 → 3.94 on the same 1–5 scale. You get a structured, sound plan on the first run.
                                                                                                                                                
- **Privacy: 100% local, your code stays on your laptop.** The scoring model (130M params, ~200MB RAM) and all the planning/grounding hooks run next to your `claude` CLI process. Nothing extra leaves your machine compared to plain `claude` — no telemetry, no cloud relay, no third-party service inspecting your diffs. Your code is processed exactly where it was already going (Anthropic) and nowhere else.    

### More details

8 real engineering tasks. 3 runs each. 2 setups (vanilla `claude` vs `claude` + sidecar). 3 independent reviewer models from 3 different vendors graded every plan and every implementation, blind.
                                                                                                                                                  
  | | Vanilla `claude` | `claude` + sidecar |  |                                                                                      
  |---|---|---|---|
  | Tasks passed (locked tests) | 92% | 100% |                                  
  | Tasks passed (rotated tests) | 90% | 100% |                                                                                            
  | Plan quality (1–5) | 3.62 | 3.94 | +0.32 |
  | Implementation quality (1–5) | 3.80 | 4.00 | +0.20 |                                                                                          
  | Stays inside promised files (1–5) | 4.09 | 4.31 | Agent goes off-plan less often |
  | Uses your repo's existing patterns (1–5) | 3.71 | 4.14 | Fewer invented helpers / new conventions |                                           
  | Code legibility (1–5) | 4.26 | 4.26 | Tied — sidecar doesn't help here |                                                                      
  | Total tokens used | 23.1M | 21.2M | −8.2% averaged across all 8 tasks |                                                                       
  | Best single-task token saving | — | −29% (PR review) | Up to ~29% on cache-heavy tasks |                                                      
  | Wall-clock per run | 547s | 645s | +98s slower — sidecar plans before it edits |
  | Where your code is processed | Anthropic only | Anthropic only + your laptop | Nothing new leaves your machine |  

### Where it doesn't help                                                                                                                        
                                                                                                                                                  
- **It costs you ~98 seconds per run.** The sidecar plans before the agent edits. If you live or die by raw turnaround, vanilla `claude` is faster.
- **Code legibility was a tie.** Both setups produce equally readable code. **The sidecar doesn't make your code prettier** — it makes the agent comply with your standards and stay on-plan.                                                   
- **Eval was 1 codebase, 8 tasks.** Numbers may shift on yours.     
  
---

## Why this exists

### Where LLMs shine

LLMs are extraordinary at **token-level pattern completion**. Function signature + docstring →
plausible body. Stack trace → suggested fix. Dataset + goal → boilerplate. Frontier models
match or exceed mid-level engineers on bounded coding tasks where the answer is a local
rewrite.

### Where LLMs fail

Drop the same model into a 50k-LOC codebase and quality collapses. LLMs are bad at:

- **Architectural invariants** — does this preserve the layer boundary? The model has no
  concept of "layer", just tokens.
- **Cross-file consequences** — a 1-line edit fan-outs to 30 callers; the model only saw 1
  file.
- **Cyclomatic envelopes** — adding a 4th conditional to an already-8-branch function makes
  it untestable; the model sees "another `if`".
- **Coupling drift** — each new import creeps the module further from its single
  responsibility, with no penalty in the loss.
- **Coherence vs the rest of the project** — a new helper introducing a different idiom from
  the codebase is "novel", but novelty is sometimes a bug not a feature.
- **Plan→implementation alignment** — plan said 4 phases, only 2 landed; plan said touch
  `auth/`, diff also rewrote `payments/`. The model doesn't audit itself.

### Why they fail (the structural blind spot)

LLMs reason over **token streams**. They don't have a graph of who calls whom, a tree of
which scope nests where, or a metric for how "different" your file is from the project's
attractor. Those properties live in the **structure** — the AST, the call graph, the
embedding manifold of the repo as a whole.

Long-context windows (1M tokens) help but don't solve. The model still has to *infer*
structure from tokens at every inference, with no persistence. Reasoning over structure is a
different kind of computation: graph diffs, embedding distances, dimensionality. LLMs aren't
optimized for it; they do it badly compared to a small specialist.

### Will scaling fix it?

Probably not, at least not soon.

- **Compute scaling** — 2024-25 long-context literature shows degraded *consistent*
  cross-document reasoning even at 1M+ tokens. Bottleneck is architectural, not budget.
- **Tool scaling** — agents calling structural tools (linters, type checkers, AST search)
  outperform ones that don't. The field is going there. But tool calls are stateless per
  question; they don't accumulate repo-level reasoning.
- **Specialized models** — small models trained for code structure (CodeBERT-style retrievers,
  code-aware embedders) keep outperforming much larger general models on code-similarity. A
  small specialist beats a large generalist for this sub-problem.

So the forecast: LLMs keep getting better at the *linguistic* surface — intent decoding,
emission, prose. They will *not* spontaneously develop reliable structural reasoning.
Hybrid systems where the LLM defers structural decisions to a specialist will be the
durable architecture.

That's what this is.

---

## The solution: System 1 + System 2

Loose nod to Kahneman: System 1 = fast, linguistic, intuitive (LLM); System 2 = slow,
structural, deliberate (the SSM scorer).

```
Claude proposes an edit
        │
        ▼
┌─────────────────────────────┐
│ pre_edit_guard.py (hook)    │  reads disk + new_string,
│                             │  reconstructs (before, after),
└──────────────┬──────────────┘  posts to the sidecar
               │
               ▼
┌─────────────────────────────────────────────┐
│ Sidecar (FastAPI, 127.0.0.1:8765)           │
│   • Tree-sitter parse → AST + call graph    │
│   • Mamba 130M forward → pooled embedding   │
│   • 8-dim risk vector (delta semantics)     │
│   • Per-kind thresholds (source/test/plan)  │
│   • Cold-start aware (new files don't lie)  │
└──────────────┬──────────────────────────────┘
               │ ImpactReport JSON
               ▼
┌─────────────────────────────┐
│ Hook decides:               │
│   regression? → exit 2,     │
│     stderr block w/ top-3   │
│     repair hints            │
│   safe? → exit 0, edit      │
│     proceeds                │
└─────────────────────────────┘
```

Concrete example. Claude proposes:

```diff
-def normalize(items):
-    if not items:
-        return []
-    return [x.lower() for x in items]
+def normalize(items):
+    return [x.lower() for x in items]
```

Sidecar response (abbreviated):

```json
{
  "architectural_impact_score": 0.31,
  "coherence_delta": 0.44,
  "file_kind": "source_code",
  "risk_vector": {"novelty": 0.94, "churn": 0.02, "...": 0.0},
  "regression_detected": true
}
```

Hook blocks with stderr listing top-3 contributors + repair hints. Claude re-reads,
revises, retries. (Today: one-shot allow/block; iterative-loop on the
[roadmap](#roadmap).)

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the deep-dive and
[`docs/HARDENING.md`](docs/HARDENING.md) for the threat model.

---

## Run it locally (6 steps, no global side-effects)

The recommended setup is **repo-scoped**: leaves every other repo and Claude session untouched. Promote to global only after you're convinced it earns its keep.

### 1. Clone + venv

```bash
git clone https://github.com/jakubkrzysztofsikora/reasoning-core.git
cd reasoning-core
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. One-time Mamba checkpoint cache (~250 MB)

```bash
huggingface-cli download state-spaces/mamba-130m-hf
```

### 3. Boot the sidecar

```bash
bash scripts/start-sidecar.sh
# CPU first run: ~30s to load Mamba weights
curl -fsS http://127.0.0.1:8765/health | jq .model_loaded   # → true
```

### 4. Activate `direnv` for repo-scoped env

The repo ships an [`.envrc`](.envrc) that loads venv, sidecar tuning, hook policy posture,
HuggingFace cache pointer (shared with sibling repos at `$HOME/.cache/huggingface` so
weights aren't downloaded twice), and a Cato VPN-aware CA bundle **only when you `cd` into
this folder**. Other repos see none of it.

```bash
brew install direnv                          # if not installed
echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc # or bash equivalent
cd ~/Repos/personal/reasoning-core
direnv allow .
export PATH="$PWD/bin:$PATH"                 # so `rc` shim resolves
```

Secrets / personal toggles → `.envrc.local` (gitignored, sourced last).

### 4b. (Optional, non–Apple-silicon) Use Scaleway-hosted generative critic

On Linux / CI / Intel Mac the local `mlx_lm.server` path is unavailable.
Point the generative critic at Scaleway's hosted OpenAI-compatible API
instead (Bearer auth via `RC_GEN_API_KEY` or `SCALEWAY_API_KEY`):

```bash
export RC_REASONER_BACKEND=remote
export RC_GEN_URL=https://api.scaleway.ai/v1/chat/completions
export RC_GEN_API_KEY=$(scw config get secret-key --profile circit)  # or your own key
export RC_GEN_MODEL=qwen3-coder-30b-a3b-instruct                     # or devstral-2-123b-instruct-2512
export RC_GEN_BUDGET_MS=15000
```

Apple-silicon users keep the local path — no changes needed.

### 5. Launch Claude from this folder

```bash
cd /path/to/reasoning-core
claude   # picks up .claude/settings.json — hooks active for THIS session only
```

Verify it's actually thinking:

```bash
curl -fsS http://127.0.0.1:8765/metrics | jq                              # score_calls, p50_ms, p95_ms
ls ~/.local/share/reasoning-core/events/$(date +%F)/ | head               # per-decision audit log
rc status                                                                 # sidecar health + posture
```

### 5b. Enable on another repo (3 commands)

You've already cloned `reasoning-core` and the sidecar is running. To gate one of
your other repos with the same hooks, run from inside that repo:

```bash
export RC_REPO=$HOME/Repos/personal/reasoning-core   # or wherever you cloned it
bash $RC_REPO/scripts/enable-in-repo.sh              # writes .envrc + .claude/settings.local.json
direnv allow .                                       # load the env
```

That's it. `claude` from that repo now goes through reasoning-core. The script
refuses to overwrite an existing `.envrc` or `.claude/settings.local.json`, and
it only writes inside the cwd — nothing global is touched. Iter-3 levers
(`RC_BEST_EFFORT_SPEC`, `RC_PLAN_GROUNDING`) are commented out in the generated
`.envrc`; uncomment + `direnv reload` to opt in.

To revert: delete `.envrc` and `.claude/settings.local.json` from the repo.

### 5c. Enable for Gemini CLI

`gemini hooks migrate` makes Gemini's hook surface Claude-compatible by design. The same sidecar + adapter layer serves Gemini agents transparently — only the config file path differs.

```bash
export RC_REPO=$HOME/Repos/personal/reasoning-core
bash $RC_REPO/scripts/enable-in-repo-gemini.sh        # writes .gemini/settings.json + .gemini/skills/
direnv allow .
gemini --yolo "say hi"                                # --yolo bypasses MCP trust prompt
```

The install script renders `.gemini/settings.json.template` with `<RC_REPO>` substituted to an absolute path (per-machine; gitignored). The template ships event names that Claude already uses (`PreToolUse`, `PostToolUse`, `SessionStart`, `UserPromptSubmit`, `PreCompact`) — no rename table.

Verified against `gemini` v0.37.1.

### 5d. Enable for GitHub Copilot CLI

GitHub Copilot CLI v1.0.29 has **no hook subcommand** — runtime gating is the `gate_edit` MCP tool only, enforced by `.copilot/copilot-instructions.md` (always loaded). Post-turn audit retroactively flags missed calls (see `eval/reconcile_session.py`).

```bash
export RC_REPO=$HOME/Repos/personal/reasoning-core
bash $RC_REPO/scripts/enable-in-repo-copilot.sh       # merges hybrid-reasoner into ~/.copilot/mcp-config.json
copilot --allow-all-tools "say hi"                    # or env COPILOT_ALLOW_ALL=1
```

The script preserves any existing user MCP servers via atomic temp-rename with a timestamped backup. Tier-2 caveat: under context pressure the agent will sometimes skip `gate_edit` — for mission-critical work prefer Claude or Gemini, where the gate is a runtime hook.

### 5e. Enable for Mistral Vibe CLI

Vibe v2.9.4 ships only a `post-agent-turn` hook today. Like Copilot, the runtime gate is `gate_edit` MCP; instructions live in `.vibe/AGENTS.md` (Vibe-scoped, NOT repo-root, to avoid double-scoring on hosts that also read `AGENTS.md`).

```bash
export RC_REPO=$HOME/Repos/personal/reasoning-core
bash $RC_REPO/scripts/enable-in-repo-vibe.sh          # writes .vibe/config.toml + AGENTS.md, registers in trusted_folders
direnv allow .
vibe --prompt "say hi" --trust                        # --trust skips per-invocation trust prompt
```

For more on per-host gaps and the production caveats (tier-2 vs tier-1 gating, `flock`-on-macOS, `direnv allow` requirement, MCP startup races), see [`docs/CLI_PARITY.md`](docs/CLI_PARITY.md).

### 6. (Optional) Promote globally — one path, every project

Repo-scoped hooks fire only when Claude runs from inside this folder. To get the same
gating across **every project on your machine**, register the hooks once at the user
level — no per-repo copy-paste, no `.claude/settings.json` in each project.

The hooks are designed to live in this single clone. Other projects reference them by
absolute path via the `RC_REPO` env var, so a regex tweak or a hook fix here propagates
everywhere on the next session.

**Order matters** — follow steps 0 → 5 in order. Sidecar must be daemonized BEFORE
flipping `S2_FAIL_CLOSED`, otherwise every Edit on every project hard-blocks until the
sidecar is up.

#### Step 0. Preflight — confirm the venv has the deps

The hooks import `tree_sitter`, `transformers` (HF Mamba port), `fastapi`, and a
handful of other non-stdlib packages that live in this repo's `requirements.txt`.
Hooks invoked via system `python3` will ImportError on first fire and silently break
every Claude session machine-wide. Confirm:

```bash
cd $RC_REPO
# Create venv + install runtime deps (this is the same install §1 ran for the
# in-repo session — you only need to do it once, but verifying here costs nothing).
test -x .venv/bin/python || python3 -m venv .venv
.venv/bin/pip install -r requirements.txt          # heavy deps live here, NOT in pyproject.toml
.venv/bin/python -c "import tree_sitter, transformers, fastapi; print('venv OK')"
```

If you previously did §1 (`pip install -r requirements.txt`), the venv is already
ready — the verify line is enough.

#### Step 1. Pin the clone path in your shell rc (`~/.zshrc` or `~/.bashrc`)

```bash
export RC_REPO="$HOME/Repos/personal/reasoning-core"   # wherever you cloned
export PATH="$RC_REPO/bin:$PATH"                        # so `rc` shim resolves anywhere
```

Reload: `exec zsh` (or `source ~/.zshrc`).

#### Step 2. Daemonize the sidecar

Always-on sidecar — no `bash scripts/start-sidecar.sh` in every shell.

**macOS:**

```bash
bash $RC_REPO/scripts/install-supervisor-launchagent.sh
launchctl list | grep reasoning-core      # → com.reasoning-core.supervisor visible
curl -fsS http://127.0.0.1:8765/health    # → {"status":"ok",...}
```

**Linux (systemd user unit):**

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/reasoning-core-sidecar.service <<EOF
[Unit]
Description=reasoning-core SSM sidecar
[Service]
Type=simple
ExecStart=$RC_REPO/.venv/bin/python -m src.s2_core
WorkingDirectory=$RC_REPO
Restart=on-failure
RestartSec=5
[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now reasoning-core-sidecar.service
curl -fsS http://127.0.0.1:8765/health    # → {"status":"ok",...}
```

#### Step 3. Register the hooks at the user level

Save the snippet below to `~/.claude/settings.json` (merge into your existing file if
present — append to the matching event arrays, do NOT replace the whole `hooks` block
or you lose any other hooks you had).

```jsonc
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "hooks": {
    "PreToolUse": [
      { "matcher": "Edit|Write|MultiEdit",
        "hooks": [{ "type": "command",
                    "command": "${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/.venv/bin/python ${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/src/hooks/pre_edit_guard.py",
                    "timeout": 60000 }] },
      { "matcher": "Write",
        "hooks": [{ "type": "command",
                    "command": "${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/.venv/bin/python ${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/src/hooks/pre_plan_guard.py",
                    "timeout": 15000 }] },
      { "matcher": "Bash",
        "hooks": [{ "type": "command",
                    "command": "${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/.venv/bin/python ${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/src/hooks/pre_bash_guard.py",
                    "timeout": 5000 }] },
      { "matcher": "Task",
        "hooks": [{ "type": "command",
                    "command": "${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/.venv/bin/python ${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/src/hooks/pre_task_guard.py",
                    "timeout": 5000 }] }
    ],
    "PostToolUse": [
      { "matcher": "Bash",
        "hooks": [{ "type": "command",
                    "command": "${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/.venv/bin/python ${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/src/hooks/post_bash_revive.py",
                    "timeout": 5000 }] },
      { "matcher": "Edit|Write|MultiEdit",
        "hooks": [{ "type": "command",
                    "command": "${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/.venv/bin/python ${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/src/hooks/post_batch_lang_audit.py",
                    "timeout": 5000 }] }
    ],
    "SessionStart": [
      { "hooks": [
          { "type": "command",
            "command": "${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/.venv/bin/python ${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/src/hooks/session_start_manifest.py",
            "timeout": 30000 },
          { "type": "command",
            "command": "${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/.venv/bin/python ${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/src/hooks/session_resume_inject.py",
            "timeout": 5000 }
      ]}
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command",
                    "command": "${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/.venv/bin/python ${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/src/hooks/session_resume_inject.py",
                    "timeout": 5000 }] }
    ],
    "PreCompact": [
      { "hooks": [{ "type": "command",
                    "command": "${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/.venv/bin/python ${RC_REPO:-/Users/you/Repos/personal/reasoning-core}/src/hooks/pre_compact_guard.py",
                    "timeout": 5000 }] }
    ]
  }
}
```

Notes on the snippet:
- The `.venv/bin/python` prefix is **load-bearing** — system `python3` lacks the deps.
- `${RC_REPO:-fallback}` is POSIX parameter expansion, evaluated by Claude Code's
  shell-invoked hook command. The fallback path is for safety; replace with your real
  clone path. One-liner if you set `$RC_REPO` already:
  ```bash
  sed -i '' "s|/Users/you/Repos/personal/reasoning-core|$RC_REPO|g" ~/.claude/settings.json   # macOS
  sed -i    "s|/Users/you/Repos/personal/reasoning-core|$RC_REPO|g" ~/.claude/settings.json   # Linux
  ```
- `session_resume_inject.py` is registered on BOTH `SessionStart` AND
  `UserPromptSubmit` — that's the in-repo `.claude/settings.json` wiring; both
  events are needed so resume-context fires consistently.

#### Step 4. Set conservative env defaults globally

Append to your shell rc. **Critical**: leave `S2_FAIL_CLOSED=0` until you've verified
the daemon stays up across reboots — fail-closed globally + sidecar crash = every Edit
on every project hard-blocks.

```bash
# ~/.zshrc
export RC_SHADOW_MODE=1            # log-only by default; flip to 0 once calibrated
export RC_LANG_LOCK=0               # off by default — repos vary; calibrate per-repo via project .envrc
export RC_PLAN_BLOCK=0              # plan-guard warn-only by default
export S2_FAIL_CLOSED=0             # fail-OPEN globally; flip to 1 only after the daemon proves stable
# Iter-3 levers stay project-scoped — DO NOT enable globally without registering
# session_start_best_effort.py first (see docs/iter3-levers.md):
# export RC_BEST_EFFORT_SPEC=1
# export RC_PLAN_GROUNDING=1
```

Per-repo `.envrc` files override these for projects where you want stricter posture
(e.g. set `S2_FAIL_CLOSED=1` and `RC_PLAN_BLOCK=1` in `.envrc` for the repo where
you've already calibrated the corpus).

#### Step 5. Verify global activation

Open Claude Code from **any** repo (not just `$RC_REPO`) and confirm hooks fire:

```bash
cd ~/some/other/project
claude
# … in another terminal …
ls ~/.local/share/reasoning-core/events/$(date +%F)/   # ← decisions from THIS project's session appear
rc status                                              # sidecar /health passes
jq -r '.project_dir' ~/.local/share/reasoning-core/events/$(date +%F)/*.jsonl | sort -u
# ← project_dir field shows the actual cwd of the session, not $RC_REPO
```

Iter-3 lever sanity (only if you opted into them in step 4):

```bash
jq 'select(.signal_source=="best_effort_spec" and .decision=="injected") | .overlay_sha' \
   ~/.local/share/reasoning-core/events/$(date +%F)/*.jsonl
# ← should print "c7080f353e94" (overlay v3) once per session
```

#### Notes & gotchas

- **`~/.claude/settings.json` + project-local `.claude/settings.local.json` MERGE
  ADDITIVELY on hook arrays.** Each matcher/event entry is appended; matching entries
  do NOT override. If `pre_edit_guard.py` is registered both globally and in a
  project's `settings.local.json`, the hook runs **twice per edit**. Solution: pick
  one — global-only (recommended for shared hooks) or project-only (recommended for
  experimental ones). Don't double-register.
- **Cato VPN / corporate TLS-MITM:** the in-repo `.envrc` builds a certifi+Cato CA
  bundle (`~/.cache/reasoning-core/ca-bundle.pem`) and exports `SSL_CERT_FILE` /
  `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE`. If you go global on a corp-network laptop,
  copy that block (or the bundle path) into your shell rc — otherwise Scaleway / HF
  Hub TLS will fail with cert-chain errors.
- **Iter-3 levers won't fire globally** unless you ALSO register
  `session_start_best_effort.py` in the SessionStart array above. The repo `.envrc`
  has a `_RC_LEVER_WARN` self-check that catches env-set-but-hook-unregistered;
  global users don't run direnv on `~/`, so the warn is silent. Either register the
  hook globally and document iter-3 enablement, or keep the levers project-scoped via
  per-repo `.envrc`. See [`docs/iter3-levers.md`](docs/iter3-levers.md).
- **Uninstall:** remove the reasoning-core entries from `~/.claude/settings.json`
  with `jq` rather than deleting the whole `hooks` block (you may have other hooks):
  ```bash
  jq 'del(.hooks.PreToolUse |= map(select(.hooks[0].command|test("reasoning-core")|not)))' \
     ~/.claude/settings.json > /tmp/s.json && mv /tmp/s.json ~/.claude/settings.json
  # repeat for PostToolUse, SessionStart, UserPromptSubmit, PreCompact, then
  launchctl unload ~/Library/LaunchAgents/com.reasoning-core.supervisor.plist  # macOS
  systemctl --user disable --now reasoning-core-sidecar.service                  # Linux
  ```
- **Failure modes to know:**
  - `RC_REPO` wrong/unset: hooks 404, every Edit fails. Symptom: stderr from Claude
    Code shows `command not found` or `python3: can't open file`. Fix: re-export or
    fix the fallback path.
  - Sidecar dead + `S2_FAIL_CLOSED=1`: every Edit hard-blocks. Symptom:
    `[hybrid-reasoner] BLOCKED: sidecar unreachable`. Fix: `rc status`,
    restart the daemon (step 2).
  - venv missing tree_sitter / transformers: every hook ImportErrors. Symptom:
    stderr noise from every Edit (`ModuleNotFoundError: No module named 'tree_sitter'`).
    Fix: `cd $RC_REPO && .venv/bin/pip install -r requirements.txt`.

---

## How it works under the hood

1. **Claude proposes an edit.** The PreToolUse hook fires before the file is modified.
2. **`pre_edit_guard.py`** reads the hook payload from stdin, **reconstructs** the
   post-edit file (`before` from disk + apply `old_string→new_string`), POSTs
   `{path, before_src, after_src}` to the sidecar.
3. **Tree-sitter** parses both sides into ASTs. Code languages get a per-module call graph;
   data languages skip the graph but still get embedded.
4. **Mamba SSM** produces a pooled embedding for each side (130M params, hidden=768, CPU).
5. **Risk vector** (8 dims, all delta-semantics): cyclomatic, fan_in, fan_out, depth, churn,
   coupling, cohesion, novelty.
6. **File-kind dispatch** picks per-kind `cd`/`ais`/`dim` thresholds. Cold-start (empty
   before) zeros structural dims so new files only get gated on content novelty.
7. The hook **blocks** (exit 2) iff:
   - `architectural_impact_score < ais_threshold[kind]`
   - `coherence_delta > cd_threshold[kind]`
   - any risk dim `> dim_ceiling[kind]`
8. Block stderr surfaces top-3 risk contributors with repair hints. Retries within 120s
   trigger a "RETRY DETECTED" banner.
9. In **shadow mode** (default), steps 7–8 still execute but the hook always returns
   exit 0; the would-be decision is logged for offline review.

---

## Hook layers

| # | Hook | Event / matcher | Purpose |
|---|---|---|---|
| L1 | `pre_bash_guard.py` | PreToolUse / `Bash` | Blocks shell-level source writes (heredoc, sed, tee), kills against the sidecar, env tampering, edits to guard files |
| L2 | `pre_edit_guard.py` | PreToolUse / `Edit\|Write\|MultiEdit` | SSM scoring; per-kind threshold dispatch; mock-detector; OOD detector; language-lock; drift policy; guard-file lock |
| L3 | `pre_plan_guard.py` | PreToolUse / `Write` (and Plan-shaped writes to `**/plans/**.md`) | Plan-time heuristics + plan-quality CGS (kNN novelty, section drift, plan→impl coherence) |
| L4 | `pre_task_guard.py` | PreToolUse / `Task` | Regex screen on subagent prompts mentioning guarded paths with mutation verbs |
| L5 | `post_bash_revive.py` | PostToolUse / `Bash` | Re-spawns sidecar when `/health` stops responding after a kill-shaped command |
| L6 | `post_batch_lang_audit.py` | PostToolUse / `Edit\|Write\|MultiEdit` | After-the-fact language-fingerprint audit; logs drift events when foreign-language ratio crosses `RC_LANG_AUDIT_THRESHOLD` |
| L7 | `pre_compact_guard.py` | PreCompact | Captures pre-compaction state so the post-compact context can be reconciled |
| L8 | `session_start_manifest.py` | SessionStart | Snapshots `RC_*` env, repo SHA, language fingerprint, active task spec; prevents mid-session env tampering |
| L9 | `session_resume_inject.py` | SessionStart (resume) + UserPromptSubmit | Re-injects pinned env from the prior session manifest into the resumed shell |

All nine wired in [`.claude/settings.json`](.claude/settings.json). Every fire emits an
audit row.

Internal helpers (libraries, not hook entrypoints): `_audit_rotation`, `_block_format`,
`_kill_switches`, `_magic_comments`, `_mock_detector`, `_ood_detector`, `_plan_quality`,
`_session_manifest`, `_shadow_mode`.

---

## CLI

Put `bin/` on PATH (`export PATH="$PWD/bin:$PATH"`) and use `rc` for diagnostics and
single-shot bypasses:

| Command | Purpose |
|---|---|
| `rc status` | Sidecar health + threshold posture (shadow mode? fail-closed? per-kind ceilings?) |
| `rc explain` | Explain the most recent block decision (top-3 risk contributors + repair hints) |
| `rc bypass-next` | Arm a single-shot bypass for the next Edit/Write — consumed on first guard fire |
| `rc skip-file <path>` | Add `<path>` to the per-session skip list (logged) |
| `rc unskip-file <path>` | Remove `<path>` from the skip list |

`rc --help` is authoritative.

---

## Supervisor & launchd (macOS)

The sidecar is a long-lived FastAPI process; if it crashes mid-session you lose scoring
until you notice. The supervisor solves both problems.

```bash
bash scripts/install-supervisor-launchagent.sh
launchctl list | grep com.reasoning-core
tail -f /tmp/rc-sidecar-supervisor.log
```

- KeepAlive=true → relaunches on crash.
- RunAtLoad=true → starts on login.
- Uninstall: `launchctl bootout gui/$UID/com.reasoning-core.supervisor` then delete the
  plist from `~/Library/LaunchAgents/`.

Linux equivalent: see [`docs/HARDENING.md`](docs/HARDENING.md).

---

## Shadow mode & kill switches

The gate ships in **shadow mode** by default (`RC_SHADOW_MODE=1` in `.envrc`). Decisions
are computed and logged; the hook always returns exit 0. This lets you observe what the
gate *would* have done on your codebase before flipping it on.

Promote to enforcement when ready:

```bash
echo 'export RC_SHADOW_MODE=0' >> .envrc.local
direnv reload
```

Escapes (in order of preference):

- **Magic comment, single edit:** prepend `# rc:bypass-next` (or `// rc:bypass-next`) to
  the file before the Edit Claude is about to fire.
- **Single-shot, single command:** `rc bypass-next` arms one bypass; the next guard fire
  consumes it.
- **Single-shot, fresh session:** `RC_BYPASS_NEXT=1 claude ...` — captured at session
  boot, consumed by the first guard fire.
- **Per-path session-wide:** `RC_ALLOW_GUARD_EDIT=1` for guarded paths,
  `RC_ALLOW_SUBAGENT_GUARD_EDIT=1` for subagent prompts naming them.
- **Last resort:** `S2_FAIL_CLOSED=0` and kill the sidecar — fails open. Don't ship this;
  it nullifies the gate.

Every escape path emits an audit row tagged with the override mechanism so abuse is
spottable later.

---

## Evaluation harness

`eval/` is the calibration + regression-test machine for the gate. See
[`eval/README.md`](eval/README.md) for full reference.

| Component | Purpose |
|---|---|
| `validate_embedder.py` | Embedder fitness test — checks Mamba pooled embeddings discriminate semantic-vs-syntactic edits |
| `calibration_corpus.py` | Mines labeled (good-edit, bad-edit) pairs from git history |
| `golden_set.py` | Pinned regression cases that must keep their decisions across releases |
| `recalibrate.py` | Page-Hinkley monthly recal of per-kind thresholds |
| `qwen_grounding_eval.py` | Computes Cohen κ between SSM gate and the generative critic. Three datasets: v1 (`datasets/grounding_pairs.jsonl`, 200 pairs git-mined), v2 (`datasets/grounding_pairs_v2.jsonl`, 138 pairs, devstral-123b judge-relabeled, kin-judge contaminated), and v3 (`datasets/grounding_pairs_v3.jsonl`, 131 high-confidence pairs from a 200-pair input × 3 cross-family judges: devstral + llama-3.3-70b + mistral-small). Independence test on v3 reports max pairwise κ = 0.6998 (passes the < 0.7 gate by 0.0002). Live sentinel `eval/runs/qwen_kappa_gate.json`: gate_pass=true, κ=0.8025, dataset_version=v3. NB: `coh_delta_epsilon.json` is currently a bootstrap placeholder; needs live-derivation from the v3 benign subset before SCR promotion. |
| `relabel_grounding_pairs.py` | Pipeline that produced v2 from v1 via Scaleway-hosted devstral-2-123b judge (`4ed3245`) |
| `run_suite.py` + `aggregate.py` + `stats.py` | Paired Wilcoxon harness across N runs |
| `synthetic_drift.py` | Generates drifted variants for stress testing |
| `build_grounding_pairs.py` | Rebuilds the labeled-pair dataset from raw sources |

Smoke run:

```bash
python3 -m eval.run_suite --task fixtures/smoke --n 2
python3 -m eval.aggregate --runs eval/runs/smoke-001
```

---

## Benchmarks — iteration 1 (draft)

> **Status: initial draft, n=1–3 per cell, single-judge.** Iteration-2 failure-mode
> fixes are **shipped** (mock-detector P1 `1724810`, plan-quality CGS P2 `ad2ea80`,
> language-fingerprint lock P3 `54c6e57`, calibration concurrent with shadow P7
> `89820b0`); the binomial sign-test acceptance harness wired in
> `eval/stats.py::sign_test` (`6a921ce`). Re-run against the same 8-task harness
> is **scheduled**, not yet executed — the numbers below are still iter-1 ground
> truth. Pre-registered acceptance: ≥7/8 task-mean wins for Setup B with ≥1.0
> BARS gap and sign-test p ≤ 0.05.
> Full per-task per-judge tables live in `~/evals/2026-05-05_*/REPORT.md`
> (gitignored — they include real Anthropic spend).

**Setup.** Two arms × eight tasks (T1, T2, T5, T7, T8, T9, E1, P0), randomized arm order
per pair, fixed-seed correctness gate + rotated-seed flake check, BARS-rubric
implementation- and plan-quality grades.

- **Setup A (control):** vanilla Claude Code, no hooks.
- **Setup B (reasoning-core):** the gate in this repo, `S2_FAIL_CLOSED=1`,
  shadow-mode off (a *measurement* configuration; default operator posture
  ships shadow-mode-on per `.envrc`).

### Headline numbers (means across 8 tasks)

| metric | Setup A (vanilla) | Setup B (reasoning-core) | Δ (B − A) | Δ% |
|---|---:|---:|---:|---:|
| **Cost / task (USD)** | $11.44 | $8.56 | −$2.88 | **−25.1%** |
| **Wall clock / task** | 1 656 s | 1 270 s | −386 s | **−23.3%** |
| **Tokens / task (main)** | 66 733 | 65 222 | −1 511 | −2.3% |
| Impl quality (BARS 1–5) | 2.90 | 2.88 | −0.02 | flat |
| Plan quality (BARS 1–5) | 2.92 | 2.50 | −0.42 | −14.4% |
| **Task wins (decision rule: gates → impl_q → plan_q → cost)** | 2 / 8 | **6 / 8** | — | — |

Suite totals: Setup A spent **$91.50** / 533 866 tokens across all 8 tasks; Setup B spent
**$68.51** / 521 772 tokens. ~$23 / 25% saved at the suite level on this single-run draft.

### Per-task verdicts

| task | winner | A impl_q / plan_q | B impl_q / plan_q | A tokens | B tokens | A $ | B $ |
|---|---|---:|---:|---:|---:|---:|---:|
| T1 | A | 5.0 / 5.0 | 3.0 / 1.0 | 71 200 | 29 800 | $13.08 | $3.41 |
| T2 | B | 3.5 / 3.0 | 5.0 / 3.0 | 121 000 | 94 200 | $22.93 | $12.39 |
| T5 | B | 1.83 / 1.67 | 3.5 / 3.0 | 84 533 | 72 208 | $15.53 | $10.26 |
| T7 | B | 1.83 / 1.67 | 3.5 / 3.0 | 84 533 | 72 208 | $15.53 | $10.26 |
| T8 | B | 3.0 / 3.0 | 4.0 / 5.0 | 41 600 | 24 662 | $6.98 | $2.83 |
| T9 | A | 1.0 / 3.0 | 1.0 / 1.0 | 37 700 | 39 894 | $2.85 | $3.49 |
| E1 | B (correctness gate) | 3.5 / 3.0 (locked 0/1) | 1.0 / 1.0 (locked 1/1) | 57 600 | 41 600 | $5.89 | $4.29 |
| P0 | B | 3.5 / 3.0 | 2.0 / 3.0 | 35 700 | 147 200 | $8.71 | $21.58 |

### What this draft shows

- **Money**: Setup B is meaningfully cheaper on 6/8 tasks. The P0 outlier (B spent
  $21.58 vs A's $8.71) inflates B's mean tokens and partially erases the per-token
  savings; without P0, B's mean cost drops to ~$5.34 (−40% vs A).
- **Wall clock**: Setup B finishes ~23% faster on average. The gate is not free
  (p95 ~5 s/Edit on CPU Mamba); the speedup comes from B avoiding regression-rework
  loops.
- **Quality**: implementation-quality means are flat. B wins by **decision rule**
  (gates → impl_q → plan_q → cost), not by raw rubric points.
- **Failures (informative)**:
  - **T1** lost because the iter-1 build had no mock-detector — Claude shipped
    placeholder code, scored low. Iter-2 ships `_mock_detector.py`.
  - **T9** lost because plan-time scoring measured plan-vs-plan novelty, not
    generic-vs-specific. Iter-2 ships the plan-quality CGS gate
    (`_plan_quality.py`, behind `RC_PLAN_QUALITY=1`).
  - **E1** is a partial-win: B passed the correctness gate (locked 1/1) where A
    failed (locked 0/1), but the rubric grader marked B's diff lower because no
    language-convention enforcement existed in iter-1. Iter-2 ships
    `RC_LANG_LOCK` + post-batch language audit.

### Caveats

- n=1 per (task, arm) cell on most tasks (T5/T7 group has n=3); CIs are wide. The
  iter-2 re-run targets n≥3 per cell with cross-family judges (Gemini + vibe) and
  a Krippendorff α inter-rater gate.
- Single-judge BARS grades; iter-2 adds the cross-family judge and per-grade
  contamination check (`eval/contamination.py` in the orchestrator harness).
- Decision rule is lexicographic (gates first); a single rubric point swing can
  flip a per-task verdict. Treat headline win-count as directional, not significant.

### Iteration-2 acceptance criterion (pre-registered)

Setup B must pass the sign-test on 8 tasks (≥7/8 wins → p ≤ 0.035; 8/8 → p = 0.0039)
with **paired bootstrap 95% CI on suite-mean BARS impl-quality excluding 0**. See
[`thoughts/shared/plans/2026-05-06-iter2-100pct-eval-plan.md`](thoughts/shared/plans/2026-05-06-iter2-100pct-eval-plan.md)
for the full v2 plan + the 19 reviewer corrections folded in.

---

## Scoring math

### 8-dim risk vector (all delta semantics)

| Dim | Formula | Normalizer |
|---|---|---|
| `cyclomatic` | `max(0, b_after − b_before)` | 20.0 |
| `fan_in` | `max(in-deg(graph_after) − in-deg(graph_before), 0)` | 8.0 |
| `fan_out` | `max(out-deg_after − out-deg_before, 0)` | 12.0 |
| `depth` | `max(d_after − d_before, 0)` | 40.0 |
| `churn` | `len(symdiff(line_set_before, line_set_after))` | 200.0 |
| `coupling` | `max(edges_after − edges_before, 0)` | 40.0 |
| `cohesion` | `max(lack_cohesion_after − lack_cohesion_before, 0)` | 1.0 |
| `novelty` | `1 − max(cos(emb_before, emb_after), 0)` | 1.0 |

### Coherence delta

`coherence_delta = ||emb_after − emb_before||₂ / sqrt(hidden_size)`. Cold-start (empty
`before_src` or <32 chars) sets it to `0.0`.

### Per-kind thresholds

| kind | `cd` | `ais` | dim ceiling |
|---|---:|---:|---:|
| `source_code` | 1.5 (env) | 0.4 (env) | 0.9 (env) |
| `test_code`   | 2.0 | 0.3 | 0.95 |
| `plan_md`     | 3.0 | 0.3 | 1.0 |
| `doc_md`      | 3.0 | 0.3 | 1.0 |
| `config`      | 1.2 | 0.5 | 0.9 |

Block fires iff `ais < threshold[kind]` OR `cd > threshold[kind]` OR any
`risk_dim > dim_ceiling[kind]`.

---

## Configuration

### Sidecar runtime

| Env var | Default | Purpose |
|---|---|---|
| `S2_DEVICE` | `cpu` | `cpu` or `cuda` |
| `S2_PORT` | `8765` | Sidecar bind port |
| `S2_URL` | `http://127.0.0.1:$S2_PORT` | Override hook target |
| `S2_TIMEOUT` | `60` | Hook /score timeout (seconds) |
| `S2_FAIL_CLOSED` | `1` | `1` blocks edits when sidecar unreachable |
| `S2_LOG_LEVEL` | `INFO` | Sidecar log level |
| `S2_SSM_CHECKPOINT` | `state-spaces/mamba-130m-hf` | Override SSM backbone |
| `HF_HOME` | `$HOME/.cache/huggingface` | HF cache (shared with sibling repos / eval worktrees) |

### Source-code thresholds

Per-kind ceilings for `test_code` / `plan_md` / `doc_md` / `config` are not
env-overridable yet — see `_KIND_THRESHOLDS` in `src/s2_core.py`. The three vars below
control only the `source_code` kind.

| Env var | Default | Purpose |
|---|---|---|
| `S2_AIS_THRESHOLD` | `0.4` | AIS threshold for `source_code` |
| `S2_COHERENCE_THRESHOLD` | `1.5` | `coherence_delta` threshold for `source_code` |
| `S2_RISK_DIM_THRESHOLD` | `0.9` | Per-dim ceiling for `source_code` |

### Hook policy posture

| Env var | Default | Purpose |
|---|---|---|
| `RC_SHADOW_MODE` | `1` | Log decisions, do not enforce |
| `RC_PLAN_BLOCK` | `1` | Plan-guard warnings escalate to hard block |
| `RC_PLAN_QUALITY` | `0` | Enable plan-quality CGS gate |
| `RC_MOCK_DETECTOR` | `1` | Reject placeholder code patterns |
| `RC_LANG_LOCK` | `1` | Reject edits introducing un-fingerprinted languages |
| `RC_LANG_ALLOW` | _unset_ | Comma-list of additional languages to permit |
| `RC_LANG_OVERRIDE` | _unset_ | Per-edit language override |
| `RC_LANG_LOCK_MAX_FILES` | `20000` | Cap files scanned when fingerprinting the repo |
| `RC_LANG_AUDIT_THRESHOLD` | `0.33` | PostToolUse foreign-language ratio that triggers an audit row |
| `RC_DRIFT_WARN` | `4.0` | **Placeholder** cumulative-drift warn level pending Phase 3.5 calibration; not production-tuned (tracker #78). |
| `RC_DRIFT_DENY` | `6.0` | **Placeholder** cumulative-drift hard-deny level pending Phase 3.5 calibration. |
| `RC_DRIFT_OVERRIDE` | _unset_ | `1` disables drift policy (hard-denied if set inline via Bash) |

### Generative repair head

| Env var | Default | Purpose |
|---|---|---|
| `RC_REASONER_BACKEND` | `mlx` | `mlx` (Apple) / `llama` / `remote` |
| `RC_GEN_URL` | local mlx port | Override for hosted endpoint (e.g. Scaleway `https://api.scaleway.ai/v1/chat/completions`) |
| `RC_GEN_API_KEY` | _unset_ | Bearer token for hosted endpoint (`80b5543`); falls through to `SCALEWAY_API_KEY` |
| `RC_GEN_MODEL` | `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit` | Model id sent in chat-completions body |
| `RC_GEN_BUDGET_MS` | `2500` | Generation budget per repair call (ms) |

### Calibration & supervisor (P7)

| Env var | Default | Purpose |
|---|---|---|
| `RC_CALIBRATION_ENABLED` | _unset_ | `1` enables Mahalanobis calibration gate in pre_edit_guard (`b7f0517`); default OFF until Phase 3.5 v3 corpus calibrates |
| `RC_CALIBRATION_PATH` | `eval/runs/calibration.json` | Override path to fitted calibration models |
| `RC_RECALIBRATE_POLL_S` | `60` | Supervisor watcher poll interval for `recalibrate.signal` (`5313498`); hot-reloadable per tick |
| `RC_BROKER_PORT` | `8764` | Supervisor broker `/health` aggregator port |

### Bypass / kill switches

| Env var | Default | Purpose |
|---|---|---|
| `RC_BYPASS_NEXT` | _unset_ | One-shot bypass; consumed on first guard fire |
| `RC_ALLOW_GUARD_EDIT` | _unset_ | Allow edits to guarded paths (captured at session boot) |
| `RC_ALLOW_SUBAGENT_GUARD_EDIT` | _unset_ | Allow Task prompts naming guarded paths |

### Audit log & state

| Env var | Default | Purpose |
|---|---|---|
| `RC_AUDIT_ROOT` | `$HOME/.local/share/reasoning-core/events` | Audit log root |
| `RC_AUDIT_RETENTION_DAYS` | `90` | Prune older audit shards on session start |
| `RC_AUDIT_CAP_BYTES` | `5368709120` (5 GiB) | Per-shard size cap before rotation |
| `RC_STATE_DIR` | _internal default_ | Session manifest + sentinel state |

### Eval / calibration

| Env var | Default | Purpose |
|---|---|---|
| `RC_LIVE` | _unset_ | `1` enables live Scaleway eval tests |
| `RC_EVAL_STUB_CLAUDE` | _unset_ | Stub Claude in eval harness |
| `RC_QWEN_KAPPA_SENTINEL` | `0.7` | Min Cohen κ for grounding eval to pass |
| `RC_TASK_SPEC` | _unset_ | Active task spec path (read by every hook for audit context) |

---

## Usage from code

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

## Project layout

```
reasoning-core/
├── README.md                       ← you are here
├── LICENSE
├── requirements.txt
├── pyproject.toml
├── .envrc                          ← repo-scoped env (direnv)
├── .claude/
│   ├── settings.json               ← 9 hook matchers + MCP server
│   └── skills/reasoning/SKILL.md
├── bin/
│   └── rc                          ← `python3 -m src.rc_cli` shim
├── launchd/
│   └── com.reasoning-core.supervisor.plist
├── src/
│   ├── ssm_backbone.py             ← Mamba loader, embed(), ast_to_tokens
│   ├── grammars.py                 ← Tree-sitter loader (12 languages)
│   ├── s2_core.py                  ← parsing, scoring, FastAPI sidecar
│   ├── mcp_reasoner.py             ← FastMCP bridge
│   ├── calibration.py              ← Mahalanobis + Ledoit-Wolf shrinkage + empirical-Bayes per-kind
│   ├── gen_client.py               ← Qwen / Scaleway generative client (Bearer auth)
│   ├── sidecar_supervisor.py       ← KeepAlive supervisor
│   ├── _supervisor_broker.py       ← cross-process broker (`/health` aggregator on RC_BROKER_PORT)
│   ├── _supervisor_env.py          ← env capture + restore
│   ├── _supervisor_recalibrate.py  ← watcher consuming `recalibrate.signal` for hot-refit (`5313498`)
│   ├── rc_cli.py                   ← admin / diagnostic CLI
│   └── hooks/
│       ├── pre_edit_guard.py
│       ├── pre_plan_guard.py
│       ├── pre_bash_guard.py
│       ├── pre_task_guard.py
│       ├── pre_compact_guard.py
│       ├── post_bash_revive.py
│       ├── post_batch_lang_audit.py
│       ├── session_start_manifest.py
│       ├── session_resume_inject.py
│       ├── audit_log.py            ← JSONL audit + retry detection
│       ├── _block_format.py        ← block message + repair hints
│       ├── _audit_rotation.py
│       ├── _kill_switches.py
│       ├── _magic_comments.py
│       ├── _mock_detector.py
│       ├── _ood_detector.py
│       ├── _plan_quality.py
│       ├── _session_manifest.py
│       ├── _shadow_mode.py
│       └── _calibration_gate.py    ← hot-path Mahalanobis gate (`b7f0517`)
├── scripts/
│   ├── start-sidecar.sh
│   ├── start-gen-sidecar.sh
│   ├── install-supervisor-launchagent.sh
│   ├── configure-scaleway.sh
│   └── test-prototype.sh
├── tests/
├── eval/
│   ├── README.md
│   ├── Dockerfile
│   ├── run_suite.py                ← top-level harness
│   ├── run_task.sh
│   ├── aggregate.py
│   ├── stats.py                    ← paired Wilcoxon
│   ├── metrics.py
│   ├── validate_embedder.py        ← embedder fitness test
│   ├── calibration_corpus.py       ← labeled corpus mining
│   ├── golden_set.py               ← regression suite
│   ├── recalibrate.py              ← Page-Hinkley monthly recal
│   ├── synthetic_drift.py
│   ├── build_grounding_pairs.py
│   ├── qwen_grounding_eval.py      ← Cohen κ ≥ 0.7 gate
│   ├── datasets/
│   │   ├── grounding_pairs.jsonl   ← 200 hand-labeled pairs
│   │   ├── swe_bench_verified_python_subset.json
│   │   └── refresh_subset.py
│   ├── prompts/system_prompt.txt
│   ├── fixtures/
│   ├── runs/
│   └── scripts/prefetch_mamba.sh
├── thoughts/shared/                ← research, plans, handoffs
└── docs/
    ├── ARCHITECTURE.md
    ├── HARDENING.md
    ├── EVAL_DESIGN.md
    ├── EVAL_RESULTS.md
    ├── VERIFICATION.md
    └── PLAN.md
```

---

## FAQ / troubleshooting

**Q: First time running, am I getting blocked?**
A: No. The gate ships in shadow mode (`RC_SHADOW_MODE=1`). Every Edit/Write is scored and
the decision is logged to `~/.local/share/reasoning-core/events/`, but the hook always
returns exit 0. Promote to enforcement after a few sessions of observation by setting
`RC_SHADOW_MODE=0` in `.envrc.local`.

**Q: The hook keeps blocking obviously-fine edits.**
A: Check `top risk contributors` in the block message. If a single dim sits at `1.00` on a
tiny edit, restart the sidecar (`bash scripts/start-sidecar.sh`) — old processes can hold
pre-refactor scoring code. If it persists, run `rc status` and `rc explain`, then open an
issue with the audit row attached.

**Q: Sidecar keeps dying mid-session.**
A: Install the launchd supervisor:
`bash scripts/install-supervisor-launchagent.sh`. KeepAlive will relaunch it on crash and
on login.

**Q: I'm on a corporate VPN (Cato / Zscaler / etc.) and `pip install` /
`huggingface-cli` fail with "self-signed certificate in certificate chain".**
A: `direnv reload` — the repo's `.envrc` builds
`~/.cache/reasoning-core/ca-bundle.pem` from `certifi` + your system Cato root and
exports `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE`. macOS only today
(uses `security find-certificate`); Linux users add their root to the bundle manually.

**Q: Sidecar takes forever to start.**
A: First run downloads Mamba weights (~250 MB). Subsequent boots ~30s on CPU.
Watch `tail -f /tmp/reasoning-core-sidecar.log`.

**Q: How do I temporarily turn it off?**
A: `cd` out of the repo (direnv unloads, hooks vanish), or export `S2_FAIL_CLOSED=0` and
kill the sidecar (hooks fail-open).

**Q: I want to edit the guard files themselves.**
A: Set `RC_ALLOW_GUARD_EDIT=1` in the shell that started Claude, restart Claude, edit. The
env is captured at session boot.

**Q: Will it slow me down?**
A: p95 ~5s per Edit on CPU. Latency is in the Mamba forward pass; CUDA / MLX kernels would
cut it to ~50ms. Tracked in roadmap.

**Q: It blocked a legitimate refactor. How do I override?**
A: Either revise to address the top-3 risk contributors (recommended), or set
`RC_ALLOW_GUARD_EDIT=1` for guarded paths, or temporarily relax the env knob
(`S2_RISK_DIM_THRESHOLD=1.01`) and restart sidecar. Don't disable globally — you'll lose
the signal that pointed at a real issue.

---

## Testing

```bash
pytest -q -m "not live"                          # offline suite
RC_LIVE=1 pytest -q tests/test_scaleway_smoke.py # live Scaleway round-trip (optional)
bash scripts/test-prototype.sh                   # full e2e gate
```

---

## Contributing

Yes please.

1. **Spec first.** Update [`docs/PLAN.md`](docs/PLAN.md) before writing code; declare
   acceptance criteria explicitly.
2. **Add a kanban entry.** Edit `board/board.json` or open an issue.
3. **Self-verify.** Before opening a PR:
   - `python3 -m py_compile $(git ls-files '*.py')` exits 0
   - `python3 -m pytest -m "not live"` is green
   - `bash -n scripts/*.sh` clean
   - `python3 -c "import json; json.load(open('.claude/settings.json'))"` passes
4. **Don't break the contracts.** HTTP `/score`, `ImpactReport` JSON, MCP tool signature
   are public.
5. **Use real things.** Real Mamba weights, real Tree-sitter grammars. Mocks only in tests.

---

## Roadmap

The current shipped surface goes well past verification-only — the gate participates in
planning, mines its own calibration corpus, and runs concurrent calibration in shadow
mode. The next phase is **closing the loop**: iterative repair so Claude re-proposes
against the repair hint until pass-or-yield.

Read [`thoughts/shared/research/`](thoughts/shared/research/) for the
risk-vector-delta-refactor + coherence-delta-calibration write-ups and
[`docs/PLAN.md`](docs/PLAN.md) for the spec.

### Shipped (see `git log --grep='^feat'`)

- **P-1 — Day-zero ergonomics:** magic-comment escapes, `RC_BYPASS_NEXT` kill switch,
  `rc` CLI.
- **P0 — Validation harness:** embedder fitness test, calibration corpus, golden set,
  shadow-mode wiring.
- **P1 — Plan-time SSM scoring + plan→code coherence gate**, mock-detector heuristics.
- **P2 — Generative repair head:** Qwen2.5-Coder-1.5B via MLX / Scaleway, behind
  `RC_REASONER_BACKEND`.
- **P3 — Calibration:** Mahalanobis over 8-dim risk space, per-kind shrinkage,
  Page-Hinkley monthly recalibration.
- **P4 — Calibration corpus + golden set + OOD detector + shadow-mode hardening.**
- **P5 — Sidecar broker + supervisor + grounding eval** on 200 labeled pairs.
- **P5 round-3 hardening** — 4-reviewer findings closed: per-dim Pareto epsilon,
  semantic safety net, stderr truncation contract, rules.yaml fail-closed
  (`b7f0517`).
- **P5 grounding pairs v2** — judge-relabeled high-confidence subset; live κ=0.74
  on v2 (kin-judge contaminated, gate advisory) (`4ed3245`).
- **P5 grounding pairs v3 — cross-family κ dataset** — 131 high-confidence
  pairs (200 input) × 3 judges (devstral + llama-3.3-70b + mistral-small);
  max pairwise-κ = 0.6998 < 0.7 independence gate; sentinel `qwen_kappa_gate.json`
  reports gate_pass=true at κ=0.8025. Replaces the kin-judge-contaminated v2.
- **Linux systemd user-unit recipe** — copy-paste `~/.config/systemd/user/reasoning-core-sidecar.service`
  documented inline in §6 (mirrors the macOS launchd supervisor).
- **P7 — Calibration concurrent with shadow mode** (Mahalanobis + Ledoit-Wolf
  shrinkage + empirical-Bayes per-kind anchor).
- **P7 supervisor watcher** — consumes `recalibrate.signal` for hot-refit without
  restart (`5313498`).
- **Iter-2 readiness blockers closed** — `GUARDED_PATHS` extended to all hook
  helpers + supervisor + gen_client + calibration; binomial sign-test in
  `eval/stats.py` for the falsifiable goal (`6a921ce`).
- **CI eval workflow stabilized** — sharded-safetensors fallback, lazy
  prefetch, `--run-id` arg, contents:write permission
  (`c452cb4`/`c0118a4`/`4997ec7`/`dcd3598`/`1738b57`/`1bc2718`).

### Open

- **Synthesize-Check-Refine loop** — Phase 3 of
  [`2026-05-06-system-2-loop-closure.md`](thoughts/shared/plans/2026-05-06-system-2-loop-closure.md):
  on block, auto-call the generative critic, re-score with sidecar, emit
  validated repair as stderr hint. Iteration is server-side; agent never
  sees the loop.
- **Hybrid symbolic gating (ADR injection)** — Phase 2 of the same plan:
  `.reasoning-core/rules.yaml` + `_rule_engine.py` for layered-import /
  forbidden-pattern / metric-threshold rules co-emitted with the neural
  risk vector through the same exit-2 pipe.
- **TTFV (<15 min) + drift visualization** — Phase 1: `rc audit-history` (last
  N commits, what would have been blocked) + `rc viz` Mermaid sparkline +
  `npx reasoning-core init` one-line installer.
- **CUDA / MLX kernels** for the Mamba forward pass (currently CPU-only; p95 ~5s).
- **SSE `/score/stream`** + Prometheus textfmt `/metrics` (current `/metrics`
  returns JSON; SSE endpoint not yet wired).
- **Pre-commit variant** so non-Claude editors are also gated.
- **Mamba-3 watch + Plan-B Mamba-2-2.7B fallback** — Phase 4: HOLD on Mamba-3
  (no public HF checkpoint as of 2026-05; CUDA-only kernels; only 1/8 risk
  dims uses SSM embedding). Plan-B fallback ships behind `RC_USE_MAMBA2_2_7B=1`.

Roadmap source of truth: [`docs/PLAN.md`](docs/PLAN.md) +
[`thoughts/shared/research/`](thoughts/shared/).

---

## Acknowledgements + License

- [Mamba](https://huggingface.co/state-spaces/mamba-130m-hf) — Albert Gu & Tri Dao.
- [Tree-sitter](https://tree-sitter.github.io/) — Max Brunsfeld.
- [Model Context Protocol](https://modelcontextprotocol.io/) — Anthropic.
- [FastMCP](https://github.com/jlowin/fastmcp) — Jeremiah Lowin.
- [direnv](https://direnv.net) — repo-scoped env without leaking into other shells.

[MIT](LICENSE) © Jakub Sikora — use it, fork it, ship something better.
