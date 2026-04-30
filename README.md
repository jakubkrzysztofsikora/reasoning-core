# reasoning-core

> A hybrid **System 1 / System 2** cognitive architecture for AI coding agents. The linguistic
> surface (Claude Code) delegates **non-textual reasoning** to a local Mamba SSM that scores every
> proposed edit against an AST/CFG knowledge graph. Bad refactors get blocked **before** they hit
> your filesystem.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.135+-009688.svg)](https://fastapi.tiangolo.com/)
[![MCP](https://img.shields.io/badge/MCP-FastMCP-3b82f6.svg)](https://modelcontextprotocol.io/)
[![Tree-sitter](https://img.shields.io/badge/Tree--sitter-multi--language-orange.svg)](https://tree-sitter.github.io/)
[![Mamba SSM](https://img.shields.io/badge/SSM-state--spaces%2Fmamba--130m-purple.svg)](https://huggingface.co/state-spaces/mamba-130m-hf)

---

## Why?

LLMs are great at producing plausible code. They are **bad** at predicting whether a change
preserves architectural invariants — fan-in/fan-out, cyclomatic envelopes, coupling drift,
semantic novelty. Those properties live in the **structure** of the codebase, not in token
streams.

`reasoning-core` separates the work:

| Layer | Role | Implementation |
| --- | --- | --- |
| **System 1** | Linguistic surface, intent decoding, code emission | Claude Code (proxied via [y-router](https://github.com/luohy15/y-router) to Scaleway's `devstral-2-123b-instruct-2512`) |
| **System 2** | Mathematical reasoning over AST/CFG | Local Python sidecar — Tree-sitter parser + real **Mamba SSM** forward pass |
| **Bridge** | Bidirectional contract | [`FastMCP`](https://modelcontextprotocol.io/) server `hybrid-reasoner` |
| **Enforcement** | Edit-time blocking | `PreToolUse` hook on `Edit` / `Write` / `MultiEdit` — exit 2 on regression |

The output is an **ImpactReport** with an Architectural Impact Score, an 8-dimensional risk
vector, a coherence delta from the Mamba embedding space, and a `regression_detected` boolean
that drives the hook's exit code.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              SYSTEM 1 (Linguistic)                           │
│                                                                              │
│   ┌──────────────────┐      y-router       ┌─────────────────────────────┐   │
│   │   Claude Code    │ ◄────proxy─────────► │  Scaleway Generative APIs   │   │
│   │  (CLI / Editor)  │                     │  devstral-2-123b-instruct   │   │
│   └─────────┬────────┘                     └─────────────────────────────┘   │
│             │                                                                │
│             │  PreToolUse hook (Edit | Write | MultiEdit)                    │
│             ▼                                                                │
│   ┌────────────────────────────┐         ┌─────────────────────────────┐     │
│   │  pre_edit_guard.py (stdlib)│ ──HTTP──┤  hybrid-reasoner (FastMCP)  │     │
│   └────────────┬───────────────┘         │  reason_over_edit(...)      │     │
│                │                         └──────────────┬──────────────┘     │
└────────────────┼────────────────────────────────────────┼────────────────────┘
                 │ POST /score                            │ HTTP
                 ▼                                        ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                       SYSTEM 2 — Local sidecar (127.0.0.1:8765)              │
│                                                                              │
│   ┌─────────────────────┐  ┌──────────────────────┐  ┌────────────────────┐  │
│   │  Tree-sitter parser │─►│  AST/CFG → tokens    │─►│  Mamba SSM forward │  │
│   │  py / js / ts /     │  │  call graph,         │  │  state-spaces/     │  │
│   │  c# / sql           │  │  cyclomatic,         │  │  mamba-130m-hf     │  │
│   │                     │  │  coupling, ...       │  │  (real weights)    │  │
│   └─────────────────────┘  └──────────────────────┘  └─────────┬──────────┘  │
│                                                                ▼             │
│                                              ┌─────────────────────────────┐ │
│                                              │   ImpactReport (JSON)       │ │
│                                              │   • architectural_impact    │ │
│                                              │   • coherence_delta (L2)    │ │
│                                              │   • risk_vector[8]          │ │
│                                              │   • regression_detected     │ │
│                                              │   • human_summary           │ │
│                                              └─────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full deep-dive, including the SSM
backbone selection rationale.

---

## Features

- ✅ **Real Mamba SSM weights** — `state-spaces/mamba-130m-hf` loaded via `transformers.AutoModel`,
  not a hash mock. Deterministic forward pass (`model.eval()` + `torch.no_grad()` + seeded).
- ✅ **5 Tree-sitter languages** — Python, JavaScript, TypeScript, C#, SQL. Auto-detection by
  file extension; typed `UnsupportedLanguageError` (no silent fallback).
- ✅ **MCP-native bridge** — `hybrid-reasoner` registered as a stdio MCP server. Any
  MCP-compatible client (Claude Code, Claude Desktop, custom) can invoke `reason_over_edit`.
- ✅ **PreToolUse hook with stdlib-only runtime** — the hook survives broken venvs because it
  uses only `urllib.request`. Exit 2 blocks the edit; exit 0 lets it through.
- ✅ **Fail-open by default**, **fail-closed by env** — `S2_FAIL_CLOSED=1` makes the hook block
  when the sidecar is unreachable; without it, it gracefully passes through.
- ✅ **Live Scaleway probe** — `scripts/configure-scaleway.sh` exercises both
  `https://api.scaleway.ai/v1/chat/completions` and your local y-router, with a fail-fast 2-second
  budget on missing credentials.
- ✅ **Reasoning skill** — `.claude/skills/reasoning/SKILL.md` teaches Claude how to translate the
  8-dim risk vector into prose warnings.

---

## Quick start

### 1. Install

```bash
git clone https://github.com/<your-org>/reasoning-core.git
cd reasoning-core
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Cache the Mamba SSM checkpoint (one-time, ~250 MB)

```bash
huggingface-cli download state-spaces/mamba-130m-hf
# or set S2_SSM_CHECKPOINT to a locally-cached model id of your choice
```

### 3. Boot the System 2 sidecar

```bash
bash scripts/start-sidecar.sh
# waits for /health to report model_loaded:true
```

In another terminal, sanity-check it:

```bash
curl -fsS http://127.0.0.1:8765/health | jq
```

### 4. Wire Claude Code

The repo ships a ready-to-use [`.claude/settings.json`](.claude/settings.json):

```jsonc
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          { "type": "command",
            "command": "python3 ${CLAUDE_PROJECT_DIR}/src/hooks/pre_edit_guard.py",
            "timeout": 35000 }
        ]
      }
    ]
  },
  "mcpServers": {
    "hybrid-reasoner": {
      "type": "stdio",
      "command": "python3",
      "args": ["-m", "src.mcp_reasoner"]
    }
  }
}
```

From here on, every `Edit`/`Write`/`MultiEdit` Claude attempts is scored. Regressions are
blocked before they touch the filesystem.

### 5. (Optional) Configure Scaleway via y-router

```bash
scw config set secret-key=$YOUR_KEY --profile newprofile
bash scripts/configure-scaleway.sh         # exports env + runs live probe
# adds:  ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN,
#        ANTHROPIC_MODEL=devstral-2-123b-instruct-2512
```

### 6. Run the e2e gate

```bash
bash scripts/test-prototype.sh
# expects PASS — bad refactor blocked, benign rename allowed across all 5 languages
```

---

## Usage

### Score a change from Python

```python
from src.s2_core import score_change

before = """
def normalize(items):
    if not items:
        return []
    return [x.lower() for x in items]
"""

after = """
def normalize(items):
    return [x.lower() for x in items]            # guard removed
"""

report = score_change("/repo/util.py", before, after)
print(report.architectural_impact_score)         # → 0.31
print(report.regression_detected)                # → True
print(report.human_summary)
# "Removed early-return guard; novelty=0.94 (dominant) + cyclomatic dropped below baseline."
```

### Invoke via the MCP bridge

```python
# from any MCP client
result = client.call_tool("hybrid-reasoner", "reason_over_edit", {
    "file_path": "/repo/util.py",
    "proposed_change": after,
    "change_kind": "edit",
})
```

### HTTP (manual)

```bash
curl -fsS -X POST http://127.0.0.1:8765/score \
  -H 'content-type: application/json' \
  -d '{"path":"/repo/util.py","before_src":"...","after_src":"..."}' | jq
```

---

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `S2_DEVICE` | `cpu` | `cpu` or `cuda` for the Mamba forward pass |
| `S2_SSM_CHECKPOINT` | `state-spaces/mamba-130m-hf` | Override the SSM backbone checkpoint |
| `S2_TIMEOUT` | `30` | Seconds the hook + bridge wait for `/score` |
| `S2_FAIL_CLOSED` | _unset_ | Set to `1` to BLOCK edits when sidecar is unreachable |
| `HF_HOME` | `~/.cache/huggingface` | Where the Mamba weights are cached |
| `ANTHROPIC_BASE_URL` | `http://localhost:8787` | y-router address (proxies to Scaleway) |
| `ANTHROPIC_MODEL` | `devstral-2-123b-instruct-2512` | Linguistic model id |
| `RC_LIVE` | _unset_ | Set to `1` to enable live Scaleway tests |

---

## How it works (brief)

1. **Claude proposes an edit.** The PreToolUse hook fires before the file is modified.
2. **`pre_edit_guard.py`** reads the hook payload from stdin and POSTs `{path, before_src,
   after_src}` to the sidecar.
3. **Tree-sitter** parses both sides into ASTs and builds a per-module call graph.
4. **AST → token sequence**, fed through the **Mamba SSM** to produce a pooled embedding for
   each side.
5. **Risk vector** (8 dims) is computed: cyclomatic, fan_in, fan_out, depth, churn, coupling,
   cohesion, and **novelty** (cosine distance between Mamba embeddings).
6. The sidecar returns an `ImpactReport`. The hook **blocks** (exit 2) if any of:
   - `architectural_impact_score < 0.4`
   - `coherence_delta > 1.5`
   - any risk dim `> 0.9`

The decision rule is intentionally simple — extend it via `.claude/skills/reasoning/SKILL.md`.

---

## Project layout

```
reasoning-core/
├── README.md                                  ← you are here
├── LICENSE                                    ← MIT
├── requirements.txt
├── pyproject.toml
├── .claude/
│   ├── settings.json                          ← PreToolUse hook + MCP server
│   └── skills/reasoning/SKILL.md              ← prose translator for risk vectors
├── src/
│   ├── ssm_backbone.py                        ← real Mamba loader, embed(), ast_to_tokens
│   ├── grammars.py                            ← Tree-sitter loader (py/js/ts/cs/sql)
│   ├── s2_core.py                             ← parsing, scoring, FastAPI HTTP service
│   ├── mcp_reasoner.py                        ← FastMCP bridge `hybrid-reasoner`
│   └── hooks/
│       └── pre_edit_guard.py                  ← stdlib-only PreToolUse hook
├── scripts/
│   ├── start-sidecar.sh                       ← boots /health-aware sidecar
│   ├── configure-scaleway.sh                  ← live Scaleway + y-router probe
│   └── test-prototype.sh                      ← e2e: bad refactor blocked, benign allowed
├── tests/
│   ├── test_s2_core.py                        ← parser + scoring + HTTP
│   ├── test_mcp_reasoner.py                   ← bridge fail-open / fail-closed / 415
│   ├── test_hook_block.py                     ← hook exit codes + payload shapes
│   └── test_scaleway_smoke.py                 ← live probe (gated by RC_LIVE=1)
├── board/
│   └── board.json                             ← multi-agent kanban (planner / engineers / QA)
└── docs/
    ├── PLAN.md
    ├── ARCHITECTURE.md                        ← SSM Backbone Selection deep-dive
    └── VERIFICATION.md                        ← cross-verification logs
```

---

## Testing

```bash
pytest -q -m "not live"                          # offline suite (fast)
RC_LIVE=1 pytest -q tests/test_scaleway_smoke.py # live Scaleway round-trip
bash scripts/test-prototype.sh                   # full e2e gate
```

Current offline result: **41 passed, 12 skipped** (skips are gated on Mamba checkpoint
availability — see `RC-102` in [`board/board.json`](board/board.json)).

---

## Development

This project was built using a **multi-agent SDLC**:

- **Engineering Manager** subagent translated [`docs/PLAN.md`](docs/PLAN.md) into the kanban
  ([`board/board.json`](board/board.json)).
- **3 Senior Fullstack Engineer** subagents implemented disjoint tranches in parallel
  (sidecar / bridge+hook / Scaleway+docs).
- **Peer code review** subagents reviewed each other's tranches before merging.
- **QA + Dev + Data Eng** subagents cross-verified the result; bugs they found
  (FastAPI return-type annotation, SQL grammar dialect mismatch) were fixed before the final
  green run.

The kanban stages are `todo → inprogress → codereview → test → done`. The full ledger of who
did what is in `board/board.json`.

---

## Contributing

Contributions are welcome. The SDLC bar is:

1. **Spec first.** Update [`docs/PLAN.md`](docs/PLAN.md) before writing code; declare your
   acceptance criteria explicitly.
2. **Add a kanban entry.** Either edit `board/board.json` directly or open an issue with the
   intended `id` / `deliverable` / `acceptance_criteria`.
3. **Self-verify.** Before opening a PR:
   - `python3 -m py_compile $(git ls-files '*.py')` exits 0
   - `python3 -m pytest -m "not live"` is green
   - `bash -n scripts/*.sh` clean
   - `python3 -c "import json; json.load(open('.claude/settings.json'))"` passes
4. **Don't break the contracts.** The HTTP `/score` shape, the `ImpactReport` JSON, and the
   MCP tool signature are public — bumping them needs a board task and a docs update.
5. **Use real things.** Real Mamba weights, real Tree-sitter grammars, real Scaleway calls.
   Mocks belong in tests only.

A typical contribution flow:

```bash
git checkout -b rc-feature-x
# ... edit, test ...
pytest -q -m "not live"
git commit -m "feat(rc-XXX): short imperative title"
git push -u origin rc-feature-x
```

---

## Roadmap — what's next

- **RC-102 — Mamba checkpoint cache automation.** Bake the HF download into
  `scripts/start-sidecar.sh` (with retry + checksum), so the e2e gate runs out of the box on a
  fresh clone with no manual `huggingface-cli` step.
- **Coherence threshold normalization.** The `coherence_delta > 1.5` rule is raw L2 of
  hidden-size vectors and is therefore not portable across SSM checkpoints. Replace with a
  scale-invariant statistic (e.g. `L2 / sqrt(hidden_size)` or a percentile-calibrated cutoff
  per checkpoint).
- **Multi-checkpoint ensemble.** Run a code-trained SSM (e.g. `code-mamba`) alongside the
  generic checkpoint and average the novelty signal.
- **Real `slide-mamba` weights.** Adopt them as soon as they're publicly released — the
  loader's `S2_SSM_CHECKPOINT` env var already supports a one-line swap.
- **More grammars.** Java, Go, Rust, Kotlin, Swift. The `grammars.py` extension table makes
  this a 5-minute add per language.
- **GPU-native batched scoring.** Today the sidecar serves one `/score` at a time on CPU.
  Batched GPU inference would cut the per-edit latency from ~3s to ~50ms.
- **Repo-level memory.** Persist a baseline embedding for the whole repo so `coherence_delta`
  becomes "drift from the project's own attractor" rather than just "drift from before_src".
- **Server-Sent Events `/score/stream`.** For multi-edit sessions, stream incremental scores
  so Claude can adjust mid-flight.
- **Prometheus metrics on `/metrics`.** Latency, AIS distribution, regression rate per
  language. Useful for tuning thresholds in production.
- **Pre-commit hook variant.** Same engine, fired by `git commit` for non-Claude workflows.

---

## Acknowledgements

- [Mamba](https://huggingface.co/state-spaces/mamba-130m-hf) — Albert Gu & Tri Dao's structured
  state-space model. The mathematical heart of System 2.
- [Tree-sitter](https://tree-sitter.github.io/) — Max Brunsfeld's incremental parser library.
- [Model Context Protocol](https://modelcontextprotocol.io/) — Anthropic's open standard for
  tool/server interop.
- [FastMCP](https://github.com/jlowin/fastmcp) — the Pythonic MCP SDK that makes the bridge
  ~150 LOC.
- [y-router](https://github.com/luohy15/y-router) — Anthropic↔OpenAI proxy that lets Claude
  Code talk to Scaleway.
- [Scaleway Generative APIs](https://www.scaleway.com/en/generative-apis/) — `devstral-2-123b`
  on serverless GPUs.

---

## License

[MIT](LICENSE) © Jakub Sikora — use it, fork it, ship something better than this.
