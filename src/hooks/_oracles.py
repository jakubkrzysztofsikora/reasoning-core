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

    report.elapsed_ms = (time.monotonic() - t0) * 1000.0
    return report


__all__ = [
    "Annotation",
    "OracleReport",
    "run_oracles",
]
