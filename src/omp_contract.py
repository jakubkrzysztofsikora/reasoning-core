"""Validated OMP prepared mutations; this module never writes candidate files.

Native staging must supply these exact bytes. Validation and a source recheck
are prerequisites for approval, not a filesystem lock or a commit operation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_TEXT_BYTES = 4 * 1024 * 1024


def _text(value: Any, name: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must be valid UTF-8") from exc
    if size > _MAX_TEXT_BYTES:
        raise ValueError(f"{name} exceeds native text limit")
    return value


def _identity(value: Any, name: str) -> str:
    text = _text(value, name)
    if not text or len(text) > 512 or any(ord(c) < 32 for c in text):
        raise ValueError(f"{name} must be a nonempty bounded identifier")
    return text


def _hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _absolute(value: Any, name: str) -> Path:
    text = _text(value, name)
    if not text or "\0" in text or "://" in text:
        raise ValueError(f"{name} must be a plain absolute path")
    path = Path(text)
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    return path


def _mapping(value: Any, fields: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{name} has missing or unknown fields")
    return value


@dataclass(frozen=True)
class Candidate:
    path: str
    operation: str
    before_src: str | None
    after_src: str | None
    expected_before_sha256: str | None
    _resolved: Path = field(repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Any, project: Path) -> Candidate:
        data = _mapping(value, {
            "path", "operation", "before_src", "after_src",
            "expected_before_sha256",
        }, "candidate")
        authored = _absolute(data["path"], "candidate path")
        try:
            resolved = authored.resolve()
        except (OSError, RuntimeError) as exc:
            raise ValueError("candidate path cannot be resolved") from exc
        if resolved == project or not resolved.is_relative_to(project):
            raise ValueError("candidate is outside project")
        operation = data["operation"]
        if operation not in ("write", "delete"):
            raise ValueError("candidate operation must be write or delete")
        before = _text(data["before_src"], "before_src", nullable=True)
        after = _text(data["after_src"], "after_src", nullable=True)
        expected = data["expected_before_sha256"]
        if before is None:
            if expected is not None or operation == "delete":
                raise ValueError("missing source must have no digest and cannot be deleted")
        else:
            expected = _hash(expected, "expected_before_sha256")
            if hashlib.sha256(before.encode("utf-8")).hexdigest() != expected:
                raise ValueError("before source digest mismatch")
        if (operation == "write" and after is None) or (
            operation == "delete" and after is not None
        ):
            raise ValueError("after content does not match operation")
        return cls(str(authored), operation, before, after, expected, resolved)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "operation": self.operation,
            "before_src": self.before_src,
            "after_src": self.after_src,
            "expected_before_sha256": self.expected_before_sha256,
        }

    def verify_source(self) -> None:
        path = Path(self.path)
        try:
            if path.resolve() != self._resolved:
                raise ValueError(f"stale candidate path: {self.path}")
            if self.before_src is None:
                if path.exists() or path.is_symlink():
                    raise ValueError(f"stale missing source: {self.path}")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != self.expected_before_sha256:
                raise ValueError(f"stale source digest: {self.path}")
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"stale or unreadable source: {self.path}") from exc


@dataclass(frozen=True)
class PreparedBatch:
    protocol_version: int
    project_dir: str
    session_id: str
    agent_id: str
    parent_agent_id: str | None
    tool_call_id: str
    final_input_sha256: str
    candidates: tuple[Candidate, ...]
    _project: Path = field(repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Any) -> PreparedBatch:
        data = _mapping(value, {
            "protocol_version", "project_dir", "session_id", "agent_id",
            "parent_agent_id", "tool_call_id", "final_input_sha256", "candidates",
        }, "prepared batch")
        if type(data["protocol_version"]) is not int or data["protocol_version"] != 1:
            raise ValueError("unsupported protocol version")
        authored = _absolute(data["project_dir"], "project_dir")
        try:
            project = authored.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("project directory does not exist") from exc
        if not project.is_dir():
            raise ValueError("project_dir must be a directory")
        items = data["candidates"]
        if not isinstance(items, list) or not 1 <= len(items) <= 256:
            raise ValueError("candidates must be a nonempty bounded list")
        candidates = tuple(Candidate.from_dict(item, project) for item in items)
        paths = [item._resolved for item in candidates]
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate candidate destination")
        parent = data["parent_agent_id"]
        if parent is not None:
            parent = _identity(parent, "parent_agent_id")
        return cls(
            1, str(authored), _identity(data["session_id"], "session_id"),
            _identity(data["agent_id"], "agent_id"), parent,
            _identity(data["tool_call_id"], "tool_call_id"),
            _hash(data["final_input_sha256"], "final_input_sha256"),
            candidates, project,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "project_dir": self.project_dir,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "tool_call_id": self.tool_call_id,
            "final_input_sha256": self.final_input_sha256,
            "candidates": [item.to_dict() for item in self.candidates],
        }

    @property
    def digest(self) -> str:
        value = self.to_dict()
        value["resolved_project"] = str(self._project)
        value["resolved_paths"] = [str(item._resolved) for item in self.candidates]
        wire = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(wire.encode("utf-8")).hexdigest()

    def verify_sources(self) -> None:
        try:
            if Path(self.project_dir).resolve(strict=True) != self._project:
                raise ValueError("stale project directory")
        except (OSError, RuntimeError) as exc:
            raise ValueError("stale project directory") from exc
        for candidate in self.candidates:
            candidate.verify_source()


@dataclass(frozen=True)
class GateDecision:
    decision: str
    reason: str
    decision_id: str
    batch_sha256: str
    final_input_sha256: str
    candidate_sha256: str

    @staticmethod
    def _candidate_digest(batch: PreparedBatch) -> str:
        wire = json.dumps(
            [item.to_dict() for item in batch.candidates],
            sort_keys=True, ensure_ascii=True, separators=(",", ":"),
        )
        return hashlib.sha256(wire.encode("utf-8")).hexdigest()

    @classmethod
    def for_batch(
        cls, batch: PreparedBatch, *, decision: str, reason: str, decision_id: str,
    ) -> GateDecision:
        return cls.from_dict({
            "decision": decision,
            "reason": reason,
            "decision_id": decision_id,
            "batch_sha256": batch.digest,
            "final_input_sha256": batch.final_input_sha256,
            "candidate_sha256": cls._candidate_digest(batch),
        }, batch)

    @classmethod
    def from_dict(cls, value: Any, batch: PreparedBatch) -> GateDecision:
        data = _mapping(value, {
            "decision", "reason", "decision_id", "batch_sha256",
            "final_input_sha256", "candidate_sha256",
        }, "gate decision")
        if data["decision"] not in ("allowed", "blocked"):
            raise ValueError("gate decision must be allowed or blocked")
        reason = _text(data["reason"], "reason")
        if not reason or not reason.strip():
            raise ValueError("gate reason must not be empty")
        decision_id = _identity(data["decision_id"], "decision_id")
        bindings = {
            "batch_sha256": batch.digest,
            "final_input_sha256": batch.final_input_sha256,
            "candidate_sha256": cls._candidate_digest(batch),
        }
        for name, expected in bindings.items():
            if _hash(data[name], name) != expected:
                raise ValueError(f"gate decision is not bound to this batch: {name}")
        return cls(
            data["decision"], reason, decision_id, bindings["batch_sha256"],
            bindings["final_input_sha256"], bindings["candidate_sha256"],
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "decision_id": self.decision_id,
            "batch_sha256": self.batch_sha256,
            "final_input_sha256": self.final_input_sha256,
            "candidate_sha256": self.candidate_sha256,
        }
