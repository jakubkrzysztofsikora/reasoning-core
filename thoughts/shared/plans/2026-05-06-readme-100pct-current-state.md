---
date: 2026-05-06
commit: 89820b09878095f87ecea20f713424056c379b63
branch: main
ticket: n/a
status: draft
---
# Plan: Bring README.md to 100% match with current repo state

## Summary
The repo has shifted significantly since the README was last rewritten (commit `7bf2d77`,
"simplify and restructure README"). Eight feature phases (P-1 through P7) have shipped that
the current README either describes as future roadmap items, omits entirely, or
contradicts. This plan re-aligns README.md to be a faithful 100% reflection of HEAD
(`89820b0`) — no aspirational copy, no stale facts.

## Scope
- **In:** `README.md` only.
- **Out:** `docs/PLAN.md` (separate spec doc; intentionally lags), `docs/ARCHITECTURE.md`,
  `docs/HARDENING.md` (only update if README links rot), `eval/README.md`,
  `scripts/README.md`. Code, tests, configs untouched.
- **Out (deliberate):** the `eval/calibrated/` untracked dir and the unstaged `.envrc`
  diff. Plan documents *current tracked + working state*; the user should commit those
  separately. README reflects what is on disk **now** including the .envrc diff (because
  users running `direnv allow .` will see those vars).

## Research References
- `thoughts/shared/research/2026-05-05-impl-state-vs-plans.md` — prior state-vs-plan delta.
- `thoughts/shared/research/2026-05-05-coherence-delta-calibration.md` — calibration history.
- `thoughts/shared/plans/2026-05-06-iter2-100pct-eval-plan.md` — iter-2 eval plan.

## Discovered drift (evidence)

| Area | README claims | Reality at HEAD |
|---|---|---|
| Hooks | 5 layers (L1–L5) | 11 hook scripts: + `pre_compact_guard.py`, `post_batch_lang_audit.py`, `session_resume_inject.py`, `session_start_manifest.py` plus internal modules `_kill_switches`, `_magic_comments`, `_mock_detector`, `_ood_detector`, `_plan_quality`, `_session_manifest`, `_shadow_mode`, `_audit_rotation` |
| `src/` layout | `ssm_backbone.py`, `grammars.py`, `s2_core.py`, `mcp_reasoner.py`, `hooks/` | + `calibration.py`, `gen_client.py`, `rc_cli.py`, `sidecar_supervisor.py`, `_supervisor_broker.py`, `_supervisor_env.py` |
| `scripts/` | `start-sidecar.sh`, `configure-scaleway.sh`, `test-prototype.sh` | + `start-gen-sidecar.sh`, `install-supervisor-launchagent.sh` |
| `bin/` | not mentioned | `bin/rc` shim → `python3 -m src.rc_cli` |
| `launchd/` | not mentioned | `launchd/com.reasoning-core.supervisor.plist` (KeepAlive) |
| `eval/` | "paired Wilcoxon harness" one-liner | full subsystem: `aggregate.py`, `build_grounding_pairs.py`, `calibration_corpus.py`, `golden_set.py`, `qwen_grounding_eval.py`, `recalibrate.py`, `run_suite.py`, `synthetic_drift.py`, `validate_embedder.py`, `metrics.py`, `stats.py`, `Dockerfile`, `datasets/grounding_pairs.jsonl` (200 pairs), `datasets/swe_bench_verified_python_subset.json`, `prompts/system_prompt.txt`, `runs/`, `fixtures/`, `scripts/prefetch_mamba.sh`, `calibrated/labels.jsonl` |
| `docs/` | `ARCHITECTURE.md`, `HARDENING.md`, `EVAL_DESIGN.md` | + `EVAL_RESULTS.md`, `VERIFICATION.md`, `PLAN.md` |
| `HF_HOME` | "`$(pwd)/.cache/huggingface` (project-local)" | `$HOME/.cache/huggingface` (since `.envrc` change) |
| Roadmap P0-P5 | listed as future | **shipped**: P0 calibration_corpus + golden_set + validate_embedder; P1 plan-quality + plan-impl coherence-gate (`RC_PLAN_QUALITY`); P2 Qwen via `gen_client.py` + MLX backend (`RC_REASONER_BACKEND`); P3 calibration via `src/calibration.py` + `eval/recalibrate.py`; P4 plan↔diff (qwen_grounding_eval, Cohen κ ≥ 0.7 gate); P5 sidecar broker (`_supervisor_broker.py`) + supervisor launchd |
| Configuration env table | 10 vars | ~25 vars actually consumed (`RC_MOCK_DETECTOR`, `RC_PLAN_QUALITY`, `RC_LANG_LOCK`, `RC_SHADOW_MODE`, `RC_REASONER_BACKEND`, `RC_GEN_BUDGET_MS`, `RC_AUDIT_RETENTION_DAYS`, `RC_LANG_ALLOW`, `RC_LANG_OVERRIDE`, `RC_DRIFT_WARN`/`DENY`/`OVERRIDE`, `RC_BYPASS_NEXT`, `S2_PORT`, `S2_URL`, `S2_LOG_LEVEL`, `RC_AUDIT_CAP_BYTES`, `RC_AUDIT_ROOT`, `RC_STATE_DIR`, `RC_QWEN_KAPPA_SENTINEL`, etc.) |
| Magic comments / kill switches | not mentioned | `_magic_comments.py` + `_kill_switches.py` (P-1 day-zero ergonomics) |
| `rc` CLI | not mentioned | `bin/rc` user-facing entry point |
| Supervisor / KeepAlive | not mentioned | launchd KeepAlive + sidecar revive supervisor |
| Mock-detector | not mentioned | shipped P1, gated by `RC_MOCK_DETECTOR=1` |
| Shadow mode | not mentioned | `RC_SHADOW_MODE=1` (default) — decisions logged, not enforced |
| Cato VPN TLS | not mentioned | `.envrc` builds combined CA bundle; users on corporate VPNs need this knowledge |
| FAQ "predates 2345fba/2873c82" | named-commit advice | commits are months old; replace with version-agnostic phrasing |
| 5-step quickstart | step 6 "Optional global promote" labeled inside a 5-step section | header says "5 steps" but lists 6 |

