"""Exercise distribution contents and init outside the editable checkout."""
import json
import os
import shutil
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
        assert "eval/host_enforcement_qualification/omp_extension_probe.ts" in names
        assert "src/data/templates/omp.reasoning_core_gate.ts" in names
        assert "src/duplicate_detection/review.py" in names
        assert "src/data/templates/omp.compatibility.json" in names
    runtime = tmp_path / "runtime"
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(runtime)], check=True)
    python = runtime / "bin/python"
    subprocess.run([str(python), "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)], check=True)
    project = tmp_path / "outside checkout"
    home = tmp_path / "home"
    home.mkdir()
    environment = dict(os.environ, HOME=str(home), RC_OMP_STRICT="1")
    project.mkdir()
    subprocess.run([str(runtime / "bin/rc"), "init", "--target", str(project),
                    "--no-sidecar", "--no-model"], cwd=project, env=environment, check=True)
    subprocess.run([str(python), "-I", "-c",
        "from src.hooks import omp_bridge; from src.hooks.adapters import claude; "
        "from eval.host_enforcement_qualification import omp; "
        "from src import _omp_doctor; from src.duplicate_detection.findings import load; "
        "from pathlib import Path; import src; "
        "assert 'site-packages' in src.__file__; "
        "assert not load(Path('missing-ledger.jsonl'))"], cwd=project, env=environment, check=True)
    subprocess.run([sys.executable, "-I", "-c",
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from src.duplicate_detection.review import stop_decision; from pathlib import Path; "
        "assert not stop_decision('.', Path('missing-ledger.jsonl')).block",
        str(wheel)], cwd=project, env=environment, check=True)
    guard = (project / ".omp/extensions/reasoning_core_gate.ts").read_text()
    assert str(python) in guard
    assert "__RC_" not in guard
    bun = shutil.which("bun")
    if bun:
        probe = subprocess.run([str(python), "-I", "-c",
            "from pathlib import Path; from eval.host_enforcement_qualification import omp; "
            "print(Path(omp.__file__).with_name('omp_extension_probe.ts'))"],
            cwd=project, env=environment, capture_output=True, text=True, check=True).stdout.strip()
        repo = Path(__file__).resolve().parents[1]
        (project / ".reasoning-core/omp-runtime.json").write_text(json.dumps({
            "python": sys.executable, "repo_root": str(repo),
        }))
        probe_env = {key: value for key, value in environment.items()
                     if not key.startswith(("RC_", "S2_", "OMP_", "CLAUDE_"))}
        probe_env.update(RC_AUDIT_ROOT=str(tmp_path / "audit"), RC_MODE="copilot", S2_FAIL_CLOSED="0")
        result = subprocess.run([bun, probe, str(project),
                                 str(repo / "integrations/omp-plugin/extensions/reasoning_core_gate.ts")],
                                cwd=project, env=probe_env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout.strip().splitlines()[-1]) == {
            "bash_allowed": True, "bash_blocked": True, "write_roundtrip": True,
            "subagent_recorded": True, "advisor_recorded": True,
        }
