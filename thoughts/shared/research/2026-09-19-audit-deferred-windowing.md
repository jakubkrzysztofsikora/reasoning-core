---
date: 2026-09-19
branch: audit-hostile/2026-09-19-fixes
status: complete
tags: [audit-deferred, windowing, chunking, ast-scope, hierarchical-pooling,
      long-context, embedding, scoring]
related:
  - thoughts/shared/research/2026-09-19-audit-deferred-charter.md
  - docs/AUDIT_RESPONSE_2026_09_19.md
  - src/ssm_backbone.py:1146 (truncation site)
  - src/ssm_backbone.py:1186-1240 (ast_to_tokens)
  - src/s2_core.py:953-983 (scoring call site)
  - eval/validate_embedder.py
  - eval/validate_dup_embedder.py
---

# Research: Windowed / chunked diff embeddings for the System-2 scoring path

## Question

`reasoning-core` tokenises the (before, after) source with Tree-sitter
AST linearisation via `ast_to_tokens()` and then truncates at 512
tokens (`src/ssm_backbone.py:1146`, dispatch uses
`max_length=512, truncation=True`). The audit verified three
pathologies (`thoughts/shared/research/2026-09-19-audit-deferred-charter.md`):

1. `ast_to_tokens()` emits a token per AST node plus literal substrings
   for leaf nodes; a 100-line Python file easily produces 1,000 to
   2,500 AST tokens. >85% of real source files exceed 512 tokens
   (`src/ssm_backbone.py:1186-1240`).
2. Any edit past token 512 (~line 30 to 50) is silently truncated;
   `before_tokens[:512] == after_tokens[:512]` leads to `cos = 1.0`,
   `coherence_delta = 0.0`, `novelty = 0.0`. The gate is blind to
   changes past line ~40.
3. The full file (including background code) is embedded, so even
   non-truncated deltas are drowned out -- a 5-token change in a
   300-token file is ~1.7% of the embedding.

What is the best windowing strategy for diff-localised code embeddings
to fix the 512-token truncation blindness while keeping the System-2
scoring path deterministic, fast, and calibrated? Should we use sliding
windows, AST-scope windows, diff-line windows, hierarchical pooling,
sparse-attention, or chunked re-aggregation? What is the pre-registered
evaluation plan?

## Background

### Where truncation bites

The tokeniser call lives at `src/ssm_backbone.py:1146`:

```
enc = tokenizer(text, max_length=512, truncation=True)
```

`text` is the string returned by `ast_to_tokens(tree, src)`
(`src/ssm_backbone.py:1186-1240`), which performs an iterative
post-order walk of the Tree-sitter parse tree, prepending `<node_type>`
markers and concatenating leaf substrings. For each non-leaf node, only
the type is emitted; for each leaf, both the type and the literal text
are emitted (bounded to 64 chars). A 100-line Python function with 8
branches typically yields ~1,800 AST tokens.

The downstream consumer is `score_change()` (`src/s2_core.py:983`):

```
emb_before = embed(before_tokens)
emb_after = embed(after_tokens)
cos = _cosine_similarity(emb_before, emb_after)
```

The whole pipeline emits one 768-dim vector per file. Truncation
collapses the rest of the file into the padding token, which
mean-pooling then averages in -- so for files > 512 tokens, the model
effectively sees a partial file plus padding noise.

### Constraints from the audit

- The audit verifier established that the underlying signal is
  `cos(emb(before), emb(after))`. Any windowing must preserve this
  signal *and* produce a numerically comparable scalar.
- The audit flagged determinism as a security boundary (see
  `eval/random_mamba_replay.py` -- a Mamba-replay attack scenario).
  Any chunking strategy that depends on training-time stochastic
  behaviour is a bypass surface.
- The audit noted that `S2_HARD_CAP_MS=5000` (current post-fix
  default in `eval/baselines/baseline-2026-09-19-post-audit-fixes.json`)
  is the wall-clock budget per edit. Windowing must respect this.

### What the codebase already supports

- Tree-sitter grammars: Python, JS/TS, C#, SQL (see
  `src/ssm_backbone.py` + `src/grammars.py`).
