# Migrating from the v0.1 quickstart to the v0.2 wheel flow

v0.2.0 ships the new bootstrap. The old `git clone` + venv + `pip install
-r requirements.txt` + `huggingface-cli download` + `install.sh` dance
still works (the files are unchanged), but the recommended path is now
two commands:

```bash
pip install reasoning-core[full]
cd /path/to/your-repo && rc init
```

## Walkthrough

1. **Install the wheel.**
   ```bash
   pip install --upgrade reasoning-core[full]
   ```
   This pulls every runtime dep the framework needs (~500 MB across
   torch, transformers, tree-sitter, fastapi, mcp, ruff, …). The
   `rc` console script lands in the same bin dir as your `pip`.

2. **Drop the old checkout.** If you previously cloned reasoning-core
   into `~/repos/reasoning-core` only to install it, you can delete that
   directory. Anything else you keep there is unaffected.

3. **Wire each repo you want gated.**
   ```bash
   cd /path/to/your-repo
   rc init
   ```
   `rc init` writes per-CLI hook files (`.claude/settings.local.json`,
   `.codex/settings.json`, `.gemini/settings.json`, `.copilot/`,
   `.kimi/settings.json`, `.vibe/config.toml`, `.pi/settings.json`),
   a `.envrc` (if you have direnv installed; otherwise you can ignore
   the file), a `.gitignore` marker block, and a per-user sidecar
   supervisor (macOS launchd / Linux systemd).

4. **Run your CLI.**
   ```bash
   claude     # or codex / gemini / copilot / kimi / vibe / pi
   ```

## Why the recommended flow changed

The v0.1 quickstart required eight visible steps before a single hook
fired:

1. `git clone` the framework
2. `cd` into it
3. `python3 -m venv .venv`
4. `source .venv/bin/activate`
5. `pip install -r requirements.txt`
6. `huggingface-cli download state-spaces/mamba-130m-hf`
7. `bash scripts/install-supervisor-launchagent.sh` (macOS) — or run a
   sidecar script by hand
8. `cd` to the target repo, `bash ~/…/reasoning-core/install.sh`

Each is a failure mode. Most users never got past step 3. The v0.2 flow
collapses all of this into a single `pip install` + a single `rc init`,
with no shell scripts and no fork-and-pray `source activate` step.

## Idempotency

`rc init` is idempotent. Re-running it in an already-wired repo records
no new files; the global `~/.copilot/mcp-config.json` is rewritten to
its canonical form (intentional merge). Uninstall with `rc
init-uninstall`, which reverts via the manifest at
`.reasoning-core/install.manifest`.

## What if I'm developing reasoning-core itself?

If you are working on `src/` in a checkout, keep using the old flow:

```bash
git clone https://github.com/jakubkrzysztofsikora/reasoning-core.git
cd reasoning-core
python3 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]           # editable install — your edits to src/ take effect immediately
huggingface-cli download state-spaces/mamba-130m-hf
bash scripts/start-sidecar.sh    # foreground sidecar for development
```

The editable install puts the same `rc` console script on PATH that end
users see, but resolves into your checkout instead of site-packages.
The legacy `install.sh` and `bin/rc` shim are still in the repo for
this reason; they will be removed in v0.4.0.

## Supply-chain hardening

v0.2 wheels are:
- Built by `cibuildwheel` on a matrix of GitHub-hosted runners.
- Signed with sigstore/cosign (keyless, OIDC-backed).
- Published to PyPI via a Trusted Publisher — no long-lived API token.
- Provenance-attested via SLSA.

The Mamba-130m checkpoint download inside `rc init` continues to use
the SHA pin `1e76775f628fbf1350fbe4dbb3d971ba64af25a1` (mutable refs
rejected; see `src/ssm_backbone.py`).

## Troubleshooting

- **`rc: command not found`** — your `pip` and the shell that runs `rc`
  disagree on which Python to use. Either activate the venv you pip'd
  into, or reinstall with `python3 -m pip install --user
  reasoning-core[full]` and ensure `~/.local/bin` is on PATH.
- **`rc init` hangs on the mamba download** — set
  `HF_HOME=/your/writable/path` and re-run, or pass `--no-model` and
  prefetch separately with `python3 -c "from huggingface_hub import
  snapshot_download; snapshot_download('state-spaces/mamba-130m-hf',
  revision='1e76775f628fbf1350fbe4dbb3d971ba64af25a1')"`.
- **`launchctl load` fails on macOS** — the supervisor install may
  require you to approve `~/Library/LaunchAgents/com.reasoning-core.supervisor.plist`
  in System Settings → Login Items. `rc init` will surface the error;
  approve and re-run with `rc init` to retry.
