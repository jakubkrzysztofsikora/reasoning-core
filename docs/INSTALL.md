# Install

The [`README`](../README.md) covers the one-command happy path. This file
documents the manual install, the global "every project on the machine"
recipe, supervisor/launchd setup, the Scaleway-hosted critic for non-Apple
machines, and the Cato/Zscaler VPN workaround.

---

## Manual repo-scoped install (no `install.sh`)

If you don't trust the script, the moving parts are:

### 1. Clone + venv

```bash
git clone https://github.com/jakubkrzysztofsikora/reasoning-core.git
cd reasoning-core
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Embedder checkpoint (one-time)

The default backend is `mamba-130m` (~250 MB). To use a different
embedder, set `RC_EMBEDDER` AND a SHA pin override (mutable refs are
rejected — supply-chain hardening).

```bash
# Default
huggingface-cli download state-spaces/mamba-130m-hf

# Or: Codestral-Mamba 7B (code-pretrained, hidden=4096)
# Loads as fp16 by default → ~14 GB resident; fp32 would be ~28 GB and OOMs
# most laptops. Override with RC_EMBEDDER_DTYPE=float32 / bfloat16 / auto.
# huggingface-cli download mistralai/Mamba-Codestral-7B-v0.1
# export RC_EMBEDDER=codestral-mamba
# export RC_MISTRALAI_MAMBA_CODESTRAL_7B_V0_1_REVISION=<40-char hex SHA>
```

Slug rule: uppercase the HuggingFace repo id, replace non-alphanumeric
with `_`, prefix `RC_`, suffix `_REVISION`. Branch names like `main`
are explicitly rejected.

Supported backends: `mamba-130m` (default), `codestral-mamba`,
`bge-code`, `unixcoder-base`, `random-mamba` (in-process control for
falsifiability tests). See [`CONFIGURATION.md#embedder-backend`](CONFIGURATION.md#embedder-backend).

### 3. Boot the stack

Recommended path: install the supervisor LaunchAgent — one command,
brings up the S2 (Mamba) sidecar AND the gen (Qwen MLX) sidecar under
single-instance locks + memory watchdogs, auto-restarts on crash, starts
on every login.

```bash
bash scripts/install-supervisor-launchagent.sh
# wait ~30s for first boot to load Mamba weights
curl -fsS http://127.0.0.1:8764/health | jq      # broker: aggregates children
curl -fsS http://127.0.0.1:8765/health | jq .model_loaded  # S2 (Mamba) → true
curl -fsS http://127.0.0.1:8766/v1/models | jq   # gen (Qwen MLX)
```

Endpoints:

| Port | Service | Notes |
|---|---|---|
| 8764 | supervisor broker `/health` | aggregated child status |
| 8765 | S2 sidecar (Mamba) | `/score`, `/health` |
| 8766 | gen sidecar (Qwen MLX) | OpenAI-compatible `/v1/*` |

The gen sidecar is wrapped by `src/gen_sidecar_launcher.py`, which polls
the child's `phys_footprint` (the value Activity Monitor reports, includes
MLX / Metal / IOSurface unified-memory) and SIGKILLs it if it exceeds
`S2_GEN_MEM_LIMIT_GB` (falls back to `S2_MEM_LIMIT_GB`, default 16). The
2026-06-02 incident showed `ru_maxrss`-based caps were blind to MLX
allocations; this closes that gap. Plist sets both caps to 25 GB on a
64 GB host.

For testing without the supervisor you can still run the legacy direct
scripts:

```bash
bash scripts/start-sidecar.sh        # S2 only, foreground
bash scripts/start-gen-sidecar.sh    # gen only, foreground
```

— but they bypass the single-instance lock and the memory watchdog only
catches the in-process S2 sidecar, so prefer the supervisor for real use.

### 4. Activate direnv

```bash
brew install direnv                                  # or: apt-get install direnv
echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc         # or bash equivalent
cd /path/to/your-repo
```