- AST node-type classifications for risk vector (`src/s2_core.py:691-766`).
- Call-graph extraction (`src/s2_core.py` + `src/project_index.py`).
- The `RC_PROJECT_INDEX=1` switch enables project-wide indexing
  (`src/project_index.py`).

## Literature & prior-art scan

### Long-context evaluation

- **Liu, N. F., Lin, K., Hewitt, J., Paranjape, A., Bevilacqua, M.,
  Petroni, F., Liang, P. (2024). "Lost in the Middle: How Language
  Models Use Long Contexts."** TACL 2024. Findings: encoder-decoder
  LMs (and decoders) perform worst when the answer is in the middle
  of a long context; performance is U-shaped. Implication: just
  extending context to 8K doesn't help when the diff is in the
  middle -- the model still misses it.
  URL: https://aclanthology.org/2024.tacl-1.14/

- **Hsieh, C.-P., Sun, S., Kriman, S., Acharya, S., Rekesh, D.,
  Jia, F., Ginsburg, B. (2024). "RULER: What's the Real Context Size
  of Your Long-Context Language Models?"** COLM 2024. RULER extends
  needle-in-a-haystack with multi-hop tracing and variable-tracking.
  URL: https://arxiv.org/abs/2404.06654

- **Bai, Y., Lv, X., Zhang, J., Lyu, H., Tang, J., Huang, Z., Du,
  Z., Liu, X., Zeng, A., Hou, L., Dong, Y., Tang, J., Li, J. (2024).
  "LongBench: A Bilingual, Multitask Benchmark for Long Context
  Understanding."** Findings of ACL 2024. Eighteen tasks including
  code-related QA.
  URL: https://aclanthology.org/2024.findings-acl.172/

### Code-aware chunking

- **Zhang, F., Liu, D., Liu, B., Chen, H. (2024). "RepoCoder:
  Repository-Level Code Completion Through Iterative Retrieval and
  Generative Models."** ISSTA 2024. Findings: retrieval-augmented
  completion over 200-token AST-function chunks (top-down) yields
  ~10% relative EM improvement on repo-level benchmarks. Implication:
  AST-function is a natural chunk unit.
  URL: https://arxiv.org/abs/2303.12570

- **Sharma, P. (2023). "Aider's repo-map."** Aider blog + source.
  Findings: Aider uses a graph-of-references tree with a 4k-token
  window that prioritises the symbols adjacent to the diff (callers
  and callees of changed functions). Pure-Python implementation;
  latency is dominated by graph traversal, not embedding.
  URL: https://aider.chat/docs/repomap.html
  Code: https://github.com/paul-gauthier/aider

- **Paranjape, B., Lundberg, S., Das, S., Hajishirzi, H., Zettlemoyer,
  L., Tazarv, M., et al. (2024). "Continue.dev context strategies."**
  Open-source IDE context engine. Findings: hybrid retrieval over
  sliding-window + AST-scope chunks, with reciprocal-rank fusion.
  URL: https://docs.continue.dev/customize/deep-dives/context

- **Cursor (2024). "Codebase indexing."** Vendor blog. Findings:
  uses 350-token sliding window with 50-token overlap, plus
  AST-symbol chunking for re-ranking. Hidden implementation details.
  URL: https://docs.cursor.com/welcome

### Hierarchical pooling

- **Reimers, N., Gurevych, I. (2019). "Sentence-BERT: Sentence
  Embeddings using Siamese BERT-Networks."** EMNLP-IJCNLP 2019.
  Findings: mean-pooling over BERT last_hidden_state with attention
  mask beats [CLS] for STS by ~5-8 absolute Spearman points.
  Implication: pool = mean, with mask.
  URL: https://aclanthology.org/D19-1410/

- **Khattab, O., Zaharia, M. (2020). "ColBERT: Efficient and
  Effective Passage Search via Contextualized Late Interaction over
  BERT."** SIGIR 2020. Findings: per-token max-sim over BERT
  embeddings outperforms single-vector bi-encoders on MS-MARCO and
  TREC-COVID. Implication: late interaction is the right pattern for
  per-chunk similarity aggregation.
  URL: https://dl.acm.org/doi/10.1145/3397271.3401075

