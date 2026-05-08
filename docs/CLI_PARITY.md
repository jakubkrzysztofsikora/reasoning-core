# CLI parity — reasoning-core

Per-host integration of the System 2 sidecar across Claude Code, Gemini
CLI, GitHub Copilot CLI, and Mistral Vibe CLI. Verified hands-on
2026-05-08.

## Parity matrix

| Surface | Claude Code | Gemini CLI v0.37.1 | Copilot CLI v1.0.29 | Vibe v2.9.4 |
|---|---|---|---|---|
| **PreToolUse hook** | ✓ exit-2 | ✓ Claude-compat (`gemini hooks migrate`) | ✗ no hook subcommand | ✗ post-agent-turn only |
| **PostToolUse hook** | ✓ | ✓ | ✗ | ✓ post_agent_turn |
| **SessionStart** | ✓ | ✓ | ✗ | ✗ |
| **UserPromptSubmit** | ✓ | ✓ | ✗ | ✗ |
| **PreCompact** | ✓ | ✓ | ✗ | ✗ |
| **MCP server (stdio)** | ✓ `.claude/settings.json` | ✓ `.gemini/settings.json` | ✓ `~/.copilot/mcp-config.json` (user-level only) | ✓ `.vibe/config.toml` `[[mcp_servers]]` |
| **Skills** | ✓ `.claude/skills/` | ✓ `.gemini/skills/` (`gemini skills`) | ✓ `.copilot/skills/` | ✓ `.vibe/skills/` |
| **Context file** | `CLAUDE.md` | `GEMINI.md` | `.copilot/copilot-instructions.md` | `.vibe/AGENTS.md` |
| **Headless trust bypass** | (built-in) | `--yolo` / `--approval-mode yolo` | `--allow-all-tools` / `COPILOT_ALLOW_ALL=1` | `--trust` (per-invocation) / `--prompt` |
| **Runtime gate path** | hooks | hooks | `gate_edit` MCP tool | `gate_edit` MCP tool + AGENTS.md instruction |
| **Audit visibility** | full | full | post-MCP-call audit row | post-MCP-call + post-agent-turn audit |

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
