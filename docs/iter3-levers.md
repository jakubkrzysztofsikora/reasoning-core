# Iter-3 Levers

> **Update 2026-06-01 — defaults flipped on.** Per audit
> [`thoughts/shared/research/2026-06-01-reasoning-core-1000pct-improvements.md`](../thoughts/shared/research/2026-06-01-reasoning-core-1000pct-improvements.md)
> §B4, `RC_BEST_EFFORT_SPEC` and `RC_PLAN_GROUNDING` now default `=1` in
> `.envrc:133,141` from this commit forward. The iter-3 symmetry
> guarantee for historical replays is preserved by the pinned
> `docs/iter3-frozen-artifacts/eval-setups-A/envrc.txt` which still
> exports `=0`; replay against that pinned `.envrc`, NOT the current
> default, to keep A vs B apples-to-apples.

> **Original symmetry guarantee (pre-2026-06-01, historical).** All three levers default OFF in `reasoning-core/.envrc`. Setup A's exported environment is bit-identical to iter-2 v3 — the iter-3 lever vars resolve to `0` (no overlay, no gate). Only Setup B opts in by exporting `=1` in its own `eval-setups/B/.envrc`. Reasoning-core ships levers; the eval team turns them on.

## Quick Start (adopter — 5 min)

You want the iter-3 levers active in your own Claude Code project (not the iter-3 eval). Four steps:

**0. Verify wiring before enabling levers** (run from the reasoning-core repo root):

```bash
./scripts/iter3-preflight.sh
# Must print "RESULT: GO" or "RESULT: GO with caveats" before proceeding.
# RESULT: NO-GO means a wiring check failed — do NOT enable levers, fix
# the failing check first (the script names which one).
```

The preflight runs eight wiring checks (`session_start_best_effort.py` injects audit receipts, `gate_plan_grounding` fires on drift, audit_only fires on missing PLAN.md, A-vs-B receipt symmetry holds, etc.) plus three operational dependency probes (Docker / scw / sidecar /health). The same checks run automatically in CI via `tests/test_iter3_wiring_smoke.py`.

**1. Enable the levers in your project's `.envrc`** (or shell rc):

```bash
export RC_BEST_EFFORT_SPEC=1   # SessionStart overlay v2 (round-6): see lever spec table below for full text.
export RC_PLAN_GROUNDING=1      # warn on edits drifting from PLAN.md (audit-only, not visible to agent)
```

**2. Register both hooks in `.claude/settings.local.json`**:

`RC_BEST_EFFORT_SPEC` needs `session_start_best_effort.py` on `SessionStart`. `RC_PLAN_GROUNDING` needs `pre_edit_guard.py` on `PreToolUse` for Edit/Write/MultiEdit.

