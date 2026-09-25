# Deferred audit items — research charter (2026-09-19)

Three audit-verified findings from the 2026-09-19 hostile review were
deferred as research items in `docs/AUDIT_RESPONSE_2026_09_19.md`:

1. **Mamba-130M is the wrong backbone for code** (embedder swap).
2. **512-token truncation blinds scoring past line ~40** (windowed
   diff embeddings).
3. **AIS / coherence_delta / novelty are algebraically redundant**
   (scoring-v3 dedup).

Three parallel research subagents (one per item) will produce written
memos in this directory. No code changes during this phase.

## Hard constraints (all subagents must obey)

- The audit verifier already established:
    * Cosine similarity is the single underlying signal
      (`CD = sqrt(2 - 2 cos)`, `AIS = (cos + 1)/2`,
      `novelty = 1 - max(cos, 0)`).
    * Mamba-130M is a causal text LM trained on The Pile — not a
      code encoder. Code-pretrained encoders exist (unixcoder-base,
      bge-code-v1, codestral-mamba-7B).
    * `ast_to_tokens()` can produce 1k–2.5k AST tokens for a 100-line
      file; the 512-token limit silently truncates 85%+ of real
      source files. Edits past token 512 collapse to cos = 1.0.
    * `RC_EMBEDDER` is already switchable (`unixcoder-base`,
      `bge-code`, `codestral-mamba`, `random-mamba`) — see
      `src/ssm_backbone.py:1-50` for the registry.

- Per `AGENTS.md`:
    * Deterministic checks are the only hard block; neural scoring
      is advisory unless corroborated by a deterministic signal.
    * Before changing a model backend, threshold, or enforcement
      default: read `eval/baselines/README.md` and capture a new
      immutable baseline.
    * Do not represent synthetic scenarios, historical telemetry, or
      a small weekly run as broad causal evidence.

## Deliverables per subagent

Each subagent writes one memo at
`thoughts/shared/research/2026-09-19-audit-deferred-<item>.md`,
then exits. The memo MUST contain:

1. **Question** restated precisely
2. **Background** (cite the audit + cite the code paths)
3. **Literature/prior-art scan** (papers, public benchmarks, code
   encoder leaderboards, fine-tuning recipes) with concrete URLs
4. **Candidate approaches** (>=3) with pros/cons
5. **Recommendation** with the chosen approach, justified
6. **Pre-registered evaluation plan** for the chosen approach:
   dataset, metrics, MDE, decision rule, baseline-capture recipe
7. **Risks and failure modes**
8. **Open questions** for the user (if any)

Each memo must be evidence-based (URLs, paper IDs, file paths,
line numbers). No speculation presented as fact.

## Hand-off back to main

After all three memos land, the main agent synthesises a single
implementation plan at
`thoughts/shared/plans/2026-09-19-audit-deferred-rollup.md`,
captures a fresh baseline, then implements the recommended subset.
