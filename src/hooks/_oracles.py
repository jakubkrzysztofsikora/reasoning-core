"""Execution-grounded oracles for Phase 2.

Run fast, local checks against the agent's proposed source. The T1/T2 oracles
operate in < 1 s combined and are designed to catch syntax errors, import
violations, and lint failures before the edit lands on disk.

Two execution modes:
  1. Worktree mode: a git worktree exists with the cumulative patch applied.
     Oracles run against files in the worktree.
  2. Source-only mode: no git repo / no worktree. Oracles run against a
     temporary file containing ``after_src``.

All oracles return an ``OracleReport`` with severity, file/line annotations,
and a human-readable message. The orchestrator decides whether to block, warn,
or continue based on RC_MODE and RC_ORACLE_BLOCK.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class Annotation:
    """A single file/line annotation from an oracle."""

    tool: str
    file_path: str
    line: int
    column: int = 0
    message: str = ""
    severity: str = "error"  # error | warning


@dataclass
class OracleReport:
    """Aggregated oracle result."""

    clean: bool = True
    annotations: List[Annotation] = field(default_factory=list)
    elapsed_ms: float = 0.0
    tool_outputs: dict[str, str] = field(default_factory=dict)

    def add(
        self,
        *,
        tool: str,
        file_path: str,
        line: int,
        column: int = 0,
        message: str,
        severity: str = "error",
    ) -> None:
        self.clean = False
        self.annotations.append(
            Annotation(
                tool=tool,
                file_path=file_path,
                line=line,
                column=column,
                message=message,
                severity=severity,
            )
        )

    def first_error(self) -> Optional[Annotation]:
        for a in self.annotations:
            if a.severity == "error":
                return a
        return None

    def summary(self) -> str:
        if self.clean:
            return "oracles passed"
        lines = []
        for a in self.annotations:
            lines.append(
                f"[{a.tool}] {a.file_path}:{a.line}:{a.column} {a.message}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# T1 — Syntactic checks
# ---------------------------------------------------------------------------


def _t1_py_compile(*, file_path: str, after_src: str, report: OracleReport) -> None:
    """Compile Python source with py_compile; report syntax errors."""
    if not file_path.endswith(".py"):
        return
    try:
        import py_compile

        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(after_src)
            tmp_path = f.name
        try:
            py_compile.compile(tmp_path, doraise=True)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except py_compile.PyCompileError as exc:
        # Extract line number from the exception text if available.
        line = 1
        txt = str(exc)
        import re

        m = re.search(r"line\s+(\d+)", txt)
        if m:
            try:
                line = int(m.group(1))
            except ValueError:
                line = 1
        report.add(
            tool="py_compile",
            file_path=file_path,
            line=line,
            message=f"syntax error: {exc}",
            severity="error",
        )
    except Exception as exc:  # noqa: BLE001
        report.add(
            tool="py_compile",
            file_path=file_path,
            line=1,
            message=f"py_compile raised {type(exc).__name__}: {exc}",
            severity="error",
        )


def _t1_parse_smoke(*, file_path: str, after_src: str, report: OracleReport) -> None:
    """Lightweight parser smoke test for Python."""
    if not file_path.endswith(".py"):
        return
    try:
        import ast

        ast.parse(after_src)
    except SyntaxError as exc:
        report.add(
            tool="ast_parse",
            file_path=file_path,
            line=exc.lineno or 1,
            column=exc.offset or 0,
            message=f"parse error: {exc.msg}",
            severity="error",
        )


# ---------------------------------------------------------------------------
# T1 — Syntactic checks (JavaScript)
# ---------------------------------------------------------------------------

# Extensions dispatched to the JS syntax oracle and the JS/TS lint oracle. Kept
# parallel to the ``.py`` guards in the Python oracles so a non-JS/TS file is
# always a no-op.
_JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")
_TS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")


def _resolve_node_bin(name: str, worktree: Optional[Path] = None) -> Optional[str]:
    """Resolve a Node CLI, preferring a project-local install over a global one.

    Real JS/TS projects install tools in ``node_modules/.bin`` rather than
    globally, so ``shutil.which`` alone would miss them. Checks the worktree
    (or cwd) first, then falls back to PATH.
    """
    roots = []
    if worktree is not None:
        roots.append(Path(worktree))
    roots.append(Path.cwd())
    for root in roots:
        candidate = root / "node_modules" / ".bin" / name
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


def _t1_node_check(*, file_path: str, after_src: str, report: OracleReport) -> None:
    """Syntax-check JavaScript with ``node --check`` (the JS ``py_compile``).

    ``.mjs`` is always ESM and ``.cjs`` always CommonJS; plain ``.js`` / ``.jsx``
    are ambiguous, so the source is checked under both module systems and only
    reported when *both* reject it. Otherwise valid ESM in a ``.js`` file (a
    ``"type": "module"`` project) would be false-blocked. Skips non-JS files and
    no-ops when ``node`` isn't installed.
    """
    if not file_path.endswith(_JS_EXTENSIONS):
        return
    if not shutil.which("node"):
        return

    import re

    suffix = Path(file_path).suffix
    if suffix == ".mjs":
        candidate_suffixes = (".mjs",)
    elif suffix == ".cjs":
        candidate_suffixes = (".cjs",)
    else:
        candidate_suffixes = (".cjs", ".mjs")

    last_stderr = ""
    for candidate_suffix in candidate_suffixes:
        with tempfile.NamedTemporaryFile(
            suffix=candidate_suffix, mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(after_src)
            tmp_path = f.name
        try:
            result = subprocess.run(
                ["node", "--check", tmp_path],
                capture_output=True,
                text=True,
                check=False,
                timeout=10.0,
            )
            if result.returncode == 0:
                return  # valid under at least one module system
            last_stderr = result.stderr or ""
        except subprocess.TimeoutExpired:
            report.add(
                tool="node",
                file_path=file_path,
                line=1,
                message="node --check timed out",
                severity="warning",
            )
            return
        except Exception as exc:  # noqa: BLE001
            report.add(
                tool="node",
                file_path=file_path,
                line=1,
                message=f"node --check failed: {exc}",
                severity="warning",
            )
            return
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # Rejected under every candidate module system -> a real syntax error.
    # node prints "<file>:<line>\n<code>\n  ^\nSyntaxError: ..." to stderr.
    stderr_lines = last_stderr.splitlines()
    line = 1
    if stderr_lines:
        m = re.search(r":(\d+)\s*$", stderr_lines[0])
        if m:
            line = int(m.group(1))
    message = "syntax error"
    for ln in stderr_lines:
        if "Error:" in ln:
            message = ln.strip()
            break
    report.add(
        tool="node",
        file_path=file_path,
        line=line,
        message=message,
        severity="error",
    )


# ---------------------------------------------------------------------------
# T2 — Static / lint checks
# ---------------------------------------------------------------------------


def _t2_ruff(
    *,
    file_path: str,
    worktree: Optional[Path] = None,
    after_src: Optional[str] = None,
    report: OracleReport,
) -> None:
    """Run ruff check on the changed file if ruff is installed.

    In worktree mode, ``file_path`` is resolved inside the worktree. In
    source-only mode, a temp file is created and ruff is invoked on it.

    Ruff is a Python linter; running it on a non-Python file (``.sh``,
    ``.md``, ``.ts``, etc.) makes it parse the content as Python and emit
    a parse error -- a guaranteed false positive that hard-blocks edits.
    Mirror the T1 oracles and skip anything that isn't a Python file.
    """
    if not file_path.endswith(".py"):
        return
    if not shutil.which("ruff"):
        return

    target: Path
    if worktree is not None:
        target = worktree / file_path
        if not target.exists():
            return
    else:
        if after_src is None:
            return
        with tempfile.NamedTemporaryFile(
            suffix=Path(file_path).suffix,
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as f:
            f.write(after_src)
            target = Path(f.name)

    try:
        result = subprocess.run(
            ["ruff", "check", "--quiet", "--output-format", "json", str(target)],
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
        if result.returncode == 0:
            return
        import json

        try:
            findings = json.loads(result.stdout)
        except json.JSONDecodeError:
            findings = []
        for finding in findings:
            report.add(
                tool="ruff",
                file_path=file_path,
                line=finding.get("location", {}).get("row", 1),
                column=finding.get("location", {}).get("column", 0),
                message=finding.get("message", ""),
                severity="warning"
                if finding.get("code", "").startswith("W")
                else "error",
            )
    except subprocess.TimeoutExpired:
        report.add(
            tool="ruff",
            file_path=file_path,
            line=1,
            message="ruff timed out",
            severity="warning",
        )
    except Exception as exc:  # noqa: BLE001
        report.add(
            tool="ruff",
            file_path=file_path,
            line=1,
            message=f"ruff failed: {exc}",
            severity="warning",
        )
    finally:
        if worktree is None and target.exists():
            try:
                target.unlink()
            except OSError:
                pass


def _t2_eslint(
    *,
    file_path: str,
    worktree: Optional[Path] = None,
    after_src: Optional[str] = None,
    report: OracleReport,
) -> None:
    """Lint JS/TS with the project's ``eslint`` if installed (the JS ``ruff``).

    Worktree mode lints the patched file in place; source-only mode lints a
    temp file. Best-effort: no-ops when ``eslint`` isn't resolvable, and treats
    non-JSON output (e.g. eslint bailing because the project has no config) as
    "nothing to report" rather than a hard failure -- so a repo without eslint
    set up is never falsely blocked.
    """
    if not file_path.endswith(_JS_EXTENSIONS + _TS_EXTENSIONS):
        return
    eslint = _resolve_node_bin("eslint", worktree)
    if not eslint:
        return

    import json

    target: Path
    cleanup = False
    if worktree is not None:
        target = worktree / file_path
        if not target.exists():
            return
    else:
        if after_src is None:
            return
        with tempfile.NamedTemporaryFile(
            suffix=Path(file_path).suffix,
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as f:
            f.write(after_src)
            target = Path(f.name)
        cleanup = True

    try:
        result = subprocess.run(
            [eslint, "--format", "json", str(target)],
            cwd=str(worktree) if worktree is not None else None,
            capture_output=True,
            text=True,
            check=False,
            timeout=15.0,
        )
        if result.returncode == 0:
            return
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            # eslint bailed before producing JSON (e.g. no config). Best-effort:
            # do not hard-block on a toolchain that isn't set up.
            payload = []
        for file_result in payload:
            for msg in file_result.get("messages", []):
                report.add(
                    tool="eslint",
                    file_path=file_path,
                    line=msg.get("line", 1),
                    column=msg.get("column", 0),
                    message=msg.get("message", ""),
                    severity="error" if msg.get("severity") == 2 else "warning",
                )
    except subprocess.TimeoutExpired:
        report.add(
            tool="eslint",
            file_path=file_path,
            line=1,
            message="eslint timed out",
            severity="warning",
        )
    except Exception as exc:  # noqa: BLE001
        report.add(
            tool="eslint",
            file_path=file_path,
            line=1,
            message=f"eslint failed: {exc}",
            severity="warning",
        )
    finally:
        if cleanup and target.exists():
            try:
                target.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_oracles(
    file_path: str,
    after_src: str,
    *,
    worktree: Optional[Path] = None,
    enable_t1: bool = True,
    enable_t2: bool = True,
) -> OracleReport:
    """Run the enabled oracle tiers against the proposed source.

    ``worktree`` is optional; if provided, T2 oracles run against the patched
    file inside the worktree. If None, T1 runs against a temp file and T2
    (ruff) is best-effort on a temp file.
    """
    import time

    report = OracleReport()
    t0 = time.monotonic()

    if enable_t1:
        _t1_py_compile(file_path=file_path, after_src=after_src, report=report)
        _t1_node_check(file_path=file_path, after_src=after_src, report=report)
        # AST parse is redundant if py_compile already passed; skip on clean.
        if not report.clean:
            _t1_parse_smoke(file_path=file_path, after_src=after_src, report=report)

    if enable_t2 and report.clean:
        _t2_ruff(
            file_path=file_path,
            worktree=worktree,
            after_src=after_src,
            report=report,
        )
        _t2_eslint(
            file_path=file_path,
            worktree=worktree,
            after_src=after_src,
            report=report,
        )

    report.elapsed_ms = (time.monotonic() - t0) * 1000.0
    return report


__all__ = [
    "Annotation",
    "OracleReport",
    "run_oracles",
]