---

## Phase 1: Header, TL;DR, ToC integrity

### Changes

#### File: `README.md` (lines 1–46)
- **What**:
  - Keep title, badges as-is. (All still accurate.)
  - **TL;DR fix (line 18–22)**: replace `bash scripts/start-sidecar.sh` with the
    user-facing alternative — show both the supervisor (recommended) and the bare
    sidecar. Mention `bin/rc` shim.
  - **ToC (lines 31–45)**: add anchors for new sections (`#cli`, `#supervisor--launchd`,
    `#evaluation-harness`, `#shadow-mode--kill-switches`).
- **Code sketch**:
  ```markdown
  ## TL;DR

  ```bash
  git clone https://github.com/jakubkrzysztofsikora/reasoning-core.git
  cd reasoning-core && python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt && huggingface-cli download state-spaces/mamba-130m-hf
  direnv allow .
  bash scripts/install-supervisor-launchagent.sh   # macOS — KeepAlive sidecar
  # or, ad-hoc:
  bash scripts/start-sidecar.sh
  export PATH="$PWD/bin:$PATH"
  claude   # every Edit/Write Claude proposes is now scored before it lands
  ```
  ```

### Success Criteria

#### Automated Verification
- [ ] All anchors in ToC resolve to `## H2` or `### H3` headings (`grep -E '^#{2,3} ' README.md` matches each `#anchor` slug).
- [ ] No 404 in internal markdown links: `python3 -c "import re,os; [print(p) for p in re.findall(r'\]\(([^)#]+)', open('README.md').read()) if not p.startswith('http') and not os.path.exists(p)]"` prints nothing.

#### Manual Verification
- [ ] TL;DR copy-paste works from a clean checkout (excluding HF download time).

### Dependencies
- Requires: nothing.
- Blocks: Phases 2–8 (they reference the new ToC anchors).

---

## Phase 2: "What you get out of the box" — add the shipped P-1…P7 features

### Changes

#### File: `README.md`, `## What you get out of the box` (lines 176–197)
- **What**: extend the bullet list to include the P-1 through P7 features actually on
  disk. Group by theme so it does not become a wall.
- **Rationale**: today the section reads as 2025-vintage but multiple features have
  shipped since.
- **Code sketch** (additions, in roughly the existing style):
  ```markdown
  - ✅ **Mock-detector heuristics** — flags placeholder code (`pass`, `NotImplementedError`,
    "TODO", suspicious return-zero) at gate time. Default-on via `RC_MOCK_DETECTOR=1`.
  - ✅ **Plan-quality gate (CGS)** — plan-time scoring of section drift, kNN novelty, and
    plan→implementation coherence. Behind `RC_PLAN_QUALITY=1`.
  - ✅ **Shadow mode by default** — `RC_SHADOW_MODE=1` logs every decision without
    enforcing. Flip to `0` once calibrated on your codebase.
  - ✅ **OOD detector + golden_set** — calibration corpus + Mahalanobis distance over the
    8-dim risk space; out-of-distribution edits surface in the audit log.
  - ✅ **Language fingerprint lock** — `RC_LANG_LOCK=1` rejects edits that introduce a
    language not present in the project's existing fingerprint (configurable via
    `RC_LANG_ALLOW`). Override per-edit with `RC_LANG_OVERRIDE`.
  - ✅ **Magic-comment escapes + kill-switches** — single-shot bypass via
    `# rc:bypass-next` magic comment; session-wide off via `RC_BYPASS_NEXT=1`. Captured
    at session boot so they cannot be edited mid-session.
  - ✅ **Generative repair head (P2 shipped)** — Qwen2.5-Coder-1.5B via
    MLX (Apple) / Scaleway (CI) selected by `RC_REASONER_BACKEND`, budgeted by
    `RC_GEN_BUDGET_MS`. Started by `scripts/start-gen-sidecar.sh`.
  - ✅ **Supervisor + launchd KeepAlive (macOS)** — `scripts/install-supervisor-launchagent.sh`
    drops `launchd/com.reasoning-core.supervisor.plist` so the sidecar restarts on crash
    and on login.
  - ✅ **`rc` CLI shim** — `bin/rc` exposes diagnostic + admin commands
    (`rc status`, `rc tail`, `rc bypass-next`).
  - ✅ **Audit log rotation** — `RC_AUDIT_RETENTION_DAYS=90` (default). Old shards are
    pruned on session start.
  - ✅ **Plan-implementation coherence gate** — PostToolUse hook diffs the active plan
    (`RC_ACTIVE_PLAN`) against the actual diff and warns on misalignment.
  - ✅ **Grounding eval harness** — 200 hand-labeled pairs in
    `eval/datasets/grounding_pairs.jsonl`; `eval/qwen_grounding_eval.py` enforces a
    Cohen κ ≥ 0.7 sentinel before promoting changes.
  ```

### Success Criteria

#### Automated Verification
- [ ] Every new bullet that names an env var matches a real reference in `src/` or
  `.envrc`: `for v in RC_MOCK_DETECTOR RC_PLAN_QUALITY RC_SHADOW_MODE RC_LANG_LOCK RC_REASONER_BACKEND RC_GEN_BUDGET_MS RC_AUDIT_RETENTION_DAYS RC_BYPASS_NEXT; do grep -rq "$v" src/ .envrc || echo "MISSING $v"; done` prints nothing.
- [ ] Every named script exists: `for s in scripts/start-gen-sidecar.sh scripts/install-supervisor-launchagent.sh bin/rc launchd/com.reasoning-core.supervisor.plist eval/datasets/grounding_pairs.jsonl eval/qwen_grounding_eval.py; do test -e "$s" || echo "MISSING $s"; done` prints nothing.

#### Manual Verification
- [ ] Bullets stay scannable; no bullet >2 lines.

### Dependencies
- Requires: Phase 1.
- Blocks: Phases 6 (Configuration), 7 (Roadmap rewrite — those bullets need to be
  removed from the roadmap once they are listed here).

---

## Phase 3: Hook layers table — expand to current hooks

### Changes

#### File: `README.md`, `## Hook layers` (lines 286–298)
- **What**: extend the 5-row table to cover the 11 hooks actually on disk. Add a
  separate "internal helpers" sub-list for `_*.py` modules (these are not hooks Claude
  fires; they are libraries shared by the hook entrypoints, so they should not be in
  the table).
