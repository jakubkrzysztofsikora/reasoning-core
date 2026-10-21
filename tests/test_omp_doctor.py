"""OMP readiness never promotes installation or health alone to qualification."""

import hashlib
import importlib
import json
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
    }, InitResult(tmp_path))
    return tmp_path


def states(checks):
    return {row["name"]: row for row in checks}


def healthy_runtime(root):
    from src import data
    return {"host_kind": "source", "host_files": json.loads(data.read_text("omp.host-files.json")), "host_sha256": "c" * 64,
            "schema_version": 1, "version": "18.8.8", "native_protocol": 1,
            "source_sha": "aafb6551616fccec65cbfb264298e7f98df8ce5f",
            "adapter_path": str(root / EXTENSION),
            "adapter_sha256": hashlib.sha256((root / EXTENSION).read_bytes()).hexdigest(),
            "native_sha256": "a" * 64, "loaded": True, "model_invoked": False}


@pytest.fixture
def probes(project, monkeypatch):
    doctor = module()
    state = {"runtime": healthy_runtime(project), "auth": {"authenticated": True,
        "python": sys.executable, "bridge_path": str(Path(__file__).resolve().parents[1] / "src/hooks/omp_bridge.py")}}
    monkeypatch.setattr(doctor, "_runtime_probe", lambda _project: state["runtime"])
    monkeypatch.setattr(doctor, "_authentication_probe", lambda _project: state["auth"])
    return state


def test_installed_loaded_authenticated_still_need_bound_qualification(project, probes):
    checks = states(module().check_omp(project))
    assert checks["omp_installed"]["ok"]
    assert checks["omp_loaded"]["ok"]
    assert checks["omp_authenticated"]["ok"]
    assert not checks["omp_qualified"]["ok"]
    assert checks["omp_qualified"]["state"] == "qualified"


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
        "omp_installed", "omp_loaded", "omp_authenticated", "omp_qualified",
    }


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
