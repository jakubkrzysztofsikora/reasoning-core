# Four-arm FeatureBench pilot (2026-09-27)

This is an exploratory, two-task host pilot. It does **not** establish a
FeatureBench leaderboard score or a causal benefit for any arm. The same
Claude Code 2.1.282 / `claude-sonnet-4-5` agent ran in every arm:

The later bounded Mamba3 + Laya Codex pilot and its runtime limitations are
recorded in [`../runs/MAMBA3_LAYA_PILOT_2026_09_28.md`](../runs/MAMBA3_LAYA_PILOT_2026_09_28.md).

| Arm | Agent configuration |
| --- | --- |
| `vanilla` | Claude only |
| `laya` | Claude with local Laya intake hint |
| `rc` | Claude with reasoning-core pre-write hook |
| `laya_rc` | Claude with both |

The frozen cases are FeatureBench v1.1 `fast` rows 96 and 99 from dataset
revision `76b4a4566e04f4bcc13c35125d4f301791efa736` (`cases.json`). Each
arm received the same Sphinx base commit, published corruption patch, hidden
test-file removal, prompt, file tools, model, $1 provider cap, and 360-second
wall cap. Arm order was shuffled with seed `20260927`. The reasoning-core
Edit/Write hook had a separate host qualification and a forbidden-test-write
preflight. The prepilot context is captured in immutable baseline
`baseline-2026-09-27-featurebench-four-arm-prepilot`.

## Results

The valid collection is local at
`~/.local/share/reasoning-core/featurebench-pilot/runs/2026-09-27-sphinx-four-arm-v4/`.
The host grade is at
`~/.local/share/reasoning-core/featurebench-pilot/grades/2026-09-27-sphinx-host-v4-final/`.
`host_grade.py` reconstructs each masked baseline from its recorded commit,
applies the final patch, restores the published FAIL_TO_PASS test file from
the pinned Sphinx base commit, and runs that file under Python 3.12. It checks
the frozen case and patch hashes. A positive control on the unmasked base
passed all 21 tests. This is a useful local defect check, not the official
container evaluator or a complete PASS_TO_PASS regression check.

| Task | Arm | Agent exit | Host task test result | Cost USD | Runtime s | Changed paths |
| --- | --- | ---: | --- | ---: | ---: | --- |
| doctest | vanilla | 0 | 1 passed, 2 failed, 8 errors | 0.476 | 193 | target source plus 2 extra helper files, including a `test_*.py` file |
| doctest | Laya | 0 | 1 passed, 2 failed, 8 errors | 0.326 | 125 | target source |
| doctest | RC | 0 | collection error: missing target symbol | 0.445 | 250 | unrelated helper file only |
| doctest | Laya + RC | 0 | collection error: missing target symbol | 0.484 | 316 | none |
| command line | vanilla | 0 | 10 failed | 0.949 | 282 | 2 target source files |
| command line | Laya | 1, provider cap | 10 failed | 1.016 | 309 | 2 target source files |
| command line | RC | 0 | collection error: missing target symbol | 0.661 | 261 | none |
| command line | Laya + RC | 0 | collection error: missing target symbol | 0.799 | 336 | none |

No arm solved either task under this host proxy. Per two-task totals, vanilla
cost $1.425 / 474 s, Laya $1.342 / 433 s, RC $1.106 / 511 s, and Laya + RC
$1.283 / 653 s. The lower RC spend reflects empty or ineffective patches,
not improved efficiency. A clean agent exit is not a solved task.

The RC audit records 9 import-cycle blocks in `rc` and 11 in `laya_rc`.
Calling `find_import_cycle` on the **original** masked source for each blocked
target also returns a cycle. The gate currently finds any reachable cycle
rather than a newly introduced one, so these are baseline-cycle false blocks.
It also recorded 3 contract blocks in `rc` and 1 in `laya_rc`, demonstrating
that the scoped path rule was active. The vanilla doctest arm wrote a new
`test_*.py` file despite the prompt's no-test instruction. Neither result
proves broader containment quality from two cases.