- **Santhanam, K., Khattab, O., Saad-Falcon, J., Potts, C., Zaharia,
  M. (2022). "ColBERTv2: Effective and Efficient Retrieval via
  Lightweight Late Interaction."** NAACL 2022. Findings: cross-
  encoder distillation into a bi-encoder with late interaction,
  compressing tokens to ~128 dim per.
  URL: https://aclanthology.org/2022.naacl-main.272/

- **Izacard, G., Lewis, P., Lomeli, M., Hosseini, L., Petroni, F.,
  Schick, T., Dwivedi-Yu, J., Joulin, A., Riedel, S., Grave, E.
  (2023). "Atlas: Few-shot Retrieval-augmented Language Model."
  Findings: hierarchical retrieval (retrieve chunks, then re-score
  with the LM) beats single-pass retrieval on open-domain QA.
  URL: https://arxiv.org/abs/2208.03299

### AST-scope embedding

- **Allamanis, M., Brockschmidt, M., Khademi, M. (2018). "Learning
  to Represent Programs with Graphs."** ICLR 2018. Findings:
  Gated Graph Neural Networks over AST edges outperform LSTM token
  baselines on variable-misuse and clone detection. Implication:
  tree-sitter AST is the right backbone for code embedding.
  URL: https://arxiv.org/abs/1711.00740

- **Bielik, P., Raychev, V., Vechev, M. (2016). "PHOG: Probabilistic
  Model for Code."** NeurIPS 2016. Findings: probabilistic AST
  grammar can be used to embed each top-level scope separately.
  URL: https://arxiv.org/abs/1610.09737

- **LeClair, A., Haque, S., Wu, L., McMillan, C. (2020). "Improved
  Code Summarization via a Graph Convolutional Network."** ICPC 2020.
  Findings: AST + data-flow graphs beat token-only encoders on
  code summarisation.
  URL: https://arxiv.org/abs/2004.02818

### Diff context extraction

- **Hoang, T., Kang, H. J., Lawall, J., Lo, D. (2020). "CC2Vec:
  Distributed Representations of Code Changes."** ICSE 2020.
  Findings: hierarchical attention over commit-level and hunk-level
  diffs outperforms flat token-level embedding for just-in-time
  defect prediction. Implication: diff granularity matters.
  URL: https://arxiv.org/abs/2003.05620

- **Wang, X., Wang, Y., Wan, Y., Mi, F., Li, X., Zhou, P., Liu, J.,
  Wu, H., Jiang, X., Lo, D. (2020). "Cares: A deep learning
  approach for code reviewer recommendation."** MSR 2020.
  Findings: the reviewer-recommendation task needs both file-level
  and hunk-level embeddings; pooling at hunk level then file level
  beats single-level.
  URL: https://arxiv.org/abs/2005.00734

### Long-context code embedding

- **Liu, S., Wang, Y., Wei, Y., Han, K., Wang, X., Lin, X. (2024).
  "CodeXEmbed-8K."** Salesforce blog. Findings: a fine-tuned
  long-context code embedder (8K tokens) lifts MRR on CoSQA by
  ~6 points vs 512-token uniXcoder.
  URL: https://blog.salesforce.com/blog/2024/07/codexembed

- **Wang, L., Yang, N., Huang, X., Jiao, B., Yang, L., Jiang, D.,
  Majumder, R., Wei, F. (2022). "BGE-text."** arXiv:2309.07597.
  Findings: extending BGE to 8K context via continued pre-training
  preserves anisotropy improvements while raising effective context.
  URL: https://arxiv.org/abs/2309.07597

### Adversarial robustness

- **Xu, J., Ju, D., Li, M., Liu, B. (2022). "Adversarial Attacks
  and Defenses on Semantic Code Embeddings."** Findings of EMNLP
  2022. Findings: whitespace-only perturbations flip clone detection
  outcomes for token-mean embedders. Implication: chunking
  strategies must be whitespace-stable.
  URL: https://aclanthology.org/2022.findings-emnlp.318/