Create a `.envrc` in the target repo (the one-command `install.sh` does this
for you; for a manual install, hand-author one — minimal form below — or
copy from the reasoning-core repo's own `.envrc` and trim to taste).

```bash
cat > .envrc <<'EOF'
export RC_REPO="$HOME/Repos/personal/reasoning-core"
if [[ -d "$RC_REPO/.venv/bin" ]]; then PATH_add "$RC_REPO/.venv/bin"; fi
export S2_DEVICE="${S2_DEVICE:-cpu}"
export S2_PORT="${S2_PORT:-8765}"
export S2_TIMEOUT="${S2_TIMEOUT:-30}"
export S2_FAIL_CLOSED="${S2_FAIL_CLOSED:-0}"
export RC_SHADOW_MODE="${RC_SHADOW_MODE:-1}"
[[ -f .envrc.local ]] && source_env .envrc.local
EOF

direnv allow .
export PATH="$RC_REPO/bin:$PATH"                     # so `rc` shim resolves
```

Secrets / personal toggles → `.envrc.local` (gitignored, sourced last).

### 5. Wire per-host config

Hand-copy from the templates:

| CLI | Source | Destination |
|---|---|---|
| Claude | `.claude/settings.json` template in `install.sh` | `.claude/settings.local.json` |
| Gemini | `.gemini/settings.json.template` | `.gemini/settings.json` (substitute `<RC_REPO>`) |
| Copilot | `.copilot/mcp-config.template.json` | merge into `~/.copilot/mcp-config.json` |
| Vibe | `.vibe/config.toml.template` | `.vibe/config.toml` (substitute `<RC_REPO>`) |

The unified `install.sh` does all of this; this section is here for the
audit trail.

---

## Non–Apple-silicon: Scaleway-hosted generative critic

On Linux / CI / Intel Mac the local `mlx_lm.server` path is unavailable.
Point the generative critic at Scaleway's hosted OpenAI-compatible API
(Bearer auth via `RC_GEN_API_KEY` or `SCALEWAY_API_KEY`):

```bash
export RC_REASONER_BACKEND=remote
export RC_GEN_URL=https://api.scaleway.ai/v1/chat/completions
export RC_GEN_API_KEY=$(scw config get secret-key --profile circit)
export RC_GEN_MODEL=qwen3-coder-30b-a3b-instruct   # or devstral-2-123b-instruct-2512
export RC_GEN_BUDGET_MS=15000
```

Apple-silicon users keep the local path — no changes needed.

---

## Supervisor (always-on stack)

`scripts/install-supervisor-launchagent.sh` is the one-step daemonize
path. It renders the plist template, copies it to
`~/Library/LaunchAgents/`, and `launchctl load`s it. The supervisor owns
both children — S2 (Mamba) and gen (Qwen MLX) — under a single broker.

### macOS — launchd

```bash
bash scripts/install-supervisor-launchagent.sh
launchctl list | grep reasoning-core         # → com.reasoning-core.supervisor visible
curl -fsS http://127.0.0.1:8764/health       # broker aggregator
curl -fsS http://127.0.0.1:8765/health       # S2 (Mamba)
curl -fsS http://127.0.0.1:8766/v1/models    # gen (Qwen MLX)
tail -f /tmp/rc-supervisor.err.log /tmp/rc-mamba.log /tmp/rc-gen.log
```

KeepAlive=true → relaunches on crash. RunAtLoad=true → starts on login.

Daily ops:

| Want | Command |
|---|---|
| Restart | `launchctl kickstart -k gui/$(id -u)/com.reasoning-core.supervisor` |
| Stop | `launchctl unload ~/Library/LaunchAgents/com.reasoning-core.supervisor.plist` |
| Start | `launchctl load -w ~/Library/LaunchAgents/com.reasoning-core.supervisor.plist` |

`kickstart -k` is the command to pick up Python source edits in `src/`
without a full reload. Edits to `launchd/com.reasoning-core.supervisor.plist`
(env vars, etc.) require re-running `install-supervisor-launchagent.sh`.
`.envrc` is NOT read by launchd — env vars meant for the supervisor must
live in the plist's `EnvironmentVariables` block.

Uninstall:

```bash
launchctl bootout gui/$UID/com.reasoning-core.supervisor
rm ~/Library/LaunchAgents/com.reasoning-core.supervisor.plist
```

### Linux — systemd user unit

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/reasoning-core-sidecar.service <<EOF
[Unit]
Description=reasoning-core SSM sidecar
[Service]
Type=simple
ExecStart=$RC_REPO/.venv/bin/python -m src.s2_core
WorkingDirectory=$RC_REPO
Restart=on-failure
RestartSec=5
[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now reasoning-core-sidecar.service
```

Uninstall: `systemctl --user disable --now reasoning-core-sidecar.service`.

---

## Promote globally — one path, every project

Repo-scoped hooks fire only when Claude runs from inside a gated folder. To
get the same gating across **every project on your machine**, register the
hooks once at the user level — no per-repo copy-paste.

**Order matters.** Sidecar must be daemonized BEFORE flipping
`S2_FAIL_CLOSED=1`, otherwise every Edit on every project hard-blocks until
the sidecar is up.

### Step 0 — Preflight

Confirm the venv has the deps. Hooks invoked via system `python3` will
ImportError on first fire:

```bash
cd $RC_REPO
test -x .venv/bin/python || python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -c "import tree_sitter, transformers, fastapi; print('venv OK')"
```

### Step 1 — Pin `$RC_REPO` in shell rc

```bash
# ~/.zshrc or ~/.bashrc
export RC_REPO="$HOME/Repos/personal/reasoning-core"
export PATH="$RC_REPO/bin:$PATH"
```

`exec zsh` (or `source ~/.zshrc`).

### Step 2 — Daemonize the sidecar

See the supervisor section above.

### Step 3 — Register hooks at the user level

Add the hook block to `~/.claude/settings.json` (merge — don't replace). The
key idea is to use `${RC_REPO}/.venv/bin/python` as the interpreter so it
resolves to the venv with all deps.

Snippet:

```jsonc
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "hooks": {
    "PreToolUse": [
      { "matcher": "Edit|Write|MultiEdit",
        "hooks": [{ "type": "command",
                    "command": "${RC_REPO}/.venv/bin/python ${RC_REPO}/src/hooks/pre_edit_guard.py",
                    "timeout": 60000 }] },
      { "matcher": "Write",
        "hooks": [{ "type": "command",
                    "command": "${RC_REPO}/.venv/bin/python ${RC_REPO}/src/hooks/pre_plan_guard.py",
                    "timeout": 15000 }] },
      { "matcher": "Bash",
        "hooks": [{ "type": "command",
                    "command": "${RC_REPO}/.venv/bin/python ${RC_REPO}/src/hooks/pre_bash_guard.py",
                    "timeout": 5000 }] },
      { "matcher": "Task",
        "hooks": [{ "type": "command",
                    "command": "${RC_REPO}/.venv/bin/python ${RC_REPO}/src/hooks/pre_task_guard.py",
                    "timeout": 5000 }] }
    ],
    "PostToolUse": [
      { "matcher": "Bash",
        "hooks": [{ "type": "command",
                    "command": "${RC_REPO}/.venv/bin/python ${RC_REPO}/src/hooks/post_bash_revive.py",
                    "timeout": 5000 }] },
      { "matcher": "Edit|Write|MultiEdit",
        "hooks": [{ "type": "command",
                    "command": "${RC_REPO}/.venv/bin/python ${RC_REPO}/src/hooks/post_batch_lang_audit.py",
                    "timeout": 5000 }] }
    ],
    "SessionStart": [
      { "hooks": [
          { "type": "command",
            "command": "${RC_REPO}/.venv/bin/python ${RC_REPO}/src/hooks/session_start_manifest.py",
            "timeout": 30000 },
          { "type": "command",
            "command": "${RC_REPO}/.venv/bin/python ${RC_REPO}/src/hooks/session_resume_inject.py",
            "timeout": 5000 }
      ]}
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command",
                    "command": "${RC_REPO}/.venv/bin/python ${RC_REPO}/src/hooks/session_resume_inject.py",
                    "timeout": 5000 }] }
    ],
    "PreCompact": [
      { "hooks": [{ "type": "command",
                    "command": "${RC_REPO}/.venv/bin/python ${RC_REPO}/src/hooks/pre_compact_guard.py",
                    "timeout": 5000 }] }
    ]
  }
}
```

The `.venv/bin/python` prefix is **load-bearing**. `${RC_REPO}` is POSIX
parameter expansion, evaluated by Claude Code's shell-invoked hook command.

### Step 4 — Conservative env defaults globally

```bash
# ~/.zshrc
export RC_SHADOW_MODE=1            # log-only by default
export RC_LANG_LOCK=0              # off by default — calibrate per-repo
export RC_PLAN_BLOCK=0
export S2_FAIL_CLOSED=0            # fail-OPEN globally; flip 1 only after daemon proves stable
```

Per-repo `.envrc` files override these for projects where you want stricter
posture.

### Step 5 — Verify

```bash
cd ~/some/other/project
claude
# … in another terminal …
ls ~/.local/share/reasoning-core/events/$(date +%F)/    # decisions from this project's session
rc status
```

### Gotchas

- **Hook arrays merge ADDITIVELY** between `~/.claude/settings.json` and
  project-local `.claude/settings.local.json`. Register a hook in both places
  and it runs **twice per edit**. Pick one.
- **Iter-3 levers won't fire globally** unless you also register
  `session_start_best_effort.py` in the global SessionStart array. See
  [`iter3-levers.md`](iter3-levers.md).
- **Uninstall (global):**
  ```bash
  jq 'del(.hooks.PreToolUse |= map(select(.hooks[0].command|test("reasoning-core")|not)))' \
     ~/.claude/settings.json > /tmp/s.json && mv /tmp/s.json ~/.claude/settings.json
  # repeat for PostToolUse, SessionStart, UserPromptSubmit, PreCompact, then
  launchctl unload ~/Library/LaunchAgents/com.reasoning-core.supervisor.plist   # macOS
  systemctl --user disable --now reasoning-core-sidecar.service                  # Linux
  ```

---

## Cato VPN / corporate TLS-MITM

Cato Networks (and Zscaler etc.) injects its own root CA into TLS chains;
`certifi`'s pinned bundle rejects it as "self-signed certificate in
certificate chain".

The repo's `.envrc` builds `~/.cache/reasoning-core/ca-bundle.pem`
(certifi + Cato root) on macOS and exports `SSL_CERT_FILE` /
`REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE`. `direnv reload` after first `cd`.

Linux users add their corporate root to the bundle manually — the `security
find-certificate` block is macOS-only.

---

## Failure modes

- **`RC_REPO` wrong/unset:** hooks 404, every Edit fails. Symptom: stderr
  shows `command not found` or `python3: can't open file`. Fix: re-export.
