"""OMP JSON bridge into shared Python guards.

Each event runs in a fresh interpreter so legacy environment-based helpers
cannot leak attribution between sessions. Native candidate preparation and
the runtime mutation boundary are separate, required integration components.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from src.omp_contract import GateDecision, PreparedBatch, _absolute, _identity

_MAX_INPUT_BYTES = 32 * 1024 * 1024
_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from src.hooks.omp_bridge import _worker_main; "
    "raise SystemExit(_worker_main())"
)


def _failure(reason: str, batch: PreparedBatch | None = None) -> dict[str, Any]:
    identifier = uuid.uuid4().hex[:12]
    if batch is not None:
        return GateDecision.for_batch(
            batch, decision="blocked", reason=reason, decision_id=identifier,
        ).to_dict()
    return {"decision": "blocked", "reason": reason, "decision_id": identifier}


def _lifecycle_identity(event: dict[str, Any], fields: set[str]) -> dict[str, str | None]:
    if set(event) != {"event", "project_dir", "session_id", "agent_id", "parent_agent_id", *fields}:
        raise ValueError("invalid event")
    project = _absolute(event["project_dir"], "project_dir").resolve(strict=True)
    if not project.is_dir():
        raise ValueError("invalid project directory")
    parent = event["parent_agent_id"]
    return {
        "project_dir": str(project),
        "session_id": _identity(event["session_id"], "session_id"),
        "agent_id": _identity(event["agent_id"], "agent_id"),
        "parent_agent_id": _identity(parent, "parent_agent_id") if parent is not None else None,
    }


def _context(event: dict[str, Any]) -> dict[str, str | None]:
    if event.get("event") in ("prepare", "result"):
        batch = PreparedBatch.from_dict(event.get("batch"))
        return {
            "project_dir": batch.project_dir, "session_id": batch.session_id,
            "agent_id": batch.agent_id, "parent_agent_id": batch.parent_agent_id,
        }
    kind = event.get("event")
    if kind == "bash":
        command = event.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("invalid command")
        cwd = event.get("cwd")
        if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
            raise ValueError("invalid cwd")
        return _lifecycle_identity(event, {"command", "cwd"})
    if kind == "subagent":
        name = event.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("invalid subagent name")
        return _lifecycle_identity(event, {"name"})
    if kind == "advisor":
        if event.get("kind") not in ("cost_changed", "yielded"):
            raise ValueError("invalid advisor kind")
        cost = event.get("cost")
        if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost)):
            raise ValueError("invalid advisor cost")
        return _lifecycle_identity(event, {"kind", "cost"})
    if kind not in ("session_start", "session_stop"):
        raise ValueError("invalid event")
    return _lifecycle_identity(event, set())


def handle_event(event: dict) -> dict:
    """Validate and dispatch one event without changing the caller's environment."""
    batch = None
    try:
        if not isinstance(event, dict):
            raise ValueError("invalid event")
        if event.get("event") in ("prepare", "result"):
            batch = PreparedBatch.from_dict(event.get("batch"))
        context = _context(event)
        wire = json.dumps(event, ensure_ascii=True)
        if len(wire.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("oversized event")
        timeout = float(os.environ.get("RC_OMP_BRIDGE_TIMEOUT_S", "30"))
        if not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError("invalid bridge timeout")
        environment = dict(os.environ)
        environment.update({
            "RC_HOST": "omp", "RC_PROJECT_DIR": context["project_dir"],
            "RC_RUN_DIR": context["project_dir"], "RC_SESSION_ID": context["session_id"],
            "OMP_PROJECT_DIR": context["project_dir"], "OMP_SESSION_ID": context["session_id"],
            # Legacy helpers still consult these directly in their isolated process.
            "CLAUDE_PROJECT_DIR": context["project_dir"],
            "CLAUDE_SESSION_ID": context["session_id"],
        })
        root = str(Path(__file__).resolve().parents[2])
        completed = subprocess.run(
            [sys.executable, "-I", "-c", _BOOTSTRAP, root],
            input=wire, text=True, encoding="utf-8", capture_output=True,
            env=environment, cwd=root, timeout=timeout, check=False,
        )
        if completed.returncode != 0:
            return _failure("bridge_process_failed", batch)
        try:
            response = json.loads(completed.stdout)
        except (ValueError, TypeError):
            return _failure("invalid_bridge_output", batch)
        if not isinstance(response, dict) or response.get("decision") not in ("allowed", "blocked"):
            return _failure("invalid_bridge_output", batch)
        if event["event"] == "prepare":
            try:
                GateDecision.from_dict(response, batch)
            except ValueError:
                return _failure("invalid_bridge_output", batch)
        elif (
            not isinstance(response.get("reason"), str)
            or not isinstance(response.get("decision_id"), str)
        ):
            return _failure("invalid_bridge_output", batch)
        return response
    except subprocess.TimeoutExpired:
        return _failure("bridge_timeout", batch)
    except (ValueError, TypeError, OSError, RuntimeError):
        return _failure("invalid_event", batch)


def _audit(
    event: dict, *, decision: str, reason: str, decision_id: str,
    signal_source: str = "omp_bridge", **extra,
) -> bool:
    from src.hooks import audit_log

    context = _context(event)
    row = audit_log.new_event(
        tool_name=f"omp:{event['event']}", decision=decision, reason=reason,
        decision_id=decision_id, signal_source=signal_source,
        agent_id=context["agent_id"], parent_agent_id=context["parent_agent_id"],
        **extra,
    )
    return audit_log.append_event(row, fsync=True) is not None


def _decision(event: dict, batch: PreparedBatch, decision: str, reason: str) -> dict:
    response = GateDecision.for_batch(
        batch, decision=decision, reason=reason, decision_id=uuid.uuid4().hex[:12],
    )
    if not _audit(
        event, decision=decision, reason=reason, decision_id=response.decision_id,
        batch_sha256=batch.digest, final_input_sha256=batch.final_input_sha256,
        tool_call_id=batch.tool_call_id,
    ):
        return _failure("bridge_audit_unavailable", batch)
    return response.to_dict()


def _prepare(event: dict) -> dict:
    from src.hooks import pre_edit_guard as guard
    from src import mcp_gate, project_index

    batch = PreparedBatch.from_dict(event["batch"])
    try:
        batch.verify_sources()
    except ValueError:
        return _decision(event, batch, "blocked", "stale_sources")

    candidate_sources = {item.path: item.after_src for item in batch.candidates}
    # Evaluate every deterministic candidate before any expensive score request.
    for candidate in batch.candidates:
        path = candidate.path
        before, after = candidate.before_src or "", candidate.after_src or ""
        if guard._guard_paths.is_guarded(path) and not guard._guard_paths.is_override_active():
            return _decision(event, batch, "blocked", "guard_file_locked")
        if Path(path).resolve().name.casefold().rstrip(".") == ".envrc.local":
            if not guard._guard_paths.is_override_active():
                return _decision(event, batch, "blocked", "envrc_local_locked")
        if guard._git_lease.edit_denial(path):
            return _decision(event, batch, "blocked", "worktree_not_leased")
    from src.hooks import _kill_switches

    # Integrity and lease checks precede every operator scoring/policy override.
    override = _kill_switches.is_disabled_globally() or _kill_switches.consume_bypass_next()
    skipped = [_kill_switches.is_file_skipped(item.path) for item in batch.candidates]
    if override or all(skipped):
        return _decision(event, batch, "allowed", "operator_override")

    for index, candidate in enumerate(batch.candidates):
        if skipped[index]:
            continue
        path = candidate.path
        before, after = candidate.before_src or "", candidate.after_src or ""
        outcomes = [
            guard._dispatch.gate_lang_lock(
                file_path=path, read_before_src=lambda _path: before,
            ),
            guard._dispatch.gate_plan_grounding(file_path=path, after_src=after),
            guard._dispatch.gate_rule_engine(
                file_path=path, before_src=before, after_src=after, report={},
            ),
        ]
        for raw in outcomes:
            outcome = guard._apply_mode(raw)
            if outcome.action == "exit_block":
                return _decision(event, batch, "blocked", outcome.reason or "deterministic_policy")
            if outcome.action not in ("pass", "exit_allow"):
                _audit(
                    event, decision="warn", reason=outcome.reason or "deterministic_advisory",
                    decision_id=uuid.uuid4().hex[:12], file_path=path,
                )

        cycle = project_index.find_import_cycle(
            batch.project_dir, path, after, candidate_sources=candidate_sources,
        )
        duplicates = project_index.find_duplicate_definitions(
            batch.project_dir, path, after, candidate_sources=candidate_sources,
        )
        cycle_key = " -> ".join(cycle) if cycle else ""
        cycle_allow = {x.strip() for x in os.getenv("RC_IMPORT_CYCLE_ALLOWLIST", "").split(",")}
        duplicate_allow = {x.strip() for x in os.getenv("RC_DUPLICATE_ALLOWLIST", "").split(",")}
        duplicates = [
            item for item in duplicates if f"{item['symbol']}@{item['path']}" not in duplicate_allow
        ]
        if (cycle and cycle_key not in cycle_allow) or duplicates:
            if guard._rc_mode() != "advise" and os.getenv("RC_STRUCTURAL_BLOCK", "1") == "1":
                return _decision(event, batch, "blocked", "deterministic_structural_policy")
            _audit(
                event, decision="warn", reason="deterministic_structural_advisory",
                decision_id=uuid.uuid4().hex[:12], file_path=path,
            )

        if guard._oracles is None:
            return _decision(event, batch, "blocked", "oracle_unavailable")
        if candidate.operation == "write":
            report = guard._oracles.run_oracles(
                file_path=path, after_src=after,
                enable_t1=os.getenv("RC_ORACLE_T1", "1") == "1",
                enable_t2=os.getenv("RC_ORACLE_T2", "1") == "1",
            )
            if not report.clean:
                if guard._rc_mode() != "advise" and os.getenv("RC_ORACLE_BLOCK") == "1":
                    return _decision(event, batch, "blocked", "deterministic_oracle_failure")
                _audit(
                    event, decision="warn", reason="deterministic_oracle_advisory",
                    decision_id=uuid.uuid4().hex[:12], file_path=path,
                )

    failure = None
    for index, candidate in enumerate(batch.candidates):
        if skipped[index]:
            continue
        score_failure = None
        report = {}
        try:
            response = mcp_gate.request_score({
                "path": candidate.path, "before_src": candidate.before_src or "",
                "after_src": candidate.after_src or "",
                "change_kind": "write" if candidate.before_src is None else "edit",
                "host": "omp", "session_id": batch.session_id,
                "project_dir": batch.project_dir, "agent_id": batch.agent_id,
                "parent_agent_id": batch.parent_agent_id, "tool_call_id": batch.tool_call_id,
            })
            if response.status_code == 401:
                score_failure = "sidecar_auth_failed"
            elif response.status_code == 415:
                _audit(
                    event, decision="warn", reason="unsupported_scoring_language",
                    decision_id=uuid.uuid4().hex[:12], file_path=candidate.path,
                )
                continue
            elif response.status_code != 200:
                score_failure = "sidecar_unavailable"
            else:
                report = response.json()
                if (
                    not isinstance(report, dict)
                    or "architectural_impact_score" not in report
                    or type(report.get("regression_detected")) is not bool
                ):
                    report = {}
                    score_failure = "invalid_score_report"
        except ValueError:
            score_failure = "invalid_score_report"
        except Exception:
            score_failure = "sidecar_unavailable"
        if score_failure:
            failure = score_failure
            if guard._fail_closed():
                return _decision(event, batch, "blocked", failure)
            _audit(event, decision="warn", reason=failure, decision_id=uuid.uuid4().hex[:12])
            continue
        if report.get("regression_detected"):
            _audit(
                event, decision="warn", reason="neural_uncorroborated_advisory",
                decision_id=uuid.uuid4().hex[:12], file_path=candidate.path,
            )
    try:
        batch.verify_sources()
    except ValueError:
        return _decision(event, batch, "blocked", "stale_sources")
    return _decision(event, batch, "allowed", failure or "deterministic_checks_passed")


def _journal_rows() -> list[dict]:
    from src.hooks import audit_log

    # Bounded lookup: an aged-out approval cannot establish a successful receipt.
    file = Path(audit_log._AUDIT_ROOT) / audit_log._today() / f"{audit_log._session_id()}.jsonl"
    try:
        with file.open("rb") as stream:
            stream.seek(0, 2)
            offset = max(0, stream.tell() - 2 * 1024 * 1024)
            stream.seek(offset)
            if offset:
                stream.readline()
            lines = stream.read().decode("utf-8").splitlines()
        return [row for line in lines if isinstance(row := json.loads(line), dict)]
    except (OSError, ValueError, UnicodeError):
        return []


def _result(event: dict) -> dict:
    import portalocker
    from src.hooks import audit_log

    if set(event) != {"event", "batch", "approval", "success", "error", "persisted_digests"}:
        raise ValueError("invalid receipt")
    batch = PreparedBatch.from_dict(event["batch"])
    approval = GateDecision.from_dict(event["approval"], batch)
    if type(event["success"]) is not bool or (
        event["error"] is not None and not isinstance(event["error"], str)
    ):
        raise ValueError("invalid receipt outcome")
    persisted = event["persisted_digests"]
    if not isinstance(persisted, dict):
        raise ValueError("invalid persisted digests")
    root = Path(audit_log._AUDIT_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / f"omp-receipt-{hashlib.sha256(approval.decision_id.encode()).hexdigest()}.lock"
    with portalocker.Lock(str(lock_path), timeout=2):
        rows = _journal_rows()
        if any(row.get("approval_decision_id") == approval.decision_id for row in rows):
            return _failure("receipt_replay", batch)
        if approval.decision != "allowed" or not any(
            row.get("signal_source") == "omp_bridge"
            and row.get("tool_name") == "omp:prepare"
            and row.get("decision") == "allowed"
            and row.get("decision_id") == approval.decision_id
            and row.get("batch_sha256") == batch.digest
            and row.get("agent_id") == batch.agent_id
            and row.get("project_dir") == batch.project_dir
            for row in rows
        ):
            return _failure("unknown_approval", batch)
        actual = {}
        expected = {}
        for candidate in batch.candidates:
            path = Path(candidate.path)
            actual[candidate.path] = (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            )
            expected[candidate.path] = (
                hashlib.sha256(candidate.after_src.encode("utf-8")).hexdigest()
                if candidate.after_src is not None else None
            )
        if persisted != actual:
            return _failure("receipt_digest_mismatch", batch)
        if event["success"] and actual != expected:
            return _failure("receipt_content_mismatch", batch)
        reason = "receipt_verified" if event["success"] else "write_failure_receipt"
        identifier = uuid.uuid4().hex[:12]
        if not _audit(
            event, decision="allowed", reason=reason, decision_id=identifier,
            approval_decision_id=approval.decision_id, tool_call_id=batch.tool_call_id,
            batch_sha256=batch.digest, persisted_digests=actual, success=event["success"],
        ):
            return _failure("bridge_audit_unavailable", batch)
        return {"decision": "allowed", "reason": reason, "decision_id": identifier}


def _bash_event(event: dict) -> dict:
    from src.hooks import pre_bash_guard as guard

    identifier = uuid.uuid4().hex[:12]
    code, message = guard.screen_command(
        event["command"], event["cwd"] or event["project_dir"],
    )
    if code == 0:
        decision, reason = "allowed", message.strip() or "bash_screen_passed"
    elif code == 2:
        decision, reason = "blocked", message.strip() or "bash_screen_blocked"
    else:
        decision, reason = "blocked", "bash_screen_error"
    if not _audit(
        event, decision=decision, reason=reason, decision_id=identifier,
        command_sha256=hashlib.sha256(event["command"].encode("utf-8")).hexdigest(),
    ):
        return _failure("bridge_audit_unavailable")
    return {"decision": decision, "reason": reason, "decision_id": identifier}


def _subagent_event(event: dict) -> dict:
    identifier = uuid.uuid4().hex[:12]
    if not _audit(
        event, decision="allowed", reason="subagent_spawn_recorded",
        decision_id=identifier, subagent_name=event["name"],
    ):
        return _failure("bridge_audit_unavailable")
    return {"decision": "allowed", "reason": "subagent_spawn_recorded", "decision_id": identifier}


def _session_stop_event(event: dict) -> dict:
    identifier = uuid.uuid4().hex[:12]
    if os.environ.get("RC_DUP_ORACLE") != "1":
        if not _audit(
            event, decision="allowed", reason="session_stop", decision_id=identifier,
        ):
            return _failure("bridge_audit_unavailable")
        return {"decision": "allowed", "reason": "session_stop", "decision_id": identifier}
    decision = None
    try:
        from src.duplicate_detection import findings as ledger
        from src.duplicate_detection.review import stop_decision

        repo_root = ledger.resolve_repo_root(event["project_dir"])
        advise = os.environ.get("RC_MODE", "").strip().lower() == "advise"
        decision = stop_decision(
            repo_root, ledger.ledger_path(repo_root), count_blocks=not advise,
        )
    except Exception:  # noqa: BLE001 - session end must not trap on a broken check
        decision = None
    if decision is None:
        if not _audit(
            event, decision="allowed", reason="dup_stop_fail_open", decision_id=identifier,
        ):
            return _failure("bridge_audit_unavailable")
        return {"decision": "allowed", "reason": "dup_stop_fail_open", "decision_id": identifier}
    fields = dict(
        extract_ids=[item["id"] for item in decision.extract],
        escalated_ids=decision.escalated, resolved_ids=decision.resolved,
    )
    if decision.block and os.environ.get("RC_MODE", "").strip().lower() != "advise":
        if not _audit(
            event, decision="blocked", reason="open_extract_duplicate",
            decision_id=identifier, **fields,
        ):
            return _failure("bridge_audit_unavailable")
        return {
            "decision": "blocked", "reason": decision.message, "decision_id": identifier,
        }
    if not _audit(
        event, decision="allowed", reason="dup_stop_advisory", decision_id=identifier, **fields,
    ):
        return _failure("bridge_audit_unavailable")
    return {"decision": "allowed", "reason": "dup_stop_advisory", "decision_id": identifier}


def _advisor_event(event: dict) -> dict:
    identifier = uuid.uuid4().hex[:12]
    if not _audit(
        event, decision="allowed", reason=f"advisor_{event['kind']}",
        decision_id=identifier, signal_source="omp-advisor", cost=event["cost"],
    ):
        return _failure("bridge_audit_unavailable")
    return {"decision": "allowed", "reason": f"advisor_{event['kind']}", "decision_id": identifier}


def _evaluate_event(event: dict) -> dict:
    _context(event)
    if event["event"] == "prepare":
        if set(event) != {"event", "batch"}:
            raise ValueError("invalid prepare")
        return _prepare(event)
    if event["event"] == "result":
        return _result(event)
    if event["event"] == "bash":
        return _bash_event(event)
    if event["event"] == "subagent":
        return _subagent_event(event)
    if event["event"] == "advisor":
        return _advisor_event(event)
    if event["event"] == "session_stop":
        return _session_stop_event(event)
    identifier = uuid.uuid4().hex[:12]
    if not _audit(
        event, decision="allowed", reason=event["event"], decision_id=identifier,
    ):
        return _failure("bridge_audit_unavailable")
    return {"decision": "allowed", "reason": event["event"], "decision_id": identifier}


def _read_event() -> dict:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("oversized event")
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("invalid event")
    return event


def _worker_main() -> int:
    batch = None
    try:
        event = _read_event()
        if event.get("event") in ("prepare", "result"):
            batch = PreparedBatch.from_dict(event.get("batch"))
        with contextlib.redirect_stdout(sys.stderr):
            response = _evaluate_event(event)
    except Exception:
        response = _failure("guard_evaluation_failed", batch)
    print(json.dumps(response))
    return 0


def main() -> int:
    try:
        response = handle_event(_read_event())
    except (ValueError, TypeError, OSError):
        print(json.dumps(_failure("invalid_event")))
        return 2
    print(json.dumps(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