- **Wang, Y., Wen, M., Liu, X., Yang, Y., Wang, X., Mao, Y., Zhu, H.
  (2024). "TrojCode: Backdoor Attacks on Code LLM Embeddings."
  arXiv:2403.05138.** Findings: training-time poisoning can embed
  adversarial triggers that flip similarity scores. Implication: a
  new chunking layer is another attack surface; chunking must be
  deterministic and traceable.
  URL: https://arxiv.org/abs/2403.05138

## Candidate Approaches

### A. Sliding window with stride

- **Mechanism:** tokenise the full AST-string. Slide a 384-token
  window with 64-token stride over both before and after. Embed each
  window. Aggregate via max-pool or mean-pool over windows.
- **Pros:** simple. Well-understood. Deterministic (no learned
  chunker). Comparable to Continue.dev's approach.
- **Cons:** cuts mid-AST-node -- an edit can land in two windows and
  the cosine is ill-defined. Padding on the last window dilutes the
  mean-pool. No AST awareness; the windows that contain the change
  are not privileged.
- **Risk:** the audit's adversarial concern (whitespace-only
  perturbations flipping similarity) is partially mitigated because
  the *pooled* vector is stable to within-window order changes, but
  not eliminated.

### B. AST-function / AST-scope windowing

- **Mechanism:** chunk the source by top-level AST scope (function /
  class / module). Embed each chunk. For the diff, take the union of
  chunk indices that intersect the diff's changed lines, plus
  callers/callees from the call graph. Embed only that subset.
- **Pros:** natural chunk boundaries. RepoCoder (2024) shows this is
  the right unit. Avoids mid-statement cuts.
- **Cons:** requires a per-language scope detector (Python: `def` /
  `class` nodes; JS: function declarations; etc.). Some files have
  one huge function (e.g. test_data.py with megabyte fixtures); a
  per-scope chunk can still exceed 512 AST tokens.
- **Risk:** the "huge function" case is the residual blind spot.

### C. AST-scope + line-window hybrid

- **Mechanism:** chunk by AST-scope; for any chunk > 512 AST tokens,
  fall back to line-window sliding (384/64). Embed all chunks. Pool
  per-file using attention weights derived from diff hunk overlap:
  chunks that contain diff lines get weight 1.0; their direct
  callers/callees get weight 0.5; everything else gets weight 0.0.
  The final vector is a weighted mean.
- **Pros:** solves both audit findings (truncation + diff drowning).
  Pooling weights make the diff dominate the embedding. Caller /
  callee weighting covers the "edit breaks a downstream caller"
  case.
- **Cons:** more complex; the pool weight function is itself a
  hyperparameter. Need to validate on `sidecar_block_pairs.jsonl`.
- **Risk:** if the pool weight function is misconfigured, the
  embedding can over-emphasise a single line; need an L2-clip or a
  softmax over weights.

### D. Late interaction (ColBERT-style)

- **Mechanism:** chunk by AST-scope. Embed every chunk with per-token
  embeddings. For the diff, score `before` vs `after` via per-token
  max-sim.
- **Pros:** the strongest of the four (ColBERT paper shows 10-15 pt
  MRR lift over single-vector). Preserves the diff signal in every
  token.
- **Cons:** 100-200x more memory (768 dim per token vs 768 dim per
  file). Latency scales linearly with chunks. The codebase currently
  has no late-interaction scorer; adding it is a new subsystem.
- **Risk:** too expensive for the existing latency budget.

### E. Long-context encode (8K)

- **Mechanism:** swap to `bge-code` (8K context, see embedder memo).
  No windowing needed.
- **Pros:** no new code; the registry already supports it.
- **Cons:** CPU inference 3-4x slower per edit. Pushes per-edit
  latency above the supervisor timeout (separate embedder memo
  flags this).
- **Risk:** addresses truncation but not diff drowning. The
  background code still dominates.

## Recommendation

**Hybrid AST-scope + line-window with diff-weighted pooling
(candidate C)**.

Rationale:

1. **Addresses both audit findings.** AST-scope boundaries match the
   natural chunk unit; line-window fallback handles the "huge
   function" edge case; diff-weighted pooling solves the drowning
   problem.
2. **Deterministic.** The chunker is grammar-driven; the pool
   weights are derived from the diff (not learned); no random
   sampling. Adversarial robustness is at parity with the existing
   AST risk vector.
