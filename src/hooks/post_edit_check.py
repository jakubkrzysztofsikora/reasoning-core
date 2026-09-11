#!/usr/bin/env python3
"""PostToolUse fast deterministic checks after an edit.

Runs syntax/parse, configured lint (ruff for Python), and project rule checks
on the edited file, records ``verification_recorded`` receipts, and returns a
compact advisory summary when a check fails. The hook never blocks, never
exits non-zero, and never consults neural scoring. Repair is bounded by
``_episodes.MAX_REPAIR_ATTEMPTS``; once exhausted, the summary tells the agent
to stop and ask the user.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

_HOOKS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _HOOKS_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from src.hooks import _episodes as _ep  # noqa: E402
from src.hooks import audit_log  # noqa: E402
from src.hooks.post_bash_verification import _explicit_decision_id  # noqa: E402

_EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}
_MAX_FILE_BYTES = 1_000_000
_MAX_YAML_BYTES = 262_144
_CHECK_TIMEOUT_S = 5.0
_DEDUPE_WINDOW_S = 15.0
_PARSE_SUFFIXES = {".py", ".json", ".toml", ".yaml", ".yml"}


def _already_checked_recently(file_path: str, source: str) -> bool:
    """Dedupe identical checks when a hook is wired twice (global + repo)."""
    digest = hashlib.sha256(
        f"{file_path}\0{source}".encode("utf-8", errors="replace")
    ).hexdigest()[:16]
    marker = Path(tempfile.gettempdir()) / f"rc-post-edit-{digest}.claim"
    try:
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return False
    except FileExistsError:
        try:
            age = time.time() - marker.stat().st_mtime
        except OSError:
            return False
        if age < _DEDUPE_WINDOW_S:
            return True
        try:
            os.utime(marker)
        except OSError:
            pass
        return False
    except OSError:
        return False


class _YamlAliasesDisabled(Exception):
    """Raised when a YAML document uses alias expansion."""


def _load_yaml_no_aliases(source: str) -> Any:
    """Parse YAML with alias expansion rejected (billion-laughs defense)."""
    import yaml

    class _NoAliasLoader(yaml.SafeLoader):
        def compose_node(self, parent, index):
            if self.check_event(yaml.events.AliasEvent):
                raise _YamlAliasesDisabled()
            return super().compose_node(parent, index)

    return yaml.load(source, Loader=_NoAliasLoader)


def _payload() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (ValueError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _file_path(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        for key in ("file_path", "filePath", "path"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _result(kind: str, status: str, first_error: str = "", command: str = "") -> dict[str, Any]:
    return {
        "kind": kind,
        "status": status,
        "first_error": first_error,
        "command": command,
    }


def parse_check(file_path: str, source: str) -> dict[str, Any] | None:
    """Cheap syntax/parse check for common text formats.

    A parser that is not installed means "cannot check", not "failed".
    """
    suffix = Path(file_path).suffix.lower()
    if suffix not in _PARSE_SUFFIXES:
        return None
    if suffix in (".yaml", ".yml"):
        if len(source.encode("utf-8", errors="replace")) > _MAX_YAML_BYTES:
            return None
        try:
            import yaml  # noqa: F401
        except ImportError:
            return None
    try:
        if suffix == ".py":
            compile(source, file_path, "exec")
        elif suffix == ".json":
            json.loads(source)
        elif suffix == ".toml":
            import tomllib

            tomllib.loads(source)
        else:
            try:
                _load_yaml_no_aliases(source)
            except _YamlAliasesDisabled:
                return None
    except SyntaxError as exc:
        return _result("parse", "failed", f"line {exc.lineno}: {exc.msg}")
    except Exception as exc:  # noqa: BLE001 - any parse failure is actionable
        message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return _result("parse", "failed", message)
    return _result("parse", "passed")


def lint_check(file_path: str, project_dir: str | None = None) -> dict[str, Any] | None:
    """Run the project linter (ruff) when it is installed; Python only."""
    if Path(file_path).suffix.lower() != ".py":
        return None
    try:
        from src.hooks import _oracles

        command = _oracles._ruff_command()
    except Exception:  # noqa: BLE001 - lint is best-effort
        return None
    if not command:
        return None
    display = " ".join(command + ["check"])
    try:
        result = subprocess.run(
            [*command, "check", "--quiet", "--output-format", "concise", file_path],
            capture_output=True,
            text=True,
            check=False,
            timeout=_CHECK_TIMEOUT_S,
            cwd=project_dir or None,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return _result("lint", "passed", command=display)
    first = next(
        (line.strip() for line in result.stdout.splitlines() if line.strip()),
        "",
    )
    if not first:
        first = next(
            (line.strip() for line in result.stderr.splitlines() if line.strip()),
            f"ruff exited {result.returncode}",
        )
    return _result("lint", "failed", first, display)


def changed_line_numbers(before: str, after: str) -> set[int]:
    """1-based lines in ``after`` that differ from ``before``.

    Empty ``before`` means the file is new, so every line counts as changed and
    an empty set is returned as the "no filter" sentinel.
    """
    if not before:
        return set()
    import difflib

    changed: set[int] = set()
    matcher = difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines())
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            changed.update(range(j1 + 1, j2 + 1))
    return changed


def _git_head_source(file_path: str, project_dir: str) -> str:
    try:
        rel = Path(file_path).resolve().relative_to(Path(project_dir).resolve())
    except (OSError, ValueError):
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", project_dir, "show", f"HEAD:{rel.as_posix()}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def rules_check(
    file_path: str,
    source: str,
    project_dir: str,
    before_src: str | None = None,
) -> dict[str, Any] | None:
    """Evaluate project rules; only violations touched by this edit fail.

    The rule engine evaluates the whole after state, so violations already
    committed at HEAD are filtered out by diffing against HEAD. Uncommitted
    work from earlier in the session is treated as in scope: a violation that
    is still present keeps failing until fixed. Without a retrievable before
    state (new file, no git), no filter is applied.
    """
    try:
        from src.hooks import _dispatch, _rule_engine

        rules = _rule_engine.load_rules(project_dir)
    except Exception:  # noqa: BLE001 - rule checks are best-effort
        return None
    if not rules:
        return None
    if before_src is None:
        before_src = _git_head_source(file_path, project_dir)
    # The rule engine derives repo-relative scopes from this env var. Hooks run
    # in short-lived single-threaded processes; set/restore is process-local.
    prior = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = project_dir
    try:
        try:
            lang = _dispatch._detect_language(file_path)
            hits = _rule_engine.evaluate_edit(file_path, before_src, source, lang, rules)
        except Exception:  # noqa: BLE001
            return None
    finally:
        if prior is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = prior
    deny = [hit for hit in hits if hit.severity == "deny"]
    changed = changed_line_numbers(before_src, source)
    if deny and changed:
        deny = [hit for hit in deny if hit.line == 0 or hit.line in changed]
    if deny:
        return _result(
            "rules", "failed",
            f"{deny[0].rule_id}: {deny[0].message}",
            command="rules.yaml",
        )
    return _result("rules", "passed", command="rules.yaml")


def _project_dir(payload: dict[str, Any]) -> str:
    """Resolve the project root, clamping a payload cwd that escapes it."""
    env_dir = os.environ.get("RC_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        root = Path(env_dir).expanduser().resolve()
        cwd = payload.get("cwd")
        if cwd:
            try:
                candidate = Path(cwd).expanduser().resolve()
                candidate.relative_to(root)
                return str(candidate)
            except (OSError, ValueError):
                return str(root)
        return str(root)
    return str(Path(payload.get("cwd") or os.getcwd()).expanduser().resolve())


def _display_path(file_path: str, project_dir: str) -> str:
    try:
        return str(Path(file_path).resolve().relative_to(Path(project_dir).resolve()))
    except (OSError, ValueError):
        return str(file_path)


def format_feedback(
    results: list[dict[str, Any]],
    *,
    failure_count: int,
    file_path: str,
    project_dir: str,
) -> str:
    """Compact evidence summary for the agent; empty when nothing failed."""
    failed = [result for result in results if result["status"] == "failed"]
    if not failed:
        return ""
    policy = _ep.recovery_policy(failure_count)
    lines = [
        "Verification: FAILED",
        f"Check: {', '.join(result['kind'] for result in failed)}",
        f"File: {_display_path(file_path, project_dir)}",
        "Files: 1",
        f"First actionable error: {failed[0]['first_error']}",
    ]
    if policy["status"] == "abstain":
        lines.append(f"Repair: budget exhausted ({policy['max_attempts']} attempts)")
        lines.append(f"Next action: {policy['next_action']}.")
    else:
        lines.append(
            f"Repair: attempt {policy['attempt']} of {policy['max_attempts']} "
            f"— fix and rerun."
        )
        lines.append("Next action: repair, then rerun.")
    return "\n".join(lines)


def _read_source(file_path: str, project_dir: str) -> str | None:
    try:
        path = Path(file_path).expanduser().resolve()
        path.relative_to(Path(project_dir).expanduser().resolve())
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _count_prior_failures(
    *,
    audit_root: str,
    file_key: str,
    session_id: str,
    project_dir: str,
    file_path: str,
) -> int:
    """Count prior failed check invocations for this file (not failed checks).

    One invocation can fail several kinds; the repair budget is about attempts,
    so failures are deduplicated by ``check_batch_id``. ``file_key`` is the
    audit filename key (``audit_log._session_id()``), which differs from the
    host payload session UUID in real sessions; rows are filtered by the
    ``session_id`` field instead.
    """
    if not file_key:
        return 0
    target = Path(file_path).expanduser().resolve()
    project = str(Path(project_dir).expanduser().resolve())
    batches: set[str] = set()
    for event in _read_session_events(audit_root, file_key):
        if event.get("event_type") != "verification_recorded":
            continue
        if event.get("verification_status") != "failed":
            continue
        if event.get("tool_name") != "post_edit_check":
            continue
        batch = event.get("check_batch_id")
        if not batch:
            continue
        if str(event.get("project_dir") or "") != project:
            continue
        event_path = event.get("file_path") or ""
        if not event_path:
            continue
        try:
            same_file = Path(event_path).expanduser().resolve() == target
        except OSError:
            same_file = False
        if same_file:
            batches.add(str(batch))
    return len(batches)


def _read_session_events(
    audit_root: str, session_key: str, *, days: int = 2
) -> list[dict[str, Any]]:
    """Read this session's file for today and yesterday; never scan the corpus.

    Two days keeps the repair budget meaningful for edit loops that straddle
    UTC midnight while staying bounded.
    """
    clean = re.sub(r"[^A-Za-z0-9_\-]", "_", session_key)[:64]
    today = _dt.datetime.now(_dt.timezone.utc).date()
    rows: list[dict[str, Any]] = []
    for offset in range(max(1, days)):
        day_dir = Path(audit_root) / (
            today - _dt.timedelta(days=offset)
        ).strftime("%Y-%m-%d")
        for name in (f"{clean}.jsonl", f"{clean}.jsonl.gz"):
            path = day_dir / name
            if not path.is_file():
                continue
            try:
                opener = gzip.open if path.suffix == ".gz" else open
                with opener(path, "rt", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(row, dict):
                            rows.append(row)
            except OSError:
                continue
    return rows


def _record_receipt(
    *,
    result: dict[str, Any],
    payload: dict[str, Any],
    file_path: str,
    project_dir: str,
    session_id: str,
    parent_decision_id: str | None,
    check_batch_id: str,
) -> None:
    try:
        audit_log.append_correlated_event(
            event_type="verification_recorded",
            tool_name="post_edit_check",
            decision=f"verification_{result['status']}",
            payload=payload,
            file_path=file_path,
            project_dir=project_dir,
            verification_kind=result["kind"],
            verification_status=result["status"],
            exit_code=0 if result["status"] == "passed" else 1,
            deterministic=True,
            command=result.get("command") or "",
            artifact_ref="",
            check_batch_id=check_batch_id,
            parent_decision_id=parent_decision_id or "",
            association_type=(
                "explicit_decision" if parent_decision_id else "session_level"
            ),
            **({"session_id": session_id} if session_id else {}),
        )
    except Exception:  # noqa: BLE001 - receipts must never break the host
        pass


def main_with_payload(payload: dict[str, Any] | None = None) -> int:
    """Run checks for one PostToolUse payload. Always returns 0."""
    try:
        return _run(payload if isinstance(payload, dict) else {})
    except Exception:  # noqa: BLE001 - hooks must never raise into the host
        return 0


def _run(payload: dict[str, Any]) -> int:
    if os.environ.get("RC_POST_EDIT_CHECKS", "1") == "0":
        return 0
    tool_name = str(payload.get("tool_name") or "")
    if tool_name and tool_name not in _EDIT_TOOLS:
        return 0
    file_path = _file_path(payload)
    if not file_path:
        return 0
    project_dir = _project_dir(payload)
    source = _read_source(file_path, project_dir)
    if source is None:
        return 0
    if _already_checked_recently(file_path, source):
        return 0

    results: list[dict[str, Any]] = []
    for check in (
        lambda: parse_check(file_path, source),
        lambda: lint_check(file_path, project_dir),
        lambda: rules_check(file_path, source, project_dir),
    ):
        try:
            result = check()
        except Exception:  # noqa: BLE001
            result = None
        if result is not None:
            results.append(result)
    if not results:
        return 0

    session_id = str(payload.get("session_id") or os.environ.get("RC_SESSION_ID") or "")
    parent_decision_id = _explicit_decision_id(payload)
    check_batch_id = uuid.uuid4().hex[:12]
    any_failed = any(result["status"] == "failed" for result in results)
    failure_count = 0
    if any_failed:
        failure_count = 1 + _count_prior_failures(
            audit_root=audit_log._AUDIT_ROOT,
            file_key=audit_log._session_id(),
            session_id=session_id,
            project_dir=project_dir,
            file_path=file_path,
        )

    for result in results:
        _record_receipt(
            result=result,
            payload=payload,
            file_path=file_path,
            project_dir=project_dir,
            session_id=session_id,
            parent_decision_id=parent_decision_id,
            check_batch_id=check_batch_id,
        )

    if any_failed:
        text = format_feedback(
            results,
            failure_count=failure_count,
            file_path=file_path,
            project_dir=project_dir,
        )
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": text,
            }
        }) + "\n")
    return 0


def main() -> int:
    return main_with_payload(_payload())


if __name__ == "__main__":
    sys.exit(main())
