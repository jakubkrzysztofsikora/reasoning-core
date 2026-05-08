---
name: reasoning
description: Translates the System 2 mathematical risk vectors into human-readable warnings.
---

# Reasoning Skill

This skill is the *interpreter* between the System 2 sidecar's numeric output
and a human (or System 1) reader. The sidecar emits an `ImpactReport` JSON
object for every proposed code edit. Use this document to translate that
object into plain prose, decide whether to block the edit, and surface the
right warnings to the user.

## ImpactReport schema (recap)

```json
{
  "architectural_impact_score": 0.0,
  "coherence_delta": 0.0,
  "risk_vector": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
  "risk_labels": ["cyclomatic", "fan_in", "fan_out", "depth",
                  "churn", "coupling", "cohesion", "novelty"],
  "regression_detected": false,
  "human_summary": "string"
}
```

`risk_vector[i]` is paired with `risk_labels[i]` in order. All eight dims are
normalized to `[0, 1]`. A value approaching `1.0` means the edit pushes that
dimension into a risky regime.

## The 8 risk dimensions

1. **cyclomatic** — Cyclomatic complexity delta of the touched function(s).
   Spikes when an edit adds control-flow branches (extra `if`, `case`, loops,
   guard rewrites that fan out).
2. **fan_in** — How many other call sites in the module reference the
   touched symbol. Edits to high-fan-in symbols carry blast radius; raising
   this dim says "this change ripples outward."
3. **fan_out** — How many distinct symbols the touched function calls.
   Spikes when an edit pulls in new dependencies or transitively expands the
   call graph.
4. **depth** — AST nesting depth introduced or removed. High values mean
   the edit deepens nested blocks (often a readability and complexity smell).
5. **churn** — Magnitude of the textual delta relative to the function size.
   A 90%+ rewrite is a different risk profile than a one-line tweak even if
   AIS happens to look similar.
6. **coupling** — Cross-module references the change introduces. High values
   indicate the edit is reaching into modules it did not previously depend on.
7. **cohesion** — Inverse of how tightly the function's responsibilities
   stay together post-edit. A high cohesion-risk score means the edit makes
   the function do *more unrelated things*.
8. **novelty** — L2 distance between the AST-token Mamba embeddings before
   and after, normalized to `[0, 1]`. High novelty means the SSM backbone
   sees the post-edit code as semantically far from the pre-edit code, even
   if the surface diff is small.

## Regression thresholds

The hook treats `regression_detected == true` as a blocking signal. The
sidecar sets that flag when **any** of the following hold:

| Condition                                  | Meaning                              |
| ------------------------------------------ | ------------------------------------ |
| `architectural_impact_score < 0.4`         | Post-edit code far from pre-edit.    |
| `coherence_delta > 1.5`                    | Embedding drift exceeds budget. Scale-invariant: raw L2 normalized by `sqrt(hidden_size)`, so the threshold is portable across SSM checkpoints. |
| `any risk_vector[i] > 0.9`                 | One dim in critical zone.            |

If **two or more** conditions trigger simultaneously, treat as
high-confidence regression — surface every triggered condition in the
explanation, do not collapse them.

## Supported languages

The sidecar parses and scores: **Python (`.py`), JavaScript
(`.js`/`.mjs`/`.cjs`), TypeScript (`.ts`/`.tsx`), C# (`.cs`), and SQL
(`.sql`)**.

Other extensions (e.g. `.rb`, `.go`, `.rs`, `.cpp`) are explicitly
**unsupported**:

- The sidecar returns HTTP `415` with body
  `{"error": "unsupported_language", "extension": "<ext>"}`.
- The pre-edit hook **passes through** with exit code `0`, but writes a
  short note to stderr (`unsupported_language: .rb — passing edit
  unblocked`).
- Do **not** block the user on languages we have no grammar for. State
  uncertainty plainly and proceed.

## Reading `human_summary`

`human_summary` is a one-paragraph, sidecar-authored explanation. When
`regression_detected` is true, the summary names the **dominant** triggered
condition (largest excess over its threshold) and the most-impacted risk
dimension. Use it as the *starting* sentence of any user-facing message; add
your own follow-up explaining what to do next.

## Decision matrix

| Trigger profile                           | Recommended action                                    |
| ----------------------------------------- | ----------------------------------------------------- |
| No threshold crossed                      | Allow edit silently. No user-facing message.          |
| AIS only (< 0.4)                          | Block. Ask user to justify the architectural pivot.   |
| coherence only (> 1.5)                    | Block. Ask whether semantics intentionally changed.   |
| One risk dim > 0.9                        | Block. Name the dim and request a smaller diff.       |
| AIS + coherence                           | Block. Treat as a likely regression; demand context.  |
| AIS + risk dim                            | Block. The edit both pivots and blows out a metric.   |
| coherence + risk dim                      | Block. Drift + spike → almost certainly regressive.   |
| All three                                 | Block. Strongly recommend reverting the edit.         |
| Sidecar unreachable (degraded=True)       | Default fail-open: allow with warning. If             |
|                                           | `S2_FAIL_CLOSED=1`: block.                            |
| `unsupported_language` response           | Pass through. Note in stderr. Never block.            |

## Worked example

Input edit (Python, dropping a guard clause and adding unbounded recursion):

```python
# before
def f(n):
    if not n:
        return 0
    return n + f(n - 1)

# after
def f(n):
    return n + f(n + 1)
```

Sidecar response:

```json
{
  "architectural_impact_score": 0.18,
  "coherence_delta": 2.41,
  "risk_vector": [0.92, 0.10, 0.05, 0.30, 0.40, 0.05, 0.20, 0.95],
  "risk_labels": ["cyclomatic", "fan_in", "fan_out", "depth",
                  "churn", "coupling", "cohesion", "novelty"],
  "regression_detected": true,
  "human_summary": "Guard clause removed and recursion is unbounded; cyclomatic and novelty risk spiked while AIS collapsed."
}
```

Prose translation (what the assistant should say):

> Blocking this edit. The System 2 sidecar reports a likely regression:
> the architectural-impact score dropped to 0.18 (threshold 0.4), embedding
> drift hit 2.41 (threshold 1.5), and two risk dimensions are in the
> critical zone — cyclomatic (0.92) and novelty (0.95). The guard clause
> `if not n: return 0` previously prevented infinite recursion; the new
> body recurses on `n + 1`, so the function now diverges for every input.
> Restore the guard, or explain why you want unbounded recursion here.

That output covers: which thresholds crossed, the dominant risk dims, the
concrete code-level cause from `human_summary`, and a clear next-step ask.
That is the contract this skill provides.