- **Where**: replace the `| L1 | … | L5 |` block.
- **Code sketch**:
  ```markdown
  | # | Hook | Tool matcher | Purpose |
  |---|---|---|---|
  | L1 | `pre_bash_guard.py` | `Bash` | Blocks shell-level source writes (heredoc, sed, tee), kills against sidecar, edits to guard files |
  | L2 | `pre_edit_guard.py` | `Edit\|Write\|MultiEdit` | SSM scoring; per-kind threshold dispatch; mock-detector; OOD detector; language-lock; guard-file lock |
  | L3 | `pre_plan_guard.py` | `Plan` (and Write to `**/plans/**.md`) | Plan-time heuristics + plan-quality CGS (kNN novelty, section drift) |
  | L4 | `pre_task_guard.py` | `Task` | Regex screen on subagent prompts mentioning guarded paths with mutation verbs |
  | L5 | `pre_compact_guard.py` | `PreCompact` | Captures pre-compaction state so the post-compact context can be reconciled |
  | L6 | `post_bash_revive.py` | `Bash` (PostToolUse) | Re-spawns sidecar when `/health` stops responding |
  | L7 | `post_batch_lang_audit.py` | `Edit\|Write\|MultiEdit` (PostToolUse) | After-the-fact language fingerprint audit; logs drift events |
  | L8 | `session_start_manifest.py` | `SessionStart` | Snapshots `RC_*` env, repo SHA, active plan path; prevents mid-session env tampering |
  | L9 | `session_resume_inject.py` | `SessionStart` (resume) | Re-injects pinned env from the prior session manifest |

  Internal helpers (not hooks Claude fires; shared by the entrypoints above):
  `_audit_rotation`, `_block_format`, `_kill_switches`, `_magic_comments`,
  `_mock_detector`, `_ood_detector`, `_plan_quality`, `_session_manifest`,
  `_shadow_mode`.
  ```

### Success Criteria

#### Automated Verification
- [ ] Every named hook exists: `for h in pre_bash_guard pre_edit_guard pre_plan_guard pre_task_guard pre_compact_guard post_bash_revive post_batch_lang_audit session_start_manifest session_resume_inject; do test -f "src/hooks/$h.py" || echo "MISSING $h"; done` prints nothing.
- [ ] Every named hook is wired in `.claude/settings.json`: `python3 -c "import json; s=open('.claude/settings.json').read(); [print('MISSING',h) for h in ['pre_bash_guard','pre_edit_guard','pre_plan_guard','pre_task_guard','pre_compact_guard','post_bash_revive','post_batch_lang_audit','session_start_manifest','session_resume_inject'] if h not in s]"` prints nothing. (Flag uncertainty: `pre_compact_guard` and `session_*` may not yet be wired in `settings.json` — verify before publishing; if not wired, label them "shipped, not yet wired" in the table footnote.)

#### Manual Verification
- [ ] Table renders cleanly in GitHub preview (no broken pipes from `\|`).

### Dependencies
- Requires: Phase 1.

---

## Phase 4: Project layout — actual on-disk tree

### Changes

#### File: `README.md`, `## Project layout` (lines 376–412)
- **What**: replace the layout block with a faithful tree from `find` output (truncated
  at depth 2 per dir, plus selective depth-3 for `src/hooks/` and `eval/`).
- **Rationale**: the current block omits more than half the shipped files.
- **Code sketch**:
  ```markdown
  ```
  reasoning-core/
  ├── README.md                       ← you are here
  ├── LICENSE
  ├── requirements.txt
  ├── pyproject.toml
  ├── .envrc                          ← repo-scoped env (direnv)
  ├── .claude/
  │   ├── settings.json               ← hook matchers + MCP server
  │   └── skills/reasoning/SKILL.md
  ├── bin/
  │   └── rc                          ← `python3 -m src.rc_cli` shim
  ├── launchd/
  │   └── com.reasoning-core.supervisor.plist
  ├── src/
  │   ├── ssm_backbone.py             ← Mamba loader, embed(), ast_to_tokens
  │   ├── grammars.py                 ← Tree-sitter loader (12 languages)
  │   ├── s2_core.py                  ← parsing, scoring, FastAPI sidecar
  │   ├── mcp_reasoner.py             ← FastMCP bridge
  │   ├── calibration.py              ← Mahalanobis + per-kind shrinkage
  │   ├── gen_client.py               ← Qwen / Scaleway generative client
  │   ├── sidecar_supervisor.py       ← KeepAlive supervisor + broker
  │   ├── _supervisor_broker.py       ← cross-process broker
  │   ├── _supervisor_env.py          ← env capture + restore
  │   ├── rc_cli.py                   ← admin / diagnostic CLI
  │   └── hooks/
  │       ├── pre_edit_guard.py
  │       ├── pre_plan_guard.py
  │       ├── pre_bash_guard.py
  │       ├── pre_task_guard.py
  │       ├── pre_compact_guard.py
  │       ├── post_bash_revive.py
  │       ├── post_batch_lang_audit.py
  │       ├── session_start_manifest.py
  │       ├── session_resume_inject.py
  │       ├── _block_format.py
  │       ├── _kill_switches.py
  │       ├── _magic_comments.py
  │       ├── _mock_detector.py
  │       ├── _ood_detector.py
  │       ├── _plan_quality.py
  │       ├── _session_manifest.py
  │       ├── _shadow_mode.py
  │       ├── _audit_rotation.py
  │       └── audit_log.py
  ├── scripts/
  │   ├── start-sidecar.sh
  │   ├── start-gen-sidecar.sh
  │   ├── install-supervisor-launchagent.sh
  │   ├── configure-scaleway.sh
  │   └── test-prototype.sh
  ├── tests/
  ├── eval/
  │   ├── README.md
  │   ├── Dockerfile
  │   ├── run_suite.py                ← top-level harness
  │   ├── run_task.sh
  │   ├── aggregate.py
  │   ├── stats.py                    ← paired Wilcoxon
  │   ├── metrics.py
  │   ├── validate_embedder.py        ← embedder fitness test
  │   ├── calibration_corpus.py       ← labeled corpus mining
  │   ├── golden_set.py               ← regression suite
  │   ├── recalibrate.py              ← Page-Hinkley monthly recal
  │   ├── synthetic_drift.py
  │   ├── build_grounding_pairs.py
  │   ├── qwen_grounding_eval.py      ← Cohen κ ≥ 0.7 gate
  │   ├── datasets/
  │   │   ├── grounding_pairs.jsonl   ← 200 hand-labeled pairs
  │   │   ├── swe_bench_verified_python_subset.json
  │   │   └── refresh_subset.py
  │   ├── prompts/system_prompt.txt
  │   ├── fixtures/
  │   ├── runs/
  │   ├── calibrated/                 ← labels.jsonl (gitignored or untracked)
  │   └── scripts/prefetch_mamba.sh
  ├── thoughts/shared/                ← research, plans, handoffs
  └── docs/
      ├── ARCHITECTURE.md
      ├── HARDENING.md
      ├── EVAL_DESIGN.md
      ├── EVAL_RESULTS.md
      ├── VERIFICATION.md
      └── PLAN.md
  ```
  ```

### Success Criteria

#### Automated Verification
- [ ] Every path in the tree (lines starting with `│   ├──` or `│   └──`) maps to an existing
  file/dir. Script:
  ```bash
  python3 - <<'EOF'
  import re, os, sys
  block = open("README.md").read().split("## Project layout",1)[1].split("```",2)[1]
  paths = re.findall(r"[├└]── ([^\s←]+)", block)
  missing = [p for p in paths if not os.path.exists(p.rstrip("/")) and not os.path.exists(p)]
  sys.exit(1 if missing else 0)
  EOF
  ```

#### Manual Verification
- [ ] Tree fits in a single screen on default GitHub preview width.

### Dependencies
- Requires: Phase 1.

---

## Phase 5: New top-level sections (CLI, supervisor, evaluation, shadow mode)

### Changes

#### File: `README.md` — insert four new H2 sections between "Hook layers" and "Scoring math".

##### 5a. `## CLI (`rc`)`
- **What**: document `bin/rc` subcommands. Source of truth: `src/rc_cli.py`. Read it,
  enumerate verbs.
- **Code sketch**:
  ```markdown
  ## CLI

  Put `bin/` on PATH (`export PATH="$PWD/bin:$PATH"`) and use `rc` for diagnostics:

  | Command | Purpose |
  |---|---|
  | `rc status` | Sidecar health + threshold posture |
  | `rc tail` | Tail today's audit log |
  | `rc bypass-next` | Single-shot bypass for the next Edit/Write |
  | `rc supervisor install` | Drop launchd plist + load |
  | `rc supervisor uninstall` | Reverse |
  | `rc calibrate` | Recompute per-kind thresholds from `eval/calibration_corpus` |

  Run `rc --help` for the authoritative list.
  ```
- **Uncertainty flag**: subcommand list is illustrative — must read `src/rc_cli.py` and
  use the actual subparser names. If the CLI does not yet expose `supervisor` or
  `calibrate`, drop those rows.

##### 5b. `## Supervisor & launchd (macOS)`
- **What**: explain `scripts/install-supervisor-launchagent.sh` +
  `launchd/com.reasoning-core.supervisor.plist`. Why KeepAlive matters (sidecar crash
  during long sessions = silent fail-open if `S2_FAIL_CLOSED=0`).
- **Code sketch**:
  ```markdown
  ## Supervisor & launchd (macOS)

  The sidecar is a long-lived FastAPI process; if it crashes mid-session you lose
  scoring until you notice. The supervisor solves both problems.

  ```bash
  bash scripts/install-supervisor-launchagent.sh
  launchctl list | grep com.reasoning-core
  tail -f /tmp/reasoning-core-supervisor.log
  ```

  - KeepAlive=true → relaunches on crash.
  - RunAtLoad=true → starts on login.
  - Uninstall: `launchctl bootout gui/$UID/com.reasoning-core.supervisor`.

  Linux equivalent: see [`docs/HARDENING.md`](docs/HARDENING.md) systemd section.
  ```

##### 5c. `## Evaluation harness`
- **What**: replace the single-line "paired Wilcoxon harness" claim with a real section.
- **Code sketch**:
  ```markdown
  ## Evaluation harness

  `eval/` is the calibration + regression-test machine for the gate. See
  [`eval/README.md`](eval/README.md) for full reference.

  | Component | Purpose |
  |---|---|
  | `validate_embedder.py` | Embedder fitness test — checks Mamba pooled embeddings discriminate semantic-vs-syntactic edits |
  | `calibration_corpus.py` | Mines labeled (good-edit, bad-edit) pairs from git history |
  | `golden_set.py` | Pinned regression cases that must keep their decisions across releases |
  | `recalibrate.py` | Page-Hinkley monthly recal of per-kind thresholds |
  | `qwen_grounding_eval.py` | Enforces Cohen κ ≥ 0.7 between SSM gate and Qwen judge on 200 hand-labeled pairs (`datasets/grounding_pairs.jsonl`) |
  | `run_suite.py` + `aggregate.py` + `stats.py` | Paired Wilcoxon harness across N runs |
  | `synthetic_drift.py` | Generates drifted variants for stress testing |

  Smoke run:
  ```bash
  python3 -m eval.run_suite --task fixtures/smoke --n 2
  python3 -m eval.aggregate --runs eval/runs/smoke-001
  ```
  ```

##### 5d. `## Shadow mode & kill switches`
- **What**: explain `RC_SHADOW_MODE`, `RC_BYPASS_NEXT`, magic comments. This is critical
  for first-time users — the gate ships in shadow mode so they will not get blocked
  out of the box.
- **Code sketch**:
  ```markdown
  ## Shadow mode & kill switches

  The gate ships in **shadow mode** by default (`RC_SHADOW_MODE=1` in `.envrc`).
  Decisions are computed and logged; the hook never blocks. This lets you observe what
  the gate *would* have done on your codebase before flipping it on.

  Promote to enforcement when ready:
  ```bash
  echo 'export RC_SHADOW_MODE=0' >> .envrc.local
  direnv reload
  ```

  Escapes (in order of preference):
  - **Magic comment, single edit:** prepend `# rc:bypass-next` (or `// rc:bypass-next`)
    to the file before the Edit Claude is about to fire.
  - **Single-shot env:** `RC_BYPASS_NEXT=1 claude ...` — captured at session boot,
    consumed by the first guard fire.
  - **Per-path session-wide:** `RC_ALLOW_GUARD_EDIT=1` for guarded paths,
    `RC_ALLOW_SUBAGENT_GUARD_EDIT=1` for subagent prompts naming them.
  - **Last resort:** `S2_FAIL_CLOSED=0` and kill the sidecar — fails open.
    Don't ship this; it nullifies the gate.

  Every escape path emits an audit row tagged with the override mechanism so you can
  spot abuse later.
  ```

### Success Criteria

#### Automated Verification
- [ ] Each new H2 has a corresponding entry in the ToC.
- [ ] Each named env var resolves: `for v in RC_SHADOW_MODE RC_BYPASS_NEXT RC_ALLOW_GUARD_EDIT RC_ALLOW_SUBAGENT_GUARD_EDIT S2_FAIL_CLOSED; do grep -rq "$v" src/ .envrc || echo MISSING $v; done` prints nothing.
- [ ] `bin/rc --help` (or `python3 -m src.rc_cli --help`) lists every subcommand the README claims (run it, compare).

#### Manual Verification
- [ ] CLI subcommand table matches the actual `--help` output exactly (no aspirational verbs).
- [ ] Shadow-mode promotion flow tested in a clean shell.

### Dependencies
- Requires: Phase 1, Phase 2.

---

## Phase 6: Configuration table — full env-var inventory

### Changes

#### File: `README.md`, `## Configuration` (lines 336–354)
- **What**: replace the 10-row table with the full inventory grouped by area. Drop
  redundant `S2_AIS_THRESHOLD` / `S2_COHERENCE_THRESHOLD` / `S2_RISK_DIM_THRESHOLD`
  duplication (mention once, link to s2_core.py for per-kind ceilings).
- **Code sketch** (table sketch — exact defaults to be confirmed against
  `src/s2_core.py` and `.envrc`):
  ```markdown
  ## Configuration

  ### Sidecar runtime
  | Env var | Default | Purpose |
  |---|---|---|
  | `S2_DEVICE` | `cpu` | `cpu` or `cuda` |
  | `S2_PORT` | `8765` | Sidecar bind port |
  | `S2_URL` | `http://127.0.0.1:$S2_PORT` | Override hook target |
  | `S2_TIMEOUT` | `60` | Hook /score timeout (s) |
  | `S2_FAIL_CLOSED` | `1` | `1` blocks edits when sidecar unreachable |
  | `S2_LOG_LEVEL` | `INFO` | Sidecar log level |
  | `S2_SSM_CHECKPOINT` | `state-spaces/mamba-130m-hf` | Override SSM backbone |
  | `HF_HOME` | `$HOME/.cache/huggingface` | HF cache dir (shared with eval worktrees) |

  ### Source-code thresholds (per-kind ceilings live in `_KIND_THRESHOLDS`, `src/s2_core.py`)
  | Env var | Default | Purpose |
  |---|---|---|
  | `S2_AIS_THRESHOLD` | `0.4` | AIS threshold for `source_code` |
  | `S2_COHERENCE_THRESHOLD` | `1.5` | `coherence_delta` threshold for `source_code` |
  | `S2_RISK_DIM_THRESHOLD` | `0.9` | Per-dim ceiling for `source_code` |

  ### Hook policy posture
  | Env var | Default | Purpose |
  |---|---|---|
  | `RC_SHADOW_MODE` | `1` | Log decisions, do not enforce |
  | `RC_PLAN_BLOCK` | `1` | Plan-guard warnings escalate to hard block |
  | `RC_PLAN_QUALITY` | `0` | Enable plan-quality CGS gate |
  | `RC_MOCK_DETECTOR` | `1` | Reject placeholder code patterns |
  | `RC_LANG_LOCK` | `1` | Reject edits introducing un-fingerprinted languages |
  | `RC_LANG_ALLOW` | _unset_ | Comma-list of language overrides |
  | `RC_LANG_OVERRIDE` | _unset_ | Per-edit language override |
  | `RC_LANG_LOCK_MAX_FILES` | _internal default_ | Audit cap for batch lang audit |
  | `RC_LANG_AUDIT_THRESHOLD` | _internal default_ | PostToolUse audit threshold |

  ### Generative repair head (P2)
  | Env var | Default | Purpose |
  |---|---|---|
  | `RC_REASONER_BACKEND` | `mlx` | `mlx` (Apple) / `llama_cpp` / `scaleway` |
  | `RC_GEN_BUDGET_MS` | `2500` | Generation budget per repair call |

  ### Bypass / kill switches
  | Env var | Default | Purpose |
  |---|---|---|
  | `RC_BYPASS_NEXT` | _unset_ | One-shot bypass; consumed on first guard fire |
  | `RC_ALLOW_GUARD_EDIT` | _unset_ | Allow edits to guarded paths (captured at session boot) |
  | `RC_ALLOW_SUBAGENT_GUARD_EDIT` | _unset_ | Allow Task prompts naming guarded paths |
  | `RC_DRIFT_WARN` / `RC_DRIFT_DENY` / `RC_DRIFT_OVERRIDE` | _unset_ | Coherence-drift policy levers |

  ### Audit log
  | Env var | Default | Purpose |
  |---|---|---|
  | `RC_AUDIT_RETENTION_DAYS` | `90` | Prune older audit shards on session start |
  | `RC_AUDIT_ROOT` | `/tmp/rc-events` | Audit log root |
  | `RC_AUDIT_CAP_BYTES` | _internal default_ | Per-shard size cap |
  | `RC_STATE_DIR` | `/tmp/rc-state` | Session manifest + sentinel state |

  ### Eval / calibration
  | Env var | Default | Purpose |
  |---|---|---|
  | `RC_LIVE` | _unset_ | `1` enables live Scaleway eval tests |
  | `RC_EVAL_STUB_CLAUDE` | _unset_ | Stub Claude in eval harness |
  | `RC_QWEN_KAPPA_SENTINEL` | `0.7` | Min Cohen κ for grounding eval to pass |
  | `RC_TASK_SPEC` | _unset_ | Active task spec path (eval harness) |
  | `RC_ACTIVE_PLAN` | _unset_ | Active plan path for plan-impl coherence gate |

  Defaults are sourced from `.envrc`; per-kind ceilings (`test_code`, `plan_md`,
  `doc_md`, `config`) are not env-overridable yet — see `_KIND_THRESHOLDS` in
  `src/s2_core.py`.
  ```

### Success Criteria

#### Automated Verification
- [ ] Every `RC_*`/`S2_*` env var named in any source file is either documented in this
  table or explicitly excluded as internal:
  ```bash
  comm -23 \
    <(grep -hoE '(RC_[A-Z_]+|S2_[A-Z_]+)' src/ -r .envrc | sort -u) \
    <(grep -oE '`(RC_[A-Z_]+|S2_[A-Z_]+)`' README.md | tr -d '`' | sort -u) \
    > /tmp/undocumented.txt
  test ! -s /tmp/undocumented.txt
  ```
- [ ] No env var documented that does not appear in code: reverse comm.

#### Manual Verification
- [ ] Defaults match `.envrc` exactly. Spot-check `S2_PORT`, `HF_HOME`,
  `RC_SHADOW_MODE`, `RC_REASONER_BACKEND`.

### Dependencies
- Requires: Phase 2 (so the new feature names match), Phase 5 (shadow-mode section
  cross-references this table).

---

## Phase 7: Roadmap — strike shipped phases, list real next steps

### Changes

#### File: `README.md`, `## Roadmap` (lines 476–502)
- **What**: rewrite the roadmap so it matches the **current** open-work state. P0
  through P5 (and P-1, P7) have shipped per the commit log; only the post-P7 surface
  remains.
- **Rationale**: roadmap currently lists shipped work as future, which is the worst
  failure mode for a README — it makes contributors duplicate work.
- **Code sketch**:
  ```markdown
  ## Roadmap

  Shipped since v0 (see `git log --grep='^feat'`):

  - **P-1 — Day-zero ergonomics:** magic-comment escapes, kill switches, `rc` CLI.
  - **P0 — Validation harness:** embedder fitness test, calibration corpus, golden set,
    shadow mode wiring.
  - **P1 — Plan-time SSM scoring + plan→code coherence gate.** Mock-detector heuristics.
  - **P2 — Generative repair head:** Qwen2.5-Coder-1.5B via MLX / Scaleway.
  - **P3 — Calibration:** Mahalanobis over 8-dim risk, per-kind shrinkage,
    Page-Hinkley monthly recal.
  - **P4 — Calibration corpus + golden set + OOD detector + shadow-mode hardening.**
  - **P5 — Sidecar broker + supervisor + grounding eval (Cohen κ ≥ 0.7).**
  - **P7 — Calibration concurrent with shadow mode.**

  Open:

  - **CodeBERTScore plan↔diff** for semantic alignment (deferred from P4).
  - **Subagent loop path** + LLM-judge gate behind `RC_COHERENCE_LLM=1` (deferred from P5).
  - **Iterative repair loop** — today the gate is one-shot allow/block; the next phase
    is closing the loop so Claude re-proposes against the repair hint until pass-or-yield.
  - **CUDA / MLX kernels** for the Mamba forward pass (currently CPU-only; p95 ~5s).
  - **SSE `/score/stream`** + Prometheus textfmt `/metrics`.
  - **Pre-commit variant** so non-Claude editors are also gated.
  - **Linux systemd service** to mirror the macOS launchd supervisor.
  - **Real `slide-mamba` weights** when public.

  Roadmap source of truth: [`thoughts/shared/research/2026-05-05-ssm-co-reasoner-deep-research.md`](thoughts/shared/research/2026-05-05-ssm-co-reasoner-deep-research.md)
  + [`docs/PLAN.md`](docs/PLAN.md).
  ```

### Success Criteria

#### Automated Verification
- [ ] Every "shipped" bullet has a matching commit: `for kw in P-1 P0 P1 P2 P3 P4 P5 P7; do git log --oneline | grep -q "$kw" || echo MISSING $kw; done` prints nothing.

#### Manual Verification
- [ ] Open-work bullets are reasonable next steps as of HEAD; nothing already in
  `git log` is listed as open.

### Dependencies
- Requires: Phase 2 (shipped features must already be listed in "What you get"
  before they can be removed from the roadmap).

---

## Phase 8: FAQ + How-it-works numerical refresh + small fact fixes

### Changes

#### File: `README.md`, `## How it works under the hood` (lines 264–284)
- **What**: append shadow-mode + supervisor mention to step 8; keep the 1–8 numbered
  flow.
- **Code sketch** (insertion, after current step 8):
  ```markdown
  9. In **shadow mode** (default), steps 7–8 still execute but the hook always returns
     exit 0; the would-be decision is logged for offline review.
  ```

#### File: `README.md`, `## FAQ / troubleshooting` (lines 416–445)
- **What**:
  - Replace the named-commit advice (`predates 2345fba` / `predates 2873c82`) with
    version-agnostic phrasing (`restart the sidecar; if it persists, run rc status`).
  - Add a "First time, am I getting blocked?" Q referencing shadow mode default.
  - Add a "Sidecar keeps dying" Q referencing the supervisor.
  - Add a "Cato VPN / corporate TLS" Q referencing the auto-built CA bundle.
- **Code sketch** (additions only):
  ```markdown
  **Q: First time running, am I getting blocked?**
  A: No. The gate ships in shadow mode (`RC_SHADOW_MODE=1`). Every Edit/Write is
  scored and the decision is logged to `/tmp/rc-events/`, but the hook always returns
  exit 0. Promote to enforcement after a few sessions of observation.

  **Q: Sidecar keeps dying mid-session.**
  A: Install the launchd supervisor:
  `bash scripts/install-supervisor-launchagent.sh`. KeepAlive will relaunch it.

  **Q: I'm on a corporate VPN (Cato / Zscaler / etc.) and `pip install` /
  `huggingface-cli` fail with "self-signed certificate in certificate chain".**
  A: `direnv reload` — the repo's `.envrc` builds
  `~/.cache/reasoning-core/ca-bundle.pem` from `certifi` + your system Cato root and
  exports `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE`. Only macOS today
  (uses `security find-certificate`); Linux users add their root manually.
  ```
  And **rewrite** the existing "hook keeps blocking obviously-fine edits" answer:
  ```markdown
  **Q: The hook keeps blocking obviously-fine edits.**
  A: Check the top risk contributors in the block message. If a single dim sits at
  `1.00` on a tiny edit, restart the sidecar (`bash scripts/start-sidecar.sh`) — old
  processes can hold pre-refactor scoring code. If it persists, run `rc status` and
  open an issue with the block payload.
  ```

#### File: `README.md`, `## Run it locally` (lines 199–261)
- **What**:
  - Header says "5 steps" but lists 6. Fix to "6 steps" or move the optional global
    promote to its own subsection outside the numbered list.
  - Step 4 mentions `HF_HOME` cache pin "project-local" — reality is `$HOME/.cache`.
    Update or drop the parenthetical.
  - Add a sentence in step 1 about `export PATH="$PWD/bin:$PATH"` so the `rc` shim
    works.
- **Code sketch**:
  ```markdown
  ## Run it locally (6 steps, no global side-effects)
  ```
  And in step 4:
  ```markdown
  HuggingFace cache shared with sibling repos at `$HOME/.cache/huggingface` (so
  weights aren't downloaded twice).
  ```

### Success Criteria

#### Automated Verification
- [ ] No commit hash appears in README except in code blocks: `grep -nE '\b[0-9a-f]{7,40}\b' README.md` returns only matches inside fenced code blocks.

#### Manual Verification
- [ ] All FAQ Qs have answers grounded in actual env vars / scripts.
- [ ] Step count in section header matches step count in section body.

### Dependencies
- Requires: Phase 5 (shadow-mode + supervisor sections must exist for FAQ to link).

---

## Phase 9: Verification pass

### Changes
None — verification only.

### Success Criteria

#### Automated Verification
- [ ] All Phase 1–8 automated checks pass in a single CI-style run.
- [ ] `markdownlint README.md` (or equivalent) clean.
- [ ] `python3 -c "import re; r=open('README.md').read(); assert 'TODO' not in r and 'TBD' not in r"` passes.
- [ ] Internal anchors all resolve (Phase 1 check, re-run).
- [ ] Every file/dir in the layout block exists (Phase 4 check, re-run).

#### Manual Verification
- [ ] Read top-to-bottom in GitHub preview. No statement contradicts an observable repo
  fact.
- [ ] A first-time user could `git clone && follow README` and end up with a working
  shadow-mode gate.

### Dependencies
- Requires: all prior phases.

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| New CLI/config wording lands ahead of the actual feature shape | Med | Med | Every Phase 5/6 sketch is flagged "verify against `src/rc_cli.py` / `.envrc`" before publishing |
| README grows past 800 lines and becomes unreadable | Med | Low | Push detail into `docs/` and link from README; new sections favor tables over prose |
| Documenting `RC_BYPASS_NEXT` etc. teaches users to bypass the gate | Low | Med | Place under "Shadow mode & kill switches" with explicit guidance that escapes are audit-logged |
| Future commits invalidate the project layout block | High (over time) | Low | The Phase 4 automated check should be in CI so layout drift is caught at PR time |
| Roadmap "shipped" claims are wrong because some P5 sub-items are partial | Med | Med | Phase 7 verification step grep-confirms each "shipped" against `git log` |

## Rollback Strategy
- Single file change. `git checkout -- README.md` reverts.
- Each phase is a self-contained edit block — partial application leaves a coherent
  README.

## Open Questions / Uncertainties (flagged, not assumed)
1. Are `pre_compact_guard.py`, `session_start_manifest.py`, `session_resume_inject.py`,
   `post_batch_lang_audit.py` actually wired in `.claude/settings.json`? Phase 3 needs
   confirmation; if not wired, hook-layer table should add a "shipped, not yet wired"
   footnote rather than imply they fire today.
2. Exact subcommand surface of `bin/rc` — Phase 5a depends on reading `src/rc_cli.py`.
3. Default value of `RC_AUDIT_ROOT` — `.envrc` does not set it; Phase 6 should check
   `src/hooks/audit_log.py` for the actual default.
4. `RC_ACTIVE_PLAN` may not be implemented yet (it appears in `.envrc` comments only).
   Phase 6 must verify before listing.
5. The unstaged `.envrc` diff and untracked `eval/calibrated/` dir — should they be
   committed before this README update lands? README documenting them while they sit
   uncommitted creates a transient inconsistency. **Recommendation:** commit `.envrc`
   first; leave `eval/calibrated/` decision to the user.

## File Ownership Summary

| File | Phase | Change Type |
|---|---|---|
| `README.md` (lines 1–46, header/TL;DR/ToC) | 1 | Modify |
| `README.md` (lines 176–197, "What you get") | 2 | Modify |
| `README.md` (lines 286–298, "Hook layers") | 3 | Modify |
| `README.md` (lines 376–412, "Project layout") | 4 | Modify |
| `README.md` (new H2s between hook layers and scoring math) | 5 | Insert |
| `README.md` (lines 336–354, "Configuration") | 6 | Modify |
| `README.md` (lines 476–502, "Roadmap") | 7 | Modify |
| `README.md` (lines 199–261 + 264–284 + 416–445, quickstart/how/FAQ) | 8 | Modify |
| `README.md` (whole file pass) | 9 | Verify-only |

No other files modified.
