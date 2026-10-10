"""Wire validation before OMP candidates can reach the shared guards."""

import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest


def contract():
    assert importlib.util.find_spec("src.omp_contract") is not None, (
        "OMP prepared-batch contract is not implemented"
    )
    from src import omp_contract
    return omp_contract


def batch(project):
    return {
        "protocol_version": 1,
        "project_dir": str(project),
        "session_id": "session-1",
        "agent_id": "parent",
        "parent_agent_id": None,
        "tool_call_id": "call-1",
        "final_input_sha256": "a" * 64,
        "candidates": [{
            "path": str(project / "one.py"),
            "operation": "write",
            "before_src": "x = 1\n",
            "after_src": "x = 2\n",
            "expected_before_sha256": hashlib.sha256(b"x = 1\n").hexdigest(),
        }],
    }


def test_complete_batch_roundtrips_without_mutating_files(tmp_path):
    (tmp_path / "one.py").write_text("x = 1\n")
    payload = batch(tmp_path)
    prepared = contract().PreparedBatch.from_dict(payload)
    assert prepared.to_dict() == payload
    prepared.verify_sources()
    assert (tmp_path / "one.py").read_text() == "x = 1\n"


def test_missing_file_and_empty_file_are_distinct(tmp_path):
    payload = batch(tmp_path)
    item = payload["candidates"][0]
    item.update(before_src=None, expected_before_sha256=None)
    prepared = contract().PreparedBatch.from_dict(payload)
    prepared.verify_sources()
    Path(item["path"]).write_text("")
    with pytest.raises(ValueError, match="stale"):
        prepared.verify_sources()


def test_last_stale_candidate_invalidates_the_batch_without_writes(tmp_path):
    (tmp_path / "one.py").write_text("x = 1\n")
    payload = batch(tmp_path)
    second = copy.deepcopy(payload["candidates"][0])
    second["path"] = str(tmp_path / "two.py")
    payload["candidates"].append(second)
    (tmp_path / "two.py").write_text("changed\n")
    prepared = contract().PreparedBatch.from_dict(payload)
    with pytest.raises(ValueError, match="stale"):
        prepared.verify_sources()
    assert (tmp_path / "one.py").read_text() == "x = 1\n"
    assert (tmp_path / "two.py").read_text() == "changed\n"


@pytest.mark.parametrize("field,value", [
    ("protocol_version", True), ("protocol_version", 2),
    ("session_id", ""), ("tool_call_id", 12),
    ("agent_id", None), ("parent_agent_id", ""),
    ("final_input_sha256", "bad"), ("candidates", []),
    ("candidates", None), ("project_dir", "relative"),
])
def test_rejects_malformed_batch_metadata(tmp_path, field, value):
    payload = batch(tmp_path)
    payload[field] = value
    with pytest.raises(ValueError):
        contract().PreparedBatch.from_dict(payload)


@pytest.mark.parametrize("field,value", [
    ("path", "../outside.py"), ("path", "xd://resource"),
    ("path", "/outside.py"), ("operation", "move"),
    ("before_src", 1), ("after_src", None),
    ("expected_before_sha256", "b" * 64),
])
def test_rejects_invalid_or_unbound_candidates(tmp_path, field, value):
    payload = batch(tmp_path)
    payload["candidates"][0][field] = value
    with pytest.raises(ValueError):
        contract().PreparedBatch.from_dict(payload)


def test_rejects_symlink_escape_and_duplicate_destination(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "alias").symlink_to(outside, target_is_directory=True)
    payload = batch(tmp_path)
    payload["candidates"][0]["path"] = str(tmp_path / "alias" / "file.py")
    with pytest.raises(ValueError, match="outside"):
        contract().PreparedBatch.from_dict(payload)
    payload = batch(tmp_path)
    payload["candidates"].append(copy.deepcopy(payload["candidates"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        contract().PreparedBatch.from_dict(payload)


def test_delete_requires_existing_source_and_no_after_content(tmp_path):
    payload = batch(tmp_path)
    item = payload["candidates"][0]
    item.update(operation="delete", after_src=None)
    prepared = contract().PreparedBatch.from_dict(payload)
    assert prepared.candidates[0].operation == "delete"
    item["after_src"] = ""
    with pytest.raises(ValueError):
        contract().PreparedBatch.from_dict(payload)


def test_approval_digest_binds_input_candidates_and_attribution(tmp_path):
    payload = batch(tmp_path)
    initial = contract().PreparedBatch.from_dict(payload).digest
    for field, value in [
        ("final_input_sha256", "b" * 64),
        ("agent_id", "child"), ("tool_call_id", "call-2"),
    ]:
        revised = copy.deepcopy(payload)
        revised[field] = value
        assert contract().PreparedBatch.from_dict(revised).digest != initial
    revised = copy.deepcopy(payload)
    revised["candidates"][0]["after_src"] = "x = 3\n"
    assert contract().PreparedBatch.from_dict(revised).digest != initial


def test_source_recheck_detects_retargeted_symlink(tmp_path):
    (tmp_path / "one.py").write_text("x = 1\n")
    prepared = contract().PreparedBatch.from_dict(batch(tmp_path))
    (tmp_path / "other.py").write_text("x = 1\n")
    (tmp_path / "one.py").unlink()
    (tmp_path / "one.py").symlink_to(tmp_path / "other.py")
    with pytest.raises(ValueError, match="stale"):
        prepared.verify_sources()

def test_decision_rejects_approval_for_changed_input(tmp_path):
    module = contract()
    prepared = module.PreparedBatch.from_dict(batch(tmp_path))
    decision = module.GateDecision.for_batch(
        prepared, decision="allowed", reason="deterministic checks passed",
        decision_id="decision-1",
    )
    assert module.GateDecision.from_dict(decision.to_dict(), prepared) == decision
    revised = batch(tmp_path)
    revised["final_input_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="bound"):
        module.GateDecision.from_dict(
            decision.to_dict(), module.PreparedBatch.from_dict(revised),
        )


@pytest.mark.parametrize("field,value", [
    ("decision", "allow"), ("decision", True),
    ("decision_id", ""), ("reason", ""),
    ("batch_sha256", "0" * 64),
    ("final_input_sha256", "0" * 64),
    ("candidate_sha256", "0" * 64),
])
def test_decision_rejects_malformed_or_unbound_response(tmp_path, field, value):
    module = contract()
    prepared = module.PreparedBatch.from_dict(batch(tmp_path))
    response = module.GateDecision.for_batch(
        prepared, decision="blocked", reason="policy denied", decision_id="deny-1",
    ).to_dict()
    response[field] = value
    with pytest.raises(ValueError):
        module.GateDecision.from_dict(response, prepared)
