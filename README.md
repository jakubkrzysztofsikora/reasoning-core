<!--- Logo --->
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/logo-dark.svg">
    <img alt="reasoning-core logo" src="./docs/logo.svg" width="300">
  </picture>
</p>

<p align="center">
  <strong>reasoning-core</strong> — local pre-edit gate for AI coding CLIs.
</p>

<p align="center">
  Audit and warn on AI edits that drift off-plan, import what's banned, or break invariants — before they hit disk.
  <br/>
  Opt-in enforcement mode can block these edits.
  <br/>
  At runtime: loopback-only sidecar, no telemetry, no cloud relay.
  <br/>
  One-time model download from HuggingFace is required for the default SSM embedder.
</p>

<p align="center">
  <a href="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/lint-and-test.yml">
    <img src="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/lint-and-test.yml/badge.svg" alt="lint-and-test">
  </a>
  <a href="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/eval.yml">
    <img src="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/eval.yml/badge.svg" alt="eval">
  </a>
  <a href="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/reasoning-core-pr-score.yml">
    <img src="https://github.com/jakubkrzysztofsikora/reasoning-core/actions/workflows/reasoning-core-pr-score.yml/badge.svg" alt="PR score">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT">
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  </a>
</p>

---

## What it is

A local scorer reads every Edit / Write an AI coding agent proposes, runs cheap
execution-grounded oracles on it (`py_compile`, `ruff`, `ast.parse`, your
`.reasoning-core/rules.yaml`), and scores it against an 8-dim risk vector. In the
**default advisory mode** it warns and audits; in **opt-in copilot mode** it can
block the change before it lands if it drifts off-plan, invents a helper you
already have, or violates a coupling/coherence threshold. One sidecar, six
CLIs (Claude Code, OpenAI Codex, Gemini, Moonshot Kimi, GitHub Copilot,
Mistral Vibe). At runtime the sidecar binds to loopback only; no telemetry,
no cloud relay, no ongoing network calls.

**Evidence status:** The benchmark figures in [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md)
and the whitepaper are historical controlled-study results from one codebase,
using an earlier risk-label schema. They are not a current real-usage guarantee.
The shipped install remains advisory/shadow by default; current real-session
quality evidence is being built from decision-linked labels and repository
outcomes.

## Quick start

Two commands. No git clone, no venv, no launchd dance, no `pip install -r
requirements.txt`. `pip install reasoning-core[full]` puts the framework
and every Python dep (torch, transformers, tree-sitter, fastapi, mcp,
ruff, …) on PATH and installs an `rc` console script. `rc init` wires
hooks into the current repo, downloads the default embedder, and brings
up the sidecar supervisor as a per-user daemon.

```bash
# 1. Install the framework (one-shot, ~5 min on broadband — pulls ~500 MB
#    of Python deps and the 250 MB mamba-130m checkpoint up front so the
#    gate is complete by the time you reach step 3).
pip install reasoning-core[full]

# 2. Wire it into the repo you want gated
cd /path/to/your-repo
rc init                         # adds .envrc, .claude/, .codex/, .gemini/,
                                # .copilot/, .kimi/, .vibe/, .pi/ + sidecar daemon

# 3. Run your CLI — hooks fire automatically
claude                           # or: codex / gemini / copilot / kimi / vibe / pi
```

If `pip install reasoning-core[full]` fails on your platform, the
fallback flow is `pip install reasoning-core && pip install -r
requirements.txt` (still no git clone). See [`docs/MIGRATION_v1.md`](docs/MIGRATION_v1.md)
for moving off the old clone-and-install flow.

When the gate blocks an edit, you see a `Decision ID` you can inspect or
override from the terminal:

```
[reasoning-core] BLOCKED: oracle failure (ruff)
  file: src/sidecar_boot.py
  line: 27
  reason: Unused import `tempfile`

[hybrid-reasoner] Decision ID: 92644989594e
  Inspect: rc explain 92644989594e
  Override: rc bypass-next
```

