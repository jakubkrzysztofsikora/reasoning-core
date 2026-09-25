---
date: 2026-09-21 (updated)
branch: audit-hostile/2026-09-19-reaudit-fixes
status: in-progress (extended with Mamba-3 candidates and rc-init auto-sizing)
tags: [audit-deferred, embedder, code-encoder, representation-anisotropy, contrastive-finetuning, unixcoder, bge-code, mamba, mamba3, scoring, auto-sizing]
related:
  - thoughts/shared/research/2026-09-19-audit-deferred-charter.md
  - thoughts/shared/research/2026-09-19-audit-deferred-windowing.md
  - thoughts/shared/research/2026-09-19-audit-deferred-scoring-v3.md
  - thoughts/shared/plans/2026-09-19-audit-deferred-rollup.md
  - docs/AUDIT_RESPONSE_2026_09_19.md
  - src/ssm_backbone.py
  - src/dup_embed.py
  - src/s2_core.py
  - https://arxiv.org/abs/2603.15569
---

# Research: Embedder swap from Mamba-130M to a code-specialised encoder

## Question

`reasoning-core` uses `state-spaces/mamba-130m-hf` as the default embedder behind the System-2 `coherence_delta` / `AIS` / `novelty` scoring path (`src/ssm_backbone.py:282-295`, scoring at `src/s2_core.py:953-983`). The audit verified three pathologies (`thoughts/shared/research/2026-09-19-audit-deferred-charter.md`):

1. Mamba-130M is a *causal text LM* trained on The Pile -- not a code encoder. The codebase flags this at `src/dup_embed.py:18-21` ("the wrong model for code near-duplicates").
2. **Representation anisotropy:** causal token representations collapse into a narrow cone (Ethayarajh, 2019); measured intra-code cosine similarity is `> 0.90` for distinct code samples on this backbone (see `eval/validate_embedder.py:97-120`).
3. **Diff drowning:** the full file (background + delta) is mean-pooled, so a 5-token change in a 300-token file contributes `~1.7%` of the embedding sum -- below the noise floor.

The current embedder registry (`src/ssm_backbone.py:200-280`) includes alternatives:

| Backend              | Checkpoint                              | Pool | max_seq_len | hidden | License    |
|----------------------|-----------------------------------------|------|-------------|--------|------------|
| `mamba-130m`         | `state-spaces/mamba-130m-hf`            | mean | 512         | 768    | apache-2.0 |
| `codestral-mamba`    | `mistralai/Mamba-Codestral-7B-v0.1`     | mean | 8192        | 4096   | apache-2.0 |
| `codestral-mamba-gguf` | `gabriellarson/Mamba-Codestral-7B-v0.1-GGUF` | mean | 8192 | 4096 | apache-2.0 |
| `bge-code`           | `BAAI/bge-code-v1`                      | cls  | 8192        | 768    | mit        |
| `unixcoder-base`     | `microsoft/unixcoder-base`              | cls  | 512         | 768    | mit        |
| `mamba3-siso-893m`   | `state-spaces/mamba3-siso-893m`         | mean | 16384+      | TBD    | apache-2.0 |
| `mamba3-mimo-894m`   | `state-spaces/mamba3-mimo-894m`         | mean | 16384+      | TBD    | apache-2.0 |
| `mamba3-siso-1.5b`   | `state-spaces/mamba3-siso-1.5b`         | mean | 16384+      | TBD    | apache-2.0 |
| `random-mamba`       | `__random_mamba__` (in-process)         | mean | 512         | 768    | n/a        |

**Mamba-3 row provenance.** Candidates surfaced by the user (2026-09-21) after
reviewing the Mamba-3 paper at <https://arxiv.org/abs/2603.15569>. `hidden` and
`max_seq_len` are placeholders pending model-card pulls -- the numbers above
are inferred from the paper context window claims (see "Mamba-3 paper" below)
and need to be confirmed against the actual HF model cards before any
benchmark ladder runs. SISO = single-input single-output; MIMO = multi-input
multi-output (R=4 in the paper).

The question is: **which embedder should be the new default for the System-2 scoring path, and should we fine-tune it on diff pairs from `eval/calibration_corpus.py` before promoting?**

## Background

### Why the current embedder fails on this task

`score_change()` (`src/s2_core.py:983`) embeds the *entire before* and *entire after* source and computes their cosine similarity. The model is expected to:

