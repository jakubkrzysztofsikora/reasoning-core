"""Install OMP guard assets with SHA ownership and preservation of user edits."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from src import data

EXTENSION = ".omp/extensions/reasoning_core_gate.ts"
COMPATIBILITY = ".omp/reasoning-core.compatibility.json"
RUNTIME = ".reasoning-core/omp-runtime.json"
OWNERSHIP = ".reasoning-core/omp-owned.json"
_ASSETS = {EXTENSION, COMPATIBILITY}
_SHA = re.compile(r"[0-9a-f]{64}")


def _safe(root: Path, relative: str) -> Path:
    path = root
    for part in Path(relative).parts:
        if part in ("..", ".") or Path(relative).is_absolute():
            raise ValueError("unsafe OMP asset path")
        path = path / part
        if path.is_symlink():
            raise ValueError(f"symlink OMP asset component: {relative}")
    if not path.resolve().is_relative_to(root):
        raise ValueError("OMP asset escaped target")
    return path


def read_ownership(root: Path) -> dict:
    path = _safe(root, OWNERSHIP)
    if not path.exists():
        return {"schema_version": 1, "files": {}}
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) != {"schema_version", "files"}:
        raise ValueError("invalid OMP ownership")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid OMP ownership version")
    files = value["files"]
    if not isinstance(files, dict) or set(files) - _ASSETS:
        raise ValueError("invalid OMP ownership paths")
    for relative, record in files.items():
        _safe(root, relative)
        if not isinstance(record, dict) or set(record) != {"sha256"}:
            raise ValueError("invalid OMP ownership record")
        if not isinstance(record["sha256"], str) or not _SHA.fullmatch(record["sha256"]):
            raise ValueError("invalid OMP ownership digest")
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".rc-omp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def render_extension(substitutions: dict[str, str]) -> str:
    text = data.read_text("omp.reasoning_core_gate.ts")
    for marker, name in [("__RC_PYTHON__", "RC_PYTHON"), ("__RC_ROOT__", "RC_REPO")]:
        value = substitutions[name]
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError("OMP runtime paths must be absolute")
        text = text.replace(json.dumps(marker), json.dumps(value, ensure_ascii=True))
    return text


def _install_strict_assets(root: Path, manifest: Path, substitutions: dict[str, str], result) -> None:
    ownership = read_ownership(root)
    paths = {relative: _safe(root, relative) for relative in _ASSETS}
    state_path = _safe(root, OWNERSHIP)
    assets = {
        EXTENSION: render_extension(substitutions).encode(),
        COMPATIBILITY: data.read_text("omp.compatibility.json").encode(),
    }
    for relative, content in assets.items():
        path = paths[relative]
        owned = ownership["files"].get(relative)
        if path.exists():
            digest = _digest(path)
            if not owned:
                result.warned.append(f"OMP unowned asset preserved: {relative}")
                continue
            if digest != owned["sha256"]:
                result.warned.append(f"OMP modified asset preserved: {relative}")
                continue
            if digest == hashlib.sha256(content).hexdigest():
                result.skipped.append(relative)
                continue
        _atomic(path, content)
        ownership["files"][relative] = {"sha256": hashlib.sha256(content).hexdigest()}
        result.wrote.append(relative)
    serialized = (json.dumps(ownership, sort_keys=True, indent=2) + "\n").encode()
    if not state_path.exists() or state_path.read_bytes() != serialized:
        _atomic(state_path, serialized)
    from src._init import _record
    _record(manifest, OWNERSHIP)
    executable = shutil.which("omp")
    detail = executable or "OMP executable unavailable"
    result.warned.append(f"OMP assets installed; loaded/authenticated/qualified runtime unverified: {detail}")


def install_omp(target: Path, manifest: Path, substitutions: dict[str, str], result,
                strict: bool | None = None) -> None:
    """Record the OMP runtime; install the strict project adapter only when ``strict``.

    ``strict`` defaults to ``RC_OMP_STRICT=1``. The strict adapter blocks every
    omp tool outside the qualified native precommit routes; the global plugin
    gates only bash/write/edit.
    """
    root = target.resolve(strict=True)
    if strict is None:
        strict = os.environ.get("RC_OMP_STRICT") == "1"
    try:
        if strict:
            _install_strict_assets(root, manifest, substitutions, result)
        else:
            outcome = uninstall_omp(root)
            result.skipped.append(EXTENSION)
            if outcome["removed"]:
                result.warned.append("Removed previous owned OMP strict assets: " + ", ".join(outcome["removed"]))
            if outcome["refused"]:
                result.warned.append("Preserved modified or unsafe OMP strict assets: " + ", ".join(outcome["refused"]))
            for relative in (EXTENSION, COMPATIBILITY):
                path = root / relative
                if relative not in outcome["refused"] and (path.exists() or path.is_symlink()):
                    result.warned.append(f"Preserved OMP strict asset without verified ownership: {relative}")
        runtime_path = _safe(root, RUNTIME)
        runtime = (json.dumps({"python": substitutions["RC_PYTHON"],
                               "repo_root": substitutions["RC_REPO"]}, sort_keys=True) + "\n").encode()
        if not runtime_path.exists() or runtime_path.read_bytes() != runtime:
            _atomic(runtime_path, runtime)
            result.wrote.append(RUNTIME)
        from src._init import _record
        _record(manifest, RUNTIME)
    except (OSError, ValueError, KeyError) as exc:
        result.warned.append(f"OMP installation refused: {exc}")


def uninstall_omp(target: Path) -> dict:
    root = target.resolve(strict=True)
    removed: list[str] = []
    refused: list[str] = []
    try:
        ownership = read_ownership(root)
        for relative, record in list(ownership["files"].items()):
            path = _safe(root, relative)
            if path.exists() and _digest(path) != record["sha256"]:
                refused.append(relative)
                continue
            if path.exists():
                path.unlink()
                removed.append(relative)
            del ownership["files"][relative]
        state_path = _safe(root, OWNERSHIP)
        if ownership["files"]:
            _atomic(state_path, (json.dumps(ownership, sort_keys=True, indent=2) + "\n").encode())
        elif state_path.exists():
            state_path.unlink()
            removed.append(OWNERSHIP)
    except (OSError, ValueError):
        refused.append(OWNERSHIP)
    return {"removed": removed, "refused": refused}
