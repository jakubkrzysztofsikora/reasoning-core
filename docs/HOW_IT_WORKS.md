# How it works

The motivation, the architecture, and the scoring math.

---

## Why this exists

### Where LLMs shine

LLMs are extraordinary at **token-level pattern completion**. Function
signature + docstring → plausible body. Stack trace → suggested fix. Dataset
+ goal → boilerplate. Frontier models match or exceed mid-level engineers
on bounded coding tasks where the answer is a local rewrite.

### Where LLMs fail

Drop the same model into a 50k-LOC codebase and quality collapses. LLMs are
bad at:

- **Architectural invariants** — does this preserve the layer boundary? The
  model has no concept of "layer", just tokens.
- **Cross-file consequences** — a 1-line edit fan-outs to 30 callers; the
  model only saw 1 file.
- **Cyclomatic envelopes** — adding a 4th conditional to an already-8-branch
  function makes it untestable; the model sees "another `if`".
- **Coupling drift** — each new import creeps the module further from its
  single responsibility, with no penalty in the loss.
- **Coherence vs the rest of the project** — a new helper introducing a
  different idiom from the codebase is "novel", but novelty is sometimes a
  bug not a feature.
- **Plan→implementation alignment** — plan said 4 phases, only 2 landed;
  plan said touch `auth/`, diff also rewrote `payments/`. The model doesn't
  audit itself.

### The structural blind spot

LLMs reason over **token streams**. They don't have a graph of who calls
whom, a tree of which scope nests where, or a metric for how "different"
your file is from the project's attractor. Those properties live in the
**structure** — the AST, the call graph, the embedding manifold of the repo
as a whole.

Long-context windows (1M tokens) help but don't solve. The model still has
to *infer* structure from tokens at every inference, with no persistence.
Reasoning over structure is a different kind of computation: graph diffs,
embedding distances, dimensionality. LLMs aren't optimized for it; they do
it badly compared to a small specialist.

### Will scaling fix it?

Probably not, at least not soon. 2024-25 long-context literature shows
degraded *consistent* cross-document reasoning even at 1M+ tokens —
bottleneck is architectural, not budget. Tool-calling agents (linters, type
checkers, AST search) outperform agents that don't, but tool calls are
stateless per question; they don't accumulate repo-level reasoning. Small
models trained for code structure keep outperforming much larger general
models on code-similarity tasks.

Forecast: LLMs keep getting better at the *linguistic* surface — intent
decoding, emission, prose. They will *not* spontaneously develop reliable
structural reasoning. Hybrid systems where the LLM defers structural
decisions to a specialist will be the durable architecture.

That's what this is.

---

## System 1 + System 2

Loose nod to Kahneman: System 1 = fast, linguistic, intuitive (LLM); System
2 = slow, structural, deliberate (the SSM scorer).

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

Hook blocks with stderr listing top-3 contributors + repair hints. Claude
re-reads, revises, retries. (Today: one-shot allow/block; iterative-loop on
the [roadmap](ROADMAP.md).)

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the deep-dive and
[`HARDENING.md`](HARDENING.md) for the threat model.

---

## Under the hood (step-by-step)

1. **Claude proposes an edit.** The PreToolUse hook fires before the file
   is modified.
2. **`pre_edit_guard.py`** reads the hook payload from stdin,
   **reconstructs** the post-edit file (`before` from disk + apply
   `old_string→new_string`), POSTs `{path, before_src, after_src}` to the
   sidecar.
3. **Tree-sitter** parses both sides into ASTs. Code languages get a
   per-module call graph; data languages skip the graph but still get
   embedded.
4. **Mamba SSM** produces a pooled embedding for each side (130M params,
   hidden=768, CPU).
5. **Risk vector** (8 dims, all delta-semantics): cyclomatic, fan_in,
   fan_out, depth, churn, coupling, cohesion, novelty.
6. **File-kind dispatch** picks per-kind `cd`/`ais`/`dim` thresholds.
   Cold-start (empty before) zeros structural dims so new files only get
   gated on content novelty.
7. The hook **blocks** (exit 2) iff:
   - `architectural_impact_score < ais_threshold[kind]`
   - `coherence_delta > cd_threshold[kind]`
   - any risk dim `> dim_ceiling[kind]`
8. Block stderr surfaces top-3 risk contributors with repair hints. Retries
   within 120s trigger a "RETRY DETECTED" banner.
9. In **shadow mode** (default), steps 7–8 still execute but the hook
   always returns exit 0; the would-be decision is logged for offline review.

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

`coherence_delta = ||emb_after − emb_before||₂ / sqrt(hidden_size)`.

Cold-start (empty `before_src` or <32 chars) sets it to `0.0`.

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