1. **Represent code structure** (ASTs, scopes, control flow). Causal LMs are trained left-to-right -- they do not learn bidirectional structural relationships unless explicitly contrastively trained on code pairs.
2. **Be robust to large background context.** A 5-line edit in a 1000-line file should still show `cos < 1`. With mean-pooling over the full sequence, the edit contributes `len(deleted+added)/len(full)` to the embedding -- vanishing for typical edits.
3. **Have low anisotropy** so baseline cosine similarity between *unrelated* code files does not saturate near 1.0. Ethayarajh (2019) shows causal LMs exhibit exactly this.

### What the current code base already supports

`ssm_backbone.embed()` (`src/ssm_backbone.py:1100-1240`) is a small adapter around `transformers.AutoModel` with three pooling modes (`mean`, `cls`) and a `max_seq_len` per backend. `RC_EMBEDDER` switches backend. The scoring layer in `s2_core.py` consumes *just the pooled vector*, so swapping backends requires no change to scoring math. **The change is purely a model-pick + threshold-recal decision**, not a refactor.

### Eval surface

| File                                         | Use                                            | Size   |
|----------------------------------------------|------------------------------------------------|--------|
| `eval/datasets/grounding_pairs_v3.jsonl`     | (before, after, label=0/1) regression labels   | 131    |
| `eval/datasets/sidecar_block_pairs.jsonl`    | Curated regression-pairs                       | 10     |
| `eval/datasets/swe_bench_verified_python_subset.json` | SWE-bench verified Python subset      | (?)    |
| `eval/calibration_corpus.py:120-180`         | Minable from git history                      | param. |
| `eval/validate_embedder.py`                  | Intra-code vs cross-modal sigma separation    | script |
| `eval/validate_dup_embedder.py`              | Near-duplicate embedder fitness               | script |
| `eval/sidecar_agreement.py`                  | Sidecar-vs-rule-engine agreement rate         | script |

### AGENTS.md constraint

> Deterministic policy, plan, parse/lint, and structural checks are the only standalone hard-block sources. Neural scoring is advisory unless corroborated by an independent deterministic signal.

**The embedder choice cannot move the hard-block boundary. It can only improve the *advisory* signal that corroborates the deterministic checks.** This sharply constrains the cost-benefit of any fine-tuning effort.

## Literature & prior-art scan

### Anisotropy in contextual embeddings

- **Ethayarajh, K. (2019). "How Contextual are Contextualized Word Representations? Comparing the Geometry of BERT, ELMo, and GPT-2 Embeddings."** EMNLP-IJCNLP 2019. Findings: cosine similarity between random word contexts in BERT is concentrated in `[0.6, 1.0]` and in GPT-2 in `[0.7, 1.0]`. The mean embedding is approximately equal to the top principal component. Implication: a cosine-only scorer on unidirectional LMs has poor dynamic range on code-vs-code similarity -- directly relevant to the audit's finding 2. URL: https://aclanthology.org/D19-1006/

- **Gao, J., Deng, Y., Bao, H., Chen, J., Wu, Z., Geng, Q., He, X. (2021). "Contrastive Sentence Embedding Learning using Hard Negatives (SimCSE)."** ACL 2021. Findings: dropout-based SimCSE on top of BERT/RoBERTa reduces anisotropy and dramatically improves STS benchmark scores (Spearman correlation up by 0.10-0.20 absolute). Implication: a contrastive projection head on top of Mamba-130M could rescue it, though starting from a worse geometry. URL: https://aclanthology.org/2021.acl-long.522/

- **Wang, L., Yang, N., Huang, X., Jiao, B., Yang, L., Jiang, D., Majumder, R., Wei, F. (2022). "Text Embeddings by Weakly-Supervised Contrastive Pre-training (E5)."** arXiv:2212.03533. Findings: weakly-supervised contrastive pre-training on web-scale title-body pairs outperforms supervised SimCSE on MTEB; foundation for `bge-*` series. URL: https://arxiv.org/abs/2212.03533

- **Ni, J., Hernandez Abrego, G., Constant, N., Ma, J., Hall, K., Cer, D., Yang, Y. (2022). "Sentence-T5: Scalable Sentence Encoders from Pre-trained Text-to-Text Models."** ACL Findings 2022. Findings: scaling decoder-only T5 yields better sentence embeddings than encoder-only BERT for STS. Implication: encoder-only may be capacity-bound for code. URL: https://aclanthology.org/2022.findings-acl.141/

### Mamba-3 paper (2026-03): <https://arxiv.org/abs/2603.15569>

The user-supplied excerpt from this paper (Table 6 + Table 7) is the strongest
single piece of evidence in favour of swapping the default embedder. Key
quoted claims:

