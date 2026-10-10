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
