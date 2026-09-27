# Local Laya intake and exploratory evaluation

Laya 0.3.20 is an open-weight decision model with a Jev-compatible
`POST /v1/systemone` route. `rc autonomous` uses it by default and sends only
`triage_brief` and allowed path names to a loopback server. No TypeSafe key
is needed. Laya gives an advisory task kind; reasoning-core's qualified
Edit/Write hook and deterministic checks continue to control edits.

The default checkpoint mode is `auto`: Laya's server Router chooses English
or multilingual from the request. The Laya maintainers recommend this over
pinning English because the English checkpoint can be confidently wrong on
non-Latin scripts. The default does not require Laya to be installed; an
unavailable server yields an uncertain hint and the coding agent continues.

## Start and run

```bash
uv pip install --python .venv/bin/python 'laya[serve]==0.3.20'
LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_DEVICE=mps \
  LAYA_MODELS=english,multilingual LAYA_PRELOAD=1 .venv/bin/laya-serve
curl -fsS http://127.0.0.1:8000/health
```

Use `LAYA_DEVICE=cpu` without MPS. First startup downloads the checkpoints;
preloading both uses more memory. An English-only preload can still route
other languages after a cold model load. Use `--laya-model english` only for a
deliberately English-only workload, or `--adapter local` to skip Laya.
The adapter accepts loopback HTTP only. A Laya outage or malformed answer
returns an uncertain hint; the coding agent still receives the full task.
The exact host/model qualification remains required. For Codex, run
`rc autonomous --host codex --model gpt-6-astra` with a fresh Codex
qualification report; for Claude, use the qualified Claude host and its
provider budget flag. The complete task format, qualification commands, and
both run commands are in [AUTONOMOUS_HARNESS.md](AUTONOMOUS_HARNESS.md).

## Repeat the intake evaluation

```bash
python -m eval.laya_triage.run --laya-url http://127.0.0.1:8000 \
  --laya-model english --out /path/to/new-eval-directory
```

The fixture in `eval/laya_triage/cases.json` has 12 hand-labeled task briefs,
four per class. The runner writes a manifest, case predictions, and summary.
Pre-run baseline: `baseline-2026-09-27-laya-prepilot`.

On 2026-09-27, a warmed MPS server with Laya 0.3.20 English classified
11/12 versus 10/12 for the rules adapter. Median decision latency was
41.3 ms for Laya and approximately 0 ms for rules; Laya p90 was 48.5 ms.
Laya's single miss was a cross-module race investigation, called `localized`
with answer probability 0.4685.

A separate one-task live smoke used the same committed fixture and qualified
Claude Code 2.1.282 in both arms. Both passed the acceptance check and emitted
the same patch. Claude Code reported $0.0644265 with Laya and $0.0647988
with rules. Laya's first decision took 1,020 ms; later warm calls were faster.

These synthetic labels and one live coding task cannot prove improved coding
quality, cost, speed, or containment. A later one-task four-arm FeatureBench
host pilot found the combined Laya + reasoning-core patch passed 16/16 task
tests while the three other patches passed 15/16; see
[the pilot report](../eval/featurebench_four_arm/README.md). That single
result is exploratory and cannot establish a general benefit.

Laya's `answer_confidence` is the chosen answer probability. Its `confidence`
has different semantics from Jev's, so the adapter records the former and
does not reuse Jev's 0.70 threshold. The checkpoint also warned about
invalid temperature values in some calibration entries. Treat its
probabilities as diagnostic until validated on a larger held-out corpus. The
Laya model card recommends domain fine-tuning and calibration rather than
relying on raw confidence for control decisions:
[Laya model card](https://huggingface.co/convaiinnovations/laya).
