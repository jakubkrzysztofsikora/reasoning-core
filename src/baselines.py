"""Immutable evaluation baseline registry.

Committed manifests are intentionally small and reproducible.  Larger raw
audit and evaluation artifacts live outside the checkout and are referenced
by path plus digest, so a checkout does not become an accidental data store.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_ID = "baseline-2026-08-09"
REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_ROOT = REPO_ROOT / "eval" / "baselines"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def redact(value: Any) -> Any:
    """Recursively redact environment/config fields whose names imply secrets."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if any(token in str(key).lower() for token in
            ("secret", "token", "password", "api_key", "apikey", "credential"))
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _git(args: list[str]) -> str | None:
    try:
        proc = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=3)
        return proc.stdout.strip() if proc.returncode == 0 else None
    except OSError:
        return None


def active_config() -> dict[str, str]:
    prefixes = ("RC_", "S2_")
    return redact({key: value for key, value in os.environ.items() if key.startswith(prefixes)})


def artifact_reference(path: Path) -> dict[str, Any]:
    item: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.is_file():
        item.update({"sha256": sha256_file(path), "bytes": path.stat().st_size})
    return item


def create_manifest(
    baseline_id: str,
    *,
    audit_root: Path | None = None,
    artifacts: list[Path] | None = None,
    oracle_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a serialisable baseline manifest without writing it."""
    config = active_config()
    head = _git(["rev-parse", "HEAD"])
    dirty = bool(_git(["status", "--porcelain"]))
    guard = REPO_ROOT / "src" / "hooks" / "pre_edit_guard.py"
    schema_version = None
    try:
        from src.s2_core import RISK_LABELS_VERSION
        schema_version = RISK_LABELS_VERSION
    except Exception:
        schema_version = "unavailable"
    refs = [artifact_reference(path) for path in artifacts or []]
    if audit_root is not None:
        refs.append(artifact_reference(audit_root))
    return {
        "schema_version": SCHEMA_VERSION,
        "baseline_id": baseline_id,
        "captured_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "code": {"git_sha": head, "dirty": dirty},
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "configuration": config,
        "configuration_hash": stable_hash(config),
        "guard_hashes": {str(guard.relative_to(REPO_ROOT)): sha256_file(guard)} if guard.is_file() else {},
        "oracle_health": oracle_health or {},
        "embedder": {"backend": os.environ.get("RC_EMBEDDER", "mamba-130m"), "revision": os.environ.get("S2_SSM_REVISION", "default")},
        "risk_label_schema_version": schema_version,
        "audit_window_metrics": {},
        "artifacts": refs,
    }


def manifest_path(baseline_id: str) -> Path:
    return REGISTRY_ROOT / f"{baseline_id}.json"


def load_manifest(baseline_id: str) -> dict[str, Any]:
    path = manifest_path(baseline_id)
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    validate_manifest(data)
    return data


def validate_manifest(manifest: dict[str, Any]) -> None:
    required = {"schema_version", "baseline_id", "captured_at", "code", "configuration_hash", "artifacts"}
    missing = required - set(manifest)
    if missing or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"invalid baseline manifest; missing={sorted(missing)}")
    if not isinstance(manifest["baseline_id"], str) or not manifest["baseline_id"]:
        raise ValueError("invalid baseline ID")


def compare_manifests(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Compare immutable manifests; never mutate either input or artifact."""
    fields = ("code", "configuration_hash", "guard_hashes", "oracle_health", "embedder", "risk_label_schema_version")
    changes = {field: {"from": left.get(field), "to": right.get(field)} for field in fields if left.get(field) != right.get(field)}
    return {"from": left["baseline_id"], "to": right["baseline_id"], "equal": not changes, "changes": changes}


def verify_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return hash verification results without changing referenced artifacts."""
    results = []
    for item in manifest.get("artifacts", []):
        path = Path(item.get("path", ""))
        expected = item.get("sha256")
        exists = path.is_file()
        actual = sha256_file(path) if exists else None
        results.append({"path": str(path), "exists": exists, "expected_sha256": expected,
                        "actual_sha256": actual, "verified": expected is None or expected == actual})
    return results
