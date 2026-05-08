"""One-shot audit-log JSONL migration helper (v1 -> v2).

Adds ``host`` and ``schema_version`` fields to existing rows that predate
Phase 0.5 of the multi-CLI parity effort. Reader code in :mod:`audit_log`
tolerates v1 rows in-place, so migration is OPTIONAL — run it when you want
clean per-host slicing in eval aggregators.

Usage::

    python -m src.hooks._audit_migrate ~/.local/share/reasoning-core/events

The script is idempotent: rows that already have ``host`` are left alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_HOST = "claude"
SCHEMA_VERSION = 2


def migrate_file(path: Path, *, default_host: str = DEFAULT_HOST) -> int:
    """Rewrite ``path`` in place. Returns the number of rows upgraded."""
    if not path.exists():
        return 0
    upgraded = 0
    out_lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            out_lines.append(raw)
            continue
        try:
            row = json.loads(raw)
        except Exception:  # noqa: BLE001 - keep malformed rows as-is
            out_lines.append(raw)
            continue
        if "host" not in row:
            row["host"] = default_host
            upgraded += 1
        if "schema_version" not in row:
            row["schema_version"] = SCHEMA_VERSION
        out_lines.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    if upgraded:
        path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return upgraded


def migrate_tree(root: Path, *, default_host: str = DEFAULT_HOST) -> dict:
    """Migrate every ``*.jsonl`` under ``root``. Returns per-file counts."""
    summary: dict = {"files": 0, "rows_upgraded": 0}
    for jsonl in root.rglob("*.jsonl"):
        n = migrate_file(jsonl, default_host=default_host)
        summary["files"] += 1
        summary["rows_upgraded"] += n
    return summary


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: python -m src.hooks._audit_migrate <root_dir>\n")
        return 2
    root = Path(argv[1]).expanduser()
    if not root.exists():
        sys.stderr.write(f"audit root not found: {root}\n")
        return 1
    summary = migrate_tree(root)
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
