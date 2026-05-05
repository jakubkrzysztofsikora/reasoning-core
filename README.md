# reasoning-core

> **Stop AI coding agents from making architecturally bad changes** — by giving them a local
> Mamba SSM that reasons about the *structure* of your codebase, not just the tokens.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#roadmap)
[![Mamba 130M](https://img.shields.io/badge/SSM-mamba--130m-purple.svg)](https://huggingface.co/state-spaces/mamba-130m-hf)
[![MCP](https://img.shields.io/badge/MCP-FastMCP-3b82f6.svg)](https://modelcontextprotocol.io/)

---

## TL;DR

```bash
git clone https://github.com/jakubkrzysztofsikora/reasoning-core.git
cd reasoning-core && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && huggingface-cli download state-spaces/mamba-130m-hf
direnv allow . && bash scripts/start-sidecar.sh
claude   # every Edit/Write Claude attempts is now scored before it lands
```

After that, every change Claude proposes goes through a structural-regression scorer.
Bad refactors get **blocked before they touch your filesystem**, with a structured repair
hint telling Claude *why* and *how* to revise. Repo-scoped via direnv — leaves every other
folder untouched.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [The solution: System 1 + System 2](#the-solution-system-1--system-2)
- [What you get out of the box](#what-you-get-out-of-the-box)
- [Run it locally (5 steps, no global side-effects)](#run-it-locally-5-steps-no-global-side-effects)
- [How it works under the hood](#how-it-works-under-the-hood)
- [Hook layers](#hook-layers)
- [Scoring math](#scoring-math)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [FAQ / troubleshooting](#faq--troubleshooting)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Acknowledgements + License](#acknowledgements--license)

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

## What you get out of the box

- ✅ **Real Mamba-130M weights** — `state-spaces/mamba-130m-hf` via `transformers.AutoModel`,
  not a hash mock. Deterministic forward (`model.eval()` + `torch.no_grad()` + seeded).
- ✅ **12 Tree-sitter languages** — Python, JS, TS, C#, SQL + 7 data languages (Markdown,
  JSON, YAML, CSS, SCSS, HTML, Dockerfile). Vue routes through HTML grammar.
- ✅ **Per-file-kind threshold dispatch** — `source_code` / `test_code` / `plan_md` /
  `doc_md` / `config` each get tuned `coherence_delta`, `AIS`, and per-dim ceiling.
- ✅ **Cold-start aware** — new-file Writes (empty `before_src`) don't trip absolute-state
  saturation; structural risk dims zero out and only `novelty` polices content.
- ✅ **Delta-semantics risk vector** — fan_in/fan_out/depth/coupling/cohesion measure the
  *change*, not the file's absolute complexity.
- ✅ **5-layer hook coverage** — Edit/Write, Plan, Bash, Task subagent, sidecar revive.
- ✅ **Structured repair hints** — every block lists top-3 risk contributors with per-dim
  hints + retry-detection banner when Claude tries the same write twice.
- ✅ **Stdlib-only hook runtime** — survives broken venvs (`urllib.request` only).
- ✅ **Repo-scoped via direnv** — env, hooks, MCP servers active *only* in this folder.
- ✅ **MCP-native bridge** — any MCP client (Claude Code, Claude Desktop, custom) can call
  `reason_over_edit`.
- ✅ **Structured audit log** — `/tmp/rc-events/<date>/<session>.jsonl` per decision.

---

## Run it locally (5 steps, no global side-effects)

The recommended setup is **repo-scoped**: leaves every other repo and Claude session
untouched. Promote to global only after you're convinced it earns its keep.

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
HuggingFace cache pin **only when you `cd` into this folder**. Other repos see none of it.

```bash
brew install direnv                          # if not installed
echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc # or bash equivalent
cd ~/Repos/personal/reasoning-core
direnv allow .
```

Secrets / personal toggles → `.envrc.local` (gitignored, sourced last).

### 5. Launch Claude from this folder

```bash
cd /path/to/reasoning-core
claude   # picks up .claude/settings.json — hooks active for THIS session only
```

Verify it's actually thinking:

```bash
curl -fsS http://127.0.0.1:8765/metrics | jq    # score_calls, p50_ms, p95_ms
ls /tmp/rc-events/$(date +%F)/ | head           # per-decision audit log
```

### 6. (Optional) Promote globally

When confident it pulls its weight, copy the hook block from `.claude/settings.json` into
`~/.claude/settings.json` (with absolute paths instead of `${CLAUDE_PROJECT_DIR}`) and
launch the sidecar via launchd / systemd. Repo-scoped is enough for most users — global
only buys scoring across other repos at the cost of losing the easy-uninstall property.

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

---

## Hook layers

| # | Hook | Tool matcher | Purpose |
|---|---|---|---|
| L1 | `pre_bash_guard.py` | `Bash` | Blocks shell-level source writes (heredoc, sed, tee), kills against sidecar, edits to guard files |
| L2 | `pre_edit_guard.py` | `Edit\|Write\|MultiEdit` | SSM scoring; per-kind threshold dispatch; guard-file lock |
| L3 | `pre_plan_guard.py` | `Plan` (and Write to `**/plans/**.md`) | Plan-time heuristics: per-file LOC budget, boundary-crossing prose, novelty drift |
| L4 | `pre_task_guard.py` | `Task` | Regex screen on subagent prompts mentioning guarded paths with mutation verbs |
| L5 | `post_bash_revive.py` | `Bash` (PostToolUse) | Re-spawns sidecar when `/health` stops responding after a kill-shaped command |

All five wired in [`.claude/settings.json`](.claude/settings.json). Every fire emits an
audit row.

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

| Env var | Default | Purpose |
|---|---|---|
| `S2_DEVICE` | `cpu` | `cpu` or `cuda` |
| `S2_SSM_CHECKPOINT` | `state-spaces/mamba-130m-hf` | Override SSM backbone |
| `S2_TIMEOUT` | `60` | Hook /score timeout |
| `S2_FAIL_CLOSED` | `1` (via `.envrc`) | `1` blocks edits when sidecar unreachable |
| `S2_AIS_THRESHOLD` | `0.4` | AIS threshold for `source_code` |
| `S2_COHERENCE_THRESHOLD` | `1.5` | `coherence_delta` threshold for `source_code` |
| `S2_RISK_DIM_THRESHOLD` | `0.9` | Per-dim ceiling for `source_code` |
| `RC_PLAN_BLOCK` | `1` (via `.envrc`) | `1` escalates plan-guard warnings to hard block |
| `RC_ALLOW_GUARD_EDIT` | _unset_ | `1` allows edits to guarded paths. Captured at session boot |
| `RC_ALLOW_SUBAGENT_GUARD_EDIT` | _unset_ | `1` allows Task prompts mentioning guarded paths |
| `HF_HOME` | `$(pwd)/.cache/huggingface` (via `.envrc`) | Project-local Mamba cache |

Per-kind thresholds for `test_code` / `plan_md` / `doc_md` / `config` are not
env-overridable yet — see `_KIND_THRESHOLDS` in `src/s2_core.py`.

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
├── README.md                    ← you are here
├── LICENSE
├── requirements.txt
├── pyproject.toml
├── .envrc                       ← repo-scoped env (direnv)
├── .claude/
│   ├── settings.json            ← 5 hook matchers + MCP server
│   └── skills/reasoning/SKILL.md
├── src/
│   ├── ssm_backbone.py          ← Mamba loader, embed(), ast_to_tokens
│   ├── grammars.py              ← Tree-sitter loader (12 languages)
│   ├── s2_core.py               ← parsing, scoring, FastAPI
│   ├── mcp_reasoner.py          ← FastMCP bridge
│   └── hooks/
│       ├── pre_edit_guard.py
│       ├── pre_plan_guard.py
│       ├── pre_bash_guard.py
│       ├── pre_task_guard.py
│       ├── post_bash_revive.py
│       ├── _block_format.py     ← block message + repair hints
│       └── audit_log.py         ← JSONL audit + retry detection
├── scripts/
│   ├── start-sidecar.sh
│   ├── configure-scaleway.sh
│   └── test-prototype.sh
├── tests/
├── eval/                        ← paired Wilcoxon harness
├── thoughts/shared/             ← research, plans, handoffs
└── docs/
    ├── ARCHITECTURE.md
    ├── HARDENING.md
    └── EVAL_DESIGN.md
```

---

## FAQ / troubleshooting

**Q: The hook keeps blocking obviously-fine edits.**
A: Check `top risk contributors` in the block message. If `churn=1.00` on a small Edit, your
sidecar predates `2345fba` (Edit-tool reconstruction fix) — restart it. If `fan_out=1.00`
on an additive edit to a busy file, your sidecar predates `2873c82` (delta-semantics
refactor) — restart it.

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

The current shipped surface is verification-only — Claude proposes, SSM judges, hook
gates. The next phase is **co-reasoning**: SSM participates in planning + suggests
revisions + closes the loop iteratively.

Read [`thoughts/shared/research/2026-05-05-ssm-co-reasoner-deep-research.md`](thoughts/shared/research/2026-05-05-ssm-co-reasoner-deep-research.md)
for the deep-research findings + 3-reviewer adversarial verdict (REQUEST_CHANGES; ship in
shadow-mode first).

Tracked next-steps:

- **P0 — validation harness** (must land first): embedder fitness test, labeled corpus
  mining from git history, shadow-mode wiring (log decisions, don't enforce).
- **P1 — plan-time SSM scoring + plan→code coherence gate** (warn-only by default):
  kNN-to-nearest-existing-file novelty, sliding-window section drift, PostToolUse
  plan-implementation gate, explicit `RC_ACTIVE_PLAN` env.
- **P2 — generative repair head**: Qwen2.5-Coder-1.5B-Instruct via MLX (Apple) /
  llama.cpp GGUF (Linux) / Scaleway-hosted (CI) selected by `RC_REASONER_BACKEND`.
- **P3 — calibration**: Mahalanobis distance over 8-dim risk space, hierarchical Bayes
  per-kind shrinkage, monthly Page-Hinkley recalibration.
- **P4 — CodeBERTScore plan↔diff** for semantic alignment.
- **P5 — subagent loop path** + LLM-judge gate behind `RC_COHERENCE_LLM=1`.

Other open items: real `slide-mamba` weights when public; CUDA / MLX kernels for non-CPU
paths; SSE `/score/stream`; Prometheus textfmt `/metrics`; pre-commit variant.

---

## Acknowledgements + License

- [Mamba](https://huggingface.co/state-spaces/mamba-130m-hf) — Albert Gu & Tri Dao.
- [Tree-sitter](https://tree-sitter.github.io/) — Max Brunsfeld.
- [Model Context Protocol](https://modelcontextprotocol.io/) — Anthropic.
- [FastMCP](https://github.com/jlowin/fastmcp) — Jeremiah Lowin.
- [direnv](https://direnv.net) — repo-scoped env without leaking into other shells.

[MIT](LICENSE) © Jakub Sikora — use it, fork it, ship something better.
