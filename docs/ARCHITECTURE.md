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
            +-------------+-------------+
            | Tree-sitter (13 grammars) |
            | AST + call graph (code)   |
            | AST only        (data)    |
            +-------------+-------------+
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
|-- .github/
|   `-- workflows/eval.yml         lint-and-test (push) + eval-smoke (push)
|                                  + eval-full (workflow_dispatch)
|-- board/board.json               kanban
|-- src/
|   |-- s2_core.py                 sidecar entrypoint + scoring engine
|   |-- grammars.py                Tree-sitter loader + select_grammar
|   |-- ssm_backbone.py            Mamba weights + embed() helper
|   |-- mcp_reasoner.py            FastMCP bridge
|   |-- audit_log.py               JSONL audit emitter for /tmp/rc-events
|   `-- hooks/
|       |-- pre_edit_guard.py      PreToolUse hook (Edit|Write|MultiEdit)
|       |-- pre_bash_guard.py      PreToolUse hook (Bash) — L1 hardening
|       |-- pre_plan_guard.py      PreToolUse hook (plan-doc Write)
|       |-- pre_task_guard.py      PreToolUse hook (Task subagent spawn)
|       `-- post_bash_revive.py    PostToolUse hook (sidecar revival)
|-- scripts/
|   |-- configure-scaleway.sh      ANTHROPIC_* env + live API probes
|   |-- start-sidecar.sh           launches sidecar with model pre-warm
|   `-- test-prototype.sh          end-to-end smoke
|-- eval/
|   |-- Dockerfile                 multi-stage image (builder+runtime)
|   |-- .dockerignore              build-context hygiene
|   |-- requirements-eval.txt      scipy, statsmodels, datasets, radon
|   |-- run_suite.py               (Track B) per-task harness orchestrator
|   |-- run_task.sh                (Track B) clones repo, drives claude
|   |-- aggregate.py               (Track B) report.json + report.md
|   |-- scripts/
|   |   |-- prefetch_mamba.sh      bake checkpoint into image w/ sha pin
|   |   |-- ast_edit_distance.py   (Track B) AST-level diff scorer
|   |   `-- cyclomatic_delta.py    (Track B) McCabe complexity delta
|   |-- prompts/system_prompt.txt  (Track B) pinned system prompt
|   |-- datasets/                  task list + fixtures
|   |-- fixtures/                  per-task seed data
|   `-- README.md                  operator runbook
|-- tests/                          pytest suite
`-- docs/
    |-- PLAN.md
    |-- ARCHITECTURE.md            (this file)
    |-- EVAL_DESIGN.md             hypothesis, metrics, decision criteria
    |-- HARDENING.md               threat model + bypass mitigations
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

### Cohesion saturation fix

Single-function files used to false-positive on the cohesion dimension —
with only 1 node in the call graph, the cohesion ratio collapsed to a
degenerate value and saturated to 1.0, tripping the `any dim > 0.9`
predicate on benign edits like an `add` → `sum2` rename. The scoring
pipeline now treats cohesion as undefined for graphs with `< 2` nodes
(returns 0.0 instead of saturating). See commit `8a2c352`.

### Coherence-delta normalization

`coherence_delta` is the L2 distance between the mean-pooled Mamba embeddings
of the pre- and post-edit AST-token streams, **normalized by
`sqrt(hidden_size)`**. The raw L2 of two `hidden_size`-D vectors scales with
the embedding dimension (a 768-D `mamba-130m` checkpoint produces drift values
~30–40 on benign edits, while a hypothetical 256-D checkpoint would produce
~17–23 for the same semantic delta). Dividing by `sqrt(hidden_size)` recasts
the metric as average per-dimension drift in standard units, making the
`COHERENCE_DELTA_THRESHOLD = 1.5` constant in `src/s2_core.py` portable across
checkpoints — swapping `S2_SSM_CHECKPOINT` no longer requires re-tuning the
regression threshold. The same normalization is applied to `cumulative_drift`
when a session baseline is registered, so both metrics share one scale.

## Tree-sitter language support

The sidecar supports thirteen languages, split into two tiers. Selection
is by file extension, done in `src/grammars.py::select_grammar`.

### Code languages (full call-graph + embedding)

| Language    | Extensions                  | Tree-sitter grammar          |
| ----------- | --------------------------- | ---------------------------- |
| Python      | `.py`                       | tree-sitter-python           |
| JavaScript  | `.js`, `.mjs`, `.cjs`       | tree-sitter-javascript       |
| TypeScript  | `.ts`, `.tsx`               | tree-sitter-typescript / tsx |
| C#          | `.cs`                       | tree-sitter-c-sharp          |
| SQL         | `.sql`                      | tree-sitter-sql              |

`build_call_graph` walks the AST for these extensions and contributes
`fan_in` / `fan_out` signal to the risk vector.

### Data languages (embedding only — call graph skipped)

| Language    | Extensions                       | Tree-sitter grammar    |
| ----------- | -------------------------------- | ---------------------- |
| Markdown    | `.md`, `.markdown`, `.mdx`       | tree-sitter-markdown   |
| JSON        | `.json`                          | tree-sitter-json       |
| YAML        | `.yaml`, `.yml`                  | tree-sitter-yaml       |
| CSS         | `.css`                           | tree-sitter-css        |
| SCSS        | `.scss`                          | tree-sitter-scss       |
| HTML        | `.html`, `.htm`                  | tree-sitter-html       |
| Dockerfile  | `Dockerfile`, `.dockerfile`      | tree-sitter-dockerfile |
| Vue         | `.vue`                           | HTML grammar (fallback)*|

*No `tree-sitter-vue` wheel exists on PyPI for Python 3.13; `.vue` files
are routed through the HTML grammar so `/score` returns 200 instead of a
runtime error. Will swap to a native Vue grammar when an upstream wheel
ships.

For data languages the AST is linearised into the SSM token stream and
contributes to `coherence_delta` and `novelty`, but `fan_in` / `fan_out`
remain 0. This is intentional: a JSON or Markdown file has no callable
graph, so faking one would inject noise.

The grammars ship via per-language wheels. No network is required at
runtime.

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

## Hardening layers

The PreToolUse contract is implemented as five layered hooks rather than a
single `Edit|Write|MultiEdit` matcher, so that bypass routes (Bash escape,
plan-doc smuggling, subagent spawn, sidecar kill) are also screened. Full
threat-model analysis lives in [`HARDENING.md`](./HARDENING.md); this is
the architectural map.

| Layer | Hook                        | Matcher          | Role                                                      | Override env                       |
| ----- | --------------------------- | ---------------- | --------------------------------------------------------- | ---------------------------------- |
| L1    | `pre_edit_guard.py`         | Edit/Write/MultiEdit | SSM regression score + guard-file lock                | `RC_ALLOW_GUARD_EDIT=1`            |
| L2    | `pre_bash_guard.py`         | Bash             | Stage-A hard-deny / stage-B guarded-write / stage-C kill / stage-D source-write | `RC_ALLOW_GUARD_EDIT=1` |
| L3    | `pre_plan_guard.py`         | Write (plan paths) | Plan-doc heuristics + SSM novelty over markdown content | `RC_PLAN_BLOCK=1` (escalate warn → block) |
| L4    | `pre_task_guard.py`         | Task             | Subagent prompt screened for mutation verbs against guarded paths | `RC_ALLOW_SUBAGENT_GUARD_EDIT=1` |
| L5    | `post_bash_revive.py`       | Bash (PostToolUse) | Detect kill-token + dead `/health`; respawn sidecar    | n/a (informational, never blocks)  |

All five hooks are stdlib-only (no `httpx`, no FastAPI, no transformers
imports) so a broken venv does not disable policy. They share an
audit-log helper at `src/audit_log.py` which appends a JSONL event to
`/tmp/rc-events/<date>/<session>.jsonl` per fire.

By default L3 ships in **warn-only** mode — operators see plan-quality
stderr noise but the write proceeds. Setting `RC_PLAN_BLOCK=1` flips the
hook to exit 2 on any flagged warning. The recommended production
profile exports `RC_PLAN_BLOCK=1` together with `S2_FAIL_CLOSED=1`.

## Evaluation Subsystem

The evaluation subsystem measures whether the PreToolUse hook + S2
sidecar (treatment) actually reduces real-world regression rate vs.
vanilla Claude Code (control). The full methodology lives in
[`EVAL_DESIGN.md`](./EVAL_DESIGN.md); this section maps it onto the code.

### Hypothesis & methodology

The benchmark is **SWE-bench Verified**, stratified to 100 Python-only
tasks with a paired design (each task runs once per arm). The primary
metric is **Regression Rate** — fraction of submitted patches that break
≥ 1 previously-passing test in the target repo. H1 is that treatment
reduces RR by ≥ 15 pp at α = 0.05 (Wilcoxon signed-rank, Holm-corrected
across 10 metrics). Power at n=100 ≈ 0.87 for δ = 0.15. Secondary
metrics cover resolved rate, AST edit distance, cyclomatic delta,
fan-in/out delta, hook FPR/TPR/recovery, latency, tokens, and novelty
drift. See `EVAL_DESIGN.md` §1–§5.

### Data flow

```
                +--------------------+
                | eval/run_suite.py  |  (orchestrator: stratified
                +----------+---------+   sample, seed=42, --parallel N)
                           |
                           v
                +--------------------+
                | eval/run_task.sh   |  one (task_id, arm) at a time
                +----------+---------+
                           |
            clones repo @ base_commit, sets env per arm
                           |
                           v
                +--------------------+        treatment-only:
                |    claude CLI      |--->  PreToolUse hooks fire
                | --model opus-4-7   |      pre_edit_guard / pre_bash_guard
                | T=0.0  max_turns=40|      pre_plan_guard / pre_task_guard
                +----------+---------+              |
                           |                        v
                           |               +-----------------+
                           |               |   S2 sidecar    |
                           |               | 127.0.0.1:8765  |
                           |               +-----------------+
                           v                        |
            +------------------------------+        | append
            | claude_transcript.jsonl      |        v
            | hook_events.jsonl (treatment)|  /tmp/rc-events/<date>/
            | sidecar.log     (treatment)  |   <session>.jsonl
            +--------------+---------------+        |
                           |                        |
                           v                        |
            +------------------------------+        |
            | repo pytest --tb=no -q       |        |
            | -> test_results.json         |        |
            +--------------+---------------+        |
                           |                        |
                           v                        |
            +------------------------------+        |
            | post-hoc scorers:            |        |
            |   ast_edit_distance.py       |        |
            |   cyclomatic_delta.py        |        |
            |   s2_core.score_change()     |        |
            | -> per_task_metrics.json     |        |
            +--------------+---------------+        |
                           |                        |
                           +-----+------------------+
                                 |
                                 v
                +----------------------------+
                |     eval/aggregate.py      |  joins arms,
                +-------------+--------------+  paired Wilcoxon,
                              |                 BCa bootstrap,
                              v                 Holm correction
                +----------------------------+
                | report.json + report.md    |
                | (10 metrics, decision tbl) |
                +----------------------------+