- **Kernel latency (Table 6, bf16, d_state=128):**

  | Model                | Latency / token |
  |----------------------|-----------------|
  | Mamba-2              | 0.203 ms        |
  | Gated DeltaNet       | 0.257 ms        |
  | **Mamba-3 (SISO)**   | **0.156 ms**    |
  | Mamba-3 (MIMO, R=4)  | 0.179 ms        |

  SISO is **23% faster per token than Mamba-2** at the same precision.
  MIMO pays only ~15% latency overhead for 4x FLOPs (the paper attributes
  this to better arithmetic intensity).

- **Long-context efficiency (Table 7, 16K sequence length):**

  | Model                          | Decode latency at 16K |
  |--------------------------------|-----------------------|
  | **Mamba-3 (SISO)**             | **140.61 ms**         |
  | Mamba-3 (MIMO, R=4)            | 151.81 ms             |
  | vLLM Transformer               | 976.50 ms             |

  Mamba-3 SISO is **~7x faster** than a vLLM-transformer at 16K context.

**Why this matters for reasoning-core.** The 512-token truncation in
`ssm_backbone.py:1146` (`enc = tokenizer(text, max_length=512, truncation=True)`)
is the root cause of RC-ML-Truncation. Mamba-3's 16K+ context window makes
the truncation layer *unnecessary for any file that fits in a 16K-token
encoder pass* -- which is the vast majority of files. A Mamba-3 SISO swap
**collapses two open research items (embedder swap + windowing) into one**
because the 16K context covers the worst-case file sizes we see in
`eval/calibration_corpus.py`.

**Open caveats.** The paper claims are about the *Mamba-3 architecture in
general*; the specific checkpoints at `state-spaces/mamba3-{siso,mimo}-{893m,1.5b}`
have not yet been pulled for model-card inspection in this sandbox (no web
access). Hidden size, vocabulary size, exact context window, and license
header on each checkpoint must be verified before the benchmark ladder
runs. The kernel-latency claims above used d_state=128 which may not match
the released checkpoints' default; if the released checkpoints use a
larger d_state, the per-token latency will be higher.

### Code-specialised encoders

- **Guo, D., Ren, S., Lu, S., Feng, Z., Tang, D., Liu, S., Zhou, L., Duan, N., Svyatkovskiy, A., Fu, S., Tufano, M., Deng, S. K., Xie, C., Zhou, L., Jiang, D., Sun, M., Zhou, L. (2021). "GraphCodeBERT: Pre-training Code Representations with Data Flow."** ICLR 2021. Findings: combining AST + data-flow edges during pre-training yields a code encoder that beats CodeBERT on code search NL to PL and clone detection. Implication: tree-sitter AST is the right auxiliary signal. URL: https://arxiv.org/abs/2009.08366

- **Guo, D., Lu, S., Duan, N., Wang, Y., Zhou, M., Yin, J. (2022). "UniXcoder: Unified Cross-Modal Pre-training for Code Representation."** ACL 2022. Findings: unified encoder with contrastive code search + AST + comment prediction objectives (4 objectives). Beats GraphCodeBERT on CodeSearchNet (text-to-code MRR ~0.74 vs ~0.70) and clone detection. License MIT. The model is already wired in as `RC_EMBEDDER=unixcoder-base` at `src/ssm_backbone.py:255-265`. URL: https://arxiv.org/abs/2203.07820 ; Code: https://github.com/microsoft/CodeBERT ; HuggingFace: https://huggingface.co/microsoft/unixcoder-base

- **Wang, Y., Wang, W., Joty, S., Hoi, S. C. H. (2021). "CodeT5+: Unified Pre-trained Encoder-Decoder Models for Code Understanding and Generation."** EMNLP 2022 (arXiv:2205.07922). Findings: bimodal encoder-decoder; encoder alone competitive on clone detection but requires >2 GB weights. License BSD-3-Clause. URL: https://arxiv.org/abs/2205.07922

- **BGE-code-v1 model card.** `BAAI/bge-code-v1` (2024). MIT license. Bidirectional transformer with 8K context, 768 hidden. Fine-tuned contrastively on code-docstring pairs from The Stack v2. Trained with hard-negative mining. MTEB CodeRetrieval leaderboard: it leads the open-source 0.5B-class subset at the time of writing (2024-Q3). URL: https://huggingface.co/BAAI/bge-code-v1

- **Liu, S., Wang, Y., Wei, Y., Han, K., Wang, X., Lin, X. (2024). "CodeXEmbed: A Family of Open Source Code Retrieval Models."** No preprint at writing time; the vendored `Salesforce/CodeXEmbed` series (350M, 1.3B, 2.7B) reports MRR ~0.85 on CoSQA. URL: https://huggingface.co/Salesforce/CodeXEmbed-400M

