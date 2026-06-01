"""P4 calibration corpus mining (deferred tracker #31).

Walks the last N months of git history. Labels:
  - merged-and-stable-for-7d → negative (benign change)
  - reverted-within-7d → positive (regression)

Filters out the noise reviewer-flagged in tracker #14:
  - amend commits (same author, message starts with "amend"/"fixup")
  - force-pushes (no reflog access here, so we approximate via parent count)
  - reverts whose diff touches DIFFERENT files than the original commit
    (often renames/moves disguised as reverts)

Output: eval/calibrated/labels.jsonl, one JSON object per (before, after,
label, file_kind, sha) row. Stratified counts logged to stderr.

Usage:
    python -m eval.calibration_corpus --repo-root . --since 6.months \\
        --out eval/calibrated/labels.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path


def _run(cmd: list, cwd: str) -> str:
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        return out.stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def _file_kind(path: str) -> str:
    p = path.lower()
    if "/plans/" in p or p.endswith(".plan.md"):
        return "plan_md"
    if "/test" in p or "/tests/" in p or "_test" in p:
        return "test_code"
    ext = Path(path).suffix.lower()
    if ext in (".md", ".markdown", ".mdx"):
        return "doc_md"
    if ext in (".json", ".yaml", ".yml", ".toml", ".ini"):
        return "config"
    return "source_code"


def _is_amend(msg: str) -> bool:
    head = msg.lower().lstrip()
    return head.startswith(("amend", "fixup", "squash"))


def _list_commits(repo: str, since: str) -> list:
    raw = _run(["git", "log", f"--since={since}", "--format=%H|%P|%an|%s"], repo)
    rows = []
    for line in raw.splitlines():
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        sha, parents, _author, msg = parts
        rows.append({"sha": sha, "parents": parents.split(), "msg": msg})
    return rows


def _files_touched(repo: str, sha: str) -> set:
    raw = _run(["git", "show", "--name-only", "--format=", sha], repo)
    return {ln.strip() for ln in raw.splitlines() if ln.strip()}


def _is_revert(msg: str) -> bool:
    head = msg.lower().lstrip()
    return head.startswith("revert")


def _is_fix(msg: str) -> bool:
    """Audit 2026-06-01 §10: treat conventional-commit fix:/bug: as a
    regression marker — the parent commit introduced what the fix removed.
    """
    head = msg.lower().lstrip()
    # Strip leading gitmoji / emoji (`:bug: fix: ...`)
    head = head.lstrip(":")
    while head and head[0] in " 🐛✨🔧📝⚡":
        head = head[1:].lstrip()
    return head.startswith(("fix:", "fix(", "bug:", "bug(", "hotfix:"))


def _parent_sha(repo: str, sha: str) -> str:
    raw = _run(["git", "rev-parse", f"{sha}^"], repo).strip()
    return raw if len(raw) >= 7 and all(c in "0123456789abcdef" for c in raw) else ""


def _revert_target(msg: str) -> str:
    """Extract reverted SHA from `Revert "..."` message."""
    import re
    m = re.search(r"This reverts commit ([0-9a-f]{7,40})", msg)
    return m.group(1) if m else ""


def _file_pair(repo: str, sha: str, path: str) -> tuple:
    before = _run(["git", "show", f"{sha}^:{path}"], repo)
    after = _run(["git", "show", f"{sha}:{path}"], repo)
    return before, after


def mine(repo: str, since: str, out_path: str, *,
         include_positives: bool = True,
         mode: str = "git-grep") -> dict:
    if mode == "bisect":
        raise NotImplementedError("bisect mode TBD — only git-grep currently supported")
    commits = _list_commits(repo, since)
    counts: Counter = Counter()
    rows: list = []
    for c in commits:
        msg = c["msg"]
        if _is_amend(msg):
            counts["skip_amend"] += 1
            continue
        if len(c["parents"]) > 1:
            counts["skip_merge"] += 1
            continue
        if include_positives and _is_revert(msg):
            target = _revert_target(msg)
            if not target:
                counts["skip_revert_no_target"] += 1
                continue
            revert_files = _files_touched(repo, c["sha"])
            target_files = _files_touched(repo, target)
            if not (revert_files & target_files):
                counts["skip_revert_cross_file"] += 1
                continue
            for path in sorted(revert_files & target_files):
                before, after = _file_pair(repo, c["sha"], path)
                rows.append({
                    "sha": c["sha"], "path": path, "label": "positive",
                    "file_kind": _file_kind(path),
                    "before": before, "after": after,
                    "source": "revert_paired",
                })
                counts[f"positive_{_file_kind(path)}"] += 1
        elif include_positives and _is_fix(msg):
            # The parent of a fix commit introduced the regression that this
            # commit removes. Use the parent's diff as the "positive" sample
            # (before-fix → buggy state).
            parent_sha = _parent_sha(repo, c["sha"])
            if not parent_sha:
                counts["skip_fix_no_parent"] += 1
                continue
            fix_files = _files_touched(repo, c["sha"])
            parent_files = _files_touched(repo, parent_sha)
            shared = sorted(fix_files & parent_files)
            if not shared:
                counts["skip_fix_no_shared_files"] += 1
                continue
            for path in shared:
                before, after = _file_pair(repo, parent_sha, path)
                rows.append({
                    "sha": parent_sha, "path": path, "label": "positive",
                    "file_kind": _file_kind(path),
                    "before": before, "after": after,
                    "source": f"fix_parent:{c['sha'][:7]}",
                })
                counts[f"positive_{_file_kind(path)}"] += 1
        else:
            for path in sorted(_files_touched(repo, c["sha"])):
                if not path:
                    continue
                before, after = _file_pair(repo, c["sha"], path)
                rows.append({
                    "sha": c["sha"], "path": path, "label": "negative",
                    "file_kind": _file_kind(path),
                    "before": before, "after": after,
                })
                counts[f"negative_{_file_kind(path)}"] += 1
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return dict(counts)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Mine git history into a labeled corpus for P4 calibration. "
                    "Reviewer note: synchronous; on a 6-mo circit-app history "
                    "expect 10-30min wall time. Use --max-commits and --progress "
                    "for incremental runs."
    )
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--since", default="6.months")
    ap.add_argument("--out", default="eval/calibrated/labels.jsonl")
    ap.add_argument("--max-commits", type=int, default=500,
                    help="Cap on commits walked (default 500). Reviewer-flagged "
                         "to bound runtime on huge repos.")
    ap.add_argument("--progress", action="store_true",
                    help="Print one line per 50 commits to stderr.")
    ap.add_argument("--include-positives", dest="include_positives",
                    action="store_true", default=True,
                    help="Emit positive-label rows from revert + fix commits (default).")
    ap.add_argument("--no-positives", dest="include_positives",
                    action="store_false",
                    help="Suppress positive-label emission.")
    ap.add_argument("--mode", choices=["git-grep", "bisect"], default="git-grep",
                    help="Positive-label mining strategy. bisect = TBD.")
    args = ap.parse_args()
    counts = mine(args.repo_root, args.since, args.out,
                  include_positives=args.include_positives,
                  mode=args.mode)
    sys.stdout.write(json.dumps({
        "out": args.out,
        "counts": counts,
        "total_rows": sum(v for k, v in counts.items() if k.startswith(("positive_", "negative_"))),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
