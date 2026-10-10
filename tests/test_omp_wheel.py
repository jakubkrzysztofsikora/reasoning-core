"""Exercise distribution contents and init outside the editable checkout."""
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


def test_omp_distribution_modules_and_installed_init(tmp_path):
    wheel_path = os.environ.get("RC_TEST_WHEEL")
    if not wheel_path:
        pytest.skip("RC_TEST_WHEEL must select an actual built wheel")
    wheel = Path(wheel_path).resolve()
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "src/hooks/adapters/__init__.py" in names
        assert "eval/host_enforcement_qualification/omp.py" in names
        assert "src/data/templates/omp.reasoning_core_gate.ts" in names
        assert "src/data/templates/omp.compatibility.json" in names
    runtime = tmp_path / "runtime"
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(runtime)], check=True)
    python = runtime / "bin/python"
    subprocess.run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)], check=True)
    project = tmp_path / "outside checkout"
    project.mkdir()
    subprocess.run([str(runtime / "bin/rc"), "init", "--target", str(project),
                    "--no-sidecar", "--no-model"], cwd=project, check=True)
    subprocess.run([str(python), "-I", "-c",
        "from src.hooks import omp_bridge; from src.hooks.adapters import claude; "
        "from eval.host_enforcement_qualification import omp; "
        "from src import _omp_doctor; import src; "
        "assert 'site-packages' in src.__file__"], cwd=project, check=True)
    guard = (project / ".omp/extensions/reasoning_core_gate.ts").read_text()
    assert str(python) in guard
    assert "__RC_" not in guard
