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
2 = slow, structural, deliberate (the neural scorer + symbolic rules).

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
┌──────────────────────────────────────────────────────┐
│ Sidecar (FastAPI, 127.0.0.1:8765)                    │
│                                                      │
│   Neural gate                                        │
│   • Tree-sitter parse → AST + call graph             │
│   • Embedder forward (RC_EMBEDDER) → pooled emb      │
│   • 11-dim risk vector (delta semantics)             │
│   • Chord-distance coherence_delta in [0, 2]         │
│   • Per-kind thresholds (source/test/plan/doc/cfg)   │
│   • Cold-start aware (new files don't lie)           │
│                                                      │
│   Symbolic gate  (RC_RULE_ENGINE=1)                  │
│   • .reasoning-core/rules.yaml                       │
│   • forbid_import / forbid_pattern                   │
│   • fail-closed by default, ≤5ms/rule, ≤50 rules     │
└──────────────────────────┬───────────────────────────┘
                           │ ImpactReport JSON
                           ▼
┌─────────────────────────────────────────────────────┐
│ Hook decides:                                       │
│   regression OR rule deny? → exit 2,                │
│     stderr block w/ top-3 contributors +            │
│     unified-diff RECOVERY hints                     │
│   safe? → exit 0, edit proceeds                     │
└─────────────────────────────────────────────────────┘
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
  "coherence_delta": 0.94,
  "file_kind": "source_code",
  "risk_vector": [0.0, 0.0, 0.0, 0.0, 0.02, 0.0, 0.0, 0.94, 0.0, 0.0, 0.0],
  "risk_labels": ["cyclomatic","fan_in","fan_out","depth","churn","coupling","cohesion","novelty","session_centroid_drift","project_fan_in","project_coupling"],
  "regression_detected": true,
  "fired_conditions": ["dim_above_ceiling"],
  "fired_dims": ["novelty"]
}
```

Hook blocks with stderr listing top-3 contributors + repair hints. Claude
re-reads, revises, retries. Today: one-shot allow/block with diff-repair
affordances on retry; iterative server-side loop on the
[roadmap](ROADMAP.md).

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the deep-dive and
[`HARDENING.md`](HARDENING.md) for the threat model.

---

## Under the hood (step-by-step)

1. **Claude proposes an edit.** The PreToolUse hook fires before the file
   is modified.
2. **`pre_edit_guard.py`** reads the hook payload from stdin,
   **reconstructs** the post-edit file (`before` from disk + apply
   `old_string→new_string`), POSTs `{path, before_src, after_src, session_id?}`
   to the sidecar.
3. **Tree-sitter** parses both sides into ASTs. Code languages get a
   per-module call graph; data languages skip the graph but still get
   embedded.
4. **Embedder** produces a pooled embedding for each side via the backend
   selected by `RC_EMBEDDER` (default `mamba-130m`, hidden=768, mean-pool;
   `codestral-mamba` is 7B/hidden=4096; `bge-code`/`unixcoder-base` use
   CLS-pooled transformer outputs).
5. **Risk vector** (11 dims, all delta-semantics):
   - 8 file-local dims: `cyclomatic`, `fan_in`, `fan_out`, `depth`,
     `churn`, `coupling`, `cohesion`, `novelty`.
   - 1 session-aware dim: `session_centroid_drift` — distance from the
     per-file baseline registered via `POST /baseline`, normalised against
     the empirical 95th percentile.
   - 2 project-wide dims (opt-in via `RC_PROJECT_INDEX=1`):
     `project_fan_in` (count of files importing the edited module) and
     `project_coupling` (cross-file edge delta).
6. **Coherence delta** is the chord distance on the L2-normalised
   embeddings (`||a/||a|| − b/||b||||`, in `[0, 2]`) — backbone-invariant,
   no `sqrt(hidden_size)` divisor. Cold-start (empty/<32-char `before_src`)
   forces `coherence_delta = 0.0` and zeros the seven file-local
   delta-shaped dims.
7. **File-kind dispatch** (`source_code` / `test_code` / `plan_md` /
   `doc_md` / `config`) picks per-kind `cd` / `ais` / `dim` thresholds.
8. The hook **blocks** (exit 2) iff:
   - `architectural_impact_score < ais_threshold[kind]`, OR
   - `coherence_delta > cd_threshold[kind]`, OR
   - any risk dim `> dim_ceiling[kind]`, OR
   - the rule engine emits a `deny` hit (when `RC_RULE_ENGINE=1`).
9. Block stderr surfaces top-3 risk contributors, their margins, and (on
   retry) unified-diff `RECOVERY` guidance pointing at the
   `validate_unified_diff` MCP tool. Retries within 120s trigger a "RETRY
   DETECTED" banner.
10. In **shadow mode** (`RC_SHADOW_MODE=1`), steps 8–9 still execute but
    the hook always returns exit 0; the would-be decision is logged for
    offline review.

---

## Scoring math

### 11-dim risk vector (`risk_labels_version=2`)

All dims normalised to `[0, 1]`. The 8 file-local dims have delta semantics.

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
| `session_centroid_drift` | `clamp(_l2_distance(emb_after, baseline) / drift_p95, 0, 1)` | _empirical p95_ |
| `project_fan_in` | `clamp(len(pidx.files_importing(module(path))) / 8, 0, 1)` | 8 |
| `project_coupling` | `clamp(Δ cross-file edges / 40, 0, 1)` | 40 |

`project_fan_in` and `project_coupling` are zero unless
`RC_PROJECT_INDEX=1` and a session id is present.

### Architectural impact score (AIS)

```
ais = max(0, min(1, (cos(emb_before, emb_after) + 1) / 2))
```

AIS = `1.0` means identical embeddings; lower = more change. Blocks below
`ais_threshold[kind]`.

### Coherence delta (chord distance)

```python
coherence_delta = _l2_distance(emb_before, emb_after)
                = ||emb_before/||emb_before||  −  emb_after/||emb_after||||
                ∈ [0, 2]
