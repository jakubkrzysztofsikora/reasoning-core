import json
from pathlib import Path

from src import baselines


def test_redact_and_compare_do_not_mutate_inputs():
    left = {"schema_version": 1, "baseline_id": "left", "captured_at": "now", "code": {"git_sha": "a"}, "configuration_hash": "a", "artifacts": []}
    right = {**left, "baseline_id": "right", "code": {"git_sha": "b"}}
    original = json.loads(json.dumps(left))
    result = baselines.compare_manifests(left, right)
    assert result["changes"]["code"]["from"] == {"git_sha": "a"}
    assert left == original
    assert baselines.redact({"api_key": "nope", "safe": "yes"}) == {"api_key": "[REDACTED]", "safe": "yes"}


def test_verify_artifact_hash(tmp_path: Path):
    artifact = tmp_path / "raw.json"
    artifact.write_text("raw", encoding="utf-8")
    manifest = {"artifacts": [baselines.artifact_reference(artifact)]}
    assert baselines.verify_artifacts(manifest)[0]["verified"] is True
    artifact.write_text("changed", encoding="utf-8")
    assert baselines.verify_artifacts(manifest)[0]["verified"] is False
