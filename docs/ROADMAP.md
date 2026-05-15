# Roadmap

The current shipped surface goes well past verification-only — the gate
participates in planning, mines its own calibration corpus, and runs
concurrent calibration in shadow mode. The next phase is **closing the
loop**: iterative repair so Claude re-proposes against the repair hint
until pass-or-yield.

Source of truth: [`PLAN.md`](PLAN.md) +
[`../thoughts/shared/research/`](../thoughts/shared/).

---

## Shipped (see `git log --grep='^feat'`)

- **P-1 — Day-zero ergonomics:** magic-comment escapes, `RC_BYPASS_NEXT`
  kill switch, `rc` CLI.
- **P0 — Validation harness:** embedder fitness test, calibration corpus,
  golden set, shadow-mode wiring.
- **P1 — Plan-time SSM scoring + plan→code coherence gate**, mock-detector
  heuristics.
- **P2 — Generative repair head:** Qwen2.5-Coder-1.5B via MLX / Scaleway,
  behind `RC_REASONER_BACKEND`.
- **P3 — Calibration:** Mahalanobis over 8-dim risk space, per-kind
  shrinkage, Page-Hinkley monthly recalibration.
- **P4 — Calibration corpus + golden set + OOD detector + shadow-mode
  hardening.**
- **P5 — Sidecar broker + supervisor + grounding eval** on 200 labeled
  pairs.
- **P5 round-3 hardening** — 4-reviewer findings closed: per-dim Pareto
  epsilon, semantic safety net, stderr truncation contract, rules.yaml
  fail-closed (`b7f0517`).
- **P5 grounding pairs v2** — judge-relabeled high-confidence subset; live
  κ=0.74 on v2 (kin-judge contaminated, gate advisory) (`4ed3245`).
- **P5 grounding pairs v3 — cross-family κ dataset** — 131 high-confidence
  pairs (200 input) × 3 judges (devstral + llama-3.3-70b + mistral-small);
  max pairwise-κ = 0.6998 < 0.7 independence gate; sentinel
  `qwen_kappa_gate.json` reports gate_pass=true at κ=0.8025. Replaces the
  kin-judge-contaminated v2.
- **Linux systemd user-unit recipe** — mirrors the macOS launchd
  supervisor; documented in [`INSTALL.md`](INSTALL.md).
- **P7 — Calibration concurrent with shadow mode** (Mahalanobis +
  Ledoit-Wolf shrinkage + empirical-Bayes per-kind anchor).
- **P7 supervisor watcher** — consumes `recalibrate.signal` for hot-refit
  without restart (`5313498`).
- **Iter-2 readiness blockers closed** — `GUARDED_PATHS` extended to all
  hook helpers + supervisor + gen_client + calibration; binomial sign-test
  in `eval/stats.py` for the falsifiable goal (`6a921ce`).
- **CI eval workflow stabilized** — sharded-safetensors fallback, lazy
  prefetch, `--run-id` arg, contents:write permission
  (`c452cb4`/`c0118a4`/`4997ec7`/`dcd3598`/`1738b57`/`1bc2718`).
- **Unified `install.sh` / `uninstall.sh`** — one command to enable all
  supported CLIs in a target repo; one to revert.

---

## Open

- **Synthesize-Check-Refine loop** — Phase 3 of
  [`../thoughts/shared/plans/2026-05-06-system-2-loop-closure.md`](../thoughts/shared/plans/2026-05-06-system-2-loop-closure.md):
  on block, auto-call the generative critic, re-score with sidecar, emit
  validated repair as stderr hint. Iteration is server-side; agent never
  sees the loop.
- **Hybrid symbolic gating (ADR injection)** — Phase 2 of the same plan:
  `.reasoning-core/rules.yaml` + `_rule_engine.py` for layered-import /
  forbidden-pattern / metric-threshold rules co-emitted with the neural
  risk vector through the same exit-2 pipe.
- **TTFV (<15 min) + drift visualization** — Phase 1: `rc audit-history`
  (last N commits, what would have been blocked) + `rc viz` Mermaid
  sparkline + `npx reasoning-core init` one-line installer.
- **CUDA / MLX kernels** for the Mamba forward pass (currently CPU-only;
  p95 ~5s).
- **SSE `/score/stream`** + Prometheus textfmt `/metrics` (current
  `/metrics` returns JSON; SSE endpoint not yet wired).
- **Pre-commit variant** so non-Claude editors are also gated.
- **Mamba-3 watch + Plan-B Mamba-2-2.7B fallback** — Phase 4: HOLD on
  Mamba-3 (no public HF checkpoint as of 2026-05; CUDA-only kernels; only
  1/8 risk dims uses SSM embedding). Plan-B fallback ships behind
  `RC_USE_MAMBA2_2_7B=1`.
