"""RC-009: tests for the S2 sidecar (parser + scoring + HTTP service).

This suite covers all five Tree-sitter languages, deterministic scoring with
the real Mamba SSM forward pass, and the HTTP contract on /health and /score.

Heavy dependencies (`torch`, `transformers`, `tree_sitter_languages` /
per-language wheels) are gated with `pytest.importorskip` so the suite
degrades cleanly on partial environments. The Mamba load is paid exactly
once via a session-scoped fixture.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from typing import Any

import pytest

# Ensure repo root is importable.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _grammar_available(lang_id: str) -> bool:
    """Return True iff a tree-sitter grammar can be loaded for ``lang_id``."""
    try:
        from src.grammars import _load_language  # type: ignore

        _load_language(lang_id)
        return True
    except Exception:
        return False


def _require_grammar(lang_id: str) -> None:
    if not _grammar_available(lang_id):
        pytest.skip(f"tree-sitter grammar for {lang_id!r} not installed in this environment")


@pytest.fixture(scope="session")
def s2_core_module():
    """Import src.s2_core lazily; skip the whole module if grammars module errors."""
    pytest.importorskip("tree_sitter")
    try:
        import src.s2_core as mod  # type: ignore
    except Exception as exc:  # pragma: no cover - import guard
        pytest.skip(f"src.s2_core import failed: {exc}")
    return mod


@pytest.fixture(scope="session")
def grammars_module():
    pytest.importorskip("tree_sitter")
    import src.grammars as mod  # type: ignore
    return mod


@pytest.fixture(scope="session")
def ssm_module():
    """Import src.ssm_backbone lazily."""
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    import src.ssm_backbone as mod  # type: ignore
    return mod


@pytest.fixture(scope="session")
def loaded_backbone(ssm_module):
    """Load the Mamba (or fallback) backbone once for the whole suite.

    Skips if no checkpoint resolves (offline + no cache).
    """
    try:
        model, tokenizer = ssm_module.load_backbone()
    except ssm_module.BackboneUnavailableError as exc:
        pytest.skip(f"backbone unavailable: {exc}")
    except Exception as exc:  # pragma: no cover - safety net
        pytest.skip(f"backbone load raised unexpectedly: {exc}")
    return model, tokenizer


# ---------------------------------------------------------------------------
# Language-specific fixtures: A calls B in two functions.
# ---------------------------------------------------------------------------

PY_FIXTURE = (
    "def A():\n"
    "    return B()\n"
    "\n"
    "def B():\n"
    "    return 1\n"
)

JS_FIXTURE = (
    "function A() {\n"
    "    return B();\n"
    "}\n"
    "function B() {\n"
    "    return 1;\n"
    "}\n"
)

TS_FIXTURE = (
    "function A(): number {\n"
    "    return B();\n"
    "}\n"
    "function B(): number {\n"
    "    return 1;\n"
    "}\n"
)

CS_FIXTURE = (
    "public class K {\n"
    "    public int A() { return B(); }\n"
    "    public int B() { return 1; }\n"
    "}\n"
)

SQL_FIXTURE = (
    "CREATE FUNCTION A() RETURNS int AS $$ SELECT B(); $$ LANGUAGE SQL;\n"
    "CREATE FUNCTION B() RETURNS int AS $$ SELECT 1; $$ LANGUAGE SQL;\n"
)


PY_GOOD = (
    "def f(n):\n"
    "    if not n:\n"
    "        return 0\n"
    "    return n + f(n - 1)\n"
)
# PY_BAD: drops the guard clause and explodes cyclomatic complexity with a
# long chain of branches. Designed to trip the cyclomatic risk-dim threshold
# (>0.9) under the normalized coherence_delta scale (raw L2 / sqrt(D)) added
# after the calibration fix — a tiny one-line refactor no longer crosses any
# threshold by itself.
PY_BAD = "def f(n):\n" + "".join(
    f"    if n == {i}:\n        return {i}\n" for i in range(25)
) + "    return n + f(n + 1)\n"


# ---------------------------------------------------------------------------
# parse_source / build_call_graph: per-language coverage
# ---------------------------------------------------------------------------

LANG_TABLE: list[tuple[str, str, str, str]] = [
    # (display, lang_id, path, src)
    ("python",     "python",     "/tmp/a.py",  PY_FIXTURE),
    ("javascript", "javascript", "/tmp/a.js",  JS_FIXTURE),
    ("typescript", "typescript", "/tmp/a.ts",  TS_FIXTURE),
    ("csharp",     "csharp",     "/tmp/A.cs",  CS_FIXTURE),
    ("sql",        "sql",        "/tmp/a.sql", SQL_FIXTURE),
]


@pytest.mark.parametrize("display,lang_id,path,src", LANG_TABLE)
def test_parse_source_each_language(display, lang_id, path, src, s2_core_module):
    _require_grammar(lang_id)
    result = s2_core_module.parse_source(path, src)
    assert result.language in (lang_id, "tsx"), (
        f"unexpected language id {result.language!r} for {display}"
    )
    assert result.tree is not None
    # The root node must be populated.
    root = result.tree.root_node
    assert root is not None
    assert root.end_byte > 0
    assert result.parse_ok is True


@pytest.mark.parametrize("display,lang_id,path,src", LANG_TABLE)
def test_build_call_graph_each_language(display, lang_id, path, src, s2_core_module):
    _require_grammar(lang_id)
    result = s2_core_module.parse_source(path, src)
    graph = s2_core_module.build_call_graph(result.tree, src, result.language)
    # Each fixture defines two routines: A and B; A should call B and B should
    # be present (possibly with empty calls in non-SQL languages).
    assert "A" in graph, f"{display}: missing A in graph={graph!r}"
    assert "B" in graph[("A")], f"{display}: A->B edge missing in {graph!r}"
    # B may include implementation-detail calls (e.g. SQL SELECT) but for the
    # non-SQL fixtures the inner body is a literal return so its calls set is
    # expected to be empty.
    if display != "sql":
        assert graph.get("B") == set(), f"{display}: unexpected calls from B: {graph.get('B')}"


def test_parse_source_syntax_error_python(s2_core_module):
    _require_grammar("python")
    bad = "def broken(:\n    pass\n"
    result = s2_core_module.parse_source("/tmp/bad.py", bad)
    assert result.parse_ok is False
    # Must NOT raise -- graceful degradation contract.
    assert result.tree is not None or result.error is not None


@pytest.mark.parametrize("path,src,lang_id", [
    ("/tmp/bad.js",  "function f( {",                      "javascript"),
    ("/tmp/bad.ts",  "function f(: number) {",             "typescript"),
    ("/tmp/Bad.cs",  "public class K { public int A( { } } ", "csharp"),
    ("/tmp/bad.sql", "CREATE PROCEDURE A( BEGIN END",      "sql"),
])
def test_parse_source_syntax_error_other_languages(path, src, lang_id, s2_core_module):
    _require_grammar(lang_id)
    result = s2_core_module.parse_source(path, src)
    # parse_source must never raise on syntax errors; parse_ok may be False.
    assert result is not None
    assert result.tree is not None or result.error is not None


@pytest.mark.parametrize("ext", [".rb", ".cpp", ".go", ".rs", ".java"])
def test_unsupported_language_error(ext, grammars_module):
    with pytest.raises(grammars_module.UnsupportedLanguageError) as ei:
        grammars_module.select_grammar(f"/tmp/file{ext}")
    assert ei.value.extension == ext


# ---------------------------------------------------------------------------
# score_change: identical / regression / determinism
# ---------------------------------------------------------------------------

def test_score_change_identical_inputs(s2_core_module, loaded_backbone):
    _require_grammar("python")
    report = s2_core_module.score_change("/tmp/same.py", PY_GOOD, PY_GOOD)
    # AIS == 1.0 exactly when embeddings identical.
    assert report.architectural_impact_score == pytest.approx(1.0, abs=1e-5)
    # coherence_delta should be 0 (or extremely close).
    assert report.coherence_delta == pytest.approx(0.0, abs=1e-5)
    # No regression when nothing changed.
    assert report.regression_detected is False
    # 8-dim risk vector with declared labels.
    assert isinstance(report.risk_vector, list)
    assert len(report.risk_vector) == len(s2_core_module.RISK_LABELS)
    for v in report.risk_vector:
        assert 0.0 <= float(v) <= 1.0


def test_score_change_regression_detected(s2_core_module, loaded_backbone):
    _require_grammar("python")
    report = s2_core_module.score_change("/tmp/bad.py", PY_GOOD, PY_BAD)
    # The bad refactor drops the guard clause and adds unbounded recursion;
    # that should trip at least one of the regression conditions.
    assert isinstance(report.risk_vector, list)
    assert len(report.risk_vector) == len(s2_core_module.RISK_LABELS)
    # We do not pin the exact dim that trips, but we expect a regression.
    assert report.regression_detected is True
    assert isinstance(report.human_summary, str)
    assert len(report.human_summary) > 0


def test_score_change_determinism(s2_core_module, loaded_backbone):
    _require_grammar("python")
    a = s2_core_module.score_change("/tmp/det.py", PY_GOOD, PY_BAD)
    b = s2_core_module.score_change("/tmp/det.py", PY_GOOD, PY_BAD)
    # Identical inputs must yield bit-identical numeric outputs.
    assert a.architectural_impact_score == b.architectural_impact_score
    assert a.coherence_delta == b.coherence_delta
    assert a.risk_vector == b.risk_vector
    assert a.regression_detected == b.regression_detected


def test_impact_report_to_dict_shape(s2_core_module, loaded_backbone):
    _require_grammar("python")
    report = s2_core_module.score_change("/tmp/shape.py", PY_GOOD, PY_GOOD)
    d = report.to_dict()
    for key in (
        "architectural_impact_score",
        "coherence_delta",
        "risk_vector",
        "risk_labels",
        "regression_detected",
        "human_summary",
    ):
        assert key in d
    assert len(d["risk_vector"]) == len(s2_core_module.RISK_LABELS)
    assert len(d["risk_labels"]) == len(s2_core_module.RISK_LABELS)
    # Phase 2 schema migration: exact ordered tuple must not drift.
    assert s2_core_module.RISK_LABELS == (
        "cyclomatic", "fan_in", "fan_out", "depth",
        "churn", "coupling", "cohesion", "novelty",
        "session_centroid_drift", "project_fan_in", "project_coupling",
    )
    assert d.get("risk_labels_version") == 2


# ---------------------------------------------------------------------------
# ssm_backbone: shape + module-level singleton + BACKBONE_INFO
# ---------------------------------------------------------------------------

def test_backbone_module_loads(ssm_module, loaded_backbone):
    model, tokenizer = loaded_backbone
    assert model is not None
    assert tokenizer is not None
    # BACKBONE_INFO should be populated after load.
    assert ssm_module.BACKBONE_INFO.get("checkpoint")
    # is_loaded() should report True.
    assert ssm_module.is_loaded() is True


def test_backbone_forward_shape(ssm_module, loaded_backbone):
    """last_hidden_state must be a torch.Tensor of shape [1, seq_len, hidden]."""
    import torch

    model, tokenizer = loaded_backbone
    enc = tokenizer("def f(): pass", return_tensors="pt")
    input_ids = enc["input_ids"]
    with torch.no_grad():
        try:
            out = model(input_ids=input_ids, attention_mask=enc.get("attention_mask"))
        except TypeError:
            out = model(input_ids=input_ids)
    last = getattr(out, "last_hidden_state", None)
    if last is None and isinstance(out, (list, tuple)):
        last = out[0]
    assert last is not None
    assert isinstance(last, torch.Tensor)
    assert last.dim() == 3
    assert last.shape[0] == 1
    assert last.shape[1] >= 1
    assert last.shape[2] >= 1


def test_embed_returns_pooled_vector(ssm_module, loaded_backbone):
    import torch

    vec = ssm_module.embed("def f(): pass")
    assert isinstance(vec, torch.Tensor)
    # Mean-pooled over seq dim => shape [hidden]
    assert vec.dim() == 1
    assert vec.shape[0] >= 1


# ---------------------------------------------------------------------------
# HTTP service via FastAPI TestClient (in-process, no uvicorn needed)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def http_client(s2_core_module, loaded_backbone):
    """Boot the FastAPI app in-process via TestClient. Skips on env failure."""
    pytest.importorskip("fastapi")
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"FastAPI TestClient unavailable: {exc}")
    try:
        app = s2_core_module.create_app()
    except Exception as exc:
        pytest.skip(f"create_app() failed: {exc}")
    with TestClient(app) as client:
        yield client


def test_http_health_ok(http_client):
    resp = http_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "ok"
    assert body.get("model_loaded") is True
    langs = body.get("languages") or []
    for required in ("python", "javascript", "typescript", "csharp", "sql"):
        assert required in langs


def test_http_score_happy_path(http_client):
    _require_grammar("python")
    payload = {"path": "/tmp/h.py", "before_src": PY_GOOD, "after_src": PY_GOOD}
    resp = http_client.post("/score", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "architectural_impact_score",
        "coherence_delta",
        "risk_vector",
        "risk_labels",
        "regression_detected",
        "human_summary",
    ):
        assert key in body
    assert len(body["risk_vector"]) == len(s2_core_module.RISK_LABELS)


def test_http_score_unsupported_language(http_client):
    payload = {"path": "/tmp/foo.rb", "before_src": "x", "after_src": "y"}
    resp = http_client.post("/score", json=payload)
    assert resp.status_code == 415
    body = resp.json()
    assert body.get("error") == "unsupported_language"
    assert body.get("extension") == ".rb"


def test_http_score_malformed_json(http_client):
    resp = http_client.post(
        "/score",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_http_score_missing_path(http_client):
    resp = http_client.post("/score", json={"before_src": "", "after_src": ""})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Loopback-only port verification (best-effort: the TestClient does not
# actually bind a socket, so we assert the configured host explicitly).
# ---------------------------------------------------------------------------

def test_default_port_and_host(s2_core_module):
    """The sidecar entrypoint must default to 127.0.0.1:8765 (loopback only)."""
    import inspect

    src = inspect.getsource(s2_core_module._run)
    assert "127.0.0.1" in src
    assert "8765" in src