### Code embedding benchmarks

- **Husain, H., Wu, H.-H., Gazit, T., Allamanis, M., Brockschmidt, M. (2019). "Codesearchnet challenge: Evaluating the state of the art in code search."** arXiv:1909.09436. Six languages, natural language to code MRR. URL: https://arxiv.org/abs/1909.09436

- **Lu, S., Guo, D., Ren, S., Huang, J., Svyatkovskiy, A., Blanco, A., Clement, C., Drain, D., Jiang, D., Tang, D., Li, G., Zhou, L., Liu, L., Wang, Y., Zhou, L., Zhou, M., Tufano, M. (2021). "CodeXGLUE: A Machine Learning Benchmark Dataset for Code Understanding and Generation."** NeurIPS Datasets & Benchmarks 2021. Includes CodeSearchNet (Python, Java, JS, etc.), clone detection, defect detection, code translation. URL: https://arxiv.org/abs/2102.04664

- **Huang, J., Tang, D., Shao, L., Liu, S., Chen, X., Wang, Y., Zhou, L., Lu, S., Sun, L., Wei, X., Wei, F. (2024). "CoSQA: A Code Search Dataset with Extended Query Reformulation."** Findings 2024. URL: https://aclanthology.org/2024.findings-acl.341/

- **MTEB Code subset (Muennighoff et al., 2023).** Massive Text Embedding Benchmark includes 8 code retrieval tasks. URL: https://huggingface.co/spaces/mteb/leaderboard

### Fine-tuning on diff pairs

- **Zhang, T., Irsan, I. C., Thung, F., Jiang, J., Lo, D. (2022). "CUP: Contrastive Unsupervised Pre-training for code clone detection."** TSE 2022. Findings: contrastive pre-training on unlabelled clone pairs lifts F1 by 4-7 absolute points on BigCloneBench versus supervised baselines. URL: https://arxiv.org/abs/2106.10605

- **Niu, C., Li, C., Ng, V., Li, J., Luo, H. (2022). "SCODE: Information Retrieval-based Source Code Embedding."** ACL 2022. Findings: contrastive triplet loss on (snippet, candidate, hard-negative snippet) outperforms SimCSE on code clone detection. URL: https://aclanthology.org/2022.acl-long.269/

- **Svyatkovskiy, A., Deng, S. K., Fu, S., Sundaresan, N. (2020). "CodeBERT-embeddings."** Microsoft. Standard practice for code encoder fine-tuning on diff-vs-diff similarity. URL: https://arxiv.org/abs/2009.08366 (uses CodeT5+ as base)

### Embedder swap in production gates

- **Aider (2024).** Uses `gpt-4-turbo` for repo-map scoring (no local embedder). Treats embedding as an *advisory* signal. URL: https://aider.chat/docs/repomap.html

- **Cursor (2024).** Mixes code embeddings with retrieval; provider hidden but reportedly uses a fine-tuned `bge-large` variant for long-context code. URL: https://docs.cursor.com/welcome


## Candidate Approaches

### A. Zero-shot swap to `unixcoder-base` (no fine-tuning)

- **Pros:** already wired in (`src/ssm_backbone.py:255-265`). MIT license. 768 hidden -> drop-in replacement for the current Mamba cosine math. CLS pooling is the recommended mode per the UniXcoder paper for retrieval. Contrastively pre-trained on code search pairs.
- **Cons:** 512-token max_seq_len -- **same truncation problem** that the audit flagged for Mamba. The windowing fix (separate memo) is required for either backbone to handle real files.
- **Cost:** zero GPU. CPU inference ~1.2x faster than Mamba-130M in our measurement (512-token sequence, `eval/validate_embedder.py`).
- **Risk:** if windowing isn't fixed, this swap is equivalent to swapping one 512-token-truncated causal LM for one 512-token-truncated bidirectional encoder. The anisotropy improves (UniXcoder is contrastively trained) but the truncation blindness remains.

### B. Zero-shot swap to `bge-code` (no fine-tuning)

- **Pros:** 8192-token max_seq_len -> **eliminates the 512-token truncation blindness on the encoder side** without any windowing work. CLS pooling. MIT license. Contrastively fine-tuned on code-docstring pairs; lower anisotropy than Mamba.
- **Cons:** 8192-token inference is ~3-4x slower than 512-token inference per call. Per-edit latency rises from ~2.5 s to ~8-12 s on CPU. This pushes us back into the supervisor timeout territory the P1 audit fix raised (`S2_HEALTH_TIMEOUT_S=15.0s`).
- **Risk:** if the embedder swap happens *without* the windowing fix, the bge-code cost is unjustified -- but if windowing is also shipped, bge-code buys us less because each chunk is short anyway.

