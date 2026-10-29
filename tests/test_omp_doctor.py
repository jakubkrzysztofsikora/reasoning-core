"""OMP readiness never promotes installation or health alone to qualification."""

import hashlib
import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from src import _install_paths
from src._init import InitResult
from src._omp_install import EXTENSION, install_omp


def module():
    return importlib.import_module("src._omp_doctor")


@pytest.fixture
def project(tmp_path):
    manifest = tmp_path / ".reasoning-core/install.manifest"
    manifest.parent.mkdir()
    manifest.touch()
    install_omp(tmp_path, manifest, {
        "RC_PYTHON": _install_paths.python_executable(), "RC_REPO": _install_paths.rc_repo_path(),
    }, InitResult(tmp_path), strict=True)
    return tmp_path


def states(checks):
    return {row["name"]: row for row in checks}


def healthy_runtime(root):
    from src import data
    compat = json.loads(data.read_text("omp.compatibility.json"))
    return {"host_kind": "source", "host_files": json.loads(data.read_text("omp.host-files.json")), "host_sha256": "c" * 64,
            "schema_version": 1, "version": compat["upstream_version"], "native_protocol": 1,
            "source_sha": compat["upstream_source_sha"],
            "adapter_path": str(root / EXTENSION),
            "adapter_sha256": hashlib.sha256((root / EXTENSION).read_bytes()).hexdigest(),
            "native_sha256": "a" * 64, "loaded": True, "model_invoked": False}


@pytest.fixture
def probes(project, monkeypatch):
    doctor = module()
    state = {"runtime": healthy_runtime(project), "auth": {"authenticated": True,
        "python": sys.executable, "bridge_path": str(Path(__file__).resolve().parents[1] / "src/hooks/omp_bridge.py")},
        "plugin_installed": (True, "user-scope plugin link, manifest entries, and gate digest verified"),
        "plugin_loaded": (True, "session probe loaded the plugin gate (mode active)")}
    monkeypatch.setattr(doctor, "_runtime_probe", lambda _project: state["runtime"])
    monkeypatch.setattr(doctor, "_authentication_probe", lambda _project: state["auth"])
    monkeypatch.setattr(doctor, "_plugin_installed", lambda _project: state["plugin_installed"])
    monkeypatch.setattr(doctor, "_plugin_loaded", lambda _project: state["plugin_loaded"])
    return state


def test_installed_loaded_authenticated_still_need_bound_qualification(project, probes):
    checks = states(module().check_omp(project))
    assert checks["omp_installed"]["ok"]
    assert checks["omp_loaded"]["ok"]
    assert checks["omp_plugin_installed"]["ok"]
    assert checks["omp_plugin_loaded"]["ok"]
    assert checks["omp_authenticated"]["ok"]
    assert not checks["omp_qualified"]["ok"]
    assert checks["omp_qualified"]["state"] == "qualified"


def test_uninstalled_plugin_skips_load_probe_and_fails_loaded(project, probes):
    probes["plugin_installed"] = (False, "plugin link, manifest entries, or gate digest mismatch")
    probes["plugin_loaded"] = (True, "session probe loaded the plugin gate (mode active)")
    checks = states(module().check_omp(project))
    assert not checks["omp_plugin_installed"]["ok"]
    assert not checks["omp_plugin_loaded"]["ok"]


@pytest.mark.parametrize("field,value", [
    ("loaded", False), ("version", "18.2.11"), ("native_protocol", 0),
    ("model_invoked", True), ("adapter_sha256", "b" * 64),
])
def test_disabled_wrong_build_and_stale_adapter_never_load(project, probes, field, value):
    probes["runtime"][field] = value
    assert not states(module().check_omp(project))["omp_loaded"]["ok"]


def test_health_without_authenticated_score_is_not_readiness(project, probes):
    probes["auth"] = {"authenticated": False, "health": True}
    assert not states(module().check_omp(project))["omp_authenticated"]["ok"]


