#!/usr/bin/env python3
"""Run the pilot's deterministic policy checks after a control write."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.hooks import _plan_contract, _rule_engine


def check_policy(project: Path, path: str) -> str | None:
    """Return the first deterministic violation for ``path``, if any."""
    project = project.resolve()
    target = (project / path).resolve()
    try:
        target.relative_to(project)
    except ValueError:
        return "path escapes project"
    if not target.is_file():
        return f"missing target: {path}"

    source = target.read_text(encoding="utf-8")
    contract = _plan_contract.Contract.load(str(project))
    violations = []
    path_violation = contract.check_path(path)
    if path_violation is not None:
        violations.append(path_violation)
    violations.extend(contract.check_imports(path, source))
    violations.extend(contract.check_invariants(path, source))
    deny = contract.first_deny(violations)
    if deny is not None:
        return f"contract violation: {deny.kind}:{deny.rule_id}: {deny.message}"

    rules = _rule_engine.load_rules(str(project))
    if rules:
        language = "python" if target.suffix == ".py" else ""
        hits = _rule_engine.evaluate_edit(path, "", source, language, rules)
        denied = next((hit for hit in hits if hit.severity == "deny"), None)
        if denied is not None:
            return f"rule violation: {denied.rule_id}: {denied.message}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--path", required=True)
    args = parser.parse_args()
    violation = check_policy(Path(args.project), args.path)
    if violation:
        print(violation, file=sys.stderr)
        return 2 if violation.startswith(("path escapes", "missing target")) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