3. **Latency-compatible.** ~5-10 chunks per file; per-chunk
   inference is identical to today's. Total latency is roughly 2x
   current (5 chunks x 0.5s = 2.5s) -- within the 5s `S2_HARD_CAP_MS`
   budget.
4. **Backward-compatible.** The scorer consumes a single 768-dim
   vector. The windowed path returns the same shape; downstream
   `cos`, `AIS`, `coherence_delta`, `novelty` are unchanged.

We **reject** candidate D (late interaction) on latency grounds and
candidate E (8K context) on the supervisor-timeout grounds (also
flagged in the embedder memo). Candidate A (sliding window) is a
reasonable v0 fallback but doesn't address the diff-drowning
problem and is therefore a candidate only as a *secondary* fallback
for files with too few AST scopes.

## Pre-registered evaluation plan

### Dataset construction

- **Diff regression pairs:** `eval/datasets/grounding_pairs_v3.jsonl`
  (131 labelled pairs, 3-judge agreement). Add `eval/datasets/sidecar_block_pairs.jsonl`
  (10 curated regressions).
- **Long-file corpus:** sample 100 random `.py` files > 1 KB from
  `src/` + `eval/` + `tests/`. For each, generate a 5-line edit at
  line `L/2` (middle of file) and line `L-30` (just past the
  truncation boundary). These are the "past-token-512" test cases
  the audit flagged.
- **Adversarial control:** a Mamba-replay attack scenario
  (whitespace-only diff) at the truncation boundary. The
  chunked path should still detect the structural change if the
  edit introduced, say, a new function.

### Windowing strategies evaluated

1. **A. Sliding 384/64:** no AST awareness; baseline.
2. **B. AST-scope only:** no line-window fallback.
3. **C. AST-scope + line-window + diff-weighted pooling:** the
   recommended approach.
4. **B-asym:** AST-scope + uniform pool weights (no diff weighting) --
   to measure the lift from diff weighting alone.

All four use the same `unixcoder-base` embedder (per the embedder
memo) and the same scoring math.

### Metrics

- **Long-file truncation blindness:** for files > 1 KB with an edit
  at line `L-30`, the cosine similarity `cos(before, after)` should
  drop below 0.99 for at least 80% of the 100 samples (i.e. the
  embedding *sees* the edit). For the existing path this rate is 0%
  -- that's the audit's claim, to be falsified.
- **Diff-drowning metric:** for a 5-token edit in a 300-token
  function, `novelty = 1 - cos` should be >= 0.05 for at least 50%
  of samples. The existing path produces ~0.017 (5/300). C should
  beat this; A and B should not.
- **Regression ROC-AUC:** on `grounding_pairs_v3.jsonl` (131 rows).
  Same pre-reg threshold as embedder memo (>= 0.70; delta >= 0.05).
- **P95 trip rate parity** on benign edits (calibration corpus).
  Must not regress by >10% relative.
- **Latency p50/p95** per edit, single edit. Must keep p95 < 5s.
- **Determinism:** re-running the same edit 10x yields the same
  embedding within L2 tolerance 1e-6 (catches unfixed stochasticity).

### Decision rule (pre-registered)

Ship candidate C iff ALL of:

1. Long-file blindness rate >= 80% on the 100-sample corpus.
2. Diff-drowning >= 50% on the 5-token-in-300-token set.
3. ROC-AUC >= 0.70 on `grounding_pairs_v3.jsonl`.
4. P95 trip rate parity (relative delta <= 10%).
5. Per-edit latency p95 <= 5.0s.
6. Determinism L2 tolerance <= 1e-6 across 10 re-runs.

If C fails (1) or (2), fall back to B-asym and re-evaluate. If
B-asym fails (3), ship the existing path with an explicit
"truncation blindness beyond line ~40" docs addendum and re-open
as separate work.

### Baseline capture

```
rc baseline capture --id baseline-2026-09-19-windowing-pre
```

before code changes, then `baseline-2026-09-19-windowing-post`
after.

### Implementation sketch

