"""Evidence-bound OMP readiness: installation, loading, auth, qualification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from src import _install_paths, data
from src._omp_install import COMPATIBILITY, EXTENSION, read_ownership, render_extension

POLICY_FILES = (".omp/settings.json", ".omp/config.yml", ".omp/config.yaml",
                ".claude/settings.yml", ".claude/settings.yaml", ".claude/settings.json",
                ".claude/settings.local.json", ".reasoning-core/rules.yaml",
                ".reasoning-core/rules.schema.yaml", ".reasoning-core/git_worktrees.yaml",
                "PLAN.md", ".reasoning-core/contract.yaml")

REQUIRED_EVIDENCE = (
    "allowed_write", "denied_unchanged", "receipt_verified",
    "child_guard_retained", "restrictions_verified",
    "extension_bash_screened", "extension_write_roundtrip", "extension_subagent_recorded",
    "extension_advisor_recorded",
)
_HASH = re.compile(r"[0-9a-f]{64}")


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _json_process(argv: list[str], payload: dict, timeout: float = 40, *, env: dict | None = None) -> dict | None:
    try:
        result = subprocess.run(
            argv, input=json.dumps(payload), encoding="utf-8", capture_output=True,
            timeout=timeout, check=False, env=env,
        )
        if result.returncode or len(result.stdout) + len(result.stderr) > 1024 * 1024:
            return None
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _runtime_probe(project: Path) -> dict | None:
    executable = shutil.which("omp")
    if not executable:
        return None
    # The pinned host patch implements this diagnostic without a provider call.
    return _json_process([executable, "--rc-native-probe"], {"project_dir": str(project)})


def _runtime_paths(project: Path) -> tuple[str, str]:
    text = (project / EXTENSION).read_text()
    values = []
    for name in ("PYTHON", "ROOT"):
        match = re.search(r"const " + name + r" = (" + r'"(?:[^"\\]|\\.)*"' + r");", text)
        if not match:
            raise ValueError("invalid OMP runtime asset")
        value = json.loads(match.group(1))
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError("invalid OMP runtime path")
        values.append(value)
    return values[0], values[1]


_AUTH_SCRIPT = """
import contextlib,json,sys
sys.path.insert(0,sys.argv[1])
from src import mcp_gate
from src.hooks import omp_bridge
event=json.load(sys.stdin)
with contextlib.redirect_stdout(sys.stderr):
    response=mcp_gate.request_score({
        "path":event["project_dir"]+"/.reasoning-core/omp-readiness.py",
        "before_src":"probe = 1\\n","after_src":"probe = 2\\n","change_kind":"edit",
        "host":"omp","session_id":"rc-omp-doctor","agent_id":"rc-omp-doctor",
        "project_dir":event["project_dir"],"tool_call_id":"readiness",
    })
    report=response.json() if response.status_code==200 else {}
authenticated=(response.status_code==200 and isinstance(report,dict)
    and type(report.get("regression_detected")) is bool
    and isinstance(report.get("architectural_impact_score"),(int,float)))
print(json.dumps({"authenticated":authenticated,"python":sys.executable,
    "bridge_path":omp_bridge.__file__}))
