# Hybrid Reasoning Core — Specification

## Architecture (System 1 / System 2)

- **System 1** = Claude Code (linguistic). Routed through y-router to Scaleway Generative APIs.
  - Endpoint: `https://api.scaleway.ai/v1` (OpenAI-compatible, proxied to Anthropic-shape via y-router).
  - Model: `devstral-2-123b-instruct-2512`.
- **System 2** = Local Python sidecar (`s2_core.py`).
  - Tree-sitter parses source files into AST/CFG knowledge graph.
  - Mock SlideMamba inference loop produces an Architectural Impact Score (AIS) for proposed edits.
- **Bridge** = `mcp_reasoner.py` (FastMCP server, name `hybrid-reasoner`).
  - Exposes tool `reason_over_edit(file_path, proposed_change)`.
  - Round-trips to S2 via UNIX socket / HTTP, returns structured math validation.
- **Hooks** = `.claude/settings.json` `PreToolUse` for `Edit` and `Write` tools.
  - Calls `hybrid-reasoner:reason_over_edit`.
  - Blocks (`exit 2`) on logical regression — i.e. AIS below threshold or coherence drift > limit.

## Repository Layout

```
reasoning-core/
├── .claude/
│   ├── settings.json                # hooks + MCP servers
│   └── skills/reasoning/SKILL.md    # how to read S2 vectors
├── board/
│   └── board.json                   # kanban: todo/inprogress/codereview/test/done
├── src/
│   ├── s2_core.py                   # System 2 sidecar (Tree-sitter + SlideMamba mock)
│   ├── mcp_reasoner.py              # FastMCP bridge
│   └── hooks/pre_edit_guard.py      # hook entrypoint
├── tests/
│   ├── test_s2_core.py
│   ├── test_mcp_reasoner.py
│   └── test_hook_block.py
├── scripts/
│   ├── test-prototype.sh            # demonstrates blocking
│   ├── configure-scaleway.sh        # exports env, scw cli with profile=newprofile
│   └── start-sidecar.sh
└── docs/
    ├── PLAN.md                      # this file
    ├── ARCHITECTURE.md
    └── VERIFICATION.md
```

## Deliverables (use these as task seeds)

### D1 — Scaleway / y-router configuration
- `scripts/configure-scaleway.sh` exports:
  - `ANTHROPIC_BASE_URL=http://localhost:8787` (y-router local) **or** the y-router public endpoint.
  - `ANTHROPIC_AUTH_TOKEN=$SCALEWAY_API_KEY`.
  - `ANTHROPIC_MODEL=devstral-2-123b-instruct-2512`.
  - `ANTHROPIC_SMALL_FAST_MODEL=devstral-2-123b-instruct-2512`.
- `scw` CLI commands invoked with `--profile newprofile`.
- Verifies `scw config get --profile newprofile` returns a non-empty `secret_key`.

### D2 — System 2 sidecar (`src/s2_core.py`)
- Tree-sitter loaders for at least Python (project is Python).
- Builds AST + simple CFG (function call graph).
- `score_change(path, before_src, after_src) -> ImpactReport`:
  - `architectural_impact_score: float` ∈ [0, 1] (1 = perfectly aligned).
  - `coherence_delta: float` (L2 distance between mock embeddings of before/after AST).
  - `risk_vector: list[float]` (8 dims: cyclomatic, fan-in, fan-out, depth, churn, coupling, cohesion, novelty).
  - `regression_detected: bool` — true when AIS < 0.4 OR coherence_delta > 1.5 OR any risk dim > 0.9.
  - `human_summary: str`.
- Runs as HTTP service on `127.0.0.1:8765` (FastAPI or stdlib `http.server`).

### D3 — MCP bridge (`src/mcp_reasoner.py`)
- FastMCP (`from mcp.server.fastmcp import FastMCP`).
- Server name: `hybrid-reasoner`.
- Tool: `reason_over_edit(file_path: str, proposed_change: str, change_kind: Literal["edit","write"]="edit") -> dict`.
- Calls S2 sidecar; returns the `ImpactReport` as a dict.
- Defensive: if sidecar down, returns `regression_detected=False` with `degraded=True` (fail-open by default; configurable via `S2_FAIL_CLOSED=1`).

### D4 — Hook (`.claude/settings.json` + `src/hooks/pre_edit_guard.py`)
- `PreToolUse` matcher: `Edit|Write|MultiEdit`.
- Runs `python3 .../pre_edit_guard.py` which:
  - Reads stdin JSON (Claude Code hook payload).
  - Calls MCP tool over local STDIO **or** directly hits the sidecar HTTP (simpler, avoids re-bootstrapping MCP from a hook).
  - Exits 2 with reason on regression; 0 otherwise.

### D5 — Skill (`.claude/skills/reasoning/SKILL.md`)
- Frontmatter: `name`, `description`.
- Translates the 8-dim risk vector into prose.
- Defines thresholds and decision matrix.

### D6 — Test prototype script (`scripts/test-prototype.sh`)
- Boots sidecar.
- Seeds a known-good function.
- Submits a deliberately bad refactor (e.g. removes a guard clause + introduces unbounded recursion) → expects `regression_detected=True` and hook exit code 2.
- Submits a benign rename → expects pass.
- Prints PASS/FAIL.

## Quality Bar

- `pytest -q` green.
- `bash scripts/test-prototype.sh` green.
- `python3 -m py_compile` on every `.py` green.
- `.claude/settings.json` validates as JSON.

## In-Scope (corrected)

- **Real SlideMamba / Mamba SSM weights.** Load a real pretrained state-space-model checkpoint
  from Hugging Face (`state-spaces/mamba-130m-hf` or compatible) as the SlideMamba backbone.
  No random mocks — the AIS computation must run a real forward pass over AST-token embeddings.
  If a downloadable `slide-mamba` checkpoint is published, prefer it; otherwise wrap real Mamba
  weights and document the substitution in `docs/ARCHITECTURE.md`.
- **Real Scaleway inference.** `scripts/configure-scaleway.sh` must perform a live POST to
  `https://api.scaleway.ai/v1/chat/completions` with `devstral-2-123b-instruct-2512`, using
  credentials read via `scw config get --profile newprofile`, and assert a 200 + non-empty
  completion. The y-router round-trip must also be exercised end-to-end before the prototype
  is considered green.
- **Real multi-language Tree-sitter.** Ship grammars for **Python, JavaScript, TypeScript,
  C#, and SQL**. The S2 sidecar must auto-select grammar by file extension
  (`.py`, `.js`/`.mjs`/`.cjs`, `.ts`/`.tsx`, `.cs`, `.sql`) and refuse (with a typed error)
  on unsupported languages — no silent fallback.

## Non-Goals

- Training new SSM weights from scratch.
- Productionising auth / multi-tenant routing for the MCP bridge.
- GPU-only execution paths (CPU inference must work, GPU is opt-in via `S2_DEVICE=cuda`).
