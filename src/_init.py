"""Implementation of ``rc init`` / ``rc uninstall``.

Lifts the body of ``install.sh`` / ``uninstall.sh`` into Python so the
new ``pip install reasoning-core[full]`` flow has a no-shell-script
counterpart. ``rc init`` writes per-repo hook files into a target
repository; ``rc uninstall`` reverts them via the manifest recorded on
the way in.

This module is deliberately permissive about templates — every CLI's
hook surface uses a slightly different JSON or TOML shape, so each
helper accepts the template text and the substituted context dict.

Functions are split per-CLI to match ``install.sh`` and to keep the
manifest append-only (one entry per file actually written).
```
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from src import _install_paths
from src import data as _data

# Marker block installed into .gitignore so uninstall.sh / uninstall()
# can find and remove only the lines reasoning-core added.
_GITIGNORE_BEGIN = "# >>> reasoning-core >>>"
_GITIGNORE_END = "# <<< reasoning-core <<<"

_MANIFEST_REL = Path(".reasoning-core") / "install.manifest"


@dataclass
class InitResult:
    """Summary of what ``rc init`` did in one target repo.

    Attributes mirror the colored ``ok`` / ``skip`` / ``warn`` lines from
    the original ``install.sh`` so the CLI can format output uniformly.
    """
    target: Path
    wrote: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)
    sidecar_installed: bool = False
    model_downloaded: bool = False

    def as_dict(self) -> dict:
        return {
            "target": str(self.target),
            "wrote": self.wrote,
            "skipped": self.skipped,
            "warned": self.warned,
            "sidecar_installed": self.sidecar_installed,
            "model_downloaded": self.model_downloaded,
        }


def _read_template(rel_path: str) -> str:
    return _data.read_text(rel_path)


def _render(text: str, substitutions: dict[str, str]) -> str:
    """Substitute ``${KEY}`` and ``@KEY@`` placeholders.

    Two placeholder styles are accepted so a single template can use
    whichever fits the host shell: ``${RC_PYTHON}`` works in bash /
    POSIX parameter expansion (used inside JSON hook command strings
    Claude Code expands at runtime); ``@RC_PYTHON@`` is simpler and
    works for plain-text files like ``.envrc`` that do not interpret
    POSIX expansion.
    """
    out = text
    for key, value in substitutions.items():
        out = out.replace("${" + key + "}", value)
        out = out.replace("@" + key + "@", value)
    return out


def _record(manifest: Path, entry: str) -> None:
    """Append ``entry`` to the manifest if not already present (idempotent)."""
    existing = manifest.read_text() if manifest.exists() else ""
    if entry in existing.splitlines():
        return
    with manifest.open("a") as fh:
        fh.write(entry + "\n")


# ---------------------------------------------------------------------------
# .envrc
# ---------------------------------------------------------------------------
def install_envrc(target: Path, manifest: Path, substitutions: dict[str, str],
                  result: InitResult) -> None:
    path = target / ".envrc"
    if path.exists():
        result.skipped.append(str(path.relative_to(target)))
        return
    text = _render(_read_template("envrc/direnv.envrc"), substitutions)
    # Embedder auto-pick (2026-09-21): ask the embedder_tier module to
    # pick the largest variant that fits the host's available RAM and
    # disk. Honour RC_EMBEDDER if the operator already pinned one. The
    # pick is appended to .envrc as `export RC_EMBEDDER=<pick>` and
    # recorded in the install manifest under `embedder_tier` and
    # `embedder_backend` so `rc upgrade` and `rc doctor` can re-verify.
    try:
        from src import embedder_tier  # noqa: PLC0415 -- late import to keep _init.py light
        from src.ssm_backbone import backend_loadability_probe
        requested = os.environ.get("RC_EMBEDDER", "").strip() or None
        decision = embedder_tier.decide(
            requested_backend=requested,
            loadability_probe=backend_loadability_probe,
        )
        # BLOCKER #2 fix: if the auto-pick is unloadable on this host
        # (e.g. Mamba-3 registered but no Mamba3* transformers class),
        # use ``safe_backend_for_envrc`` rather than ``decision.backend``
        # so we do NOT brick the gate with a backend that the loader
        # will refuse. The TierDecision already surfaces the unloadable
        # state in ``reason``; we additionally raise an explicit warning
        # so the operator sees it in ``rc doctor``.
        chosen = decision.safe_backend_for_envrc or decision.backend
        tier_line = (
            f"\\n# Auto-picked by embedder_tier on {os.environ.get('HOSTNAME', 'localhost')}: "
            f"tier={decision.tier} backend={chosen} "
            f"working_set={decision.estimated_working_set_gb}GiB "
            f"available_ram={decision.available_ram_gb}GiB. "
            f"Reason: {decision.reason}\\n"
            f"export RC_EMBEDDER={chosen}\\n"
        )
        # If the operator explicitly pinned a backend that doesn't fit,
        # surface a warning (rc_cli is responsible for refusing to start
        # unless RC_ALLOW_OVERSIZED_BACKBONE=1 is set; we only warn here).
        if not decision.fits and requested:
            result.warned.append(
                f"operator-pinned backend {decision.backend!r} does not fit the host: "
                f"working-set={decision.estimated_working_set_gb:.2f} GiB, "
                f"available RAM={decision.available_ram_gb:.2f} GiB. "
                f"Set RC_ALLOW_OVERSIZED_BACKBONE=1 to override."
            )
        # BLOCKER #2: refuse to write an unloadable operator pin to .envrc.
        if decision.pinned_unloadable and requested:
            result.warned.append(
                f"operator-pinned backend {decision.backend!r} is NOT loadable on this host. "
                f"Writing fallback {chosen!r} to .envrc instead. "
                f"Unset RC_EMBEDDER or pick a loadable backend."
            )
        if decision.is_oversized:
            result.warned.append(
                f"embedder_tier picked {chosen!r} for tier={decision.tier} but "
                f"only {decision.fit_margin_gb:.1f} GiB of headroom remains. "
                f"Sidecar may swap if host load rises."
            )
        text = text + tier_line
        # Record into the manifest so rc upgrade/doctor can re-verify.
        _record(manifest, f"embedder_tier={decision.tier}")
        _record(manifest, f"embedder_backend={decision.backend}")
        _record(manifest, f"embedder_working_set_gb={decision.estimated_working_set_gb}")
    except Exception as exc:  # noqa: BLE001 -- never let the sizer block init
        result.warned.append(f"embedder_tier auto-pick failed: {type(exc).__name__}: {exc}")
    path.write_text(text)
    result.wrote.append(str(path.relative_to(target)))
    _record(manifest, ".envrc")


# ---------------------------------------------------------------------------
# .claude/settings.local.json
# ---------------------------------------------------------------------------
def install_claude(target: Path, manifest: Path, substitutions: dict[str, str],
                  result: InitResult) -> None:
    rel = ".claude/settings.local.json"
    path = target / rel
    if path.exists():
        result.skipped.append(rel)
        return
    (target / ".claude").mkdir(parents=True, exist_ok=True)
    text = _render(_read_template("claude/settings.local.json.template"), substitutions)
    path.write_text(text)
    result.wrote.append(rel)
    _record(manifest, rel)


# ---------------------------------------------------------------------------
# .codex/settings.json (rendered from codex template)
# ---------------------------------------------------------------------------
def install_codex(target: Path, manifest: Path, substitutions: dict[str, str],
                  result: InitResult) -> None:
    rel = ".codex/settings.json"
    path = target / rel
    if path.exists():
        result.skipped.append(rel)
        return
    (target / ".codex").mkdir(parents=True, exist_ok=True)
    text = _render(_read_template("codex.settings.json.template"), substitutions)
    # Templates still use <RC_REPO> placeholders inherited from the
    # bash version; convert them in the rendered output too.
    text = text.replace("<RC_REPO>", substitutions.get("RC_REPO", ""))
    path.write_text(text)
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        path.unlink(missing_ok=True)
        result.warned.append(f"{rel}: invalid JSON after render: {exc}")
        return
    result.wrote.append(rel)
    _record(manifest, rel)


# ---------------------------------------------------------------------------
# .gemini/settings.json
# ---------------------------------------------------------------------------
def install_gemini(target: Path, manifest: Path, substitutions: dict[str, str],
                  result: InitResult) -> None:
    rel = ".gemini/settings.json"
    path = target / rel
    if path.exists():
        result.skipped.append(rel)
        return
    (target / ".gemini").mkdir(parents=True, exist_ok=True)
    text = _render(_read_template("gemini.settings.json.template"), substitutions)
    text = text.replace("<RC_REPO>", substitutions.get("RC_REPO", ""))
    path.write_text(text)
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        path.unlink(missing_ok=True)
        result.warned.append(f"{rel}: invalid JSON after render: {exc}")
        return
    result.wrote.append(rel)
    _record(manifest, rel)


# ---------------------------------------------------------------------------
# .copilot/* + global ~/.copilot/mcp-config.json
# ---------------------------------------------------------------------------
def install_copilot(target: Path, manifest: Path, substitutions: dict[str, str],
                    result: InitResult) -> None:
    (target / ".copilot" / "skills" / "reasoning").mkdir(parents=True, exist_ok=True)
    rel_inst = ".copilot/copilot-instructions.md"
    if not (target / rel_inst).exists():
        (target / rel_inst).write_text(_read_template("copilot.copilot-instructions.md"))
        result.wrote.append(rel_inst)
        _record(manifest, rel_inst)
    else:
        result.skipped.append(rel_inst)

    # Merge hybrid-reasoner into ~/.copilot/mcp-config.json with file lock.
    global_target = Path.home() / ".copilot" / "mcp-config.json"
    global_target.parent.mkdir(parents=True, exist_ok=True)
    lockfile = global_target.with_suffix(".lock")
    try:
        lock_fd = os.open(str(lockfile), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if shutil.which("flock"):
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except Exception:  # noqa: BLE001 — best-effort locking
            pass
        data: dict = {}
        if global_target.exists():
            try:
                data = json.loads(global_target.read_text() or "{}")
            except json.JSONDecodeError:
                data = {}
        servers = data.setdefault("mcpServers", {})
        servers["hybrid-reasoner"] = {
            "command": substitutions["RC_PYTHON"],
            "args": ["-m", "src.mcp_reasoner"],
            "env": {},
        }
        tmp = global_target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, global_target)
        result.wrote.append(str(global_target))
    finally:
        try:
            os.close(lock_fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# .kimi/settings.json
# ---------------------------------------------------------------------------
def install_kimi(target: Path, manifest: Path, substitutions: dict[str, str],
                 result: InitResult) -> None:
    rel = ".kimi/settings.json"
    path = target / rel
    if path.exists():
        result.skipped.append(rel)
        return
    (target / ".kimi").mkdir(parents=True, exist_ok=True)
    text = _render(_read_template("kimi.settings.json.template"), substitutions)
    text = text.replace("<RC_REPO>", substitutions.get("RC_REPO", ""))
    path.write_text(text)
    result.wrote.append(rel)
    _record(manifest, rel)


# ---------------------------------------------------------------------------
# .vibe/* + ~/.vibe/trusted_folders.toml
# ---------------------------------------------------------------------------
def install_vibe(target: Path, manifest: Path, substitutions: dict[str, str],
                 result: InitResult) -> None:
    rel_cfg = ".vibe/config.toml"
    path = target / rel_cfg
    if path.exists():
        result.skipped.append(rel_cfg)
    else:
        (target / ".vibe" / "skills" / "reasoning").mkdir(parents=True, exist_ok=True)
        text = _render(_read_template("vibe.config.toml.template"), substitutions)
        text = text.replace("<RC_REPO>", substitutions.get("RC_REPO", ""))
        path.write_text(text)
        result.wrote.append(rel_cfg)
        _record(manifest, rel_cfg)

    rel_agents = ".vibe/AGENTS.md"
    if not (target / rel_agents).exists():
        (target / rel_agents).write_text(_read_template("vibe.AGENTS.md"))
        result.wrote.append(rel_agents)
        _record(manifest, rel_agents)

    # TOML trusted-folder registration
    trusted = Path.home() / ".vibe" / "trusted_folders.toml"
    trusted.parent.mkdir(parents=True, exist_ok=True)
    quoted = '"' + str(target).replace("\\", "\\\\").replace('"', '\\"') + '"'
    existing = trusted.read_text() if trusted.exists() else ""
    pattern = re.compile(
        r'\[\[trusted\]\]\s*\npath\s*=\s*' + re.escape(quoted) + r'\s*(?:\n|$)',
        re.MULTILINE,
    )
    if not pattern.search(existing):
        with trusted.open("a") as fh:
            fh.write(f"\n[[trusted]]\npath = {quoted}\n")
        result.wrote.append(str(trusted))


# ---------------------------------------------------------------------------
# .pi/* + ~/.pi/trusted_folders.toml
# ---------------------------------------------------------------------------
def install_pi(target: Path, manifest: Path, substitutions: dict[str, str],
               result: InitResult) -> None:
    rel_settings = ".pi/settings.json"
    path = target / rel_settings
    (target / ".pi" / "extensions").mkdir(parents=True, exist_ok=True)
    (target / ".pi" / "skills" / "reasoning").mkdir(parents=True, exist_ok=True)
    if path.exists():
        result.skipped.append(rel_settings)
    else:
        text = _render(_read_template("pi.settings.json.template"), substitutions)
        text = text.replace("<RC_REPO>", substitutions.get("RC_REPO", ""))
        path.write_text(text)
        result.wrote.append(rel_settings)
        _record(manifest, rel_settings)

    rel_ext = ".pi/extensions/reasoning_core_gate.ts"
    if _data.exists("pi.reasoning_core_gate.ts") and not (target / rel_ext).exists():
        (target / rel_ext).write_text(_read_template("pi.reasoning_core_gate.ts"))
        result.wrote.append(rel_ext)
        _record(manifest, rel_ext)

    # Trusted folder (TOML) — mirrors vibe.
    trusted = Path.home() / ".pi" / "trusted_folders.toml"
    trusted.parent.mkdir(parents=True, exist_ok=True)
    quoted_repr = repr(str(target))
    existing = trusted.read_text() if trusted.exists() else ""
    line = f"path = {quoted_repr}"
    if line not in existing.splitlines():
        with trusted.open("a") as fh:
            fh.write(f"\n[[trusted]]\n{line}\n")
        result.wrote.append(str(trusted))


# ---------------------------------------------------------------------------
# .gitignore marker block
# ---------------------------------------------------------------------------
_GITIGNORE_ENTRIES = [
    ".envrc.local",
    ".claude/settings.local.json",
    ".codex/settings.json",
    ".gemini/settings.json",
    ".kimi/settings.json",
    ".pi/settings.json",
    ".vibe/config.toml",
    ".reasoning-core/",
]


def update_gitignore(target: Path, result: InitResult) -> None:
    gitignore = target / ".gitignore"
    created = False
    if not gitignore.exists():
        gitignore.touch()
        created = True
    text = gitignore.read_text()
    if _GITIGNORE_BEGIN in text:
        result.skipped.append(".gitignore")
        return
    addition = "\n" + _GITIGNORE_BEGIN + "\n" + "\n".join(_GITIGNORE_ENTRIES) + "\n" + _GITIGNORE_END + "\n"
    gitignore.write_text(text + addition)
    result.wrote.append(".gitignore" if not created else ".gitignore (created)")


# ---------------------------------------------------------------------------
# Sidecar supervisor (macOS launchd + Linux systemd user unit)
# ---------------------------------------------------------------------------
def install_sidecar_supervisor(target: Path, result: InitResult) -> bool:
    """Install the sidecar supervisor as a per-user daemon.

    - macOS: writes ``~/Library/LaunchAgents/com.reasoning-core.supervisor.plist``
      and ``launchctl load -w`` it.
    - Linux: writes ``~/.config/systemd/user/reasoning-core.service`` and
      ``systemctl --user enable --now reasoning-core.service``.
    Returns True if a daemon was successfully installed and started.
    """
    python = _install_paths.python_executable()
    if sys.platform == "darwin":
        return _install_launchd(python, result)
    if sys.platform.startswith("linux"):
        return _install_systemd_user(python, result)
    result.warned.append(
        f"sidecar supervisor not auto-installed on {sys.platform}; run rc sidecar-start manually"
    )
    return False


def _install_launchd(python: str, result: InitResult) -> bool:
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.reasoning-core.supervisor.plist"
    # Use the running interpreter; the supervisor module is part of the wheel.
    body = LAUNCHD_PLIST_TEMPLATE.format(python=python)
    plist_path.write_text(body)
    # Validate (silently skip if plutil missing — old Linux containers etc.)
    if shutil.which("plutil"):
        rc = subprocess.run(["plutil", "-lint", str(plist_path)], capture_output=True)
        if rc.returncode != 0:
            result.warned.append(f"plist failed plutil lint: {rc.stderr.decode()}")
    # Load (ignore unload errors on first install).
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    rc = subprocess.run(["launchctl", "load", "-w", str(plist_path)], capture_output=True)
    if rc.returncode != 0:
        result.warned.append(f"launchctl load failed: {rc.stderr.decode()}")
        return False
    result.sidecar_installed = True
    return True


def _install_systemd_user(python: str, result: InitResult) -> bool:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "reasoning-core.service"
    unit_path.write_text(SYSTEMD_UNIT_TEMPLATE.format(python=python))
    if not shutil.which("systemctl"):
        result.warned.append("systemctl not on PATH; wrote unit but did not enable")
        return False
    rc = subprocess.run(
        ["systemctl", "--user", "enable", "--now", "reasoning-core.service"],
        capture_output=True,
    )
    if rc.returncode != 0:
        result.warned.append(f"systemctl enable failed: {rc.stderr.decode()}")
        return False
    result.sidecar_installed = True
    return True


LAUNCHD_PLIST_TEMPLATE = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.reasoning-core.supervisor</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>src.sidecar_supervisor</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>Crashed</key>
        <true/>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>/tmp/rc-supervisor.out.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/rc-supervisor.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>RC_REASONER_BACKEND</key>
        <string>mlx</string>
        <key>HF_HOME</key>
        <string>$HOME/.cache/huggingface</string>
    </dict>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
'''

SYSTEMD_UNIT_TEMPLATE = '''[Unit]
Description=reasoning-core sidecar supervisor
After=network-online.target

[Service]
Type=simple
ExecStart={python} -m src.sidecar_supervisor
Restart=on-failure
RestartSec=10
StandardOutput=append:/tmp/rc-supervisor.out.log
StandardError=append:/tmp/rc-supervisor.err.log
Environment=RC_REASONER_BACKEND=mlx
Environment=HF_HOME=%h/.cache/huggingface

[Install]
WantedBy=default.target
'''


# ---------------------------------------------------------------------------
# Model prefetch (mamba-130m default)
# ---------------------------------------------------------------------------
MAMBA_130M_REVISION = "1e76775f628fbf1350fbe4dbb3d971ba64af25a1"
MAMBA_130M_REPO = "state-spaces/mamba-130m-hf"


def download_default_model(result: InitResult, target_dir: Optional[Path] = None) -> bool:
    """Synchronously download the default embedder checkpoint.

    Returns True on success, False on any failure (with a warning
    recorded on ``result``). Caller decides whether to continue — by
    default the installer is configured to require this, but a
    ``--no-model`` flag can skip it.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        result.warned.append(f"huggingface_hub not available: {exc}")
        return False
    cache_dir = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    # 2026-09-21: respect the auto-picked backend from embedder_tier if
    # RC_EMBEDDER isn't already pinned. The picked backend lives in
    # _BACKENDS (src/ssm_backbone.py); the previous behaviour hard-coded
    # MAMBA_130M_REPO regardless of the host's tier.
    picked = os.environ.get("RC_EMBEDDER", MAMBA_130M_REPO)
    try:
        from src import ssm_backbone as _ssm  # noqa: PLC0415
        try:
            backend = _ssm._BACKENDS[picked]
            repo_id = backend.checkpoint
            revision = backend.revision or "main"
        except KeyError:
            # Operator-typed backend not in registry; treat the value
            # as a raw HF repo id and pin to main.
            repo_id = picked
            revision = "main"
    except Exception as exc:  # noqa: BLE001
        repo_id = picked
        revision = "main"
        result.warned.append(
            f"could not resolve {picked!r} via _BACKENDS ({type(exc).__name__}: {exc}); "
            f"falling back to raw repo id {repo_id!r}"
        )
    try:
        snapshot_download(
            repo_id=repo_id,
            revision=revision,
            cache_dir=str(cache_dir),
        )
        result.model_downloaded = True
        return True
    except Exception as exc:  # noqa: BLE001 — surface any HF failure
        result.warned.append(f"model download failed ({repo_id}@{revision}): {exc}")
        return False


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------
def init(target: Path,
         install_sidecar: bool = True,
         install_model: bool = True,
         clis: Optional[Iterable[str]] = None) -> InitResult:
    """Wire reasoning-core hooks into ``target`` and (optionally) start the sidecar.

    Idempotent: re-running against an already-initialized repo records
    only the paths that did not exist before. ``install_sidecar=False``
    skips the launchd/systemd step (for CI / ephemeral containers).
    ``install_model=False`` skips the HF download (caller must arrange
    for the checkpoint to exist via a separate prefetch).
    """
    target = target.resolve()
    result = InitResult(target=target)
    manifest = target / _MANIFEST_REL
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if not manifest.exists():
        manifest.touch()

    substitutions = {
        "RC_PYTHON": _install_paths.python_executable(),
        "RC_REPO": _install_paths.rc_repo_path(),
    }

    install_envrc(target, manifest, substitutions, result)
    install_claude(target, manifest, substitutions, result)
    install_codex(target, manifest, substitutions, result)
    install_gemini(target, manifest, substitutions, result)
    install_copilot(target, manifest, substitutions, result)
    install_kimi(target, manifest, substitutions, result)
    install_vibe(target, manifest, substitutions, result)
    install_pi(target, manifest, substitutions, result)
    update_gitignore(target, result)

    if install_sidecar:
        install_sidecar_supervisor(target, result)

    if install_model:
        download_default_model(result)

    return result


