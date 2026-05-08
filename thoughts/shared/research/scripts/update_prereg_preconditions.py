"""Set iter3-prereg.json preconditions_satisfied flags based on phase 0.4 + 0.5
result JSONs. Conservative: only flips a flag from false → true when the
corresponding report's `verdict_pass` (or equivalent) is true. Never flips a
flag that was already true. Always re-emits the prereg with stable indent.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", required=True)
    ap.add_argument("--reliability-report", required=True)
    ap.add_argument("--c-at-a-report", required=True)
    args = ap.parse_args(argv)

    prereg_p = Path(args.prereg)
    rel = json.loads(Path(args.reliability_report).read_text())
    cat = json.loads(Path(args.c_at_a_report).read_text())

    rel_pass = bool(rel.get("verdicts", {}).get("overall_pass"))
    cat_pass = bool(cat.get("verdict_pass"))

    prereg = json.loads(prereg_p.read_text())
    pre = prereg.setdefault("preconditions_satisfied", {})
    prior_sha = sha256_file(prereg_p)

    actions: list[str] = []
    if rel_pass and not pre.get("phase_0_5_decomposer_reliability_passed"):
        pre["phase_0_5_decomposer_reliability_passed"] = True
        actions.append("set phase_0_5_decomposer_reliability_passed=true")
    if cat_pass and not pre.get("phase_0_4_c_at_a_retroactive_sanity_passed"):
        pre["phase_0_4_c_at_a_retroactive_sanity_passed"] = True
        actions.append("set phase_0_4_c_at_a_retroactive_sanity_passed=true")

    log = prereg.setdefault("amendment_log", [])
    log.append({
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "action": "phase_0_4_0_5_preconditions_update",
        "rel_pass": rel_pass,
        "cat_pass": cat_pass,
        "actions": actions,
        "prior_prereg_sha256": prior_sha,
        "reliability_report": str(args.reliability_report),
        "c_at_a_report": str(args.c_at_a_report),
    })

    prereg_p.write_text(json.dumps(prereg, indent=2) + "\n")
    new_sha = sha256_file(prereg_p)

    print(json.dumps({
        "rel_pass": rel_pass,
        "cat_pass": cat_pass,
        "actions": actions,
        "prior_sha256": prior_sha,
        "new_sha256": new_sha,
        "preconditions_satisfied": pre,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
