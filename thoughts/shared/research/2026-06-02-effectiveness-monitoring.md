---
date: 2026-06-02
commit: 5bb9fce + post-restart patches
branch: main
tags: [effectiveness, monitoring, verdict, gaps]
status: complete
related:
  - 2026-06-02-community-pain-points.md
  - 2026-06-02-pain-feature-mapping.md
  - 2026-06-01-reasoning-core-1000pct-improvements.md
---

# Effectiveness verdict: do today's gates address the community pains?

## Setup

Today's restart enabled three new capabilities:
1. `validate_imports` MCP tool registered in `mcp_reasoner.py` (closes pain 6).
2. `RC_PROJECT_INDEX=1` promoted to plist default (closes pain 2 partially).
3. README marketing leads with "loopback only, zero telemetry" (closes pain 5
   discoverability gap).

Tool: `scripts/monitor-effectiveness.py` reads
`~/.local/share/reasoning-core/events/<YYYY-MM-DD>/*.jsonl` and bins events
into the 9 community pain categories.

## Corpus

| Metric | Value |
|---|---|
| Events read (7-day window) | 1,832 |
| Days with data | 1 (2026-06-02 only) |
| Anon sessions today | 41 |
| Decision breakdown | allowed=1095, fail-open=282, warn=164, audit_only=110, blocked=83, injected=74 |

Note: the 7-day window currently contains only today's data because the audit
log directory was rotated on 2026-06-02. Future runs after another workday
will give a real time series.

## Verdict per pain category

| # | Pain | Total fires | Today | Verdict | Reading |
|---|---|---|---|---|---|
| 1 | Scope creep | 279 | 279 | **active** | `plan_grounding` firing on ~15% of edits |
| 2 | Pattern blindness | 0 | 0 | **silent** | `RC_PROJECT_INDEX=1` only live since restart; Phase-2 dims need a warm session baseline |
| 3 | Spec drift | 0 | 0 | **silent** | `plan_quality` (CGS) hasn't fired — either every plan passed, or the gate isn't wired into the live writes |
| 4 | Token waste | 245 | 245 | **active** | hard_cap_exceeded=234 / gen_timeout=11; the *gate* is catching waste, but high cap-firing rate suggests a sidecar-perf issue, not pure agent-waste |
| 5 | Local-only violations | 0 | 0 | **expected silent** | architecture prevents non-loopback; 0 fires is the correct value |
| 6 | Hallucinated APIs | 0 | 0 | **too new to tell** | `validate_imports` registered today, no MCP calls into it yet |
| 7 | Runtime enforcement | 83 | 83 | **active** | 83 hard blocks today — but 65% are guard self-protection, not agent-rule blocks (see below) |
| 8 | Over-engineering | 0 | 0 | **silent** | LOC budget (1200) not yet exceeded by any plan write today |
| 9 | Repo conventions | 0 | 0 | **silent** | `RC_LANG_LOCK` + `forbid_pattern` haven't matched; small daily edit volume on this repo |

## Top block reasons today (decision=blocked)

| Count | Reason | Category |
|---|---|---|
| 26 | shell command targets the guard or sidecar | self-protection |
| 22 | guard_file_locked | self-protection |
| 11 | shell-level source write detected | self-protection |
| 6 | shell write outside declared language family | self-protection |
| 4 | sidecar_unavailable_fail_closed | infrastructure |
| 4 | **regression_detected** | reasoning-quality |
| 2 | **plan_impl_drift** | reasoning-quality |

**6 of 83 blocks (7%) are reasoning-quality** — the rest are self-protective
shell barriers and infra failures. This matches the audit finding from
`2026-06-01-reasoning-core-1000pct-improvements.md` §1 (65% self-protection,
21% reasoning-quality on the 25-day historical corpus). New gates haven't
shifted the ratio yet.

## Top non-block reasons today

| Count | Reason |
|---|---|
| 234 | `sidecar_unavailable:hard_cap_exceeded:1500ms` (warn / fail-open) |
| 166 | `plan_impl_drift` |
| 110 | `no_plan_md` |
| 68 | `rc_best_effort_spec=1` |
| 41 | `sidecar_unavailable: Connection refused` |
| 18 | `ok` |

Two important signals:
- **234 hard_cap_exceeded events** out of 1832 (12.8%) — the 1500 ms cap is
  too tight for the live sidecar's tail. The `.envrc` was bumped to 3000 ms
  on 2026-06-01 (commit `54eed96`) but the runtime is still configured
  at 1500 ms — the bump didn't take effect because `.envrc` is not read by
  launchd-spawned children. **This is the same `.envrc`-vs-plist gap that bit
  us on the memory cap earlier today.**
