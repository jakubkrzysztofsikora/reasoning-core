# Architecture

## System 1 / System 2 split

The reasoning core is a two-brain layout inspired by Kahneman's
fast/slow framing.

- **System 1 (linguistic, fast)** — Claude Code itself, routed through
  `y-router` to **Scaleway Generative APIs**. Model:
  `devstral-2-123b-instruct-2512`. System 1 produces source-code
  *proposals*: edits, rewrites, new files. It is fluent but does not
  guarantee structural soundness.

- **System 2 (mathematical, slow)** — A local Python sidecar
  (`src/s2_core.py`) that parses each proposed edit with **Tree-sitter**,
  builds an AST + call-graph, and runs a **real Mamba state-space-model
  forward pass** over the linearized AST tokens. It returns a typed
  `ImpactReport` with an architectural-impact score (AIS), a coherence
  delta, and an 8-dimensional risk vector.

- **Bridge** — `src/mcp_reasoner.py` exposes a FastMCP server named
  `hybrid-reasoner` with the tool `reason_over_edit(file_path,
  proposed_change, change_kind)`, which calls the sidecar over loopback
  HTTP.

- **Pre-edit hook** — `src/hooks/pre_edit_guard.py` is registered as a
  Claude Code `PreToolUse` hook for `Edit|Write|MultiEdit`. It reads the
  hook payload from stdin, calls the sidecar directly at
  `127.0.0.1:8765/score`, and exits `2` (block) when
  `regression_detected` is true. Otherwise it exits `0`.

## Data flow

```
                +-----------------+
   user -->     |   Claude Code    |  <-- y-router  <-- Scaleway
                |   (System 1)     |       :8787      api.scaleway.ai
                +-----------------+
                        |
                        | Edit / Write / MultiEdit (intent)
                        v
                +-----------------+
                |  PreToolUse hook |   src/hooks/pre_edit_guard.py
                +-----------------+
                        |
                        | POST /score  (loopback)
                        v
                +-----------------+
                |   S2 sidecar     |   127.0.0.1:8765
                |  (FastAPI/HTTP)  |
                +--------+---------+
                         |
                         | parse + analyze
                         v
            +------------+-------------+
            | Tree-sitter (5 grammars) |
            | AST + call graph         |
            +------------+-------------+
                         |
                         v
            +-------------------------+
            | Mamba SSM forward pass  |   state-spaces/mamba-130m-hf
            | (real pretrained weights)|  via transformers.AutoModel
            +------------+------------+
                         |
                         v
                +-----------------+
                |  ImpactReport    |  -->  hook returns exit 0 / 2
                +-----------------+
```

## Ports

| Port  | Process               | Bind         | Notes                                |
| ----- | --------------------- | ------------ | ------------------------------------ |
| 8765  | S2 sidecar (HTTP)     | `127.0.0.1`  | Loopback only; refuses external NIC. |
| 8787  | y-router (Anthropic)  | `127.0.0.1`  | Default `ANTHROPIC_BASE_URL`.        |

The y-router is an external dependency (not shipped here). It must be
running locally for the live Scaleway probe in
`scripts/configure-scaleway.sh`.

## File map

```
reasoning-core/
|-- .claude/
|   |-- settings.json              hook + MCP server registration
|   `-- skills/reasoning/SKILL.md  prose translator for the 8 risk dims
|-- board/board.json               kanban
|-- src/
|   |-- s2_core.py                 sidecar entrypoint + scoring engine
|   |-- grammars.py                Tree-sitter loader + select_grammar
|   |-- ssm_backbone.py            Mamba weights + embed() helper
|   |-- mcp_reasoner.py            FastMCP bridge
|   `-- hooks/pre_edit_guard.py    PreToolUse hook
|-- scripts/
|   |-- configure-scaleway.sh      ANTHROPIC_* env + live API probes
|   |-- start-sidecar.sh           launches sidecar with model pre-warm
|   `-- test-prototype.sh          end-to-end smoke
|-- tests/                          pytest suite
`-- docs/
    |-- PLAN.md
    |-- ARCHITECTURE.md            (this file)
    `-- VERIFICATION.md
