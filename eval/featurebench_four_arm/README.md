# Four-arm FeatureBench pilot (2026-09-27)

This is an exploratory, two-task host pilot. It does **not** establish a
FeatureBench leaderboard score or a causal benefit for any arm. The same
Claude Code 2.1.282 / `claude-sonnet-4-5` agent ran in every arm:

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

## Boundaries and next experiment

The official FeatureBench grader is implemented in `grade.py`, but was not
run. Its Sphinx container image is roughly 10 GB and only 11 GiB of host disk
was free when grading began; pulling it risked filling the machine. The
host proxy also omits PASS_TO_PASS tests, container dependencies, and the
official score calculation. Collections v1-v3 are invalid: v1 had checkout
encoding artifacts, v2 omitted the corruption patch, and v3 omitted `PLAN.md`
so the path contract was inactive. They must not be pooled with v4.

Before an RC rerun, fix cycle admission to compare before and after graphs,
capture a new immutable baseline as required by `eval/baselines/README.md`,
and requalify the host hook. Keep the same frozen tasks, or use a larger
preregistered set with official container grading and blinded human review.
Do not reinterpret this two-task pilot as a speedup or quality gain.

For reproduction after restoring disk headroom, run the pinned rollout with
`run.py`, then `grade.py` against the official FeatureBench checkout. For the
host proxy, run `host_grade.py` with the rollout path, the pinned Sphinx source
clone, an isolated Python 3.12 environment with Sphinx test dependencies, and
a new output directory. Both graders refuse incomplete or hash-drifted
rollouts.