## Fixed gate and Seaborn Codex rerun

The import-cycle gate now tests whether a proposed edit adds an edge that
closes a cycle. Existing cycles and duplicate definitions are compared against
the baseline source, so unchanged findings do not block an unrelated write.
The fixed-gate and Codex prepilot contexts were captured as separate immutable
baselines in `eval/baselines/` before this comparison.

The new task is pinned FeatureBench v1.1 `fast` row 81,
`mwaskom__seaborn.7001ebe7.test_bar.123ed709.lv1`, in `cases_seaborn.json`.
It has 16 focused `tests/_marks/test_bar.py` tests. The unmasked source passed
all 16; the published masked task failed all 16. The same Codex CLI 0.156.1
and `gpt-6-sol` model ran in every arm. The Laya arms received the same local
typed decision hint. The RC arms used the Codex `apply_patch` pre-tool hook
with deterministic rules and the local sidecar. Arm order was shuffled with
seed `20260927`, and each arm had a 420-second wall cap. The run stopped once
before the Laya+RC agent launched when Laya briefly returned unavailable;
it resumed the staged, clean workspace after a successful retry. Both runner
hashes are in the manifest.

The rollout is at
`~/.local/share/reasoning-core/featurebench-pilot/runs/2026-09-27-seaborn-codex-four-arm-v1/`.
The hash-checked local host grade is at
`~/.local/share/reasoning-core/featurebench-pilot/grades/2026-09-27-seaborn-codex-four-arm-v1/`.

| Arm | Focused host test | Runtime | Input tokens (cached) | Output tokens | Source files changed |
| --- | --- | ---: | ---: | ---: | --- |
| Vanilla | 15/16 | 207 s | 779,787 (738,816) | 7,874 | 2 |
| Laya | 15/16 | 250 s | 1,097,246 (1,013,504) | 7,661 | 2 |
| RC | 15/16 | 202 s | 793,519 (740,480) | 6,662 | 2 |
| Laya + RC | 16/16 | 305 s | 1,584,876 (1,510,272) | 9,530 | 2 |

The three failures were the same `test_auto_edgewidth` broadcasting error.
An additional local check on the combined patch ran `tests/_marks` and
`tests/_core/test_scales.py`: 186 passed and 1 expected failure.
No arm changed tests, and the RC arms recorded no policy block. The Codex
bridge qualification showed an allowed source edit and a direct forbidden
patch denied before mutation. The qualification agent declined to attempt the
forbidden edit itself. Codex shell writes and other specialized tool paths are
outside this hook's pre-write coverage; the final patch and changed paths were
audited after each arm. This one-task host proxy is not an official
FeatureBench score or evidence of a general causal quality or speed benefit.

## Boundaries and next experiment

The official FeatureBench grader is implemented in `grade.py`, but was not
run. Its Sphinx container image is roughly 10 GB and only 11 GiB of host disk
was free when grading began; pulling it risked filling the machine. The
host proxy also omits PASS_TO_PASS tests, container dependencies, and the
official score calculation. Collections v1-v3 are invalid: v1 had checkout
encoding artifacts, v2 omitted the corruption patch, and v3 omitted `PLAN.md`
so the path contract was inactive. They must not be pooled with v4.

The gate fix and one-task rerun above address the observed false blocks. A
larger preregistered set with official container grading and blinded human
review is still needed before comparing product quality or speed.

For reproduction after restoring disk headroom, run the pinned rollout with
`run.py`, then `grade.py` against the official FeatureBench checkout. For the
host proxy, run `host_grade.py` with the rollout path, the pinned Sphinx source
clone, an isolated Python 3.12 environment with Sphinx test dependencies, and
a new output directory. Both graders refuse incomplete or hash-drifted
rollouts.
