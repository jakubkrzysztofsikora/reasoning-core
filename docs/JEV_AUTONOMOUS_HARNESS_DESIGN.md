# Jev + reasoning-core: autonomous coding harness design

Status: design and evaluation protocol, 2026-09-27. A bounded task-triage
adapter and qualified Claude Code runner are implemented in `src/autonomous.py`.
The four-arm evaluation and any combined outcome remain unmeasured. Pre-pilot
baseline: `baseline-2026-09-27-jev-design-prepilot`.

## Evidence check

The supplied synthesis is a useful architecture sketch, but several details
need correction before they become a product claim:

| Claim in synthesis | Evidence-based interpretation |
|---|---|
| Jev is a coding-agent model or transparent Claude/Codex proxy | TypeSafe says Jev is a typed decision API, not a chat/code model or a drop-in coding-agent backend. Integrate it in an orchestrator or supported middleware, while retaining a generative coding model. [1][2] |
| Founder is Diego Almeida; typical response is 50-120 ms | TypeSafe names **Diogo Almeida** and advertises **70-500 ms** end-to-end, measured near its service. Neither is a local benchmark. [3] |
| Jev's confidence 0.90 guarantees exactly 90% accuracy | Choice/Score `confidence` summarizes the answer distribution; Noul returns a probability without that confidence field. Vendor calibration is a model-level claim, not a guarantee on this repo's tasks. Measure calibration and abstention locally. [4][5] |
| Hosted cost is $42 per billion input tokens | This is the vendor's launch price, equivalent to $0.042 per million input tokens; verify current billing before a paid run. [3] |
| reasoning-core catches every disk write atomically, including shell scripts | The existing scoped host qualification only established pre-write blocking for Claude Code 2.1.274 `Edit`/`Write`; shell and other mutation tools were disabled. Hooks are not OS-level filesystem interception or atomic multi-file transactions. [6][7] |
| Mamba structural score can hard-block by itself; 20-50 ms forward pass | Default neural scoring is advisory without independent deterministic corroboration. The repo's CPU evaluation guide budgets roughly 3 seconds for Mamba inference, so the proposed latency is unmeasured. [8][9] |
| Existing >10x result proves universal safety or faster coding | The frozen 150-pair result measures observer-detected invalid policy writes on one host/model/tool lane: 133/150 control versus 0/150 treatment. It does not establish speed, code quality, full filesystem containment, or Mamba's causal contribution. [7] |

## Smallest useful product

Build a **task-level decision adapter**, not a model-provider proxy. The
adapter accepts a bounded task brief, repository policy digest, and a few
trusted status fields. It returns typed `task_kind` (Choice),
`needs_deep_reasoning` (Noul), and `uncertainty/risk` (Score) decisions through
Jev's documented API. The exact questions, model version, and state schema are
versioned. The first pilot uses Jev only for task triage and a short advisory
hint; model selection and mutation permissions remain fixed. This isolates
whether the extra decision helps at all before adding a routing confound.

The same adapter interface accepts a **local rules implementation**: explicit
file/path detection, task-keyword patterns, test metadata, and an `uncertain`
return. It is a cheap local control arm with the same bounded output contract,
not a claim to reproduce Jev's learned calibration or semantic ability. Do not
send source, diffs, credentials, or private issue text to hosted Jev by default;
the adapter sends only redacted fixture/task state in the first pilot. Jev is
early access and its ordinary API is hosted; enterprise zero data retention
is separately offered. [10]

Use the existing Claude Code qualified `Edit`/`Write` lane for the first live
pilot. Keep reasoning-core's deterministic plan, path, parse/lint, and rule
checks as the only default hard-block signals. Never convert a Jev probability
or an uncorroborated Mamba score into write authority. A Jev timeout, API
failure, malformed response, or low-confidence answer returns `uncertain` and
passes the full task to the generative model with no Jev hint. For any new host
or mutation tool, re-run host qualification before enabling unattended work.

### Autonomous loop

1. Create a disposable Git worktree from a pinned commit inside a container or
   equivalent sandbox. Mount only the task repository and approved toolchain;
   keep credentials outside the runner. A Git worktree alone does not contain
   arbitrary shell writes.
2. Freeze task statement, allowed paths, policy, acceptance tests, time/token
   budget, and output destination. Reject tasks without an independently
   checkable success condition.
3. Ask the decision adapter once at intake. Attach its result and provenance
   as *data* to the coding model; do not inject it as a higher-priority system
   instruction. The model can ignore an incorrect hint.
4. Run the existing coding agent with qualified mutation tools and
   reasoning-core enforcement. After each accepted edit, run deterministic
   checks in the sandbox. Record decision IDs and first violating snapshots
   with the external observer.
5. On a denial, return the exact rule/parse diagnostic and permit at most two
   bounded repair attempts. Stop on repeated identical denials, test
   regression, budget exhaustion, or an unqualified mutation route.
6. If acceptance tests pass and the independent policy oracle passes, produce
   a patch, run manifest, and draft PR or review packet. Promotion/merge is a
   separate policy action; the coding loop itself can run unattended.

