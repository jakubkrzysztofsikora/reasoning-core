"""Tests for the JavaScript / TypeScript execution-grounded oracles.

Mirrors tests/test_oracles_and_patch_tracker.py. Each check is tool-optional
(node / eslint), so tests that need a toolchain are skipped when it is absent --
matching the runtime's best-effort, skip-if-missing contract.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = str(REPO_ROOT / "src")
HOOKS_DIR = str(REPO_ROOT / "src" / "hooks")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import _oracles  # type: ignore

HAS_NODE = shutil.which("node") is not None
HAS_ESLINT = shutil.which("eslint") is not None


# ---------------------------------------------------------------------------
# Dispatch / no-op behaviour (tool-independent)
# ---------------------------------------------------------------------------


def test_unknown_extension_is_noop():
    # A file matching no oracle (not .py/.js/.ts/...) is always clean.
    report = _oracles.run_oracles("notes.txt", "not code at all\n")
    assert report.clean is True
    assert report.annotations == []


def test_python_path_unaffected_by_js_ts_oracles():
    # Regression guard: the JS/TS oracles must not touch Python files.
    report = _oracles.run_oracles(
        "src/foo.py", "def f():\n    return 1\n", enable_t2=False
    )
    assert report.clean is True
    assert all(a.tool not in {"node", "eslint"} for a in report.annotations)


def test_typescript_with_imports_is_not_blocked():
    # Regression guard against a single-file tsc oracle: a TS edit that imports
    # project-local modules must NOT be blocked. Checking such a file in
    # isolation would raise TS2307 ("cannot find module") on every import; the
    # oracle layer must never false-block valid code.
    report = _oracles.run_oracles(
        "component.ts",
        "import { helper } from './helper';\n"
        "export const y: number = helper(1);\n",
    )
    assert all(a.severity != "error" for a in report.annotations)


# ---------------------------------------------------------------------------
# T1 — node --check (JavaScript syntax)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_NODE, reason="node not installed")
def test_node_check_flags_js_syntax_error():
    report = _oracles.run_oracles("bad.js", "const = 1;\n", enable_t2=False)
    assert report.clean is False
    assert any(a.tool == "node" for a in report.annotations)
    err = report.first_error()
    assert err is not None and err.file_path == "bad.js"


@pytest.mark.skipif(not HAS_NODE, reason="node not installed")
def test_node_check_passes_valid_commonjs():
    report = _oracles.run_oracles(
        "good.js", "const x = 1;\nconsole.log(x);\n", enable_t2=False
    )
    assert all(a.tool != "node" for a in report.annotations)


@pytest.mark.skipif(not HAS_NODE, reason="node not installed")
def test_node_check_passes_valid_esm_in_js():
    # A "type": "module" project uses ESM syntax in .js files. node --check
    # defaults to CommonJS, so this must also be checked as a module, otherwise
    # valid ESM would be false-blocked.
    report = _oracles.run_oracles(
        "mod.js",
        "export const x = 1;\nimport { y } from './y.js';\n",
        enable_t2=False,
    )
    assert all(a.tool != "node" for a in report.annotations)


@pytest.mark.skipif(not HAS_NODE, reason="node not installed")
def test_node_check_flags_error_broken_under_both_module_systems():
    # Invalid under CommonJS *and* ESM -> still flagged.
    report = _oracles.run_oracles("broken.mjs", "export const = ;\n", enable_t2=False)
    assert report.clean is False
    assert any(a.tool == "node" for a in report.annotations)


# ---------------------------------------------------------------------------
# T1 — JSX must not be routed to `node --check` (Node cannot parse JSX)
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


def test_node_check_skips_jsx_even_when_node_is_present(monkeypatch):
    # `node --check` cannot parse JSX under any module system, so valid React
    # (`return <div />`) is false-flagged as a syntax error whenever node is
    # installed. .jsx must be skipped BEFORE node runs -- asserted
    # node-independently by mocking node as present and having it reject input.
    calls = []
    monkeypatch.setattr(_oracles.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(
        _oracles.subprocess,
        "run",
        lambda *a, **k: calls.append(a)
        or _FakeProc(returncode=1, stderr="tmp.cjs:2\nSyntaxError: Unexpected token '<'"),
    )
    report = _oracles.OracleReport()
    _oracles._t1_node_check(
        file_path="App.jsx",
        after_src='export function App() {\n  return <div className="x" />;\n}\n',
        report=report,
    )
    assert calls == []  # node --check never invoked for JSX
    assert report.clean is True
    assert all(a.tool != "node" for a in report.annotations)


def test_node_check_still_runs_on_plain_js(monkeypatch):
    # Guard against over-correction: plain .js must still be syntax-checked.
    calls = []
    monkeypatch.setattr(_oracles.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(
        _oracles.subprocess, "run", lambda *a, **k: calls.append(a) or _FakeProc(returncode=0)
    )
    report = _oracles.OracleReport()
    _oracles._t1_node_check(file_path="good.js", after_src="const x = 1;\n", report=report)
    assert calls != []  # node --check was invoked for .js


@pytest.mark.skipif(not HAS_NODE, reason="node not installed")
def test_node_check_passes_valid_jsx_end_to_end():
    # Full path with a real node: valid JSX in a .jsx file must not be flagged.
    report = _oracles.run_oracles(
        "App.jsx", "export function App() {\n  return <div />;\n}\n", enable_t2=False
    )
    assert all(a.tool != "node" for a in report.annotations)


# ---------------------------------------------------------------------------
# T2 — eslint (JS/TS lint, best-effort)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_ESLINT, reason="eslint not installed")
def test_eslint_best_effort_never_hard_fails_without_config():
    # With no resolvable eslint config, the oracle must stay quiet, not block.
    report = _oracles.run_oracles("solo.ts", "const x = 1;\n")
    assert all(
        a.severity != "error" or a.tool != "eslint" for a in report.annotations
    )