"""


def _authentication_probe(project: Path) -> dict | None:
    try:
        python, root = _runtime_paths(project)
        return _json_process([python, "-I", "-c", _AUTH_SCRIPT, root], {"project_dir": str(project)})
    except (OSError, ValueError):
        return None


def qualification_binding(project: Path, runtime: dict) -> dict:
    policies = POLICY_FILES
    framework = Path(_install_paths.rc_repo_path())
    return {
        "version": runtime.get("version"), "source_sha": runtime.get("source_sha"),
        "native_protocol": runtime.get("native_protocol"),
        "native_sha256": runtime.get("native_sha256"),
        "host_sha256": runtime.get("host_sha256"),
        "host_configuration_sha256": runtime.get("host_configuration_sha256"),
        "adapter_sha256": _sha(project / EXTENSION),
        "compatibility_sha256": _sha(project / COMPATIBILITY),
        "bridge_sha256": _sha(framework / "src/hooks/omp_bridge.py"),
        "contract_sha256": _sha(framework / "src/omp_contract.py"),
        "configuration": {path: _sha(project / path) for path in policies},
        "implementation": {str(path.relative_to(framework)): _sha(path)
                           for path in sorted((framework / "src").rglob("*.py"))},
        "environment": {name: hashlib.sha256(value.encode()).hexdigest()
                        for name, value in sorted(os.environ.items())
                        if name.startswith(("RC_", "S2_", "OMP_", "PI_"))},
    }


def _host_verified(runtime: dict, compatibility: dict) -> bool:
    if runtime.get("host_kind") == "binary":
        expected = compatibility.get("host_binary_sha256")
        return isinstance(expected, str) and _HASH.fullmatch(expected) is not None and runtime.get("host_sha256") == expected
    try:
        expected = json.loads(data.read_text("omp.host-files.json"))
        return runtime.get("host_kind") == "source" and runtime.get("host_files") == expected and isinstance(runtime.get("host_sha256"), str) and _HASH.fullmatch(runtime["host_sha256"]) is not None
    except (OSError, ValueError):
        return False


PLUGIN_PACKAGE = "reasoning-core"
PLUGIN_DIR = "integrations/omp-plugin"
PLUGIN_GATE = "extensions/reasoning_core_gate.ts"


def _plugin_expected_mode(project: Path) -> str:
    rc_dir = project / ".reasoning-core"
    if not rc_dir.is_dir():
        return "dormant"
    try:
        runtime = json.loads((rc_dir / "omp-runtime.json").read_text())
    except (OSError, ValueError):
        return "failclosed"
    if isinstance(runtime, dict) and runtime.get("python") and runtime.get("repo_root"):
        return "active"
    return "failclosed"


def _plugin_installed() -> tuple[bool, str]:
    try:
        plugin = Path(_install_paths.rc_repo_path()) / PLUGIN_DIR
        gate = plugin / PLUGIN_GATE
        pkg = json.loads((plugin / "package.json").read_text())
        manifest = json.loads((plugin / "plugin.json").read_text())
        link = Path.home() / ".omp/plugins/node_modules" / PLUGIN_PACKAGE
        entry = f"./{PLUGIN_GATE}"
        installed = (
            gate.is_file()
            and entry in (pkg.get("omp") or {}).get("extensions", [])
            and entry in manifest.get("extensions", [])
            and manifest.get("integrity", {}).get(Path(PLUGIN_GATE).name) == _sha(gate)
            and link.is_symlink()
            and link.resolve() == plugin.resolve()
        )
        return installed, ("user-scope plugin link, manifest entries, and gate digest verified" if installed
                           else "plugin link, manifest entries, or gate digest mismatch")
    except (OSError, ValueError, KeyError):
        return False, "plugin assets unreadable"


def _plugin_loaded(project: Path) -> tuple[bool, str]:
    executable = shutil.which("omp")
    if not executable:
        return False, "omp executable unavailable"
    logs = Path.home() / ".omp/logs"
    try:
        proc = subprocess.Popen([executable, "--print", "reasoning-core plugin load probe"], cwd=project,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as err:
        return False, f"probe spawn failed: {err}"
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    matches = [p for p in logs.glob(f"omp.*.{proc.pid}.log")] if logs.is_dir() else []
    for log in matches:
        try:
            for line in log.read_text(errors="replace").splitlines():
                if "reasoning-core gate loaded" not in line:
                    continue
                record = json.loads(line)
                if record.get("message") != "reasoning-core gate loaded":
                    continue
                mode = record.get("mode")
                expected = _plugin_expected_mode(project)
                if mode == expected:
                    return True, f"session probe loaded the plugin gate (mode {mode})"
                return False, f"gate loaded with mode {mode}, expected {expected}"
        except (OSError, ValueError):
            continue
    return False, "session probe produced no gate load record"


def check_omp(project: Path) -> list[dict]:
    project = project.resolve()
    checks = []
    def append(name: str, ok: bool, detail: str, state: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "state": state})

    installed = False
    compatibility = {}
    try:
        ownership = read_ownership(project)
        expected = render_extension({
            "RC_PYTHON": _install_paths.python_executable(), "RC_REPO": _install_paths.rc_repo_path(),
        }).encode()
        installed = (
            set(ownership["files"]) == {EXTENSION, COMPATIBILITY}
            and all(_sha(project / name) == row["sha256"] for name, row in ownership["files"].items())
            and (project / EXTENSION).read_bytes() == expected
            and (project / COMPATIBILITY).read_text() == data.read_text("omp.compatibility.json")
        )
        compatibility = json.loads(data.read_text("omp.compatibility.json"))
    except (OSError, ValueError, KeyError):
        pass
    append("omp_installed", installed, "owned assets and current runtime paths match" if installed
           else "assets missing, modified, symlinked, or bound to another interpreter/package", "installed")

    runtime = _runtime_probe(project) if installed else None
    loaded = (
        isinstance(runtime, dict) and runtime.get("schema_version") == 1
        and runtime.get("loaded") is True and runtime.get("model_invoked") is False
        and runtime.get("version") == compatibility.get("upstream_version")
        and runtime.get("source_sha") == compatibility.get("upstream_source_sha")
        and type(runtime.get("native_protocol")) is int and runtime.get("native_protocol") == 1
        and runtime.get("adapter_path") == str(project / EXTENSION)
        and runtime.get("adapter_sha256") == _sha(project / EXTENSION)
        and _host_verified(runtime, compatibility)
        and isinstance(runtime.get("native_sha256"), str)
        and _HASH.fullmatch(runtime["native_sha256"]) is not None
    )
    append("omp_loaded", loaded, "actual host loaded the owned adapter and native protocol 1"
           if loaded else "executable unavailable, discovery disabled, wrong native build, or invalid host probe", "loaded")

    plugin_installed, plugin_detail = _plugin_installed()
    append("omp_plugin_installed", plugin_installed, plugin_detail, "installed")
    plugin_loaded, plugin_loaded_detail = _plugin_loaded(project) if plugin_installed else (False, "plugin not installed")
    append("omp_plugin_loaded", plugin_loaded, plugin_loaded_detail, "loaded")

    auth = _authentication_probe(project) if installed else None
    authenticated = False
    if isinstance(auth, dict) and auth.get("authenticated") is True:
        try:
            python, root = _runtime_paths(project)
            authenticated = (
                Path(auth["python"]).resolve() == Path(python).resolve()
                and Path(auth["bridge_path"]).resolve() == (Path(root) / "src/hooks/omp_bridge.py").resolve()
            )
        except (OSError, ValueError, KeyError, TypeError):
            pass
    append("omp_authenticated", authenticated, "authenticated score through the installed adapter interpreter"
           if authenticated else "authenticated scoring/import readiness not demonstrated", "authenticated")

    qualified = False
    if installed and loaded and authenticated:
        try:
            report = json.loads((project / ".reasoning-core/omp-qualification.json").read_text())
            qualified = (
                isinstance(report, dict) and report.get("schema_version") == 1
                and report.get("binding") == qualification_binding(project, runtime)
                and isinstance(report.get("evidence"), dict)
                and all(report["evidence"].get(name) is True for name in REQUIRED_EVIDENCE)
            )
        except (OSError, ValueError, TypeError):
            pass
    append("omp_qualified", qualified, "immutable qualification matches current runtime and configuration"
           if qualified else "qualification absent, incomplete, or stale; installation is not qualification", "qualified")
    return checks
