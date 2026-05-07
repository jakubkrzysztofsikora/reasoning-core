# Iter-3 atomic-claim decomposer prompt — version `iter3.v1`

This prompt is referenced from `iter3-prereg.json:atomic_claim_decomposer_prompt` and is frozen pre-sweep. Any change after sweep starts is a methodology amendment per the prereg's `amendment_protocol`.

The decomposer is invoked twice per (setup, task, run) cell: once on `PLAN.md` to extract `plan_claims`, once on `DIVERGENCES.md` to extract `declared_divergent`. The two outputs feed `compute_coverage_at_accuracy()` (eval/aggregate.py).

The decomposer is NOT a judge — it does not score correctness or honesty. It only enumerates the atomic propositions a downstream verifier will check.

## Extraction protocol (programmatic)

Runtime extraction matches the **markdown heading `## Prompt text (verbatim, frozen)` anchored as a paragraph-level heading** (preceded by blank line, followed by blank line + fenced code block). This avoids collision with in-code mentions of the heading string elsewhere in this file. Anchor regex: `\n\n## Prompt text \(verbatim, frozen\)\n\n` followed by triple backtick fence.

Implementation (`eval/aggregate.py:load_decomposer_prompt`):

```python
import re

def load_decomposer_prompt(md_path: Path) -> str:
    # Normalize line endings (CRLF -> LF) defensively against editor reformat
    text = md_path.read_text().replace("\r\n", "\n").replace("\r", "\n")
    # Anchor on actual markdown heading; tolerate trailing whitespace
    m = re.search(
        r"\n[ \t]*\n## Prompt text \(verbatim, frozen\)[ \t]*\n[ \t]*\n```\n",
        text,
    )
    if not m:
        raise ValueError("decomposer prompt heading not found")
    fence_open = m.end()
    fence_close = text.index("\n```", fence_open)
    body = text[fence_open:fence_close]
    if body.count("<<<DOCUMENT_TEXT>>>") != 1:
        raise ValueError(
            f"decomposer prompt must contain exactly one substitution sentinel; got {body.count('<<<DOCUMENT_TEXT>>>')}"
        )
    return body
```

The extracted prompt body contains the sentinel `<<<DOCUMENT_TEXT>>>` exactly once. Substitution is via `str.replace`, NOT `str.format` or f-string interpolation:

```python
def render_decomposer_prompt(prompt: str, document_text: str) -> str:
    if prompt.count("<<<DOCUMENT_TEXT>>>") != 1:
        raise ValueError("decomposer prompt must contain exactly one substitution sentinel")
    if "<<<DOCUMENT_TEXT>>>" in document_text:
        # Reviewer-flagged collision: a document literally containing the sentinel
        # would shadow the substitution boundary (str.replace is not greedy here,
        # but downstream audit / re-extraction would mis-identify the body/doc split).
        raise ValueError(
            "document body contains the substitution sentinel literal; "
            "either escape it in the document or change the sentinel for this run"
        )
    return prompt.replace("<<<DOCUMENT_TEXT>>>", document_text)
```

Sentinel chosen to avoid collision with JSON literals `{` / `}` that appear in the prompt's schema example.

## Invocation parameters (frozen)

All three judges (Gemini, Vibe, Qwen-Coder) invoke the decomposer with identical sampling parameters:

| param | value |
|---|---|
| `temperature` | `0.0` |
| `top_p` | `1.0` |
| `max_tokens` | `4096` |
| `system_prompt` | `null` (decomposer is the entire user prompt) |
| `stop_sequences` | `[]` |
| `response_format` | `json_object` (where supported); JSON-extraction wrapper otherwise |

Document input pre-processing:
- The PLAN.md or DIVERGENCES.md file is read raw (UTF-8, no markdown stripping, no frontmatter removal).
- No truncation; documents larger than the model context window cause the decomposer to fail with `error: document_too_large` and the cell is flagged in coverage gate.
- No additional system message or context is prepended.

## Prompt text (verbatim, frozen)

```
You are an atomic-claim decomposer for software engineering specs. Your job
is to extract every independently-verifiable claim from the input document
into a flat JSON list. Each claim must be:

- Atomic: one proposition per claim. "Tests pass and PR review is complete"
  is two claims, not one.
- Verifiable: someone with access to the codebase + sandbox can determine
  truth-or-falsity in finite time. "Code is elegant" is NOT verifiable;
  "Cypress test cypress/e2e/login.cy.ts:42 passes against backend at
  localhost:5010" IS verifiable.
- Self-contained: each claim must be readable without reference to other
  claims. Resolve pronouns; expand "this" / "that" / "the test" into
  concrete identifiers.

Schema:
[
  {
    "id": "claim-001",
    "text": "<verbatim claim text, self-contained>",
    "type": "test_passes | test_exists | file_modified | endpoint_reachable | fixture_present | tool_invoked | configuration_set | infra_state | external_dependency_available | other",
    "evidence_locator": "<file:line, endpoint URL, container name, or 'TBD' if the claim does not yet have grounded evidence>"
  }
]

Rules:
- Do NOT score, judge, or evaluate any claim. Only enumerate.
- Do NOT add claims that are not in the input — no inference, no extrapolation.
- Do NOT split a single proposition into multiple atomic claims for inflation.
  "The test passes" is one claim, not three (test exists + test runs + test
  returns pass).
- Use evidence_locator='TBD' sparingly. If more than 30% of claims in your
  output have evidence_locator='TBD', the document will be flagged as
  decomposition-unstable and excluded from C@Acc computation.
- Output JSON only. No prose preamble, no trailing markdown.

Input document follows on the next line.
---
<<<DOCUMENT_TEXT>>>
```

## Reliability test protocol (Phase 0.5)

Run this prompt against 10 representative iter-2 PLAN.md files using each of the three judges (Gemini, Vibe, Qwen-Coder) as decomposer at the frozen invocation parameters.

For each pair of decomposers (G-V, G-Q, V-Q), compute:

1. **Jaccard overlap on (type, evidence_locator) tuples** — for each document, compute the set of `(type, evidence_locator)` pairs from each decomposer; report Jaccard between the two sets. Robust to claim-text rewording. **PRIMARY GATE.**
2. **Pearson correlation on claim-count per document** — measures whether decomposers agree on the *granularity* of the decomposition. **SECONDARY (smoke test only).**

**Pre-registered gating thresholds** (from `iter3-prereg.json:thresholds.fixed_structural_constants`):

- **Jaccard floor (primary)**: `decomposer_jaccard_floor = 0.6`. Any pair below this on the 10-doc fixture aborts iter-3 plan and triggers fallback to coarser binary "spec-claim attempted vs declared divergent" rubric.
- **Pearson floor (smoke)**: `decomposer_pearson_count_smoke_test_floor = 0.7`. Below this is a warning; doesn't gate alone but combined with Jaccard fail forces fallback.
- **TBD cap (per-document)**: `decomposer_tbd_evidence_locator_max_pct = 30%`. Documents exceeding this in any decomposer's output are excluded from C@Acc computation for that document.

## Why this prompt is frozen

The C@Acc denominator (`coverage = attempted / total_plan_claims`) is downstream of `total_plan_claims = len(parse_plan_atomic_claims(plan_path, decomposer_prompt))`. If the decomposer prompt is mutable mid-sweep or post-sweep, the verdict is a function of the prompt, not the data. FActScore (Min et al., EMNLP 2023) explicitly demonstrates this prompt-sensitivity for atomic-claim decomposition.

The frozen-prompt commitment (extraction rule + sentinel + invocation params + reliability gate) is what makes iter-3's C@Acc reproducible.