- **41 Connection refused** events — the sidecar was offline during a window
  before today's reinstall. Now resolved (broker healthy since restart).

## Does the answer change for each pain?

### Pains the gates ARE addressing (evidence: live event counts)

- **Pain 1 (scope creep):** `plan_grounding` fired 279 times today across 41
  sessions. Direct hit on the dominant community complaint.
- **Pain 4 (token waste):** the hard cap is firing — but more than half the
  firings appear to be *sidecar slowness*, not *agent waste*. The gate is
  protecting the agent's wall-clock; whether it's saving tokens depends on
  whether the agent retries (currently fail-open, so it doesn't).
- **Pain 5 (local-only):** zero violations because the architecture
  forbids them. README update means future users will *know* this.
- **Pain 7 (CLAUDE.md runtime enforcement):** the primitive works (83 hard
  blocks today) but the 7% reasoning-quality ratio means most of the value
  is in self-protection, not in catching agent rule-breaks.

### Pains the gates HAVE NOT yet addressed (evidence: 0 fires)

- **Pain 2 (pattern blindness):** `RC_PROJECT_INDEX=1` is now live, but Phase-2
  dims (`session_centroid_drift`, `project_fan_in`, `project_coupling`) need
  per-session baseline samples to fire. They will start producing signal after
  several edit rounds in the same session. **Need 1 more day to verify.**
- **Pain 3 (spec drift):** `plan_quality.composite_gate_score` (CGS) didn't
  fire on any plan write today. Either every plan crossed the threshold (good
  news), or the gate isn't being triggered (bad news). Need to instrument a
  CGS-evaluated-on-pass event to distinguish.
- **Pain 6 (hallucinated APIs):** `validate_imports` registered but no MCP
  client called it yet. The tool is *invoked by the agent*, not by a hook —
  so the agent has to know to call it. Discoverability problem.
- **Pain 8 (over-engineering):** LOC budget 1200 not exceeded today. Sample
  size too small.
- **Pain 9 (repo conventions):** rules.yaml has 5 rules; none matched today.

## What needs another go

### High priority

1. **Fix the `S2_HARD_CAP_MS=1500` → `3000` gap in the plist.** The bump
   landed in `.envrc` (commit `54eed96`) but launchd-spawned children read
   the plist, not `.envrc`. 234 cap-exceeded fires today is the smoking
   gun. → Add `S2_HARD_CAP_MS=3000` to the plist `EnvironmentVariables`.
2. **Make `validate_imports` auto-callable.** Currently the agent has to
   know to invoke it. The hook layer (`pre_edit_guard.py`) could call it
   automatically for Python/TS Edits with new imports. → New gate
   `gate_import_existence` in `_dispatch.py`. (Hook code is guarded → operator
   patch.)
3. **Add a CGS-pass event.** Today's data can't distinguish "every plan is
   high quality" from "the gate didn't run". → Emit `signal_source=plan_quality
   decision=ok` when CGS ≥ 0.75.

### Medium priority

4. **Wait 1–2 days then re-run the monitor.** Phase-2 dims will need warm
   session baselines to fire. If they still show zero after 3+ active
   sessions, the dims are degenerate as the 25-day audit suggested.
5. **Surface `unknown_module` suggestions in the agent context.** Today the
   `validate_imports` response includes `suggestion: [near_matches]` but no
   one consumes it. The agent only sees it if it explicitly calls the tool.

### Low priority

6. **Repo-convention rule learning.** Currently `rules.yaml` is hand-authored
   (5 rules total). Could extract patterns from the corpus to auto-generate.
   Out of scope for this iteration.

## Reproducibility

```bash
.venv/bin/python scripts/monitor-effectiveness.py 7
```

Run this after a workday of normal use. Compare today's verdict table to the
new run.

## Honest takeaway

**3 of 9 pains have live evidence of the gate working today** (scope creep,
token waste, runtime enforcement). **3 pains are too-new-to-tell** (pattern
blindness, hallucinated APIs, over-engineering). **3 pains have insufficient
volume to verify** (spec drift, local-only is by-design-silent, repo
conventions). The single highest-value follow-up is fixing the
`S2_HARD_CAP_MS=1500` gap that's causing 12.8% of edits to fall back to
symbolic-only — it's the same `.envrc`-vs-plist class of bug we already hit
once today on memory caps.

We need **one more workday of evidence** before claiming the new gates fix
the silent categories. The infrastructure to observe is in place.
