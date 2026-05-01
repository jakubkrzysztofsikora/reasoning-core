# Hardening — closing the agent-bypass surface

The original PreToolUse hook fired only on `Edit | Write | MultiEdit`. A
sufficiently motivated agent (or a confused one) can route around that
matcher. This doc is the threat model + countermeasures.

## Threat model — five bypass paths

| # | Path | Mitigation layer |
|---|------|------------------|
| 1 | `cat > foo.py <<EOF` / `tee` / `sed -i` / `python -c "open(...,'w')"` via **Bash** | L1 — `pre_bash_guard.py` (PreToolUse on `Bash`) |
| 2 | `pkill -f s2_core` then perform unguarded edits while sidecar is down | L1 hard-deny + L3 sidecar revival |
| 3 | Edit `.claude/settings.json` to remove the matcher | L2 — guard-file lock in `pre_edit_guard.py` |
| 4 | Edit the hook script itself (`pre_edit_guard.py` → no-op) | L2 — same lock applies to `src/hooks/*` |
| 5 | Sidecar dies for an unrelated reason (OOM, crash) and hook fail-opens | L3 — `post_bash_revive.py` + `S2_FAIL_CLOSED=1` |

## Layers shipped

### L1 — Bash guard (`src/hooks/pre_bash_guard.py`)

Stdlib-only, ~250 LOC, regex-based. Four checks in order:

1. **Hard-deny patterns** — `pkill`/`kill` against sidecar processes, shell
   redirection to `.claude/settings.json`, `rm` on guard files, `chmod -x`
   on the hook, `unset` of safety env vars. Always block, regardless of
   leading token.
2. **Guarded path + write op** — if the command both touches a guarded
   path AND looks like a write (redirection / sed / heredoc / python -c
   open-w), block.
3. **Process kill against sidecar tokens** — second-pass regex that catches
   `kill -9 <pid>` when the pid was `pgrep`'d earlier in the same command.
4. **Generic source-write detection** — `>` / `>>` / `tee` / `sed -i` /
   `python -c "open(...,'w')"` / `node -e "writeFileSync"` / `cat <<EOF >`
   targeting any file with a tracked source extension (`.py .js .mjs .cjs
   .ts .tsx .cs .sql .sh .json .yaml .yml .md .markdown .mdx .toml .ini`).

The check ordering matters: write-pattern detection runs **before** the
safe-leading-tokens allowlist. `echo x > main.py` blocks even though
`echo` is in the safe list.

Override: `RC_ALLOW_GUARD_EDIT=1` in the shell that started Claude. Value
is checked at hook-execution time so flipping it requires a Claude
session restart (the env is captured at session boot).

### L2 — Guard-file lock (extension to `src/hooks/pre_edit_guard.py`)

Before scoring an edit, the hook checks the target path against
`GUARDED_PATHS`:

```
.claude/settings.json
.claude/settings.local.json
src/hooks/pre_edit_guard.py
src/hooks/pre_bash_guard.py
src/s2_core.py
src/grammars.py
src/ssm_backbone.py
src/mcp_reasoner.py
scripts/start-sidecar.sh
```

If matched and `RC_ALLOW_GUARD_EDIT != "1"`, exit 2. Same override env
as L1 — single toggle for both layers.

### L3 — Sidecar revival (`src/hooks/post_bash_revive.py`)

PostToolUse hook on `Bash`. After every Bash command, if the command
**looks like** a kill (regex match against `pkill|killall|launchctl
unload|kill <pid>`) AND the sidecar's `/health` no longer responds, the
hook spawns `scripts/start-sidecar.sh` in the background (detached
session, log to `/tmp/rc-sidecar-revive.log`).

Defense-in-depth — L1 already blocks the obvious kill patterns; L3 is
the catch-all for indirect kills (e.g. an exec'd subscript, an OOM, a
crash in the SSM forward pass).

The revive hook is informational: it never blocks (always exit 0).
Its only side-effect is process spawn + a stderr note.

## Recommended runtime env

Put these in the shell that launches Claude Code:

```bash
export S2_FAIL_CLOSED=1   # sidecar down → block, not fail-open
export S2_TIMEOUT=60      # CPU Mamba can take ~30s, give headroom
# Do NOT export RC_ALLOW_GUARD_EDIT unless you intentionally want to
# allow Claude to edit the guard infrastructure.
```

`.claude/settings.json` already wires the three hooks into the matcher
set. After updating: restart Claude Code so the new settings load.

## Override flow — when you DO need to edit the guard

```bash
export RC_ALLOW_GUARD_EDIT=1
# restart Claude in the same shell
claude
```

Inside the session, edits to `src/hooks/*`, `.claude/settings.json`,
`src/s2_core.py`, etc. now go through the SSM scorer like any other
file. The override is binary; there is no per-file allow-list.

## Audit

After a session, the artifacts to review are:

- `/tmp/rc-monitor.log` — sidecar /health every 30s (if you ran the
  monitor poller).
- `/tmp/rc-sidecar-revive.log` — stdout of any L3 revivals.
- Stderr in your Claude transcript — every L1/L2/L3 message starts with
  `[hybrid-reasoner]`.

A future commit will land structured JSONL audit at
`/tmp/rc-events/<date>/<session>.jsonl` with per-edit decisions
(planned in the instrumentation gap analysis — see
`docs/EVAL_DESIGN.md`).

## Known residual gaps

1. **Subagent inheritance.** When Claude spawns a `Task` subagent, the
   subagent's tool calls do not run through this project's hooks unless
   the subagent's working dir is also under `CLAUDE_PROJECT_DIR`. Today
   this is not enforced — open issue.
2. **Indirect file rewrites.** A `make` target or `npm run build` that
   internally writes to source files via codegen will not be caught by
   the Bash regex (the regex sees `make build`, not the codegen output).
   In practice this is rare; flag if observed.
3. **Plan-time scoring.** The hook scores the *file* at write time. A
   plan that describes 1 huge file is only caught when Claude tries to
   write it. Scoring the plan document earlier would shift the gate
   left. Tracked separately as a roadmap item.
