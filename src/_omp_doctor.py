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

REQUIRED_EVIDENCE = (
    "allowed_write", "denied_unchanged", "receipt_verified",
    "child_guard_retained", "restrictions_verified",
)
_HASH = re.compile(r"[0-9a-f]{64}")


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _json_process(argv: list[str], payload: dict, timeout: float = 40) -> dict | None:
    try:
        result = subprocess.run(
            argv, input=json.dumps(payload), encoding="utf-8", capture_output=True,
            timeout=timeout, check=False,
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
    policies = [".omp/settings.json", ".reasoning-core/rules.yaml",
                ".reasoning-core/rules.schema.yaml", ".reasoning-core/git_worktrees.yaml"]
    framework = Path(_install_paths.rc_repo_path())
    return {
        "version": runtime.get("version"), "source_sha": runtime.get("source_sha"),
        "native_protocol": runtime.get("native_protocol"),
        "native_sha256": runtime.get("native_sha256"),
        "adapter_sha256": _sha(project / EXTENSION),
        "compatibility_sha256": _sha(project / COMPATIBILITY),
        "bridge_sha256": _sha(framework / "src/hooks/omp_bridge.py"),
        "contract_sha256": _sha(framework / "src/omp_contract.py"),
        "configuration": {path: _sha(project / path) for path in policies},
        "environment": {name: os.environ.get(name) for name in (
            "RC_MODE", "S2_FAIL_CLOSED", "RC_ORACLE_BLOCK", "RC_ORACLE_T1", "RC_ORACLE_T2",
            "RC_STRUCTURAL_BLOCK", "RC_RULE_ENGINE", "RC_LANG_LOCK", "RC_ALLOW_GUARD_EDIT",
        )},
    }


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
        and isinstance(runtime.get("native_sha256"), str)
        and _HASH.fullmatch(runtime["native_sha256"]) is not None
    )
    append("omp_loaded", loaded, "actual host loaded the owned adapter and native protocol 1"
           if loaded else "executable unavailable, discovery disabled, wrong native build, or invalid host probe", "loaded")

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
