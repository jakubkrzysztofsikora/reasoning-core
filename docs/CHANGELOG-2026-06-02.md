# CHANGELOG — 2026-06-02

Memory-watchdog incident response. Host swap-thrashed after the gen
sidecar (`mlx_lm.server`) grew to 36 GB and the S2 sidecar to 37 GB under
a nominal 32 GB cap.

## Root causes

1. The watchdog read `ru_maxrss`, which on macOS counts only CPU resident
   pages. MLX / Metal / IOSurface unified-memory allocations are
   invisible to it — Activity Monitor's "Memory" column (the OS memory
   pressure source) reports `phys_footprint`, which is what we needed.
   The "32 GB cap" therefore never fired.
2. `mlx_lm.server` runs as an external Python entry point and never
   calls `apply_boot_guards` — it had no memory watchdog at all.
3. Sidecars were started by hand from shells (`start-sidecar.sh`,
   `start-gen-sidecar.sh`), bypassing the supervisor's single-instance
   lock. Zombie processes from prior sessions accumulated until the
   host ran out of headroom.

## Behavior changes

- **`S2_MEM_LIMIT_GB` default `32` → `25`** in `.envrc`. With both
  sidecars active on a 64 GB host the new ceiling leaves headroom for
  GPU drivers and the rest of macOS.
- **`launchd/com.reasoning-core.supervisor.plist`** now declares
  `RC_REASONER_BACKEND=mlx`, `S2_MEM_LIMIT_GB=25`,
  `S2_GEN_MEM_LIMIT_GB=25`, and `PYTHONPATH=__REPO__:__REPO__/src` so the
  supervisor brings up both children and forwards the caps.
- **Gen sidecar is now wrapped** by `src/gen_sidecar_launcher.py`. The
  launcher polls the `mlx_lm.server` child's `phys_footprint` and
  SIGKILLs it on cap-exceeded; the launcher then exits 75 so the
  supervisor restarts it.

## Code changes

- `src/sidecar_boot.py`
  - New `_phys_footprint_darwin(pid=None)` reads
    `proc_pid_rusage(RUSAGE_INFO_V4).ri_phys_footprint`.
  - `_rss_bytes()` returns live phys_footprint on macOS (was monotonic
    `ru_maxrss`).
  - Accepts a `pid` arg so the gen launcher can poll its child.
- `src/gen_sidecar_launcher.py` (new). Wraps `mlx_lm.server` /
  `llama_cpp.server`, applies the single-instance flock, and runs the
  watchdog against the child pid. Resolves `mlx_lm.server` via
  `.venv/bin/` first so launchd-spawned processes don't need it on
  PATH.
- `src/_supervisor_env.py` allowlist extended:
  `S2_MEM_LIMIT_GB`, `S2_GEN_MEM_LIMIT_GB`, `S2_GEN_MEM_POLL_S`,
  `S2_SINGLE_INSTANCE`, `S2_GEN_SINGLE_INSTANCE`.
- `src/sidecar_supervisor.py` gen child now runs
  `python -m gen_sidecar_launcher` instead of
  `bash scripts/start-gen-sidecar.sh`.

## Tests

- `tests/test_sidecar_boot.py` — 11 tests, includes regression that the
  watchdog actually fires on cap exceeded.
- `tests/test_gen_sidecar_launcher.py` (new) — 6 tests, includes a live
  test that allocates 200 MB in a child, caps at 1 MB, and verifies the
  child receives SIGKILL while the launcher exits 75.

## Operator notes

- `scripts/start-sidecar.sh` and `scripts/start-gen-sidecar.sh` still
  work for manual / foreground testing but bypass the single-instance
  lock — prefer `bash scripts/install-supervisor-launchagent.sh`.
- Picking up Python edits in `src/`:
  `launchctl kickstart -k gui/$(id -u)/com.reasoning-core.supervisor`.
- Picking up plist edits: re-run `install-supervisor-launchagent.sh`.
- launchd does not read `.envrc`; env vars meant for the supervisor
  must live in the plist `EnvironmentVariables` block.