- **Sidecar dead + `S2_FAIL_CLOSED=1`:** every Edit hard-blocks. Symptom:
  `[hybrid-reasoner] BLOCKED: sidecar unreachable`. Fix: `rc status`,
  restart the daemon.
- **venv missing deps:** every hook ImportErrors. Symptom:
  `ModuleNotFoundError: No module named 'tree_sitter'` / `'yaml'`. Fix:
  `cd $RC_REPO && .venv/bin/pip install -r requirements.txt` (PyYAML is
  required for the rule engine; the in-tree fallback is opt-in only).
- **First-run sidecar slow:** downloads embedder weights. `mamba-130m`:
  ~250 MB, ~30s on CPU. `codestral-mamba`: ~14 GB. Watch
  `tail -f /tmp/reasoning-core-sidecar.log`.
- **`BackboneUnavailableError: No pinned revision for ...`**: a
  non-default `RC_EMBEDDER` backend needs `RC_<REPO_SLUG>_REVISION=<40-char
  SHA>`. Branch names are explicitly rejected.
- **`S2_URL points outside loopback`**: set `S2_ALLOW_REMOTE=1` to opt into
  remote sidecar. Off by default; blocks SSRF-style source exfil via
  `.envrc`/`.mcp.json` injection.
- **Hosted critic refuses to send `Authorization`**: add the host to
  `RC_GEN_ALLOWED_HOSTS=api.scaleway.ai,...` (CSV). Bearer auth is scoped
  to loopback or allowed hosts; cross-origin 30x redirects refused.
