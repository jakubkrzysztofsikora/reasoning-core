# Mamba3 + Laya Codex pilot (2026-09-28)

This is a one-task exploratory host proxy, not an official FeatureBench score
or a causal ranking. The frozen task is Seaborn FeatureBench v1.1 `fast` row 81
at dataset revision `76b4a4566e04f4bcc13c35125d4f301791efa736`.
Every arm used Codex CLI 0.156.1, `gpt-6-sol`, the same masked source, and a
420-second cap. The focused host grader restored the pinned hidden test file
and ran 16 tests. It does not run the official container evaluator or the
PASS_TO_PASS suite.

The reconciled branch loads `state-spaces/mamba3-siso-893m` at commit
`e205b6e6d6075d089140d2e9170970aabc05c481` through the portable
CPU implementation. The checkpoint's model card names a Llama 3.1 tokenizer;
the runner used the pinned public Unsloth mirror. The installed tokenizer's
128,000-entry vocabulary matched the public NousResearch mirror, while their
special-token maps differed only in the pad token. This is a practical
compatibility check, not an official kernel parity test.

## Results

| Arm | Focused tests | Agent runtime | Laya answer | RC score outcome |
| --- | ---: | ---: | --- | --- |
| Vanilla | 15/16 | 318 s | None | None |
| Laya | 15/16 | 263 s | OK | None |
| RC | 15/16 | 200 s | None | 5 neural advisory responses |
| Laya + RC | 15/16 | 262 s | OK | 4 neural advisory responses |

Both guarded arms used a 64-token, changed-line context and a 20-second
score cap. The sidecar reported `model_loaded:true`, the exact Mamba3 backend
and revision, and `input_token_budget:64` before the run. Nine `/score`
calls completed with zero errors (median 3.22 s, p95 12.75 s). The audit
recorded no symbolic fallback and no policy block in the guarded arms.
Each arm changed the same two source paths and no tests. The one remaining
focused test failure was present in all four final patches. No setup won on
task quality. RC was fastest in this run, but one task with stochastic agent
execution cannot establish a speed advantage.

The first run used the model's full 2,048-token context. All four patches
again passed 15/16 tests, but the guarded arms timed out their neural score
calls and used symbolic fallback (five RC and four Laya+RC events). Its
sidecar score median exceeded 480 seconds because the portable recurrent
path is too slow on long files. It is evidence of a runtime limit, not a
Mamba3 quality comparison. An incomplete bounded v1 run was interrupted
before any arm completed and is marked invalidated locally.

## Reproduction and limits

- Frozen cases: `eval/featurebench_four_arm/cases_seaborn_mamba3_bounded.json`.
- Pre-run baseline: `baseline-2026-09-28-mamba3-bounded-codex-pilot`.
- Local rollout: `~/.local/share/reasoning-core/featurebench-pilot/runs/2026-09-28-seaborn-codex-mamba3-bounded-v2/`.
- Local host grade: `~/.local/share/reasoning-core/featurebench-pilot/grades/2026-09-28-seaborn-codex-mamba3-bounded-v2/`.
- The run manifest records the frozen case hash, model and host version,
  bridge and runner hashes, arm order, Laya status, backend health, score cap,
  usage, elapsed time, patch hashes, and changed paths.

The 64-token edit context changes the neural input distribution; thresholds
calibrated on Mamba-130M cannot be assumed valid. All uncorroborated neural
findings therefore remain advisory. The model's output has not been compared
numerically against the official CUDA kernel. More tasks and blinded
solution review are needed before an automatic backend change or a quality
claim.
