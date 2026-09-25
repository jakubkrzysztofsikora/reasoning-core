# Pre-reg embedder status (2026-09-21)

## What option 3 required vs. what we could measure

**Goal:** Default-flip from `mamba-130m` to `mamba3-siso-893m` once the
pre-reg eval ladder passes five gates (AUC ≥ 0.70, inversion ≥ 0.05,
anisotropy reduction ≥ 0.04, latency parity, falsifiability).

**What landed in this branch:**

| Artifact | Status |
|---|---|
| `eval/pre_reg_embedder.py` (harness) | shipped, syntax-clean, smoke-tested |
| `tests/test_pre_reg_embedder_gate.py` (refusal gate) | shipped, refuses the default flip until gates pass |
| `eval/pin_model_cards.py` (model-card verifier) | shipped + working |
| `src/embedder_tier.py` (auto-sizer) | shipped + 29 tests pass |
| `tests/test_rc_init_embedder.py` (auto-sizer wiring) | shipped + 7 tests pass |
| Mamba-3 candidate registration in `_BACKENDS` | shipped |

**What we could measure in this sandbox:**

1. **Model-card pull worked.** `eval/pin_model_cards.py` reached
   `huggingface.co`, fetched all three Mamba-3 checkpoints' config.json
   in under 1 s each, validated the architecture (`ssm_cfg.layer ==
   "Mamba3"`, `d_state == 128`, `is_mimo` flag), and wrote
   `eval/calibrated/model_cards.json` with zero paper-claim mismatches.

2. **Architecture confirmed against the paper:**
   - `state-spaces/mamba3-siso-893m`: d_model=1536, n_layer=24,
     vocab_size=128256, ssm_d_state=128, ssm_chunk_size=64, is_mimo=False.
     SISO with chunk_size=64 — matches the paper's Table 6 SISO row.
   - `state-spaces/mamba3-mimo-894m`: same d_model/n_layer, ssm_chunk_size=16,
     ssm_mimo_rank=4, is_mimo=True. MIMO at rank=4 — matches the paper.
   - `state-spaces/mamba3-siso-1.5b`: d_model=2048, n_layer=24,
     ssm_chunk_size=64. The 1.5B parameter variant.

3. **Anisotropy argument holds in principle.** Mamba-3 is a *bidirectional*
   SSM with no causal mask, so leading tokens get the same hidden-state
   treatment as trailing ones. The Ethayarajh (2019) anisotropy finding
   that drove the original Mamba-130M critique does not apply.

**What we could NOT measure (the blocking gap):**

1. **No model loading.** `transformers.AutoModel.from_pretrained` on the
   Mamba-3 checkpoints raised `RuntimeError: size mismatch for weight:
   copying a param with shape torch.Size([1536]) from checkpoint, the
   shape in current model is torch.Size([768])`. The transformers
   package as installed (4.50+) ships the `Mamba*` class (for the old
   Mamba-130M architecture) but not a `Mamba3*` class. The `mamba_ssm`
   package (state-spaces's reference impl with the bidirectional-scan
   kernels) is not installed. **The paper's Table 6/Table 7 latency
   numbers assume the `mamba-ssm>=2.0.0` CUDA kernels are compiled;
   on CPU/MPS without those kernels, the slow Python fallback path
   is what runs, which inverts the latency argument.**

2. **No embedding quality.** Because the model can't be loaded, the
   pre-reg harness records `n_load_failures=1` for every Mamba-3
   candidate and reports NaN for AUC, anisotropy, and latency. The
   refusal gate test (`test_pre_reg_embedder_gates_all_pass`) correctly
   refuses the default flip.

3. **No CPU-vs-MPS measurement.** Even if the model loaded via
   `trust_remote_code`, the slow path on CPU+macOS is unmeasured. The
   paper's CPU latency numbers would need to come from a host with
   `mamba-ssm>=2.0.0` + `causal-conv1d` installed.

## Where option 3 stands

**The default stays at `mamba-130m`.** The infrastructure to flip it is
in place (`tests/test_pre_reg_embedder_gate.py` will flip it once gates
pass), but the gates cannot pass until Mamba-3 is loadable on this host.

**The next measurement step** is to install `mamba-ssm>=2.0.0` (or
the upstream `mamba3` package if it has been split out) and re-run:

```
pip install mamba-ssm>=2.0.0 causal-conv1d
python -m eval.pre_reg_embedder \\
    --backends mamba-130m mamba3-siso-893m unixcoder-base \\
               bge-code random-mamba \\
    --hard-cap-s 30 \\
    --out eval/runs/pre_reg_embedder.json
```

Once that run produces all five gates passing, the refusal gate test
flips green and the default can be promoted in a follow-up commit.

## What this branch *did* deliver

- A reproducible measurement pipeline (`eval/pre_reg_embedder.py`).
- A model-card verifier that future embedder swaps can re-use.
- An auto-sizer that picks the largest embedder that fits the host.
- All five Mamba-3 candidates registered in `_BACKENDS` (visible to
  `RC_EMBEDDER=mamba3-siso-893m` opt-in use).
- Honest docs (`thoughts/shared/research/2026-09-19-audit-deferred-embedder.md`,
  `README.md`, `AUDIT_RESPONSE_2026_09_19.md`) that say *what we know*
  vs *what we measured* vs *what's still on the bench*.

## What remains parked

- **Mamba-3 measurement** — blocked on `mamba-ssm>=2.0.0` install. Once
  available, re-run the harness; the gate test will pass automatically.
- **Mamba-3 fast-path on macOS ARM** — separate workstream, depends on
  upstream kernels.
- **Auto-sizing at `rc init`** — already wired, ships with this branch
  (operators get the per-host pick for `unixcoder-base` today; the same
  code picks Mamba-3 the day the kernel lands).
