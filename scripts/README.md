# scripts/

Operational scripts for the reasoning-core sidecars.

## Sidecar supervisor (launchd LaunchAgent)

The supervisor (`src/sidecar_supervisor.py`) boots and monitors the Mamba sidecar
(port `8765`) and, when `RC_REASONER_BACKEND` is `mlx` or `llama`, the generative
sidecar (port `8766`). It polls `/health` every 5s, restarts dead children with
exponential backoff, and circuit-breaks for 60s after 3 consecutive failures.

### Install

```sh
./scripts/install-supervisor-launchagent.sh
```

The installer:
1. Reads `launchd/com.reasoning-core.supervisor.plist`.
2. Substitutes the `__REPO__` placeholder with the current repo path.
3. Writes the rendered plist to `~/Library/LaunchAgents/com.reasoning-core.supervisor.plist`.
4. Runs `launchctl unload` (ignoring errors), then `launchctl load -w`.

It assumes a venv at `${REPO}/.venv` with `src.sidecar_supervisor` importable.

To override the optional generative backend, edit
`~/Library/LaunchAgents/com.reasoning-core.supervisor.plist` and set
`RC_REASONER_BACKEND` (e.g. `mlx`), then `launchctl unload` + `launchctl load -w`.

### Status

```sh
launchctl list | grep reasoning-core
```

A non-zero PID in the first column means the supervisor is running.

### Logs

```sh
tail -f /tmp/rc-supervisor.out.log /tmp/rc-supervisor.err.log
```

Per-child sidecar logs land in `/tmp/rc-mamba.log` and `/tmp/rc-gen.log`.

### Uninstall

```sh
launchctl unload ~/Library/LaunchAgents/com.reasoning-core.supervisor.plist
rm ~/Library/LaunchAgents/com.reasoning-core.supervisor.plist
```