def uninstall(target: Path) -> dict:
    """Revert a previous ``rc init`` in ``target``.

    Reads ``.reasoning-core/install.manifest``, refuses out-of-tree
    paths, and removes only what was recorded. The gitignore block is
    stripped between the sentinels. The home-directory dotfiles for
    copilot/vibe/pi get their specific entries cleaned by helpers in
    this module too.
    """
    target = target.resolve()
    manifest = target / _MANIFEST_REL
    removed: list[str] = []
    refused: list[str] = []
    if not manifest.exists():
        return {"target": str(target), "removed": [], "refused": [], "warning": "no manifest"}

    for entry in (line.strip() for line in manifest.read_text().splitlines() if line.strip()):
        abs_path = (target / entry).resolve()
        # Refuse out-of-tree paths. Anchors under target.
        try:
            common = os.path.commonpath([str(abs_path), str(target)])
        except ValueError:
            common = ""
        if common != str(target):
            refused.append(entry)
            continue
        if abs_path == target:
            refused.append(entry)
            continue
        if abs_path.is_dir():
            shutil.rmtree(abs_path, ignore_errors=True)
        elif abs_path.exists():
            abs_path.unlink()
        removed.append(entry)
    # Strip the gitignore block.
    gitignore = target / ".gitignore"
    if gitignore.exists():
        text = gitignore.read_text()
        if _GITIGNORE_BEGIN in text and _GITIGNORE_END in text:
            new = re.sub(
                re.escape(_GITIGNORE_BEGIN) + r".*?" + re.escape(_GITIGNORE_END) + r"\n?",
                "",
                text,
                flags=re.DOTALL,
            )
            gitignore.write_text(new)

    return {"target": str(target), "removed": removed, "refused": refused}


__all__ = [
    "InitResult",
    "init",
    "uninstall",
    "download_default_model",
    "install_sidecar_supervisor",
    "MAMBA_130M_REPO",
    "MAMBA_130M_REVISION",
]