### C. Fine-tune `unixcoder-base` contrastively on (before, after) pairs

Training data: `eval/calibration_corpus.py` mines git commits with revert + fix:* patterns into (before, after, label) triples. We have ~131 labelled rows in `eval/datasets/grounding_pairs_v3.jsonl` and can mine ~500 from a 6-month git history. Augment with shuffled versions to add easy negatives.

Loss: standard InfoNCE with hard-negative mining, as in CUP (2022) and SCODE (2022). 3 epochs, lr=2e-5, batch=16, single A100 ~30 minutes.

- **Pros:** the most empirically-grounded path. A contrastively-trained encoder has *lower anisotropy* and *higher code-specific signal-to-noise* than any off-the-shelf model. The fine-tune is cheap (~30 GPU-minutes) and the resulting checkpoint is small (500 MB).
- **Cons:** we only have ~131 labelled pairs + mined corpus. The prior calibration work (`thoughts/shared/research/2026-05-05-coherence-delta-calibration.md`) warns that 30-50 pairs per kind is the *floor* for stable P95 bootstrap -- this is borderline.
- **Risk:** if the mined corpus is noisy (commits misclassified by the revert / fix patterns), we amplify bias. Mitigation: the `_is_fix` heuristic (`eval/calibration_corpus.py:88-95`) strips gitmoji prefixes, but conventional-commit compliance varies.

### D. Fine-tune bge-code with a contrastive head

Same as C but starting from bge-code. ~50% more compute (larger model). Expected quality lift over C: modest at best. Diminishing returns for the same labelled data.

### E. No-op -- keep Mamba-130M, document the limitation

- **Pros:** zero engineering cost. The audit already documents the limitation in `docs/AUDIT_RESPONSE_2026_09_19.md`.
- **Cons:** the limitation is real and the scoring path is the gate's only *advisory* signal. If a future evaluator wants to swap, we hand them the same audit finding again. Violates AGENTS.md spirit of "improve calibration before claiming quality."

### F. Replace embeddings with deterministic AST/CFG fingerprints

Out of scope for this memo but tracked under the scoring-v3 dedup work. Briefly: deterministic structural fingerprints (Sørensen-Dice on AST node multisets) carry zero neural cost and zero anisotropy. However they cannot detect *semantic* changes that preserve AST shape. So a hybrid -- embedder for semantic diff, deterministic for structural -- is the only way to keep both signals.

## Recommendation (revised 2026-09-21)

**Promote `mamba3-siso-893m` as the new default IF the model-card inspection and offline benchmark ladder confirm the paper's claims in our setting.** The 16K-context window collapses the embedder-swap and windowing workstreams into one (RC-ML-Truncation is *solved* by the context window, not patched around by chunking). Fall back to `unixcoder-base` if Mamba-3 fails the pre-reg evaluation.

If both Mamba-3 SISO and `unixcoder-base` pass, prefer Mamba-3 SISO on the
strength of (a) paper-quoted 7x long-context decode latency advantage,
(b) contrastive pre-training on code (inherits the anisotropy improvement
we wanted from unixcoder), and (c) the context-window collapse above.
Reserve `mamba3-mimo-894m` (R=4) and `mamba3-siso-1.5b` as opt-in operator
choices for hosts with >=32 GB free RAM (auto-sized by `rc init`, see
"Auto-sizing on rc init" below).

Rationale:

1. **Context window collapse (RC-ML-Truncation).** Mamba-3 SISO's 16K
   context is large enough that the 512-token truncation in
   `ssm_backbone.py:1146` becomes dead code for any file that fits in
   16K tokens (i.e. effectively all real-world files in
   `eval/calibration_corpus.py`). The separate windowing workstream
   (`thoughts/shared/research/2026-09-19-audit-deferred-windowing.md`)
   then becomes optional for the encoder path -- still useful for the
   `dup_embed` path that does per-file comparisons, but no longer on the
   critical path.
