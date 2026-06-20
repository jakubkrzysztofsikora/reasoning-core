"""score_diff.py — score a git diff with reasoning-core's in-process Mamba scorer.

Hardened per adversarial review:
  - merge-base used consistently for the file list AND before/after blobs
  - renames/deletes/binaries handled; one bad file never crashes the run
  - aggregate uses min(AIS)  (AIS=1.0 is identical, so min == most-divergent)
  - .vue excluded by default (HTML grammar collapses structural dims)
  - every score_change wrapped; advisory exit 0 unless --fail-on regression
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from pathlib import Path

# Put the repo ROOT on sys.path and import as `src.s2_core` (package-qualified).
# s2_core uses relative imports internally, so it MUST be imported as part of the
# `src` package (bare `from s2_core import` raises "attempted relative import").
# The eval image sets PYTHONPATH=/app and bakes the repo at /app via COPY . /app,
# so /app already has `src/` as a package; default RC_REPO=/app.
_REPO_ROOT = Path(os.environ.get("RC_REPO", "/app"))
sys.path.insert(0, str(_REPO_ROOT))


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, check=False,
    ).stdout


def _merge_base(repo, target):
    mb = _git(repo, "merge-base", target, "HEAD").strip()
    return mb or target


def _changed(repo, base):
    """Return (status, path) for changed files vs base; renames -> new path."""
    out = _git(repo, "diff", "--name-status", "-M", f"{base}...HEAD")
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        rows.append((parts[0][0], parts[-1]))  # status letter, (new) path
    return rows


def _is_binary(repo, base, path):
    out = _git(repo, "diff", "--numstat", f"{base}...HEAD", "--", path)
    return out.startswith("-\t-")


def _show(repo, ref, path):
    return _git(repo, "show", f"{ref}:{path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--target", required=True, help="target ref, e.g. origin/develop")
    ap.add_argument("--out", required=True)
    ap.add_argument("--extensions", default="cs,ts,tsx")
    ap.add_argument("--fail-on", choices=["none", "regression"], default="none")
    args = ap.parse_args()

    exts = {e.strip().lstrip(".") for e in args.extensions.split(",") if e.strip()}
    try:
        from src.s2_core import score_change
        from src.grammars import UnsupportedLanguageError
    except Exception as exc:
        print(f"reasoning-core import failed: {exc}", file=sys.stderr)
        json.dump({"files": [], "error": str(exc)}, open(args.out, "w"))
        return 0

    base = _merge_base(args.repo, args.target)
    results = []
    for status, path in _changed(args.repo, base):
        if status == "D":
            continue
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext not in exts:
            continue
        if _is_binary(args.repo, base, path):
            continue
        before = "" if status == "A" else _show(args.repo, base, path)
        after = _show(args.repo, "HEAD", path)
        if not after:
            continue
        try:
            r = score_change(path, before, after)
        except UnsupportedLanguageError:
            continue
        except Exception as exc:
            print(f"score_change failed for {path}: {exc}", file=sys.stderr)
            continue
        results.append({
            "path": path,
            "architectural_impact_score": round(float(r.architectural_impact_score), 4),
            "coherence_delta": round(float(r.coherence_delta), 4),
            "regression_detected": bool(r.regression_detected),
            "top_fired_dims": list(getattr(r, "fired_dims", []) or [])[:3],
        })

    any_reg = any(f["regression_detected"] for f in results)
    min_ais = min((f["architectural_impact_score"] for f in results), default=None)
    payload = {
        "files": results,
        "any_regression_detected": any_reg,
        "min_architectural_impact_score": min_ais,
        "scored_count": len(results),
    }
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(json.dumps(payload, indent=2))

    if args.fail_on == "regression" and any_reg:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
