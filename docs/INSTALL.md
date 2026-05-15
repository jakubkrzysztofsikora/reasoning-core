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

### 2. Mamba checkpoint (~250 MB, one-time)

```bash
huggingface-cli download state-spaces/mamba-130m-hf
```

### 3. Boot the sidecar

```bash
bash scripts/start-sidecar.sh
curl -fsS http://127.0.0.1:8765/health | jq .model_loaded   # → true
```

First CPU boot is ~30s to load Mamba weights.

### 4. Activate direnv

```bash
brew install direnv                                  # or: apt-get install direnv
echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc         # or bash equivalent
cd /path/to/your-repo
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

## Supervisor (always-on sidecar)

A short-lived `bash scripts/start-sidecar.sh` is fine for testing. For
real use, daemonize.

### macOS — launchd

```bash
bash scripts/install-supervisor-launchagent.sh
launchctl list | grep reasoning-core      # → com.reasoning-core.supervisor visible
curl -fsS http://127.0.0.1:8765/health    # → {"status":"ok",...}
tail -f /tmp/rc-sidecar-supervisor.log
```

KeepAlive=true → relaunches on crash. RunAtLoad=true → starts on login.

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
  `ModuleNotFoundError: No module named 'tree_sitter'`. Fix:
  `cd $RC_REPO && .venv/bin/pip install -r requirements.txt`.
- **First-run sidecar slow:** downloads Mamba weights (~250 MB). Subsequent
  boots ~30s on CPU. Watch `tail -f /tmp/reasoning-core-sidecar.log`.
