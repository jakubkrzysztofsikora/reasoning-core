# Usage

Day-to-day operating manual: the `rc` CLI, hook layers, rule engine, diff
audit, shadow mode, bypass switches, FAQ.

---

## `rc` CLI

Put `bin/` on PATH (`export PATH="$RC_REPO/bin:$PATH"`).

| Command | Purpose |
|---|---|
| `rc status` | Sidecar health + threshold posture (shadow mode? fail-closed? per-kind ceilings? active embedder backend? RC_MODE?) |
| `rc explain <decision-id>` | Full audit row for a single decision |
| `rc bypass-next` | Arm a single-shot bypass for the next Edit/Write — consumed on first guard fire |
| `rc confirm-next` | Record operator agreement with the next block — emits `operator_confirmed` |
| `rc skip-file <path>` | Add `<path>` to the per-session skip list (logged) |
| `rc unskip-file <path>` | Remove `<path>` from the skip list |
| `rc enable-enforcement` | First-run wizard: scaffold `PLAN.md` from `README.md`, flip `RC_MODE=copilot` |
| `rc reasoning-efficiency [--days N]` | Audit-log composite north-star metric (drift caught per gate-second) |
| `rc override-survival [--days N]` | Fraction of operator overrides that survived in the codebase |
| `rc audit-history [-n N] [--json] [--reasons]` | Mine recent git history and label commits for Phase-4 calibration feedback |

`rc enable-enforcement` runs the first-run wizard: it scaffolds `PLAN.md` from
`README.md`, shows the 48-hour shadow report checklist, and flips the repo to
`RC_MODE=copilot`.

`rc audit-history` labels the last `n` commits as positive/negative using a
48-hour follow-up-fix heuristic. Use `--json` for machine-readable output and
`--reasons` to print why each commit was labelled.

---

## Hook layers

| # | Hook | Event / matcher | Purpose |
|---|---|---|---|
| L1 | `pre_bash_guard.py` | PreToolUse / `Bash` | Blocks shell-level source writes (heredoc, sed, tee), kills against the sidecar, env tampering, edits to guard files |
| L2 | `pre_edit_guard.py` | PreToolUse / `Edit\|Write\|MultiEdit` | Neural scoring; per-kind threshold dispatch; mock-detector; OOD detector; language-lock; drift policy; guard-file lock; rule-engine dispatch when `RC_RULE_ENGINE=1` |
| L3 | `pre_plan_guard.py` | PreToolUse / `Write` (and Plan-shaped writes to `**/plans/**.md`) | Plan-time heuristics + plan-quality CGS (kNN novelty, section drift, plan→impl coherence) |
| L4 | `pre_task_guard.py` | PreToolUse / `Task` | Regex screen on subagent prompts mentioning guarded paths with mutation verbs |
| L5 | `post_bash_revive.py` | PostToolUse / `Bash` | Re-spawns sidecar when `/health` stops responding after a kill-shaped command |
| L6 | `post_batch_lang_audit.py` | PostToolUse / `Edit\|Write\|MultiEdit` | After-the-fact language-fingerprint audit; logs drift events when foreign-language ratio crosses `RC_LANG_AUDIT_THRESHOLD` |
| L7 | `pre_compact_guard.py` | PreCompact | Captures pre-compaction state so post-compact context can be reconciled |
| L8 | `session_start_manifest.py` | SessionStart | Snapshots `RC_*` env, repo SHA, language fingerprint, active task spec; prevents mid-session env tampering |
| L9 | `session_resume_inject.py` | SessionStart (resume) + UserPromptSubmit | Re-injects pinned env from the prior session manifest into the resumed shell |
| L10 | `post_assistant_diff_audit.py` | Stop (opt-in, `RC_DIFF_AUDIT=1`) | Scans the last assistant diff in transcript via `validate_unified_diff`; injects advisory + best-effort repaired patch |

L1–L9 are wired in the reasoning-core repo's own
[`.claude/settings.json`](../.claude/settings.json) and in the per-repo
[`.claude/settings.local.json`](../install.sh) that `install.sh` generates
for any target repo. L10 is opt-in. Every fire emits an audit row to
`~/.local/share/reasoning-core/events/` (schema v3).

Note: hook arrays in `~/.claude/settings.json`, the repo's
`.claude/settings.json`, and a per-repo `.claude/settings.local.json` merge
**additively**. Register the same hook in two of those and it runs twice
per edit. Pick one source-of-truth per environment.

Internal helpers (libraries, not hook entrypoints): `_audit_rotation`,
`_block_format`, `_calibration_gate`, `_dispatch`, `_guard_paths`,
`_host_env`, `_kill_switches`, `_magic_comments`, `_mock_detector`,
`_ood_detector`, `_plan_quality`, `_rule_engine`, `_session_manifest`,
`_shadow_mode`.