If your `.claude/settings.local.json` is empty (or doesn't exist), paste this complete file:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${RC_REPO:-/path/to/reasoning-core}/src/hooks/session_start_best_effort.py",
            "timeout": 10000
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${RC_REPO:-/path/to/reasoning-core}/src/hooks/pre_edit_guard.py",
            "timeout": 15000
          }
        ]
      }
    ]
  }
}
```

If your `.claude/settings.local.json` already has `hooks.SessionStart` or `hooks.PreToolUse` arrays, **merge by appending** to the existing arrays — don't replace them, or you will lose your other hooks. Claude Code's `hooks.<event>` is a list of matcher-groups; each matcher-group has its own nested `hooks` array. Add a new matcher-group entry alongside whatever's already there.

Replace `/path/to/reasoning-core` with your clone path, OR `export RC_REPO=...` in your `.envrc`.

> If you set `RC_BEST_EFFORT_SPEC=1` or `RC_PLAN_GROUNDING=1` but skip this step, `direnv allow` will print a loud WARN at shell load — the self-check at the bottom of `reasoning-core/.envrc` greps your `.claude/settings.local.json` for the required hook commands and surfaces the gap before any Claude Code session starts.

**3. Verify both levers fired**. Open a new Claude Code session, make one edit, then:

```bash
# Confirm RC_BEST_EFFORT_SPEC fired (look for an "injected" receipt):
jq 'select(.signal_source=="best_effort_spec" and .decision=="injected")' \
   ~/.local/share/reasoning-core/events/$(date +%Y-%m-%d)/*.jsonl | head

# Confirm RC_PLAN_GROUNDING fired on a drift edit (only if you edited a file
# NOT named in your project's PLAN.md):
jq 'select(.signal_source=="plan_grounding" and .decision=="warn")' \
   ~/.local/share/reasoning-core/events/$(date +%Y-%m-%d)/*.jsonl | head
```

Expected `RC_PLAN_GROUNDING` advisory on stderr when edits drift from plan:
```
[reasoning-core] WARN: edit drifts from plan — src/foo.py not in PLAN.md (12 files in plan)
```

If you set `RC_BEST_EFFORT_SPEC=1` but the SessionStart hook isn't registered, `direnv allow` will print a loud WARN at shell load — catches the most common wiring mistake before any session starts.

**Defaults are safe.** All levers are unset/`=0` out of the box. Setting them to `=1` adds advisory signals; only `RC_PLAN_GROUNDING=2` (block tier) can refuse an edit, and that mode is off by default.

---

**Reviewer SHA-pin verification.** `/Users/jakubsikora/eval-setups/` is not a git repository, so SHA pinning by git revision is not available. Iter-3 freeze captures expected SHAs in [`docs/iter3-frozen-manifest.json`](iter3-frozen-manifest.json) (this repo). Reviewer reproduces via:

```bash
# 1. Setup A unchanged from iter-2 v3 (symmetry guarantee).
shasum -a 256 /Users/jakubsikora/eval-setups/A/.envrc
shasum -a 256 /Users/jakubsikora/eval-setups/A/settings.local.json
# Both MUST match docs/iter3-frozen-manifest.json setup_a.*_sha256.

# 2. Setup B hook actually registered (catches env-var theatre).
grep -c session_start_best_effort /Users/jakubsikora/eval-setups/B/settings.local.json
# MUST equal 1.
```

**Audit-log verification** (run after iter-3 sweep collects).

Audit-log location depends on whether `RC_AUDIT_ROOT` is set:

- **Default path** (no override, hook fires from a normal Claude Code session):
  `~/.local/share/reasoning-core/events/<DATE>/<session_id>.jsonl`
- **Eval-team-redirected path** (eval framework typically redirects per-run for isolation):
  `$RC_AUDIT_ROOT/<DATE>/<session_id>.jsonl` where `RC_AUDIT_ROOT` is set per run by the eval spawner. Common pattern: `RC_AUDIT_ROOT=<eval-folder>/runs/<setup>/<task>/run-<N>/audit/`.

A reviewer must check **both** locations OR have the eval team document which root was used. Iter-3 freeze recommendation: eval team adds `audit_root_per_run` to its frozen manifest so reviewers know exactly where to look.

```bash
# Aggregate over BOTH default and any RC_AUDIT_ROOT-redirected location.
# Substitute <DATE> with the iter-3 sweep date(s); substitute <RC_AUDIT_ROOT>
# with the eval team's per-run root if they overrode it.

DEFAULT_ROOT="$HOME/.local/share/reasoning-core/events"
EVAL_ROOTS="<RC_AUDIT_ROOT_GLOB_PATTERN>"  # e.g. /path/to/iter3-eval/runs/B/*/run-*/audit/