This reuses the current host hooks, observer, corpus validation, and run
manifests. It avoids writing a new filesystem interception layer or editing
Claude/Codex network traffic. Jev does not need to classify every tool call:
that would add remote latency and create a dependency at the safety boundary.
Context trimming can be tested later, with deterministic retention of the
task, policy, file provenance, and recent diagnostics; no first-pilot pruning
of safety instructions or evidence.

## Pareto evaluation

The pilot asks one question: **does adding task-level Jev triage improve useful
autonomous task completion per dollar or minute without weakening containment?**
It is exploratory, so no public causal or calibration claim follows from it.

| Arm | Decision adapter | Egress gate |
|---|---|---|
| A | No hint | No reasoning-core; same human-readable policy |
| B | Jev hint | No reasoning-core; same human-readable policy |
| C | No hint | reasoning-core qualified enforcement |
| D | Jev hint | reasoning-core qualified enforcement |

Add the local-rules adapter as an optional fifth arm only if the first four
arms show a plausible Jev benefit. The four-arm design estimates each layer's
contribution and whether the combination adds value beyond reasoning-core.
Use the **same pinned generative model, host version, tool permissions, task
prompt, test budget, and randomized arm order** in all arms. All arms run in
disposable sandboxes; an external observer and held-out oracle are identical
and invisible to the agent. Disable shell mutation until separately qualified.

Freeze 12 new task fixtures before any run: six localized changes and six
multi-file changes, including allowed solutions under path/dependency/plan
constraints. Reuse the existing corpus machinery and observer, but do not
reuse the 150 confirmatory tasks to tune Jev prompts and then call the result
confirmatory. Have an independent reviewer verify each task's solution and
expected tests. Cap each attempt at two repairs and a predeclared time/token
budget. Log model version, decision questions, Jev request/response timings,
input/output tokens, provider charges, hook timings, observer snapshots,
tests, and final diff hashes. Store redacted decision data; never log API keys.

Primary pilot outcome: **compliant completion per total USD and per wall-clock
minute**, shown as raw numerator and denominator by arm. Guardrails:
observer-detected invalid write escapes, final policy violations, host/hook
failures, no-edit runs, and false blocks. Report task-paired deltas, medians
and p90 latencies, and all failures; do not hide failed attempts by averaging
only successful runs. Record Jev answer correctness and abstention against
blinded task labels. Measure empirical calibration only with a larger held-out
set; 12 tasks cannot validate a 0.70 confidence threshold.

The pilot has a **$50 total provider spend cap** and a stop rule: stop if any
unqualified write reaches outside the sandbox, the observer fails, or the cap
is reached. Price Jev with the current provider bill and the measured input
tokens; generative-model calls will likely dominate cost. A useful promotion
signal is D improving paired compliant completions or reducing median
wall-clock time by at least 15% versus C, with no additional observed policy
escapes or host failures. That is a *pilot decision rule*, not a statistical
proof. If the signal appears, freeze a separate 40+ task corpus and protocol,
capture a new immutable baseline, rerun the host qualification, and repeat
with blinded labels and uncertainty intervals before publishing any claim.

## Delivery order

1. Implement a pure `DecisionAdapter` interface, local-rules adapter, and
   Jev adapter with strict schema validation, bounded state, timeout, and
   fallback to `uncertain`.
2. Add task-intake integration to the existing runner and structured receipts.
   Keep the write gate unchanged.
3. Run offline labeled triage fixtures, then the four-arm disposable pilot.
4. Add model routing or context selection only if the intake-only pilot shows
   value. Test each as a separate intervention with a fresh frozen baseline.

## Sources

[1] TypeSafe, [Jev with coding agents](https://docs.typesafe.ai/introduction/coding-agents.md).
[2] TypeSafe, [Introduction](https://docs.typesafe.ai/introduction.md) and [API reference](https://docs.typesafe.ai/api.md).
[3] TypeSafe, [Introducing System One Models & Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev), 2026-09-15.
[4] TypeSafe, [Confidence](https://docs.typesafe.ai/confidence.md).
[5] TypeSafe, [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md), reviewed 2026-09-17.
[6] `eval/baselines/baseline-2026-09-15-host-enforcement-qualification.json` and `docs/CLI_PARITY.md`.
[7] `docs/EVAL_10X_PROTOCOL.md`, `docs/EVAL_RESULTS.md`, and `eval/runs/ten-x-confirmatory-v2-direct-claude-20260917T211830Z/report.json`.
[8] `AGENTS.md`, `docs/CONFIGURATION.md`, and `src/hooks/pre_edit_guard.py`.
[9] `eval/README.md` (CPU Mamba budget).
[10] TypeSafe, [Legal](https://docs.typesafe.ai/legal.md).
[11] LangChain, [Building a Harness with Jev](https://www.langchain.com/blog/building-a-harness-with-jev), 2026-09-17 (integration example, not independent benchmark evidence).