---

## Architectural rule engine

Opt-in symbolic gate co-emitted with the neural risk vector through the
same exit-2 pipe.

```bash
export RC_RULE_ENGINE=1                  # enable
# export RC_RULE_ENGINE_LENIENT=1        # soft-degrade evaluator errors to warn
```

Write rules to `.reasoning-core/rules.yaml` at your repo root. Two rule
types are supported:

```yaml
corpus_version: v1
rules:
  - id: no_hooks_import_supervisor
    type: forbid_import
    severity: deny              # deny | warn | shadow
    language: python            # python | javascript | typescript | tsx
    target: src.sidecar_supervisor
    scope: src/hooks/**
    message: "Hooks must not import sidecar_supervisor directly"

  - id: no_shell_true
    type: forbid_pattern
    severity: deny
    language: python
    pattern: 'subprocess\.run\s*\([^)]*shell\s*=\s*True'
    scope: src/**
    message: "subprocess.run with shell=True is forbidden"
```

Hard limits: ≤50 rules, ≤5 ms per rule, `corpus_version` must equal `"v1"`.

Per-call bypass: prepend `# rc:skip-rule:<id>` (Python) or
`// rc:skip-rule:<id>` (JS/TS) in the source body before the matched line.

PyYAML is the supported parser; the in-tree fallback
`_basic_yaml_parse` only activates under
`RC_RULE_ENGINE_ALLOW_BASIC_YAML=1` (security-review flagged divergence
surface).

A starter file ships at `.reasoning-core/rules.yaml` in the reasoning-core
repo — copy + adapt for the target repo, or write your own from scratch.

---

## Embedder backends (`RC_EMBEDDER`)

Default is `mamba-130m` — works out of the box because its checkpoint SHA
is pinned in `_PINNED_REVISIONS`. Other backends carry `revision="main"`
and are fail-closed until you supply a pinned SHA:

```bash
# Use codestral-mamba (7B, code-pretrained, hidden=4096)
# Defaults to fp16 (~14 GB RAM). Override with RC_EMBEDDER_DTYPE if needed.
export RC_EMBEDDER=codestral-mamba
export RC_MISTRALAI_MAMBA_CODESTRAL_7B_V0_1_REVISION=<40-char hex SHA>

# Or BAAI/bge-code-v1 (transformer baseline)
export RC_EMBEDDER=bge-code
export RC_BAAI_BGE_CODE_V1_REVISION=<40-char hex SHA>
```

Slug rule: uppercase the HuggingFace repo id, replace non-alphanumeric with
`_`, prefix with `RC_`, suffix with `_REVISION`. Mutable refs (branches,
tags) are rejected — supply-chain hardening.

The 7B Codestral-Mamba is meaningfully better at code-similarity but
materially slower on CPU. Loads at fp16 by default to stay inside ~14 GB
RAM on a laptop (`RC_EMBEDDER_DTYPE=float32` to force fp32 at ~28 GB).
Use it on machines with CUDA or MLX when you can.

---

## Diff audit (`RC_DIFF_AUDIT=1`)

When the agent emits a malformed unified diff (root cause of 2/10
swebench-iter1 D2 Setup-B failures), the Stop hook
`post_assistant_diff_audit.py` scans the last assistant message in
transcript, runs `validate_unified_diff(patch)`, and injects an
`additionalContext` advisory plus the best-effort repaired patch.

```bash
export RC_DIFF_AUDIT=1
```

Detected error classes: `missing_prefix`, `empty_context`, `count_mismatch`,
`bad_hunk_header`, `missing_hunk`. The MCP tool never raises — invalid
input is reported in the response, not via exception.

The same tool is exposed as a manual MCP call (`validate_unified_diff`) and
surfaces in retry-block stderr as `RECOVERY` guidance when a previously
blocked edit comes back as a malformed patch.

---

## Shadow mode

The gate ships in **advise mode** by default (`RC_MODE=advise` and
`RC_SHADOW_MODE=1` in the generated `.envrc`). Decisions are computed and
logged; the hook always returns exit 0. This lets you observe what the gate
*would* have done on your codebase before flipping it on.

Promote to enforcement when ready:

```bash
rc enable-enforcement     # wizard: scaffold PLAN.md, review shadow report, flip to copilot
```

Or manually:

```bash
echo 'export RC_MODE=copilot' >> .envrc.local
direnv reload
```

---

## Bypass / kill switches

In order of preference (least → most invasive):

- **Magic comment, single edit.** Prepend `# rc:bypass-next` (or `//
  rc:bypass-next`) to the file before the Edit Claude is about to fire.
- **Per-rule bypass.** `# rc:skip-rule:<id>` / `// rc:skip-rule:<id>` —
  scoped to the rule-engine, doesn't affect the neural gate.