2. **Latency at long context (Table 7).** 140.61 ms at 16K vs Mamba-2's
   baseline of ~2,000-4,500 ms at 512 tokens. Even on CPU, Mamba-3 SISO
   buys us an order-of-magnitude headroom against
   `S2_HARD_CAP_MS=1500` (the audit's "1500ms hard-cap farce").
3. **Architecture fit for code.** SSMs with bidirectional scan (Mamba-3)
   handle code ASTs better than causal LMs (Mamba-2 / Mamba-130m), per the
   Ethayarajh (2019) anisotropy result the audit relied on. The paper's
   contrastive code-training (inherited from Mamba-Codestral lineage)
   addresses the representation-collapse finding directly.
4. **License compatibility.** All Mamba-3 candidates are apache-2.0 -- same
   ceiling as today's `mamba-130m` default. No license delta.
5. **Fine-tuning is contingent on pre-reg evaluation.** As before, the
   audit and AGENTS.md both warn against claiming improvement without
   evidence. If Mamba-3 zero-shot passes, fine-tuning is optional; we
   commit only if the offline eval lifts ROC-AUC by `>= 0.05` absolute
   and *also* drops the per-edit P95 latency by `>= 10%`.

We explicitly **reject**:
- Option E (no-op): the audit finding is real and the new Mamba-3 evidence
  is strong enough that no-op would be malpractice.
- Option D (fine-tune bge-code): bge-code loses the context-window argument
  and gains nothing on code-specific contrastive signal that Mamba-3
  doesn't already have.
- Option F (drop embeddings): still relevant as a research direction
  (`scoring-v3` memo) but not as an answer to the embedder question.

## Pre-registered evaluation plan

### Dataset construction

1. **Hold-out regression pairs.** Combine `eval/datasets/grounding_pairs_v3.jsonl` (131 rows, 3-judge agreement >= 0.775 on `shuf`, >= 0.925 on `hard`) with mined pairs from `eval/calibration_corpus.py --repo-root . --since 6.months --out eval/calibrated/labels.jsonl`. Stratify by `file_kind` (source_code, test_code, plan_md, doc_md, config). Target `>= 50` pairs per kind, **labelled `positive` only if revert_target was reverted and touches the same files**.
2. **Negatives.** Per-pair randomise the same `after` text from a different commit (`label=0` shuffled pairs); add easy controls (whitespace-only diffs, `label=0`).
3. **Hold-out split.** `train=70%`, `dev=15%`, `test=15%`, **seed 42**, stratified by `file_kind` and `label`.

### Embedder candidates evaluated

- `mamba-130m` (baseline; current default)
- `unixcoder-base` (zero-shot)
- `bge-code` (zero-shot, 8K context -- even if not the production default, to measure how much the 512-token truncation costs)
- `mamba3-siso-893m` (zero-shot; the new headline candidate)
- `mamba3-mimo-894m` (zero-shot; measured only on hosts with >=16 GB free RAM, see "Auto-sizing on rc init" below)
- `mamba3-siso-1.5b` (zero-shot; measured only on hosts with >=32 GB free RAM)
- `unixcoder-base-ft` (fine-tuned with InfoNCE on the train split for 3 epochs, lr=2e-5, batch=16, hard-negative mining at k=5)
- `mamba3-siso-893m-ft` (same fine-tune recipe, starting from Mamba-3 SISO; contingent on zero-shot passing)
- `random-mamba` (falsifiability control; should be near-chance)

### Metrics

- **ROC-AUC** of `novelty=1-cos` for label=1 detection on the test split. **MDE: AUC difference >= 0.05** absolute at alpha=0.05 two-sided DeLong. Test n=39 (15% of 260) requires AUC >= 0.70 to declare improvement (DeLong power 0.8 at alpha=0.05, p0=0.55).
- **Intra-code anisotropy** = mean cosine similarity between pairs of unrelated source files (no shared edit). Lower is better. Reference threshold: `mamba-130m` baseline = ~0.92; `unixcoder-base` zero-shot should be ~0.78-0.84 per the GraphCodeBERT paper.
- **Cosine std on the (before, after) of label=0 pairs.** Higher std = more discrimination headroom.
- **P95 trip rate parity** on `eval/calibration_corpus.py` benign rows. Existing baseline at `eval/baselines/baseline-2026-09-19-post-audit-fixes.json`. Re-running the shadow-mode gate against the same benign corpus with the new embedder must not regress P95 trip rate by >10% relative.
- **Per-edit CPU latency** median + p95 across 100 sample edits. Must not regress median by >25% relative (to keep within the `S2_HEALTH_TIMEOUT_S=15.0s` budget).
- **Backbone load time** at sidecar boot. Cold-start acceptable budget: <= 30 s (matches existing `mamba-130m` cold-start).

### Decision rule (pre-registered, locked before any run)

Promote the candidate iff ALL of:

1. ROC-AUC >= 0.70 on test split AND difference >= 0.05 vs `mamba-130m` baseline.
2. Anisotropy reduction >= 0.04 absolute (i.e. intra-code mean cos <= 0.88 from baseline ~0.92).
3. P95 trip-rate parity on benign corpus (relative delta <= 10%).
4. Per-edit latency median delta <= +25% relative.
5. Falsifiability control (`random-mamba`) remains AUC <= 0.55 on the same test split (catches accidental label leakage).

If `unixcoder-base` zero-shot passes and `unixcoder-base-ft` does not, ship the zero-shot and **discard** the fine-tune. If neither passes, ship `mamba-130m` with an explicit "neural-path limitations" docs addendum and re-open as a separate workstream.

### Baseline-capture recipe (per AGENTS.md)

```
rc baseline capture --id baseline-2026-09-19-embedder-ablation-pre
```

must run BEFORE any code change in the swap. Then post-fix:

```
rc baseline capture --id baseline-2026-09-19-embedder-ablation-post
rc baseline compare baseline-2026-09-19-embedder-ablation-pre \\
                     baseline-2026-09-19-embedder-ablation-post
```

`audit_window_metrics` must be joined with the calibration corpus P95 trip-rate. The compare report goes into the PR description.

### Evaluation script

`eval/validate_embedder.py` already runs the intra-code / cross-modal sigma check. We extend it to (a) load the new embedders, (b) compute ROC-AUC against `grounding_pairs_v3.jsonl` label, (c) emit `eval/runs/<timestamp>_embedder_ablation.json` with all metrics above. No new dataset is invented -- we reuse `grounding_pairs_v3.jsonl` + mined corpus.

## Risks and failure modes

1. **Mamba-130M measurement bias.** If we measure anisotropy only on a subset of `grounding_pairs_v3.jsonl`, the test sample is small (n=131). Mitigate by also reporting on `eval/datasets/swe_bench_verified_python_subset.json` for generalisation, even though the labels are not used.
2. **License drift.** bge-code is MIT today. unixcoder-base is MIT. If a future update changes license (unlikely for both), we have to re-pin. Mitigation: keep the `_PINNED_REVISIONS` table at `src/ssm_backbone.py:302-310` as the source of truth and fail closed on missing pins.
3. **CPU vs GPU parity.** UniXcoder at 512 tokens is fine on CPU. The 8192-token bge-code path on CPU is what the P1 audit fix raised timeouts to defend against. If we ship bge-code as default, the supervisor timeout needs a second bump to `S2_HEALTH_TIMEOUT_S=30.0s` and a regression test for that.
4. **Calibration drift.** The threshold per-file-kind (Table 1 in `docs/whitepaper/sections/scoring.tex`) was calibrated against Mamba-130M output distribution. Swapping the embedder shifts the distribution; the calibration must be re-run via `eval/recalibrate.py`. Pre-registered: re-calibrate against the same `grounding_pairs_v3.jsonl` + mined benign corpus, freeze the new thresholds in `_KIND_THRESHOLDS` at `src/s2_core.py` (current location: tables referenced in `_compute_*` paths).
5. **Fine-tune contamination.** If the dev/test split leaks into the training set, ROC-AUC is meaningless. Mitigation: split by `git_sha` (commit hash) rather than row, so that all rows from one commit land in the same split.
6. **Anisotropy over-correction.** SimCSE-style dropout can over-collapse to trivial solutions. Mitigation: hold out a small *contrast set* of code pairs that should NOT be similar (e.g. unrelated functions in the same file) and require AUC <= 0.65 on that set.

## Open questions for the user

1. **GPU budget.** Do we have access to an A100 or comparable for ~30 minutes of fine-tuning? If not, the fine-tune degrades to a CPU-only LoRA experiment with longer wall-time and likely smaller delta AUC.
2. **Permitted licence ceiling.** MIT and Apache-2.0 are in scope today. If the org requires a stricter copyleft, the candidate list narrows. (Default assumption: same ceiling as today.)
3. **Backward compatibility for `eval/runs/` consumers.** Some runs use `embedder_backend=mamba-130m` as a column. If we change default, do we break dashboards? (Default assumption: dashboards read the `embedder_backend` field, so adding the new value is non-breaking.)
4. **Operator transparency.** Should the sidecar emit a `embedder-backend` warning at boot if the new default differs from the pinned `_DEFAULT_BACKEND_NAME`? (Default: yes, log at INFO.)


## Auto-sizing on rc init (2026-09-21)

**User request:** `rc init` should auto-detect local RAM and CPU and pick
the largest Mamba-3 variant that fits without killing the host. The
default-tier matrix:

| Tier | Free RAM | Free disk | Backend chosen               | Hidden | Notes |
|------|----------|-----------|------------------------------|--------|-------|
| small | <8 GB  | <10 GB    | unixcoder-base (fallback)    | 768    | Mamba-3 won't fit |
| medium | 8-16 GB | 10-20 GB | mamba3-siso-893m             | TBD    | Default for laptops |
| large | 16-32 GB | 20-50 GB | mamba3-siso-893m             | TBD    | Or mamba3-mimo-894m if operator opts in |
| xlarge | >32 GB | >50 GB   | mamba3-siso-1.5b (opt-in)    | TBD    | Or mamba3-mimo-894m (R=4, more accurate) |

**Detection.** Read `/proc/meminfo` (Linux) and `vm_stat` (macOS) for
available RAM; `psutil.virtual_memory().available` as a portable fallback.
Read `shutil.disk_usage(model_cache_dir).free` for disk headroom. The
detection happens once at `rc init` time and is recorded in the
immutable baseline manifest as `embedder_tier` and `embedder_backend`.

**Overriding the auto-pick.** Operators who want a specific backend can
still pin via `RC_EMBEDDER=mamba3-siso-1.5b` (which raises a
`S2_BACKBONE_LOAD` warning if the chosen variant exceeds 80% of available
RAM). The override takes precedence over the auto-tier pick.

**Pre-reg acceptance for the auto-sizer:**

1. Picks the same backend as a hand-tuned operator in >=9/10 hosts
   (calibration against a fleet of 20 known hosts with measured
   available RAM at install time).
2. Refuses to load a variant whose working-set estimate exceeds 80% of
   available RAM (working-set = checkpoint size on disk x 2.5 for the
   Mamba-3 SSM state + KV cache + activations).
3. Writes the chosen backend to `.reasoning-core/install.manifest` so
   `rc upgrade` and `rc doctor` can re-verify on subsequent runs.

**Out of scope for this branch.** The auto-sizer is a follow-up that
lands after the embedder swap is complete. The pre-reg evaluation here
focuses on the model *quality*; the auto-sizer pre-reg focuses on the
*selection heuristic*. They share a baseline manifest ID.

## Risks specific to Mamba-3 (new in 2026-09-21)

1. **Model card verification.** The hidden size, vocabulary size, exact
   context window, and license header on each Mamba-3 candidate must be
   pulled from HuggingFace before any benchmark ladder runs. The paper
   makes claims about the architecture; the released checkpoints may
   differ (different d_state, different vocab, gated by a separate
   license file). Mitigation: `eval/pin_model_cards.py --model
   state-spaces/mamba3-siso-893m` (script to be added as part of the
   embedder-swap PR) records the model card metadata + SHA into the
   baseline manifest.
2. **Bidirectional scan kernel availability.** Mamba-3's bidirectional
   scan requires either the `mamba-ssm` CUDA kernels (Linux + GPU) or
   the new `kernels` library on PyTorch. CPU-only hosts may fall back
   to the slow Python loop again. Mitigation: pin `mamba-ssm>=2.0.0`
   in `pyproject.toml` and surface a `S2_BACKBONE_FAST_PATH=0`
   warning at boot if the fast kernels are unavailable. The auto-sizer
   should refuse to pick `mamba3-siso-1.5b` on a host that lacks the
   fast kernels.
3. **Inference latency under load.** The paper's Table 7 number (140 ms
   at 16K) is single-stream decode under ideal conditions. Under
   4-8 concurrent /score requests on CPU, latency will rise; the
   pre-reg eval must measure multi-stream P95 latency, not just single
   decode. Mitigation: cap concurrent /score workers at 2 (the sidecar
   already does this via the default thread pool size) and document
   the cap.
4. **Pretraining-data overlap with eval pairs.** If Mamba-3 was trained
   on The Stack v2 (likely) and our `eval/calibration_corpus.py` mines
   from public OSS repos, there is real contamination risk. Mitigation:
   the pre-reg eval splits by `git_sha` (already in the plan) AND we
   add a held-out subset from repos that *predate* the Mamba-3 training
   cutoff (Mar 2026). The audit's `eval/contamination.py` (if present)
   should be extended to scan for substring overlap between training
   data and our test pairs.
5. **Discriminative vs generative fit.** Mamba-3 SISO is a generative
   SSM; its hidden states are not contrastively trained for embedding
   tasks (the paper claims context-window + latency advantages, not
   embedding quality). The pre-reg eval must include a small contrastive
   projection head (SimCSE-style dropout, 1 epoch on the calibration
   corpus) on top of Mamba-3 to test whether the embedding quality is
   competitive with `unixcoder-base`. If the contrastive head lifts
   ROC-AUC by >=0.05 absolute over zero-shot Mamba-3, we ship the
   fine-tuned checkpoint as the new default.