# Setup B sessions: must contain best_effort_spec receipts with decision=injected.
jq -r 'select(.signal_source=="best_effort_spec" and .decision=="injected") | .session_id' \
   "$DEFAULT_ROOT"/<DATE>/*.jsonl $EVAL_ROOTS/<DATE>/*.jsonl 2>/dev/null \
   | sort -u | wc -l
# MUST equal the count of distinct Setup B sessions in the iter-3 window.

# Setup A sessions: must yield ZERO injected receipts. Will have decision="skipped"
# receipts IF the hook is also registered for Setup A (recommended for falsifiability
# — see iter-4 ablation #4 below).
jq -r 'select(.signal_source=="best_effort_spec" and .decision=="injected") | .session_id' \
   <Setup-A-audit-paths> 2>/dev/null | sort -u | wc -l
# MUST be 0.
```

Reasoning-core ships three default-off levers in iter-3. The eval team independently decides whether to enable them via their own `eval-setups/B/.envrc` and `settings.local.json`.

Plan: [`thoughts/shared/plans/2026-05-07-iter3-decisive-win.md`](../thoughts/shared/plans/2026-05-07-iter3-decisive-win.md)
Reviews: LLM-scientist + agentic-AI engineer + AI-newsletter tech reviewer (RC-only scope), all PROCEED with revisions; full Phase-1-5 validation pass folded in.

## Lever spec

| Env var | Default | Modes | Effect | Implementation |
|---------|---------|-------|--------|----------------|
| `RC_BEST_EFFORT_SPEC` | unset (off) | `1` = on; anything else = off | When on, `session_start_best_effort.py` emits a `hookSpecificOutput.additionalContext` JSON envelope at SessionStart with the iter-3 overlay v2: *"Never ship a DIVERGENCES.md alone — unless the contract requires no production change (e.g. rename already applied upstream, no behavioral change required), in which case ship a one-line PLAN.md stating why no edit is appropriate and produce no other artifact."* (Round-6 added the no-op carve-out so T8-style "cutover is no-op" tasks aren't pressured to invent fake edits.) Authoritative source: [`src/hooks/session_start_best_effort.py`](../src/hooks/session_start_best_effort.py) `_OVERLAY` constant. Substitution-recipe variants (e.g. "...and emit a compilable stub against the closest available contract") deferred to iter-4 under a separate env var; iter-4 must also add a `RC_BEST_EFFORT_SPEC=3` arm (license-removal WITHOUT carve-out) to isolate the carve-out's contribution. | [`src/hooks/session_start_best_effort.py`](../src/hooks/session_start_best_effort.py) |
| `RC_PLAN_GROUNDING` | unset (`0`) | `0` = off; `1` = warn (audit-only); `2` = hard block | At `pre_edit_guard` time, cross-references the edit's `file_path` against paths mentioned in the run's `PLAN.md`. Mode 1 emits stderr advisory + audit event tagged `signal_source=plan_grounding decision=warn`. Mode 2 records audit block + exit 2. Audit-visible signal is NOT shown to the agent — neutralizes the path-stuffing failure mode. | [`src/hooks/_dispatch.py:gate_plan_grounding`](../src/hooks/_dispatch.py) |
| `RC_RUN_DIR` | unset | (path) | Optional override for PLAN.md resolution. Precedence: `RC_RUN_DIR` > `CLAUDE_PROJECT_DIR` > `cwd`. | [`src/hooks/_dispatch.py:_resolve_plan_path`](../src/hooks/_dispatch.py) |

## Reproducibility

To reproduce iter-3 Setup B exactly as evaluated, the eval team's `eval-setups/B/.envrc` should export:

```bash
# RC_BEST_EFFORT_SPEC=1   # ENABLE: SessionStart overlay nudges agent away from divergence-only bailout
# RC_PLAN_GROUNDING=1     # ENABLE: warn-only plan-impl drift signal (audit-visible only)
```

Setup A keeps both unset (vanilla baseline).

The reasoning-core git SHA at iter-3 freeze pins the lever code; eval team's frozen manifest pins which env vars were exported. Together these define the iter-3 measurement.

## SessionStart hook registration

Eval team registers the overlay hook in their own `settings.local.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${RC_REPO:-/Users/jakubsikora/Repos/personal/reasoning-core}/src/hooks/session_start_best_effort.py",
            "timeout": 10000
          }
        ]
      }
    ]
  }
}
```

If `RC_BEST_EFFORT_SPEC` is unset, the hook exits 0 with no stdout — wiring is a no-op when the lever is off.

## Iter-4 falsifying ablations

Per the LLM-scientist + newsletter v3 reviews, single-factor variants the next iteration should run:

1. **Overlay text minimal vs full substitution recipe.** Iter-3 ships only the license-removal sentence (*"Never ship a DIVERGENCES.md alone."*). Iter-4 must test a substitution-recipe variant under a separate env var (e.g. `RC_BEST_EFFORT_RECIPE=1`) appending *"…and emit a compilable stub against the closest available contract"* — running both lets us attribute any T1/P0/E1 correctness-gate movement to license-removal-alone vs the full recipe.
2. **No-op placebo overlay** (`RC_BEST_EFFORT_SPEC=2` reserved). Inject a benign content-equivalent overlay (*"Follow the task contract."*) of identical length and SessionStart timing. Separates "any system-context injection" Hawthorne effect from "this specific instruction".
3. **`RC_PLAN_GROUNDING` mode 1 vs mode 2 vs unset.** Three-way ablation tells whether the warn-only audit signal alone moves plan-impl jaccard, or whether agent-visible blocking is needed.
4. **`RC_BEST_EFFORT_SPEC` on vs off, plan-grounding off in both.** Isolates the SessionStart overlay's contribution to T1/P0/E1 correctness-gate pass rates from plan-grounding's contribution to jaccard.
5. **Corpus benchmark hardening.** Second blind annotator on the existing 25 edits + Cohen's κ; one held-out plan from outside the iter-2 sweep author labeled by the second annotator only; bootstrap (precision, recall) CIs reported alongside the point estimates.

## Out-of-scope (NOT shipped in iter-3)

Reasoning-core did not modify any of the following — these are owned by the eval team:

- Eval prompts, rubric, judge models, honesty bonus design, α gate, T7 rotation, T9 reference-review embedding, cache cost reporting, grade-coverage gate
- Docker provisioning for T1 / E1 cross-system tasks
- `eval-setups/B/.envrc` or `settings.local.json` (eval team owns; the env-var values listed above are *recommendations*, not commits in this repo)
- `~/.claude/projects/.../memory/*.md` (Claude Code user-memory layer)
- Setup A — receives zero changes in iter-3 from the reasoning-core side

## Standalone benchmark

The corpus benchmark at [`tests/test_plan_grounding_corpus.py`](../tests/test_plan_grounding_corpus.py) runs the gate against five frozen iter-2 plans (`tests/fixtures/plans/{P0,T2,T5,T8,T9}/`) with hand-labeled `expected_in_plan` ground truth and asserts `precision ≥ 0.90`, `recall ≥ 0.80`.

Current corpus performance (5 fixtures, 25 labeled edits): **precision=1.000, recall=0.800**. With n=25 the Wilson 95% CI on recall spans approximately [0.58, 0.92] — the 0.80 floor sits inside the CI, so one fixture relabel could flip pass↔fail. Treat this as a **CI regression canary for the path-extraction regex**, not a generalizable benchmark claim.

**Closure-of-evaluator caveat (scientist + newsletter review)**: the same author wrote the regex (`src/hooks/_plan_paths.py`), the five fixture plans, the `expected_in_plan` ground-truth labels, and the precision/recall floors. To upgrade this from a regression canary to a publishable benchmark, iter-4 needs (a) a second annotator blind to the regex, (b) Cohen's κ on the labels, (c) at least one held-out plan from a different author/project, and (d) bootstrap CIs on (precision, recall).

E1 was excluded from the fixture set: its 1.6 KB plan has too few path references for path-extraction stress; T7 was excluded because both arms ceiling-saturated in iter-2 (no signal). The five chosen fixtures span three distinct plan styles (smoke / spec-only / cross-workload refactor).

**SHA pin for citations**: when citing the 1.000 / 0.800 numbers, pin `src/hooks/_plan_paths.py` to its SHA at iter-3 freeze (currently `df1043b8b3b8bc0ead972684fd34c02f8148fa1c1ec98f3e5fa867ca7d144cb7`). A regex tweak post-freeze will silently rebase the floor.

## Confounds and known limits

Documenting up-front so iter-4 measurement design accounts for them:

- **`plan_refs_count` is not the coupling metric.** The audit-log field is plan length × regex coverage × the agent's path-citation verbosity, not plan-impl coupling. To attribute coupling, the eval-side aggregator must compute `(edits_in_plan / total_edits)` per run, not aggregate `plan_refs_count` directly.
- **No iter-3 placebo.** Iter-3 ships the active overlay only. Iter-4 should add a `RC_BEST_EFFORT_SPEC=2` mode that injects a content-equivalent benign overlay ("Follow the task contract.") to separate "any SessionStart context injection" from "this specific instruction".
- **`_plan_paths.distinct_file_paths` is shared by both `pre_plan_guard` and `gate_plan_grounding`.** A regex tweak intended to fix grounding recall will silently shift `pre_plan_guard`'s distinct-file count threshold. Iter-4 ablations must hold this constant or update both gates' thresholds together.

## File map

| File | Purpose |
|------|---------|
| `src/hooks/_plan_paths.py` | Single-source-of-truth path extractor (`distinct_file_paths`, `extract_files_with_loc`) |
| `src/hooks/session_start_best_effort.py` | SessionStart-hook overlay (env-gated by `RC_BEST_EFFORT_SPEC`) |
| `src/hooks/_dispatch.py` | New `gate_plan_grounding` + `_resolve_plan_path` |
| `src/hooks/pre_edit_guard.py` | Hand-wires the gate before `_extract_changes` (line 444) |
| `src/hooks/pre_plan_guard.py` | Refactored to consume `_plan_paths` (no behavior change) |
| `tests/test_plan_paths.py` | Helper unit + parity tests |
| `tests/test_session_start_best_effort.py` | JSON envelope shape + env-gating |
| `tests/test_plan_grounding.py` | Gate decision matrix |
| `tests/test_hook_block.py` | Three end-to-end integration cases |
| `tests/test_plan_grounding_corpus.py` | Standalone precision/recall benchmark |
| `tests/fixtures/plans/<task>/` | Five frozen iter-2 PLAN.md + edits.jsonl pairs |
| `tests/test_iter3_wiring_smoke.py` | Round-6 self-contained CI smoke (5 wiring tests + Docker probe) — independent of eval framework |
| `scripts/iter3-preflight.sh` | Round-6 operator-runnable preflight (8 wiring checks + 3 dependency probes) — runs before any iter-3 sweep kickoff |
