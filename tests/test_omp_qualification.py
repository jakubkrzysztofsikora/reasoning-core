"""Qualification records are immutable and cannot follow operator symlinks."""
import json

import pytest

from eval.host_enforcement_qualification.omp import write_report
from src._omp_doctor import REQUIRED_EVIDENCE


def report():
    return {"schema_version": 1, "evidence": {name: True for name in REQUIRED_EVIDENCE}}


def test_report_refuses_overwrite_and_incomplete_evidence(tmp_path):
    (tmp_path / ".reasoning-core").mkdir()
    incomplete = report()
    incomplete["evidence"]["receipt_verified"] = False
    with pytest.raises(ValueError):
        write_report(tmp_path, incomplete)
    path = write_report(tmp_path, report())
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_report(tmp_path, report())
    assert path.read_bytes() == original
    assert json.loads(original)["evidence"]["receipt_verified"] is True


def test_qualification_refuses_symlinked_state_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".reasoning-core").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        write_report(tmp_path, report())
    assert not list(outside.iterdir())


@pytest.mark.parametrize("include_auth", [True, False])
def test_qualification_requires_successful_authentication(tmp_path, monkeypatch, include_auth):
    from eval.host_enforcement_qualification import omp

    checks = [
        {"name": "omp_installed", "ok": True},
        {"name": "omp_loaded", "ok": True},
        {"name": "omp_plugin_installed", "ok": True},
        {"name": "omp_plugin_loaded", "ok": True},
    ]
    if include_auth:
        checks.append({"name": "omp_authenticated", "ok": False})
    monkeypatch.setattr(omp, "check_omp", lambda _project: checks)
    monkeypatch.setattr(omp.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="authenticated scoring must pass"):
        omp.qualify(tmp_path)


@pytest.mark.parametrize("name", ["omp_plugin_installed", "omp_plugin_loaded"])
def test_qualification_requires_installed_and_loaded_plugin(tmp_path, monkeypatch, name):
    from eval.host_enforcement_qualification import omp

    checks = [
        {"name": "omp_installed", "ok": True},
        {"name": "omp_loaded", "ok": True},
        {"name": "omp_authenticated", "ok": True},
        {"name": "omp_plugin_installed", "ok": name != "omp_plugin_installed"},
        {"name": "omp_plugin_loaded", "ok": name != "omp_plugin_loaded"},
    ]
    monkeypatch.setattr(omp, "check_omp", lambda _project: checks)
    monkeypatch.setattr(omp.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="installation, loading and authenticated scoring must pass"):
        omp.qualify(tmp_path)
