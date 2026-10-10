"""Owned OMP asset lifecycle; user configuration must survive every operation."""

import hashlib
import importlib
import json
from pathlib import Path

import pytest

from src._init import InitResult

EXT = ".omp/extensions/reasoning_core_gate.ts"
META = ".omp/reasoning-core.compatibility.json"
STATE = ".reasoning-core/omp-owned.json"


def module():
    return importlib.import_module("src._omp_install")


def install(root, python="/opt/runtime python", package="/opt/package root"):
    manifest = root / ".reasoning-core/install.manifest"
    manifest.parent.mkdir(exist_ok=True)
    manifest.touch(exist_ok=True)
    result = InitResult(root)
    module().install_omp(root, manifest, {"RC_PYTHON": python, "RC_REPO": package}, result)
    return result


def test_fresh_and_reinit_are_idempotent_with_exact_asset_ownership(tmp_path):
    result = install(tmp_path)
    assert EXT in result.wrote and META in result.wrote
    text = (tmp_path / EXT).read_text()
    assert 'const PYTHON = "/opt/runtime python";' in text
    assert 'const ROOT = "/opt/package root";' in text
    state = json.loads((tmp_path / STATE).read_text())
    assert state["files"][EXT]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert install(tmp_path).wrote == []
    assert STATE in (tmp_path / ".reasoning-core/install.manifest").read_text().splitlines()


def test_canonical_asset_parity():
    from src import data
    assert data.read_text("omp.reasoning_core_gate.ts") == (
        Path(__file__).resolve().parents[1] / EXT
    ).read_text()
    assert json.loads(data.read_text("omp.compatibility.json"))["native_protocol"] == 1


def test_existing_user_settings_extensions_and_changed_owned_assets_survive(tmp_path):
    settings = tmp_path / ".omp/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"extensions":["user.ts"],"mcpServers":{"mine":{}}}')
    user = tmp_path / ".omp/extensions/user.ts"
    user.parent.mkdir()
    user.write_text("user code")
    before = settings.read_bytes()
    install(tmp_path)
    changed = tmp_path / EXT
    changed.write_text(changed.read_text() + "// user customization\n")
    owned = changed.read_bytes()
    result = install(tmp_path, python="/opt/new interpreter")
    assert changed.read_bytes() == owned
    assert any("modified" in item for item in result.warned)
    assert settings.read_bytes() == before and user.read_text() == "user code"
    outcome = module().uninstall_omp(tmp_path)
    assert EXT in outcome["refused"]
    assert changed.read_bytes() == owned
    assert settings.read_bytes() == before and user.exists()


def test_unowned_existing_extension_is_preserved(tmp_path):
    path = tmp_path / EXT
    path.parent.mkdir(parents=True)
    path.write_text("operator extension")
    result = install(tmp_path)
    assert path.read_text() == "operator extension"
    assert any("unowned" in item for item in result.warned)
    assert EXT not in json.loads((tmp_path / STATE).read_text())["files"]


def test_upgrade_replaces_only_unchanged_owned_assets(tmp_path):
    install(tmp_path)
    result = install(tmp_path, python='/opt/new "python"')
    assert EXT in result.wrote
    assert 'const PYTHON = "/opt/new \\"python\\"";' in (tmp_path / EXT).read_text()
    outcome = module().uninstall_omp(tmp_path)
    assert set(outcome["removed"]) == {EXT, META, STATE}
    assert not (tmp_path / EXT).exists()
    assert not outcome["refused"]


@pytest.mark.parametrize("component", [".omp", ".omp/extensions", ".reasoning-core/omp-owned.json"])
def test_symlink_components_refuse_external_writes(tmp_path, component):
    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    path = root / component
    path.parent.mkdir(parents=True, exist_ok=True)
    destination = external / "state.json" if path.suffix else external
    if path.suffix:
        destination.write_text("{}")
    path.symlink_to(destination, target_is_directory=not path.suffix)
    result = install(root)
    assert result.warned
    assert not (external / "reasoning_core_gate.ts").exists()
    if path.suffix:
        assert destination.read_text() == "{}"


def test_malformed_ownership_refuses_upgrade_without_touching_assets(tmp_path):
    install(tmp_path)
    ext = (tmp_path / EXT).read_bytes()
    (tmp_path / STATE).write_text('{"files":{"../../outside":{"sha256":"bad"}}}')
    result = install(tmp_path, python="/opt/new")
    assert result.warned
    assert (tmp_path / EXT).read_bytes() == ext


def test_absent_omp_installs_but_does_not_claim_load_or_qualification(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    result = install(tmp_path)
    assert (tmp_path / EXT).is_file()
    assert any("installed" in item and "unverified" in item for item in result.warned)


def test_default_init_and_uninstall_use_owned_lifecycle(tmp_path, monkeypatch):
    from src import _init
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    root = tmp_path / "repo"
    root.mkdir()
    _init.init(root, install_sidecar=False, install_model=False)
    assert (root / EXT).exists()
    (root / EXT).write_text("// user modified")
    result = _init.uninstall(root)
    assert EXT in result["refused"]
    assert (root / EXT).read_text() == "// user modified"

def test_legacy_shell_uses_owned_assets_and_preserves_modified_files(tmp_path):
    import os
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    root = tmp_path / "target with spaces"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    tools = tmp_path / "bin"
    tools.mkdir()
    direnv = tools / "direnv"
    direnv.write_text("#!/bin/sh\nexit 0\n")
    direnv.chmod(0o755)
    environment = dict(os.environ, HOME=str(home), RC_REPO=str(repo), RC_PYTHON=sys.executable)
    environment["PATH"] = str(tools) + os.pathsep + environment["PATH"]
    installed = subprocess.run(
        ["bash", str(repo / "install.sh")], cwd=root, env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert installed.returncode == 0, installed.stderr
    assert (root / EXT).exists()
    (root / EXT).write_text("// preserved operator change")
    note = root / ".reasoning-core/operator.note"
    note.write_text("preserved state")
    removed = subprocess.run(
        ["bash", str(repo / "uninstall.sh")], cwd=root, env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert removed.returncode == 0, removed.stderr
    assert (root / EXT).read_text() == "// preserved operator change"
    assert note.read_text() == "preserved state"
    assert (root / STATE).exists()

def test_reinit_extends_owned_gitignore_block_without_replacing_user_entries(tmp_path):
    from src._init import update_gitignore
    path = tmp_path / ".gitignore"
    path.write_text("user-outside\n# >>> reasoning-core >>>\n.pi/settings.json\nuser-inside\n# <<< reasoning-core <<<\ntail\n")
    result = InitResult(tmp_path)
    update_gitignore(tmp_path, result)
    assert EXT in path.read_text()
    assert META in path.read_text()
    assert "user-inside" in path.read_text() and "user-outside" in path.read_text()
    assert path.read_text().endswith("tail\n")

def test_omp_ownership_and_bundled_guard_assets_are_protected(tmp_path):
    from src.hooks import _guard_paths
    for relative in [STATE, META, EXT, "src/_omp_install.py", "src/data/templates/omp.reasoning_core_gate.ts"]:
        assert _guard_paths.is_guarded(str(tmp_path / relative)), relative
