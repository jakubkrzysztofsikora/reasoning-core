# Usage

Day-to-day operating manual: the `rc` CLI, hook layers, shadow mode, bypass
switches, FAQ.

---

## `rc` CLI

Put `bin/` on PATH (`export PATH="$RC_REPO/bin:$PATH"`).

| Command | Purpose |
|---|---|
| `rc status` | Sidecar health + threshold posture (shadow mode? fail-closed? per-kind ceilings?) |
| `rc explain` | Explain the most recent block decision (top-3 risk contributors + repair hints) |
| `rc bypass-next` | Arm a single-shot bypass for the next Edit/Write — consumed on first guard fire |
| `rc skip-file <path>` | Add `<path>` to the per-session skip list (logged) |
| `rc unskip-file <path>` | Remove `<path>` from the skip list |

`rc --help` is authoritative.

---

## Hook layers

| # | Hook | Event / matcher | Purpose |
|---|---|---|---|
| L1 | `pre_bash_guard.py` | PreToolUse / `Bash` | Blocks shell-level source writes (heredoc, sed, tee), kills against the sidecar, env tampering, edits to guard files |
| L2 | `pre_edit_guard.py` | PreToolUse / `Edit\|Write\|MultiEdit` | SSM scoring; per-kind threshold dispatch; mock-detector; OOD detector; language-lock; drift policy; guard-file lock |
| L3 | `pre_plan_guard.py` | PreToolUse / `Write` (and Plan-shaped writes to `**/plans/**.md`) | Plan-time heuristics + plan-quality CGS (kNN novelty, section drift, plan→impl coherence) |
| L4 | `pre_task_guard.py` | PreToolUse / `Task` | Regex screen on subagent prompts mentioning guarded paths with mutation verbs |
| L5 | `post_bash_revive.py` | PostToolUse / `Bash` | Re-spawns sidecar when `/health` stops responding after a kill-shaped command |
| L6 | `post_batch_lang_audit.py` | PostToolUse / `Edit\|Write\|MultiEdit` | After-the-fact language-fingerprint audit; logs drift events when foreign-language ratio crosses `RC_LANG_AUDIT_THRESHOLD` |
| L7 | `pre_compact_guard.py` | PreCompact | Captures pre-compaction state so post-compact context can be reconciled |
| L8 | `session_start_manifest.py` | SessionStart | Snapshots `RC_*` env, repo SHA, language fingerprint, active task spec; prevents mid-session env tampering |
| L9 | `session_resume_inject.py` | SessionStart (resume) + UserPromptSubmit | Re-injects pinned env from the prior session manifest into the resumed shell |

All wired in [`.claude/settings.json`](../.claude/settings.json). Every fire
emits an audit row to `~/.local/share/reasoning-core/events/`.

Internal helpers (libraries, not hook entrypoints): `_audit_rotation`,
`_block_format`, `_kill_switches`, `_magic_comments`, `_mock_detector`,
`_ood_detector`, `_plan_quality`, `_session_manifest`, `_shadow_mode`.

---

## Shadow mode

The gate ships in **shadow mode** by default (`RC_SHADOW_MODE=1` in
`.envrc`). Decisions are computed and logged; the hook always returns exit 0.
This lets you observe what the gate *would* have done on your codebase
before flipping it on.

Promote to enforcement when ready:

```bash
echo 'export RC_SHADOW_MODE=0' >> .envrc.local
direnv reload
```

---

## Bypass / kill switches

In order of preference (least → most invasive):

- **Magic comment, single edit.** Prepend `# rc:bypass-next` (or `//
  rc:bypass-next`) to the file before the Edit Claude is about to fire.
- **Single-shot, single command.** `rc bypass-next` arms one bypass; the
  next guard fire consumes it.
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
pre-refactor scoring code. If it persists, run `rc status` and `rc explain`,
then open an issue with the audit row.

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
A: First run downloads Mamba weights (~250 MB). Subsequent boots ~30s on
CPU. Watch `tail -f /tmp/reasoning-core-sidecar.log`.

**Q: How do I temporarily turn it off?**
A: `cd` out of the repo (direnv unloads, hooks vanish), or export
`S2_FAIL_CLOSED=0` and kill the sidecar.

**Q: I want to edit the guard files themselves.**
A: Set `RC_ALLOW_GUARD_EDIT=1` in the shell that started Claude, restart
Claude, edit. The env is captured at session boot.

**Q: Will it slow me down?**
A: p95 ~5s per Edit on CPU. Latency is in the Mamba forward pass; CUDA /
MLX kernels would cut it to ~50ms. Tracked in the roadmap.

**Q: It blocked a legitimate refactor. How do I override?**
A: Either revise to address the top-3 risk contributors (recommended), set
`RC_ALLOW_GUARD_EDIT=1` for guarded paths, or temporarily relax the env knob
(`S2_RISK_DIM_THRESHOLD=1.01`) and restart sidecar. Don't disable globally
— you'll lose the signal that pointed at a real issue.