```

## SSM Backbone Selection

The lead spec calls for a "SlideMamba" backbone. **SlideMamba is not
publicly released as a downloadable checkpoint at the time of writing.**
Rather than ship a random-weights mock (which would invalidate the AIS
math), this project substitutes a real, pretrained Mamba state-space
model and documents the substitution explicitly here.

### Chosen checkpoint

| Field             | Value                                          |
| ----------------- | ---------------------------------------------- |
| Hugging Face id   | `state-spaces/mamba-130m-hf`                   |
| Source URL        | https://huggingface.co/state-spaces/mamba-130m-hf |
| Architecture      | Mamba (selective state-space model)            |
| Parameters        | ~130M                                          |
| Hidden size       | 768                                            |
| License           | Apache-2.0                                     |
| Loader            | `transformers.AutoModel.from_pretrained`       |
| Tokenizer         | `transformers.AutoTokenizer.from_pretrained` (GPT-NeoX BPE) |
| Override env var  | `S2_SSM_CHECKPOINT`                            |

The choice optimizes for: (a) public availability with no auth gate,
(b) small enough to load on a developer laptop CPU in <30s, (c) a real
SSM (not a Transformer) so the architectural framing of "fast linguistic
S1 + slow mathematical S2 SSM" stays honest, and (d) Apache-2.0 license
so the substitution is unambiguously redistributable.

### Substitution rationale

The published SlideMamba paper proposes a sliding-window variant of
Mamba targeted at long-context code understanding. There is currently no
released checkpoint or reference implementation at a stable URL. We
considered three options:

1. **Random-init Mamba** (mock weights). Rejected: AIS would be noise,
   and any "regression" signal would be a fabricated coincidence. The
   lead's quality bar explicitly rules this out.
2. **A Transformer of similar size** (e.g. distilgpt2). Rejected: would
   contradict the architectural premise that System 2 is a state-space
   model. Reviewers reading the code would assume a Mamba and find a
   Transformer.
3. **A real, public Mamba checkpoint** as the SSM backbone. **Chosen.**
   This preserves the SSM family (selective state-space scan, linear-time
   sequence modelling) and ships actually pretrained weights, so the
   coherence-delta and novelty signals reflect real semantic embeddings.

If a `slide-mamba` checkpoint is published later, swap it in by setting
`S2_SSM_CHECKPOINT=org/slide-mamba-XYZ` — no code change required. The
loader uses `transformers.AutoModel.from_pretrained`, which will resolve
any compatible checkpoint via the same code path.

### Caveats

- `mamba-130m` requires the `mamba-ssm` and `causal-conv1d` Python
  packages to use the fused CUDA kernels. On macOS arm64 (CPU-only), the
  loader falls back to the pure-PyTorch path that ships in the
  `transformers` integration. This is slower (~3s per forward pass for
  ~2KB inputs on CPU) but functionally identical.
- The model is loaded once per process via a singleton in
  `src/ssm_backbone.py`. The sidecar pre-warms it before binding the
  HTTP listener so the first `/score` request does not pay the cold-load
  cost.
- Set `S2_DEVICE=cuda` to opt into GPU inference if available.

## Tree-sitter language support

The sidecar supports five languages. Selection is by file extension,
done in `src/grammars.py::select_grammar`.

| Language    | Extensions                  | Tree-sitter grammar          |
| ----------- | --------------------------- | ---------------------------- |
| Python      | `.py`                       | tree-sitter-python           |
| JavaScript  | `.js`, `.mjs`, `.cjs`       | tree-sitter-javascript       |
| TypeScript  | `.ts`, `.tsx`               | tree-sitter-typescript / tsx |
| C#          | `.cs`                       | tree-sitter-c-sharp          |
| SQL         | `.sql`                      | tree-sitter-sql              |

The grammars ship via the `tree-sitter-languages` aggregate wheel (or
per-language wheels — documented inline in `grammars.py`). No network is
required at runtime.

### Unsupported-language error contract

Any other extension (`.rb`, `.go`, `.rs`, `.cpp`, `.java`, `.kt`, ...)
is **rejected explicitly** — there is no silent fallback.

| Layer            | Behavior                                                        |
| ---------------- | --------------------------------------------------------------- |
| `select_grammar` | Raises `UnsupportedLanguageError(extension=".rb")`.              |
| Sidecar `/score` | Returns HTTP **415** with body `{"error": "unsupported_language", "extension": ".rb"}`. |
| MCP bridge       | Translates 415 to `{"degraded": true, "reason": "unsupported_language", "extension": ".rb", "regression_detected": false}`. |
| Pre-edit hook    | Exits **0** (does not block), writes `unsupported_language: .rb — passing edit unblocked` to stderr. |

The intent: never block a user on a language we cannot analyse, but make
the gap audible so it is obvious why no risk math was applied.
