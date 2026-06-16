# CLI parity — reasoning-core

Per-host integration of the System 2 sidecar across Claude Code, OpenAI
Codex CLI, Gemini CLI, GitHub Copilot CLI, Moonshot Kimi CLI, and Mistral
Vibe CLI. Verified hands-on 2026-05-08 (Codex + Kimi added 2026-06-15).

## Parity matrix

| Surface | Claude Code | Codex CLI | Gemini CLI | Copilot CLI | Kimi CLI | Vibe |
|---|---|---|---|---|---|---|---|
| **PreToolUse hook** | ✓ exit-2 | ✓ Claude-compat | ✓ Claude-compat | ✗ | ✓ Claude-compat | ✗ |
| **PostToolUse hook** | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ |
| **SessionStart** | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ |
| **UserPromptSubmit** | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ |
| **PreCompact** | ✓ | ✓ | ✓ | ✗ | ✓ | ✗ |
| **MCP server** | ✓ `.claude/` | ✓ `.codex/` | ✓ `.gemini/` | ✓ `~/.copilot/` | ✓ `.kimi/` | ✓ `.vibe/` |
| **Skills** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Context file** | `CLAUDE.md` | `CODEX.md` | `GEMINI.md` | `copilot-instructions.md` | `KIMI.md` | `AGENTS.md` |
| **Gate path** | hooks | hooks | hooks | MCP tool | hooks | MCP tool |
| **Audit visibility** | full | full | full | post-MCP-call | full | post-MCP-call |

## Install

Each host has a dedicated install script; all materialise host-specific
config under per-host directories and merge user-level config (only
where required) with a backup.

```bash
export RC_REPO=$HOME/Repos/personal/reasoning-core

# Claude (legacy entry point, default behaviour preserved)
bash $RC_REPO/scripts/enable-in-repo.sh

# Gemini
bash $RC_REPO/scripts/enable-in-repo-gemini.sh

# Copilot
bash $RC_REPO/scripts/enable-in-repo-copilot.sh

# Codex
bash $RC_REPO/scripts/enable-in-repo-codex.sh

# Kimi
bash $RC_REPO/scripts/enable-in-repo-kimi.sh

# Vibe
bash $RC_REPO/scripts/enable-in-repo-vibe.sh
```

All scripts:
- Refuse to overwrite existing per-host config (use `--force` to override).
- Embed `RC_REPO` as an absolute path in the generated per-host config
  (e.g. `.gemini/settings.json`); these generated files are gitignored
  per-machine. The committed `.template` source-of-truth uses `<RC_REPO>`.
- Add `RC_HOST=<host>` to `.envrc` so `_host_env.host()` resolves the
  per-host project_dir / session_id without ambiguity.

## Known gaps

### Copilot CLI
- No hook subcommand on v1.0.29. Runtime gate is the `gate_edit` MCP
  tool only; pre-edit interception relies on the agent honouring
  `.copilot/copilot-instructions.md`. Post-turn audit retroactively
  flags missed calls.
- MCP config is user-level (`~/.copilot/mcp-config.json`) — no per-repo
  override. `enable-in-repo-copilot.sh` merges the `hybrid-reasoner`
  entry, preserving existing user MCP servers via atomic temp-rename
  with a timestamped backup.

### Vibe CLI
- PreToolUse hooks not yet shipped. Same MCP-only gate path as Copilot.
- `.vibe/AGENTS.md` is intentionally Vibe-scoped (NOT repo-root) —
  Claude / Cursor / Codex now read top-level `AGENTS.md` and a shared
  file would cause double-scoring on hosts that already have runtime
  hooks.

### Gemini CLI
- Trust prompt on first MCP server load. Headless eval must pass
  `--yolo` or pre-populate `~/.gemini/trusted_mcp.json`.
- `GEMINI.md` is per-cwd; running `gemini` from a parent dir won't
  pick it up.

## Production caveats (power-user review 2026-05-08)

- **Tier-2 gating on Copilot / Vibe**: the runtime gate is the `gate_edit`
  MCP tool, enforced only by `AGENTS.md` / `copilot-instructions.md` —
  a soft prompt. Under context pressure or multi-step tool chains the
  agent sometimes skips the call, the regression lands, and the
  post-hoc audit row is never written (because the tool was never
  called). For mission-critical work, use Claude or Gemini where the
  gate is a runtime hook, not a model-honoured contract.

- **Session-end reconciliation**: run `SESSION_ID=<sid>
  python -m eval.reconcile_session` after a Copilot/Vibe session to
  diff git working-tree changes against `gate_edit` audit rows.
  Mismatches indicate skipped gate calls that need human review.

- **`flock` on macOS**: install scripts use `command -v flock` and skip
  locking if absent. Two parallel `enable-in-repo-copilot.sh` runs from
  different repos can lost-update on stock macOS unless `brew install
  flock` is present. Sequential installs are safe.

- **Sidecar must be running before agent launch**: install scripts do
  not start the sidecar. Run `bash scripts/start-sidecar.sh` first; the
  install scripts only configure the host CLI to call it.

- **`COPILOT_ALLOW_ALL=1` does not auto-trust new MCP servers**: first
  Copilot session after install may surface a one-shot MCP-trust prompt.
  Pre-seed `~/.copilot/trusted-servers` for fully unattended runs.

- **`direnv allow` required**: enable scripts append `RC_HOST=...` to
  `.envrc` but do not run `direnv allow` for you. Headless runs from
  shells without direnv get `RC_HOST=unknown`, which collapses the
  audit log's per-host slicing.

## Concurrency notes

All hosts hit a single sidecar at `127.0.0.1:8765`. Multi-host concurrent
writers on `audit_log.jsonl` are serialised via `portalocker.LOCK_EX`
with `fsync=True` on the `mcp_gate.gate_edit` path (host may exit before
deferred I/O lands). Tested with 16 concurrent procs writing >8KB rows.

## Verification commands (2026-05-08)

```bash
gemini --version          # 0.37.1 confirmed
gemini hooks --help       # subcommand "migrate" — Claude-compat schema
copilot --version         # GitHub Copilot CLI 1.0.29
copilot --help | grep -i hook   # no hook subcommand
vibe --version            # vibe 2.9.4
vibe --help               # --prompt --trust --enabled-tools confirmed
```