@pytest.mark.parametrize("response", [None, {}, [], {"loaded": True}])
def test_unavailable_or_malformed_runtime_is_explicit(project, probes, response):
    probes["runtime"] = response
    checks = states(module().check_omp(project))
    assert not checks["omp_loaded"]["ok"]
    assert not checks["omp_qualified"]["ok"]


def test_modified_asset_and_wrong_interpreter_import_fail_installation(project, probes):
    (project / EXTENSION).write_text("// user customization")
    assert not states(module().check_omp(project))["omp_installed"]["ok"]


def test_stale_qualification_digest_cannot_claim_support(project, probes):
    report = {"schema_version": 1, "binding": module().qualification_binding(project, probes["runtime"]),
              "evidence": {name: True for name in module().REQUIRED_EVIDENCE}}
    path = project / ".reasoning-core/omp-qualification.json"
    path.write_text(json.dumps(report))
    assert states(module().check_omp(project))["omp_qualified"]["ok"]
    report["binding"]["adapter_sha256"] = "b" * 64
    path.write_text(json.dumps(report))
    assert not states(module().check_omp(project))["omp_qualified"]["ok"]


def test_cli_omp_host_does_not_require_claude_wiring(project, probes, capsys):
    from src import rc_cli
    assert rc_cli.main(["doctor", "--host", "omp", "--project-dir", str(project), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert {row["name"] for row in report["checks"]} == {
        "omp_installed", "omp_loaded", "omp_plugin_installed", "omp_plugin_loaded",
        "omp_authenticated", "omp_qualified",
    }


def test_status_host_omp_reports_gate_state_and_advisor_ledger(project, probes, monkeypatch, capsys):
    from src import rc_cli
    import audit_log
    monkeypatch.setenv("RC_RUN_DIR", str(project))
    monkeypatch.setenv("RC_AUDIT_ROOT", str(project / "audit"))
    monkeypatch.setattr(audit_log, "_AUDIT_ROOT", project / "audit")
    monkeypatch.setattr(rc_cli, "_audit_root", lambda: project / "audit")
    monkeypatch.setattr(rc_cli, "_today_dir", lambda: project / "audit" / "2026-10-28")
    day = project / "audit/2026-10-28"
    day.mkdir(parents=True)
    (day / "s1.jsonl").write_text(
        json.dumps({"tool_name": "omp:advisor", "kind": "cost_changed", "cost": 0.25}) + "\n"
        + json.dumps({"tool_name": "omp:edit", "decision": "allow"}) + "\n"
        + json.dumps({"tool_name": "omp:advisor", "kind": "yielded", "cost": None}) + "\n")
    monkeypatch.setenv("RC_MODE", "copilot")
    assert rc_cli.main(["status", "--host", "omp"]) == 0
    out = capsys.readouterr().out
    assert "mode                             active" in out
    assert "RC_MODE                          copilot" in out
    assert "advisor_ledger_rows_today        2" in out
    assert "today_rows                       3" in out
    assert "qualification                    <absent>" in out


@pytest.mark.parametrize("name", ["RC_PLAN_GROUNDING", "RC_STRUCTURAL_ALLOW", "RC_LANG_OVERRIDE"])
def test_effective_enforcement_environment_is_bound(project, probes, monkeypatch, name):
    initial = module().qualification_binding(project, probes["runtime"])
    monkeypatch.setenv(name, "changed-policy")
    assert module().qualification_binding(project, probes["runtime"]) != initial

@pytest.mark.parametrize("path", ["PLAN.md", ".reasoning-core/contract.yaml"])
def test_plan_dependencies_are_bound(project, probes, path):
    initial = module().qualification_binding(project, probes["runtime"])
    (project / path).write_text("changed policy")
    assert module().qualification_binding(project, probes["runtime"]) != initial

def test_same_version_host_implementation_change_invalidates_qualification(project, probes):
    doctor = module()
    runtime = probes["runtime"]
    runtime["host_sha256"] = "c" * 64
    report = {"schema_version": 1, "binding": doctor.qualification_binding(project, runtime),
              "evidence": {name: True for name in doctor.REQUIRED_EVIDENCE}}
    (project / ".reasoning-core/omp-qualification.json").write_text(json.dumps(report))
    assert states(doctor.check_omp(project))["omp_qualified"]["ok"]
    runtime["host_sha256"] = "d" * 64
    assert not states(doctor.check_omp(project))["omp_qualified"]["ok"]


@pytest.mark.parametrize("python,root", [
    ("relative/python", "/absolute/root"),
    ("/absolute/python", "relative/root"),
    (["python"], "/absolute/root"),
])
def test_plugin_mode_rejects_invalid_runtime_paths(tmp_path, python, root):
    rc_dir = tmp_path / ".reasoning-core"
    rc_dir.mkdir()
    (rc_dir / "omp-runtime.json").write_text(json.dumps({"python": python, "repo_root": root}))
    assert module()._plugin_expected_mode(tmp_path) == "failclosed"


@pytest.mark.parametrize("files,expected", [
    ({}, "dormant"),
    ({"install.manifest": ""}, "dormant"),
    ({"omp-owned.json": "{}"}, "failclosed"),
    ({"omp-runtime.json": "not json"}, "failclosed"),
])
def test_plugin_mode_requires_omp_install_markers(tmp_path, files, expected):
    rc_dir = tmp_path / ".reasoning-core"
    rc_dir.mkdir()
    for name, content in files.items():
        (rc_dir / name).write_text(content)
    assert module()._plugin_expected_mode(tmp_path) == expected


@pytest.mark.parametrize("scope", ["user", "project"])
def test_plugin_doctor_accepts_installed_package_without_source_checkout(tmp_path, monkeypatch, scope):
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    doctor = module()
    monkeypatch.setattr(doctor._install_paths, "rc_repo_path", lambda: str(tmp_path / "wheel"))
    monkeypatch.setattr(doctor, "_plugin_loaded", lambda _project: (True, "loaded"))
    root = home / ".omp/plugins" if scope == "user" else project / ".omp/plugins"
    installed = root / "node_modules/reasoning-core"
    source = Path(__file__).resolve().parents[1] / "integrations/omp-plugin"
    shutil.copytree(source, installed)
    checks = states(doctor.check_omp(project))
    assert checks["omp_plugin_installed"]["ok"]
    assert checks["omp_plugin_loaded"]["ok"]
    (installed / "extensions/reasoning_core_gate.ts").write_text("tampered")
    assert not states(doctor.check_omp(project))["omp_plugin_installed"]["ok"]


def test_changing_plugin_invalidates_qualification(project, probes):
    doctor = module()
    gate = project / ".omp/plugins/node_modules/reasoning-core/extensions/reasoning_core_gate.ts"
    gate.parent.mkdir(parents=True)
    gate.write_text("first gate")
    report = {"schema_version": 1, "binding": doctor.qualification_binding(project, probes["runtime"]),
              "evidence": {name: True for name in doctor.REQUIRED_EVIDENCE}}
    (project / ".reasoning-core/omp-qualification.json").write_text(json.dumps(report))
    assert states(doctor.check_omp(project))["omp_qualified"]["ok"]
    gate.write_text("changed gate")
    assert not states(doctor.check_omp(project))["omp_qualified"]["ok"]


@pytest.mark.parametrize("name", ["plugin_installed", "plugin_loaded"])
def test_qualification_requires_current_plugin_health(project, probes, name):
    doctor = module()
    report = {"schema_version": 1, "binding": doctor.qualification_binding(project, probes["runtime"]),
              "evidence": {key: True for key in doctor.REQUIRED_EVIDENCE}}
    (project / ".reasoning-core/omp-qualification.json").write_text(json.dumps(report))
    assert states(doctor.check_omp(project))["omp_qualified"]["ok"]
    probes[name] = (False, "not available")
    assert not states(doctor.check_omp(project))["omp_qualified"]["ok"]