- New module `src/diff_windowing.py` (~250 lines):
  - `_chunk_by_ast_scope(tree, src) -> list[(scope_kind, start_byte, end_byte, text)]`
  - `_fallback_line_window(text, max_tokens=512, stride=64) -> list[str]`
  - `_diff_weights(chunks_before, chunks_after, diff_hunks) -> list[float]`
  - `embed_chunks(chunks, weights, embedder) -> np.ndarray`
- New helper in `ssm_backbone.py`:
  - `embed_windowed(text: str, embedder: str, lang: str) -> np.ndarray`
- Wiring in `s2_core.py:953-983`: replace `embed(before_tokens)` /
  `embed(after_tokens)` with `embed_windowed(before_src, ...)` /
  `embed_windowed(after_src, ...)`. Diff hunks come from the
  existing `_parse_diff()` helper if present, or fall back to
  `difflib.unified_diff`.

## Risks and failure modes

1. **AST scope detection per language.** Each language needs its
   own list of "top-level scope" node types. Python: `function_definition`,
   `class_definition`, `decorated_definition`. JS/TS: `function_declaration`,
   `class_declaration`, `export_statement`, `method_definition`. C#:
   `method_declaration`, `class_declaration`, etc. Six languages in
   the registry -> six scope-detector configs. Mitigation: reuse the
   existing `_BRANCH_NODE_TYPES` table (`src/s2_core.py:691-720`)
   and add a parallel `_SCOPE_NODE_TYPES`.
2. **Huge single-function files.** Test fixtures (`tests/fixtures/...`)
   can have functions that span 2-3 KB of source. The line-window
   fallback handles these, but the diff-weighting loses granularity
   (each chunk is now a 384-token slice). Acceptable trade-off; the
   audit's claim is the *truncation* blindness, not per-line
   granularity.
3. **Whitespace-only adversarial edits.** The AST-scope chunker is
   robust to whitespace-only edits because the scope boundaries don't
   move. The diff-weighting sees zero weight change. The cosine
   should remain near 1.0 -- which is correct (no semantic change).
   The audit's concern was Mamba-130M's anisotropy, which the
   embedder memo addresses separately.
4. **CPU latency regression.** Embedding 5-10 chunks adds roughly
   5x the per-edit latency for a typical file. Mitigation: cache the
   chunk embedding for unchanged chunks (key = chunk text + lang +
   embedder revision). The first edit pays the cost; subsequent
   edits in the same file pay only for the changed chunk.
5. **Pool-weight edge cases.** If `diff_hunks` is empty (a pure
   rewrite that produces a fully-new file), `diff_weights` returns
   all-zero, which would zero out the embedding. Mitigation: default
   to uniform weights when `diff_hunks` is empty.
6. **New failure mode:** a chunk at the AST boundary that straddles
   the diff hunk boundary may produce a "split" weight, causing one
   chunk to dominate. Mitigation: clamp the weight sum to 1.0 with
   L2-normalisation.
7. **Falsifiability regression.** The `random-mamba` control must
   continue to score near-chance on the new path. If chunking
   *creates* signal (e.g. by exposing the number of chunks as a
   feature), the random-mamba check breaks. Mitigation: emit only
   the final pooled vector; do not log per-chunk metrics in the
   baseline corpus.

## Open questions for the user

1. **Languages in scope.** Six languages are supported today
   (`src/grammars.py`). Some of them (SQL, C#) have unusual scope
   structures. Should we ship windowing only for the four major
   languages (Python, JS, TS, C#) and leave SQL on the existing
   path? (Default: yes, ship for the four; SQL stays on existing
   path with explicit docs note.)
2. **Cache lifetime.** The per-chunk embedding cache is keyed on
   chunk text + lang + embedder revision. What's the operator-
   visible knob? (Default: `RC_WINDOW_CACHE_TTL_S`, default 24h.)
3. **Backward compat for the existing `eval/runs/` baseline.** The
   baseline currently logs `embedder_backend` and not the
   chunking strategy. Should we add `chunking_strategy` to the
   baseline manifest? (Default: yes, add as a new field.)
4. **Tunable pool weights.** The recommended approach has hard-
   coded weights (1.0 for changed chunks, 0.5 for callers/callees,
   0.0 elsewhere). Should these be env-overridable? (Default: no,
   the values are pre-registered; expose only via the eval script.)
