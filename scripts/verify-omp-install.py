#!/usr/bin/env python3
"""Read back OMP readiness through the invoking installed Python runtime."""
import argparse
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

from src._omp_doctor import check_omp

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    args = parser.parse_args()
    checks = check_omp(args.project_dir)
    report = {
        "schema_version": 1, "machine": platform.node(), "python": sys.executable,
        "package_version": importlib.metadata.version("reasoning-core"),
        "project_dir": str(args.project_dir.resolve()), "checks": checks,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if all(row["ok"] for row in checks) else 1

if __name__ == "__main__":
    raise SystemExit(main())