```

- Symmetric zero (both embeddings degenerate) → `0.0`.
- Asymmetric zero (one side zero, the other non-zero) → `2.0` — a
  "delete all code" edit no longer passes the coherence gate by
  collapsing one embedding to zero.
- Cold-start (`before_src.strip() == ""` or `len < 32`) → `0.0`.

This metric is dimension-invariant and pool-invariant: a `mean`-pooled
Mamba embedding and a `cls`-pooled transformer embedding produce
comparable chord distances for the same semantic content.

The earlier `L2 / sqrt(hidden_size)` form (`risk_labels_version=1`) is gone
— calibrated `cd` thresholds tuned on the old scale (e.g. the old default
`1.5`) are above the chord-distance bound `2.0` and would silently disable
the gate. The sidecar logs a warning at load time if
`S2_COHERENCE_THRESHOLD > 2.0`.

### Per-kind thresholds

| kind | `cd` | `ais` | dim ceiling |
|---|---:|---:|---:|
| `source_code` | `0.5` (env) | `0.4` (env) | `0.9` (env) |
| `test_code`   | `0.7` | `0.3` | `0.95` |
| `plan_md`     | `1.0` | `0.3` | `1.0` |
| `doc_md`      | `1.0` | `0.3` | `1.0` |
| `config`      | `0.4` | `0.5` | `0.9` |

Block fires iff `ais < threshold[kind]` OR `cd > threshold[kind]` OR any
`risk_dim > dim_ceiling[kind]`.

---

## Architectural rule engine

Opt-in via `RC_RULE_ENGINE=1`. Loads `.reasoning-core/rules.yaml` from the
project root. Each rule is one of two types:

- `forbid_import` — forbid a Python module path or JS/TS import target
  (relative or absolute) in files matching a glob `scope`.
- `forbid_pattern` — forbid a regex match in files matching `scope` and
  `language`.

Hard limits enforced at load:

- ≤50 rules total.
- ≤5 ms per-rule wall time (warned and skipped per call).
- `corpus_version` must match `_REQUIRED_CORPUS_VERSION` ("v1").
- Schema error → `exit(2)` unless `RC_RULE_ENGINE_LENIENT=1`.

Fail-closed by default: evaluator exceptions synthesize a `deny` `RuleHit`
rather than letting the edit through. JS/TS edits can carry bypass comments
in their native syntax: `// rc:skip-rule:<id>`.

A starter `rules.yaml` ships at `.reasoning-core/rules.yaml` in the
reasoning-core repo itself; copy and adapt to the target repo.

---

## MCP tools

The `hybrid-reasoner` MCP server exposes two tools across every supported
host:

- **`gate_edit(path, before_src, after_src)`** — the synthetic PreToolUse
  gate for Tier-2 hosts (Copilot, Vibe) that lack a runtime PreToolUse hook.
  Returns `{decision: "allow"|"block", message, impact_report}`. Blocks under
  the same regression / rule-engine criteria as the runtime hook.
- **`validate_unified_diff(patch)`** — structural unified-diff validator
  with best-effort repair. Detects `missing_prefix`, `empty_context`,
  `count_mismatch`, `bad_hunk_header`, `missing_hunk`; returns a repaired
  patch when possible. Never raises. Invoked by `RC_DIFF_AUDIT=1` Stop hook
  for post-turn audit, and surfaced as a `RECOVERY` hint in retry-block
  stderr.