```

### Scoring pipeline

Each per-task metric is computed from one of three sources, kept
separate so the eval harness never has to hot-load the sidecar weights
when scoring offline.

1. **AST + complexity (offline).** `eval/scripts/ast_edit_distance.py`
   parses the gold and Claude patches, applies them to a synthesized
   base, and runs `ast.dump` diff via `difflib.SequenceMatcher` to
   produce an integer node-edit count. `eval/scripts/cyclomatic_delta.py`
   walks the touched `.py` files with `radon` to produce a McCabe sum
   delta (patched − base). Both are stdlib-light and deterministic;
   neither needs the sidecar.

2. **Mamba-derived (online, sidecar).** Fan-in/out delta and novelty
   drift come from `s2_core.score_change(file_path, before_src,
   after_src)` — the same code path the runtime hook uses. The eval
   harness reuses the warm sidecar (one process per worker) so the
   ~3 s Mamba forward pass is amortized across all calls in a run.
   `coherence_delta` and the 8-dimensional risk vector come straight
   from the returned `ImpactReport`.

3. **Hook telemetry (treatment only).** `hook_events.jsonl` (one line
   per PreToolUse fire) is consumed alongside the structured audit
   stream at `/tmp/rc-events/<date>/<session>.jsonl` to compute FPBR,
   TPBR, BRR, and hook overhead p50/p95. Replay verification for TPBR
   re-applies blocked content to a scratch clone and re-runs pytest;
   that step is the only post-hoc pass that needs network and a clean
   filesystem, so it runs after the main eval, not interleaved with it.

The aggregator (`eval/aggregate.py`) joins per-task JSON across arms on
`task_id`, computes paired differences `d_i = metric_i(treatment) −
metric_i(control)`, runs `scipy.stats.wilcoxon` and a BCa bootstrap
(10 000 resamples), and applies Holm-Bonferroni across the full 10-metric
panel before printing the decision block defined in `EVAL_DESIGN.md` §7.

### CI integration

`/.github/workflows/eval.yml` (Track D) wires three jobs to the surface
this subsystem exposes:

- **`lint-and-test`** runs on every push (and on PRs targeting `main`).
  Python 3.11 host, no Docker. Steps: `pip install`, `py_compile`,
  `json.load` on `.claude/settings.json`, `pytest -q -m 'not live'`,
  `shellcheck` on `scripts/*.sh` and `eval/run_task.sh`. 5-minute
  timeout. This is the gate every change must clear before any eval
  job runs.
- **`eval-smoke`** runs on every push to `main` against the image
  produced by `eval/Dockerfile`. Defaults to **n=2 paired tasks** on the
  free `ubuntu-latest` runner (the SSM weights + tree-sitter wheels +
  Mamba forward pass exhaust the 7 GB RAM budget at higher n). Operators
  can request `n=5+` via `workflow_dispatch` against `ubuntu-latest-large`,
  which has 14 GB. `--parallel 1`, 30-minute budget. Posts a sticky
  comment on the commit SHA with the headline metrics and uploads
  `eval/results/` as an artifact (30-day retention). Both n=2 and n=5
  are descriptive only — power calc forbids ship/kill verdicts at these
  sizes.
- **`eval-full`** is `workflow_dispatch` only, default `n_tasks=100`,
  6-hour budget, gated by the `eval-full-approved` GitHub environment.
  Required for any production ship/kill decision.

### Repro

To re-run any single eval result locally (assuming the venv is
activated and `RC_LIVE=1`, `ANTHROPIC_API_KEY`, and `HF_HOME` are set):

```bash
# one (task, arm) at a time — control first per the protocol (§4.2)
bash eval/run_task.sh django__django-11099 control
bash eval/run_task.sh django__django-11099 treatment

# inspect raw artifacts
ls eval/results/django__django-11099/{control,treatment}/

# regenerate the cross-arm report
python3 eval/aggregate.py \
    --results-dir eval/results \
    --out eval/results/report --format both
```

To repro a full smoke from scratch:

```bash
pip install -r requirements.txt -r eval/requirements-eval.txt
RC_LIVE=1 S2_FAIL_CLOSED=0 S2_TIMEOUT=30 \
    python3 eval/run_suite.py --n 5 --arms vanilla,treatment --parallel 1
python3 eval/aggregate.py --results-dir eval/results \
    --out eval/results/report --format both
```

The Docker path (`docker build -f eval/Dockerfile -t reasoning-core-eval
.`) produces the identical environment the CI workflow uses; see
[`eval/README.md`](../eval/README.md) for the full operator runbook.
