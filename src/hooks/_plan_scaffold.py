"""Auto-scaffold PLAN.md from README.md on first SessionStart.

Audit 2026-06-01 §B4: with RC_PLAN_GROUNDING flipped to default-on, repos
without a PLAN.md silently degrade to `audit_only:no_plan_md` for every
Edit. Generating a stub on first contact keeps the plan-grounding signal
alive even on day-zero installs. Override with RC_NO_PLAN_SCAFFOLD=1.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional


_DEFAULT_TEMPLATE = """# PLAN

{goal}

## Files in scope

- {readme_rel}  # documentation seed
- TODO: list 3-5 source files the next change touches

## Tasks

- [ ] TODO: first concrete task

<!-- Auto-scaffolded by reasoning-core because RC_PLAN_GROUNDING is enabled
     and no PLAN.md was found in this repo. Set RC_NO_PLAN_SCAFFOLD=1 to
     skip this on future sessions. -->
"""


def _extract_goal(readme_text: str) -> str:
    """Pull a one-line goal from the first non-heading paragraph of README."""
    if not readme_text:
        return "TODO: describe goal"
    for raw in readme_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("<") and line.endswith(">"):
            continue  # HTML tags / comments in markdown
        # Strip badge markdown like [![x](y)](z) at line start
        cleaned = re.sub(r"^\[!\[.*?\)\]\(.*?\)\s*", "", line)
        cleaned = cleaned.strip()
        if cleaned:
            return cleaned[:200]
    return "TODO: describe goal"


def maybe_scaffold_plan(project_dir: str, *, force: bool = False) -> Optional[Path]:
    """Create a stub PLAN.md from README.md if one doesn't exist.

    Returns the path written, or None if the scaffold was skipped (already
    exists, no README found, RC_NO_PLAN_SCAFFOLD=1, etc.).
    """
    if os.environ.get("RC_NO_PLAN_SCAFFOLD") == "1":
        return None
    root = Path(project_dir)
    if not root.is_dir():
        return None
    plan_path = root / "PLAN.md"
    if plan_path.exists():
        return None
    readme_path = root / "README.md"
    readme_text = ""
    if readme_path.exists():
        try:
            readme_text = readme_path.read_text(encoding="utf-8", errors="replace")[:1500]
        except OSError:
            readme_text = ""
    elif not force:
        return None
    goal = _extract_goal(readme_text)
    readme_rel = "README.md" if readme_path.exists() else "TODO: project README"
    body = _DEFAULT_TEMPLATE.format(goal=goal, readme_rel=readme_rel)
    try:
        plan_path.write_text(body, encoding="utf-8")
    except OSError:
        return None
    return plan_path