Every block is keyed by an ID linked to the audit log. `rc explain` shows the
verdict, `rc bypass-next` arms one override, and the override + decision stay
paired in the shadow report so calibration stays grounded in operator behavior,
not the gate's guesses.

## What you get

**Pre-execution oracles — milliseconds, free**
- `py_compile` — Python syntax on the proposed diff
- `ruff` — lint, imports, style on the proposed diff
- `ast.parse` — semantic parse smoke test
- `.reasoning-core/rules.yaml` — your project's `forbid_import` / `forbid_pattern` list

**Neural risk vector — 8 dims per edit**

The default scoring vector is always-on: `cyclomatic`, `fan_in`, `fan_out`,
`depth`, `churn`, `coupling`, `cohesion`, `novelty`. Three additional optional
dimensions (`session_centroid_drift`, `project_fan_in`, `project_coupling`) are
emitted only when **both** conditions are met: a session baseline is registered
(via the sidecar's `/baseline` endpoint) **and** the hook passes `session_id` to
`/score`. The shipped `.envrc` sets `RC_PROJECT_INDEX=1` by default, but the
session-baseline path is rarely hit in practice — so the 8-dim vector is what
production edits get. All dimensions are scored in [0, 1] with a chord-distance
`coherence_delta` on [0, 2].

**Decision-ID footer on every block** — `exit-2` blocks always end with
`Decision ID: <hex>` and an `rc explain` / `rc bypass-next` follow-up so the
audit log and operator action stay linked by the same ID.

**Self-calibrating from your git history** — `rc audit-history` labels a
commit negative if it was followed within 48 h by a fix/revert/hotfix on the
same files. The labeled feedback recalibrates thresholds where you actually
make mistakes.

**Same hooks across 6 CLIs**

| CLI | Hook surface | Tier |
|---|---|---|
| Claude Code, OpenAI Codex, Gemini CLI, Moonshot Kimi | runtime PreToolUse | 1 |
| GitHub Copilot CLI, Mistral Vibe CLI | MCP `gate_edit` + post-turn audit | 2 |

Tier-2 means the gate runs at the model layer — the LLM is asked to call
`gate_edit` before every write. Under context pressure it sometimes skips;
for mission-critical work use a Tier-1 host. Detail:
[`docs/CLI_PARITY.md`](docs/CLI_PARITY.md).

**Local-only by construction** — sidecars bind `127.0.0.1` only, refuse
external NIC. The hook chain, the audit log, and the rule engine all run on
your machine.

## Configure

Defaults installed by `rc init` are honest-opt-in — the gate warns and audits,
never blocks. Enforcement is opt-in via `rc enable-enforcement` and requires an
authenticated operator action. See [`docs/HARDENING.md`](docs/HARDENING.md) for
the guard-integrity model.

```bash
export RC_MODE=advise              # advise | copilot | autopilot
export S2_HARD_CAP_MS=1500         # client cap on /score POST
export S2_COHERENCE_THRESHOLD=0.09 # chord-distance ceiling (95th-pct)
export S2_TIMEOUT=30               # server cap on /score
export RC_RULE_ENGINE=1            # enforce .reasoning-core/rules.yaml
```

Per-machine overrides → `.envrc.local` (gitignored). Run `rc enable-enforcement`
after ~48 h of shadow review and manually authoring a `PLAN.md` to promote to
`RC_MODE=copilot`. Full env-var table: [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).


## Audit & hardening

A hostile technical audit of this codebase was filed on 2026-09-19 and is
publicly addressed in [`docs/AUDIT_RESPONSE_2026_09_19.md`](docs/AUDIT_RESPONSE_2026_09_19.md).
Verified findings (async-event-loop starvation, supervisor self-termination,
`_BASELINES` memory leak, singleflight blocking, several shell-guard bypasses)
were fixed on the `audit-hostile/2026-09-19-fixes` branch; claims the audit
got wrong (label polarity, the `ais<0.4` dead-code claim, install.sh direnv
trust) are documented as rejected. Per `AGENTS.md`, a pre-fix and post-fix
immutable baseline were captured before and after the changes.

The shell-guard regex set now blocks `git apply`, `git checkout <sha>`,
`git restore`, `git stash apply`, `mv/cp/install/rsync` to source extensions,
`pathlib.Path().write_text(...)`, and `base64 -d | bash|sh|zsh|eval`.
Tunable knobs: `S2_HEALTH_TIMEOUT_S` (default 15s), `S2_HEALTH_GRACE_S`
(default 60s), `S2_BASELINE_MAX_SESSIONS` (default 256),
`S2_BASELINE_TTL_S` (default 24h).

## `rc` CLI

```bash
rc status                   # sidecar health + threshold posture
rc init                     # wire hooks into the current repo (replaces install.sh)
rc init --no-sidecar        # same, but skip the launchd/systemd daemon
rc init --no-model          # same, but skip the mamba-130m download
rc init-uninstall           # revert rc init via .reasoning-core/install.manifest
rc doctor                   # verify agent-hook wiring and evidence capture
rc explain <decision-id>    # why the last edit was blocked
rc bypass-next              # arm one bypass for the next Edit/Write
rc confirm-next             # audit ground-truth (operator_confirmed event)
rc enable-enforcement       # flip repo to copilot mode (authenticated; requires PLAN.md)
rc disable-enforcement      # revert to advisory mode
rc auth-bootstrap           # generate + store enforcement token (Linux/macOS)
rc label <decision-id>      # label an audit decision (builds the eval training set)
rc label --random           # pick one unlabeled decision and label it
rc label-stats              # progress toward the 20-positive-per-label target
rc benchmark                # Markdown report from your local audit log
rc record-verification      # persist a deterministic test/lint/build result
rc real-session-eval        # correlate real sessions with labels and outcomes
rc episodes                 # derived edit episodes (checks, repairs, final status)
rc reasoning-efficiency     # composite north-star metric from the audit log
rc audit-history            # label commits negative if followed by fix/revert
```

## Use it from code

```python
from src.s2_core import score_change
r = score_change("/repo/util.py", before, after)
print(r.architectural_impact_score, r.regression_detected, r.file_kind)
```

```bash
curl -fsS -X POST http://127.0.0.1:8765/score \
  -H 'content-type: application/json' \
  -d '{"path":"/repo/util.py","before_src":"...","after_src":"..."}' | jq
```

## Documentation

- [`docs/INSTALL.md`](docs/INSTALL.md) — manual install, troubleshooting
- [`docs/USAGE.md`](docs/USAGE.md) — hook layers, rule engine, shadow mode, FAQ
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — every `RC_*` / `S2_*` env var
- [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) — System 1 + System 2 architecture
- [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) — eval numbers, per-task verdicts
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — what's shipped, what's open
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — deep technical dive
- [`docs/HARDENING.md`](docs/HARDENING.md) — threat model
- [`docs/CLI_PARITY.md`](docs/CLI_PARITY.md) — per-host caveats

## Contributing

Spec first ([`docs/PLAN.md`](docs/PLAN.md)). Self-verify before pushing:

```bash
pytest -m "not live and not slow"         # fast offline gate
pytest -m "slow" --timeout=600            # slow suite (SSM sidecar)
bash -n scripts/*.sh install.sh uninstall.sh  # syntax check
python3 -c "import json; json.load(open('.claude/settings.json'))"
```

## License

[MIT](LICENSE) © Jakub Sikora.

## Acknowledgements

[Mamba](https://huggingface.co/state-spaces/mamba-130m-hf) (Gu & Dao) ·
[Model Context Protocol](https://modelcontextprotocol.io/) (Anthropic) ·
[direnv](https://direnv.net) · [tree-sitter](https://tree-sitter.github.io/).