- **Single-shot, single command.** `rc bypass-next` arms one bypass; the
  next guard fire consumes it and emits `operator_override`. `rc
  confirm-next` arms a confirmation that the next block was correct and
  emits `operator_confirmed` — the ground-truth signal for false-positive
  measurement.
- **Single-shot, fresh session.** `RC_BYPASS_NEXT=1 claude ...` — captured
  at session boot, consumed by the first guard fire.
- **Per-path session-wide.** `RC_ALLOW_GUARD_EDIT=1` for guarded paths,
  `RC_ALLOW_SUBAGENT_GUARD_EDIT=1` for subagent prompts naming them.
- **Last resort.** `S2_FAIL_CLOSED=0` and kill the sidecar — fails open.
  Don't ship this; it nullifies the gate.

Every escape path emits an audit row tagged with the override mechanism so
abuse is spottable later.

---

## Testing

```bash
pytest -q -m "not live"                          # offline suite
RC_LIVE=1 pytest -q tests/test_scaleway_smoke.py # live Scaleway round-trip (optional)
bash scripts/test-prototype.sh                   # full e2e gate
pytest -q tests/test_security_hardening.py       # supply-chain + URL exfil tests
```

---

## FAQ

**Q: First time running, am I getting blocked?**
A: No. The gate ships in shadow mode (`RC_SHADOW_MODE=1`). Every Edit/Write
is scored and the decision is logged to
`~/.local/share/reasoning-core/events/`, but the hook always returns exit 0.
Promote to enforcement after a few sessions of observation.

**Q: The hook keeps blocking obviously-fine edits.**
A: Check `top risk contributors` in the block message. If a single dim sits
at `1.00` on a tiny edit, restart the sidecar — old processes can hold
pre-refactor scoring code. Note that `coherence_delta` ranges `[0, 2]` (chord
distance) on the current build; legacy thresholds tuned on the old
`L2/sqrt(D)` scale (e.g. `1.5`+) silently disable the gate. Run `rc status`
and `rc explain`, then open an issue with the audit row.

**Q: Sidecar keeps dying mid-session.**
A: Install the supervisor (`bash scripts/install-supervisor-launchagent.sh`
on macOS, systemd user unit on Linux — see [`INSTALL.md`](INSTALL.md)).
KeepAlive will relaunch it on crash.

**Q: I'm on a corporate VPN (Cato / Zscaler) and `pip install` fails with
"self-signed certificate in certificate chain".**
A: `direnv reload` — the repo's `.envrc` builds
`~/.cache/reasoning-core/ca-bundle.pem` from `certifi` + your system root.
macOS only today; Linux users add their root manually.

**Q: Sidecar takes forever to start.**
A: First run downloads embedder weights. For default `mamba-130m`: ~250 MB,
~30s on CPU subsequently. `codestral-mamba` is ~14 GB at the default fp16
load (~28 GB at fp32 — set `RC_EMBEDDER_DTYPE=float32` if you need it,
but it will OOM most laptops). Use codestral on CUDA or MLX when you can.
Watch `tail -f /tmp/reasoning-core-sidecar.log`.

**Q: How do I temporarily turn it off?**
A: `cd` out of the repo (direnv unloads, hooks vanish), or export
`S2_FAIL_CLOSED=0` and kill the sidecar.

**Q: I want to edit the guard files themselves.**
A: Set `RC_ALLOW_GUARD_EDIT=1` in the shell that started Claude, restart
Claude, edit. The env is captured at session boot.

**Q: Sidecar refuses to talk to my non-loopback address.**
A: That's intentional. `S2_URL` must point at `127.0.0.1` / `localhost`
unless `S2_ALLOW_REMOTE=1` is also set. The check blocks SSRF-style source
content exfil via `.envrc` / `.mcp.json` / IDE-config injection.

**Q: My hosted generative critic gets `RC_GEN_ALLOWED_HOSTS` errors.**
A: Bearer auth is scoped to loopback or hosts in
`RC_GEN_ALLOWED_HOSTS=api.scaleway.ai,...`. Cross-origin 30x redirects are
refused — urllib doesn't strip the `Authorization` header on redirect, so a
malicious upstream could otherwise capture the key.

**Q: Will it slow me down?**
A: p95 ~5s per Edit on CPU with `mamba-130m`. Latency is dominated by the
embedder forward pass; CUDA / MLX kernels would cut it to ~50ms. Tracked in
the roadmap.

**Q: It blocked a legitimate refactor. How do I override?**
A: Either revise to address the top-3 risk contributors (recommended), set
`RC_ALLOW_GUARD_EDIT=1` for guarded paths, or temporarily relax the env knob
(`S2_RISK_DIM_THRESHOLD=1.01`) and restart sidecar. Don't disable globally
— you'll lose the signal that pointed at a real issue.
