"""System 2 sidecar: Tree-sitter AST/CFG + Mamba-backed ImpactReport scoring +
HTTP service on 127.0.0.1:8765.

Public API (consumed by RC-006/RC-007):

    parse_source(path, src) -> ParseResult
    build_call_graph(tree, src, language) -> dict[str, set[str]]
    score_change(path, before_src, after_src) -> ImpactReport
    select_grammar / UnsupportedLanguageError  (re-exported from grammars.py)

HTTP contract (consumed by mcp_reasoner + pre_edit_guard):

    GET  /health
    POST /score   { path, before_src, after_src }

Run with: ``python3 -m src.s2_core``
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Optional

from .grammars import (
    EXTENSION_MAP,
    PUBLIC_LANGUAGE,
    SUPPORTED_LANGUAGES,
    UnsupportedLanguageError,
    get_parser,
    select_grammar,
)
from .ssm_backbone import (
    BACKBONE_INFO,
    BackboneUnavailableError,
    ast_to_tokens,
    embed,
    is_loaded,
    load_backbone,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

RISK_LABELS: tuple[str, ...] = (
    "cyclomatic",
    "fan_in",
    "fan_out",
    "depth",
    "churn",
    "coupling",
    "cohesion",
    "novelty",
)


@dataclass
class ParseResult:
    tree: Any
    language: str
    parse_ok: bool
    error: Optional[str] = None


@dataclass
class ImpactReport:
    architectural_impact_score: float
    coherence_delta: float
    risk_vector: list[float]
    regression_detected: bool
    human_summary: str
    risk_labels: list[str] = field(default_factory=lambda: list(RISK_LABELS))
    cumulative_drift: Optional[float] = None
    cold_start: bool = False
    file_kind: Optional[str] = None
    cd_threshold: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "architectural_impact_score": float(self.architectural_impact_score),
            "coherence_delta": float(self.coherence_delta),
            "risk_vector": [float(x) for x in self.risk_vector],
            "risk_labels": list(self.risk_labels),
            "regression_detected": bool(self.regression_detected),
            "human_summary": str(self.human_summary),
            "cold_start": bool(self.cold_start),
        }
        if self.cumulative_drift is not None:
            out["cumulative_drift"] = float(self.cumulative_drift)
        if self.file_kind is not None:
            out["file_kind"] = str(self.file_kind)
        if self.cd_threshold is not None:
            out["cd_threshold"] = float(self.cd_threshold)
        return out


# ---------------------------------------------------------------------------
# Per-session baseline registry + /metrics ring buffer
# ---------------------------------------------------------------------------

# Session id -> mean-pooled torch tensor (CPU). We keep the type loose to avoid
# importing torch at module load time.
_BASELINES: dict[str, Any] = {}
_BASELINES_LOCK = Lock()

# Ring buffer of recent /score latencies in milliseconds. Bounded at 1000.
_METRICS_LATENCIES: deque = deque(maxlen=1000)
_METRICS_ERRORS: int = 0
_METRICS_LOCK = Lock()
_BOOT_TS: float = time.monotonic()


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if pct <= 0:
        return float(sorted_values[0])
    if pct >= 100:
        return float(sorted_values[-1])
    k = (len(sorted_values) - 1) * (pct / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(sorted_values[lo])
    frac = k - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


def _record_latency(latency_ms: float, error: bool = False) -> None:
    with _METRICS_LOCK:
        _METRICS_LATENCIES.append(float(latency_ms))
        if error:
            global _METRICS_ERRORS
            _METRICS_ERRORS += 1


def _metrics_snapshot() -> dict[str, Any]:
    with _METRICS_LOCK:
        latencies = sorted(_METRICS_LATENCIES)
        errors = _METRICS_ERRORS
    return {
        "score_calls": len(latencies),
        "p50_ms": _percentile(latencies, 50),
        "p95_ms": _percentile(latencies, 95),
        "p99_ms": _percentile(latencies, 99),
        "errors": errors,
        "uptime_s": float(time.monotonic() - _BOOT_TS),
    }


def _set_session_baseline(session_id: str, vec: Any) -> None:
    with _BASELINES_LOCK:
        _BASELINES[session_id] = vec


def _get_session_baseline(session_id: str) -> Any:
    with _BASELINES_LOCK:
        return _BASELINES.get(session_id)


def _clear_session_baselines() -> None:
    """Test helper — wipe registry between tests."""
    with _BASELINES_LOCK:
        _BASELINES.clear()


# ---------------------------------------------------------------------------
# Parser front-end
# ---------------------------------------------------------------------------

def parse_source(path: str, src: str) -> ParseResult:
    """Parse ``src`` using the grammar selected by ``path``'s extension.

    Never raises on syntax errors -- returns ``parse_ok=False`` instead. Does
    raise ``UnsupportedLanguageError`` for unsupported extensions (caller
    decides what to do).
    """
    lang_id, _ = select_grammar(path)
    parser = get_parser(lang_id)
    src_bytes = src.encode("utf-8", errors="replace") if isinstance(src, str) else (src or b"")
    try:
        tree = parser.parse(src_bytes)
    except Exception as exc:
        return ParseResult(tree=None, language=lang_id, parse_ok=False, error=str(exc))

    parse_ok = True
    try:
        if tree.root_node.has_error:  # type: ignore[attr-defined]
            parse_ok = False
    except Exception:
        # Older bindings: walk for ERROR nodes manually, bounded.
        try:
            stack = [tree.root_node]
            steps = 0
            while stack and steps < 4096:
                steps += 1
                node = stack.pop()
                if getattr(node, "type", "") == "ERROR" or getattr(node, "is_missing", False):
                    parse_ok = False
                    break
                stack.extend(list(getattr(node, "children", [])))
        except Exception:
            pass

    return ParseResult(tree=tree, language=lang_id, parse_ok=parse_ok)


# ---------------------------------------------------------------------------
# Call graph builder
# ---------------------------------------------------------------------------

def _node_text(node: Any, src_bytes: bytes) -> str:
    try:
        return src_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
    except Exception:
        return ""


def _walk(node: Any):
    """Iterative pre-order traversal yielding every descendant node."""
    if node is None:
        return
    stack = [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        yield n
        try:
            stack.extend(reversed(list(n.children)))
        except Exception:
            pass


def _identifier_of(node: Any, src_bytes: bytes, field_names: tuple[str, ...] = ("name",)) -> Optional[str]:
    for fname in field_names:
        try:
            child = node.child_by_field_name(fname)
        except Exception:
            child = None
        if child is not None:
            text = _node_text(child, src_bytes).strip()
            if text:
                return text
    # Fallback: first identifier child.
    try:
        for child in node.children:
            if "identifier" in getattr(child, "type", "") or getattr(child, "type", "") == "identifier":
                text = _node_text(child, src_bytes).strip()
                if text:
                    return text
    except Exception:
        pass
    return None


def _python_calls(func_node: Any, src_bytes: bytes) -> set[str]:
    out: set[str] = set()
    for n in _walk(func_node):
        if getattr(n, "type", "") == "call":
            try:
                callee = n.child_by_field_name("function")
            except Exception:
                callee = None
            if callee is None:
                continue
            t = getattr(callee, "type", "")
            if t == "identifier":
                out.add(_node_text(callee, src_bytes).strip())
            elif t == "attribute":
                try:
                    attr = callee.child_by_field_name("attribute")
                except Exception:
                    attr = None
                if attr is not None:
                    out.add(_node_text(attr, src_bytes).strip())
    return {x for x in out if x}


def _js_like_calls(func_node: Any, src_bytes: bytes) -> set[str]:
    out: set[str] = set()
    for n in _walk(func_node):
        if getattr(n, "type", "") == "call_expression":
            try:
                callee = n.child_by_field_name("function")
            except Exception:
                callee = None
            if callee is None:
                continue
            t = getattr(callee, "type", "")
            if t == "identifier":
                out.add(_node_text(callee, src_bytes).strip())
            elif t == "member_expression":
                try:
                    prop = callee.child_by_field_name("property")
                except Exception:
                    prop = None
                if prop is not None:
                    out.add(_node_text(prop, src_bytes).strip())
    return {x for x in out if x}


def _csharp_calls(func_node: Any, src_bytes: bytes) -> set[str]:
    out: set[str] = set()
    for n in _walk(func_node):
        if getattr(n, "type", "") == "invocation_expression":
            try:
                callee = n.child_by_field_name("function")
            except Exception:
                callee = None
            if callee is None:
                # Fallback to first child.
                try:
                    callee = n.children[0]
                except Exception:
                    callee = None
            if callee is None:
                continue
            t = getattr(callee, "type", "")
            if t == "identifier":
                out.add(_node_text(callee, src_bytes).strip())
            elif t == "member_access_expression":
                try:
                    name = callee.child_by_field_name("name")
                except Exception:
                    name = None
                if name is not None:
                    out.add(_node_text(name, src_bytes).strip())
                else:
                    # Last identifier descendant.
                    last = None
                    for d in _walk(callee):
                        if getattr(d, "type", "") == "identifier":
                            last = d
                    if last is not None:
                        out.add(_node_text(last, src_bytes).strip())
            else:
                # Generic / qualified names: pick last identifier descendant.
                last = None
                for d in _walk(callee):
                    if getattr(d, "type", "") == "identifier":
                        last = d
                if last is not None:
                    out.add(_node_text(last, src_bytes).strip())
    return {x for x in out if x}


def _sql_invocations(node: Any, src_bytes: bytes) -> set[str]:
    """Collect EXEC/CALL targets and function-call identifiers under a node."""
    out: set[str] = set()
    for n in _walk(node):
        ntype = getattr(n, "type", "")
        # invocation: most SQL grammars expose call_expression / function_call /
        # invocation. Also catch EXEC/EXECUTE statements heuristically.
        if ntype in {"invocation", "function_call", "call_expression"}:
            ident = None
            try:
                ident = n.child_by_field_name("function") or n.child_by_field_name("name")
            except Exception:
                ident = None
            if ident is None:
                # First identifier descendant.
                for d in _walk(n):
                    if getattr(d, "type", "") == "identifier":
                        ident = d
                        break
            if ident is not None:
                out.add(_node_text(ident, src_bytes).strip())
        elif ntype in {"execute_statement", "exec_statement", "call_statement"}:
            for d in _walk(n):
                if getattr(d, "type", "") == "identifier":
                    out.add(_node_text(d, src_bytes).strip())
                    break
    return {x for x in out if x}


def _python_functions(root: Any, src_bytes: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for n in _walk(root):
        if getattr(n, "type", "") in {"function_definition", "async_function_definition"}:
            name = _identifier_of(n, src_bytes)
            if name:
                out[name] = n
    return out


def _js_functions(root: Any, src_bytes: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for n in _walk(root):
        t = getattr(n, "type", "")
        if t in {"function_declaration", "method_definition", "function_expression"}:
            name = _identifier_of(n, src_bytes)
            if name:
                out[name] = n
        elif t == "lexical_declaration" or t == "variable_declaration":
            # const foo = () => { ... } / var foo = function() {}
            for child in _walk(n):
                if getattr(child, "type", "") == "variable_declarator":
                    try:
                        nm = child.child_by_field_name("name")
                        val = child.child_by_field_name("value")
                    except Exception:
                        nm, val = None, None
                    if nm is not None and val is not None and getattr(val, "type", "") in {
                        "arrow_function",
                        "function_expression",
                        "function",
                    }:
                        text = _node_text(nm, src_bytes).strip()
                        if text:
                            out[text] = val
    return out


def _csharp_methods(root: Any, src_bytes: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for n in _walk(root):
        t = getattr(n, "type", "")
        if t in {"method_declaration", "local_function_statement", "constructor_declaration"}:
            name = _identifier_of(n, src_bytes)
            if name:
                out[name] = n
    return out


def _sql_routines(root: Any, src_bytes: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for n in _walk(root):
        t = getattr(n, "type", "")
        if t in {
            "create_function_statement",
            "create_procedure_statement",
            "create_function",
            "create_procedure",
            "function_definition",
            "procedure_definition",
        }:
            name = _identifier_of(n, src_bytes, field_names=("name",))
            if name is None:
                # Last identifier in the create-stmt header.
                for d in _walk(n):
                    if getattr(d, "type", "") == "identifier":
                        name = _node_text(d, src_bytes).strip()
                        break
            if name:
                out[name] = n
    return out


def build_call_graph(tree: Any, src: str, language: str) -> dict[str, set[str]]:
    """Return {function_name: {called_names}} for the supported languages.

    Returns an empty dict on any failure (per the graceful-degradation
    contract in RC-003 acceptance criteria).
    """
    if tree is None:
        return {}
    src_bytes = src.encode("utf-8", errors="replace") if isinstance(src, str) else (src or b"")
    try:
        root = tree.root_node
    except Exception:
        return {}

    try:
        # Structured-data languages (markdown / json / yaml) have no call
        # semantics — return empty graph; novelty scoring still runs in
        # score_change via the SSM embedding pass.
        if language in {"markdown", "json", "yaml"}:
            return {}
        if language == "python":
            funcs = _python_functions(root, src_bytes)
            return {name: _python_calls(node, src_bytes) for name, node in funcs.items()}
        if language in {"javascript", "typescript", "tsx"}:
            funcs = _js_functions(root, src_bytes)
            return {name: _js_like_calls(node, src_bytes) for name, node in funcs.items()}
        if language == "csharp":
            methods = _csharp_methods(root, src_bytes)
            return {name: _csharp_calls(node, src_bytes) for name, node in methods.items()}
        if language == "sql":
            routines = _sql_routines(root, src_bytes)
            graph: dict[str, set[str]] = {}
            for name, node in routines.items():
                graph[name] = _sql_invocations(node, src_bytes)
            if not graph:
                # No routines: fall back to a module-level node mapping table refs.
                tables: set[str] = set()
                for n in _walk(root):
                    if getattr(n, "type", "") in {"object_reference", "table_reference", "relation"}:
                        for d in _walk(n):
                            if getattr(d, "type", "") == "identifier":
                                tables.add(_node_text(d, src_bytes).strip())
                                break
                graph["_module"] = {t for t in tables if t}
            return graph
    except Exception as exc:
        logger.debug("build_call_graph failed for %s: %s", language, exc)
        return {}

    return {}


# ---------------------------------------------------------------------------
# AST/CFG metrics for the risk vector
# ---------------------------------------------------------------------------

_BRANCH_NODE_TYPES = {
    # Python
    "if_statement", "for_statement", "while_statement", "try_statement",
    "elif_clause", "except_clause", "conditional_expression",
    "boolean_operator",
    # JS/TS
    "if_statement", "for_statement", "while_statement", "do_statement",
    "switch_case", "ternary_expression", "catch_clause", "logical_expression",
    # C#
    "if_statement", "for_statement", "for_each_statement", "while_statement",
    "do_statement", "switch_section", "catch_clause", "case_switch_label",
    "conditional_expression",
    # SQL
    "case", "when_clause", "if_statement",
}


def _count_branches(root: Any) -> int:
    if root is None:
        return 0
    n = 0
    for node in _walk(root):
        if getattr(node, "type", "") in _BRANCH_NODE_TYPES:
            n += 1
    return n


def _max_depth(root: Any) -> int:
    """Simple structural max-depth via DFS (bounded)."""
    if root is None:
        return 0
    best = 0
    stack: list[tuple[Any, int]] = [(root, 1)]
    steps = 0
    while stack and steps < 8192:
        steps += 1
        node, d = stack.pop()
        if d > best:
            best = d
        try:
            children = list(node.children)
        except Exception:
            children = []
        for c in children:
            stack.append((c, d + 1))
    return best


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _norm(x: float, scale: float) -> float:
    if scale <= 0:
        return 0.0
    v = x / scale
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return float(v)


def _compute_risk_vector(
    parse_before: ParseResult,
    parse_after: ParseResult,
    graph_before: dict[str, set[str]],
    graph_after: dict[str, set[str]],
    novelty: float,
    before_src: str,
    after_src: str,
) -> list[float]:
    # All structural dims measure the DELTA the edit introduces, not the
    # absolute property of the after-state. A 1-line edit to a busy file
    # should not saturate fan_out / coupling / depth just because the file
    # is structurally complex — the edit didn't make it worse.

    # cyclomatic: pure delta (was hybrid with 0.25*b_after absolute leak).
    b_before = _count_branches(parse_before.tree.root_node) if parse_before.tree else 0
    b_after = _count_branches(parse_after.tree.root_node) if parse_after.tree else 0
    cyclomatic = _norm(float(max(0, b_after - b_before)), 20.0)

    # fan_in / fan_out: delta of max in/out-degree between before and after graphs.
    def _max_indeg(graph: dict[str, set[str]]) -> int:
        counts: dict[str, int] = {n: 0 for n in graph}
        for callers in graph.values():
            for callee in callers:
                if callee in counts:
                    counts[callee] = counts.get(callee, 0) + 1
        return max(counts.values(), default=0)

    fan_in = _norm(float(max(_max_indeg(graph_after) - _max_indeg(graph_before), 0)), 8.0)
    fan_out_after = max((len(v) for v in graph_after.values()), default=0)
    fan_out_before = max((len(v) for v in graph_before.values()), default=0)
    fan_out = _norm(float(max(fan_out_after - fan_out_before, 0)), 12.0)

    # depth: delta of max AST depth (was max(d_before, d_after) — absolute).
    d_before = _max_depth(parse_before.tree.root_node) if parse_before.tree else 0
    d_after = _max_depth(parse_after.tree.root_node) if parse_after.tree else 0
    depth = _norm(float(max(d_after - d_before, 0)), 40.0)

    # churn: line-edit-distance (already a delta).
    before_lines = (before_src or "").splitlines()
    after_lines = (after_src or "").splitlines()
    set_before = set(before_lines)
    set_after = set(after_lines)
    churn_lines = len(set_before.symmetric_difference(set_after))
    churn = _norm(float(churn_lines), 200.0)

    # coupling: delta of total edges (was absolute on graph_after).
    edges_after = sum(len(v) for v in graph_after.values())
    edges_before = sum(len(v) for v in graph_before.values())
    coupling = _norm(float(max(edges_after - edges_before, 0)), 40.0)

    # cohesion: delta of lack-of-cohesion (was absolute). Penalise edits that
    # *increase* isolated-node ratio, not files that are inherently fragmented.
    def _lack_cohesion(graph: dict[str, set[str]]) -> float:
        n = len(graph)
        if n < 2:
            return 0.0
        isolated = sum(1 for v in graph.values() if not v)
        return _safe_div(float(isolated), float(n))

    cohesion_delta = max(_lack_cohesion(graph_after) - _lack_cohesion(graph_before), 0.0)
    cohesion = _norm(cohesion_delta, 1.0)

    # novelty: cosine-distance of the two pooled embeddings, already in [0,1].
    novelty_dim = _norm(novelty, 1.0)

    return [
        float(cyclomatic),
        float(fan_in),
        float(fan_out),
        float(depth),
        float(churn),
        float(coupling),
        float(cohesion),
        float(novelty_dim),
    ]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    """Parse a float from os.environ, falling back to ``default`` on missing/bad."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Thresholds are env-overridable so the operator can tune without a code edit.
# Defaults match the calibrated values; see .envrc § "Sidecar tuning".
_REGRESSION_AIS_THRESHOLD = _env_float("S2_AIS_THRESHOLD", 0.4)
# Threshold applied to the *normalized* coherence_delta (raw L2 / sqrt(hidden_size)).
# Value is checkpoint-portable: average per-dimension embedding drift in standard units.
COHERENCE_DELTA_THRESHOLD = _env_float("S2_COHERENCE_THRESHOLD", 1.5)
_REGRESSION_COHERENCE_THRESHOLD = COHERENCE_DELTA_THRESHOLD
_REGRESSION_RISK_DIM_THRESHOLD = _env_float("S2_RISK_DIM_THRESHOLD", 0.9)


def _file_kind(path: str) -> str:
    """Classify a path into one of the buckets used by ``_KIND_THRESHOLDS``.

    Plan markdown and prose docs have a wider statistical distribution than
    source code, so the same coherence_delta threshold over-blocks them.
    """
    p = (path or "").lower()
    if "/plans/" in p or p.endswith(".plan.md"):
        return "plan_md"
    if "/test" in p or "/tests/" in p or "_test" in p:
        return "test_code"
    ext = Path(p).suffix
    if ext in (".md", ".markdown", ".mdx"):
        return "doc_md"
    if ext in (".json", ".yaml", ".yml", ".toml", ".ini"):
        return "config"
    return "source_code"


# Per-kind thresholds. ``source_code`` falls back to the env-overridable
# constants above; the others are intentionally laxer because their content
# distribution doesn't match the calibrated source-code baseline.
_KIND_THRESHOLDS: dict[str, dict[str, float]] = {
    "source_code": {"cd": COHERENCE_DELTA_THRESHOLD, "ais": _REGRESSION_AIS_THRESHOLD, "dim": _REGRESSION_RISK_DIM_THRESHOLD},
    "test_code":   {"cd": 2.0, "ais": 0.3, "dim": 0.95},
    "plan_md":     {"cd": 3.0, "ais": 0.3, "dim": 1.0},
    "doc_md":      {"cd": 3.0, "ais": 0.3, "dim": 1.0},
    "config":      {"cd": 1.2, "ais": 0.5, "dim": 0.9},
}


def _cosine_similarity(a, b) -> float:
    """Compute cosine sim between two 1D torch tensors. Returns float in [-1, 1]."""
    import torch

    if a is None or b is None:
        return 1.0
    na = torch.linalg.norm(a)
    nb = torch.linalg.norm(b)
    if float(na) == 0.0 or float(nb) == 0.0:
        return 1.0
    return float(torch.dot(a, b) / (na * nb))


def _l2_distance(a, b) -> float:
    import torch

    if a is None or b is None:
        return 0.0
    return float(torch.linalg.norm(a - b))


def _backbone_hidden_size(fallback_vec: Any = None) -> int:
    """Return the embedding dimension. Prefers BACKBONE_INFO; falls back to the
    last dim of an embedding tensor; returns 1 as a last resort to avoid
    divide-by-zero in the coherence_delta normalization.
    """
    h = BACKBONE_INFO.get("hidden_size") if isinstance(BACKBONE_INFO, dict) else None
    try:
        h_int = int(h) if h is not None else 0
    except (TypeError, ValueError):
        h_int = 0
    if h_int > 0:
        return h_int
    if fallback_vec is not None:
        try:
            return int(fallback_vec.shape[-1])
        except Exception:  # noqa: BLE001
            pass
    return 1


def _summarize(
    ais: float,
    coherence_delta: float,
    risk_vector: list[float],
    regression: bool,
    language: str,
) -> str:
    if regression:
        # Find dominant risk dim.
        idx = max(range(len(risk_vector)), key=lambda i: risk_vector[i])
        dim = RISK_LABELS[idx]
        return (
            f"Regression detected in {language}: AIS={ais:.3f}, "
            f"coherence_delta={coherence_delta:.3f}, dominant_risk={dim}={risk_vector[idx]:.3f}."
        )
    return (
        f"Change accepted ({language}): AIS={ais:.3f}, "
        f"coherence_delta={coherence_delta:.3f}, max_risk={max(risk_vector):.3f}."
    )


def score_change(
    path: str,
    before_src: str,
    after_src: str,
    *,
    session_id: Optional[str] = None,
) -> ImpactReport:
    """Score a proposed source edit. Hard contract -- see board RC-004.

    When ``session_id`` is provided AND a baseline has been registered for that
    session via /baseline, the report includes ``cumulative_drift`` =
    ``||embed(after_src) - baseline||_2``. This drift never affects
    ``regression_detected`` (the sidecar exposes the number; consumers decide
    whether to act on it). The plumbing is opt-in to keep test determinism.
    """
    # Grammar selection (raises UnsupportedLanguageError to caller).
    lang_id, _ = select_grammar(path)

    parse_before = parse_source(path, before_src or "")
    parse_after = parse_source(path, after_src or "")

    graph_before = build_call_graph(parse_before.tree, before_src or "", parse_before.language)
    graph_after = build_call_graph(parse_after.tree, after_src or "", parse_after.language)

    # AST -> token strings; falls back to raw source if tree is unusable.
    before_tokens = ast_to_tokens(parse_before.tree, before_src or "")
    after_tokens = ast_to_tokens(parse_after.tree, after_src or "")

    # Forward pass through the real Mamba backbone.
    try:
        emb_before = embed(before_tokens)
        emb_after = embed(after_tokens)
        cos = _cosine_similarity(emb_before, emb_after)
        raw_l2 = _l2_distance(emb_before, emb_after)
    except BackboneUnavailableError:
        # We propagate this -- the sidecar should fail loudly rather than
        # silently degrade scoring quality.
        raise
    except Exception as exc:
        logger.exception("Backbone forward pass failed: %s", exc)
        raise BackboneUnavailableError(f"forward pass failed: {exc}") from exc

    # AIS in [0, 1]: 1.0 == identical embeddings. Map cos in [-1,1] -> [0,1].
    ais = max(0.0, min(1.0, (cos + 1.0) / 2.0))

    # Normalize coherence_delta by sqrt(hidden_size) so the threshold is
    # checkpoint-portable: a raw L2 over a 768-D vector dwarfs the same drift
    # over a 256-D vector for the same edit. Dividing by sqrt(D) recasts the
    # value as average per-dimension drift in standard units (see VERIFICATION.md).
    # Cold-start: an empty/near-empty before_src has no meaningful baseline to
    # compare against, so the L2-magnitude proxy degenerates. Skip cd entirely
    # for these cases — the 8-dim risk_vector still flags churn/cyclomatic on bad content.
    hidden_size = _backbone_hidden_size(emb_after)
    cold_start = (not before_src.strip()) or (len(before_src) < 32)
    if cold_start:
        coherence_delta = 0.0
    else:
        coherence_delta = float(raw_l2) / math.sqrt(max(hidden_size, 1))

    # novelty in [0, 1]: 1 - max(cos, 0).
    novelty = max(0.0, min(1.0, 1.0 - max(cos, 0.0)))

    risk_vector = _compute_risk_vector(
        parse_before,
        parse_after,
        graph_before,
        graph_after,
        novelty,
        before_src or "",
        after_src or "",
    )

    # Cold-start: with empty before_src, every structural delta (cyclomatic,
    # fan_in, fan_out, depth, churn, coupling, cohesion) reduces to the
    # absolute after-state — the metric loses its delta meaning. Zero them
    # for cold-start writes; novelty (cosine of pooled embeddings) and the
    # AIS / coherence_delta paths still reason about content quality.
    if cold_start and len(risk_vector) >= 7:
        risk_vector = list(risk_vector)
        for i in range(7):
            risk_vector[i] = 0.0

    kind = _file_kind(path)
    t = _KIND_THRESHOLDS.get(kind, _KIND_THRESHOLDS["source_code"])
    dim_ceiling = t.get("dim", _REGRESSION_RISK_DIM_THRESHOLD)
    regression = (
        ais < t["ais"]
        or coherence_delta > t["cd"]
        or any(dim > dim_ceiling for dim in risk_vector)
    )

    public_lang = PUBLIC_LANGUAGE.get(lang_id, lang_id)
    summary = _summarize(ais, coherence_delta, risk_vector, regression, public_lang)

    cumulative_drift: Optional[float] = None
    if session_id:
        baseline_vec = _get_session_baseline(session_id)
        if baseline_vec is not None:
            try:
                # Same normalization as coherence_delta — keep both metrics on
                # the same scale so the threshold semantics are consistent.
                cumulative_drift = float(_l2_distance(emb_after, baseline_vec)) / math.sqrt(
                    max(hidden_size, 1)
                )
            except Exception:  # noqa: BLE001
                cumulative_drift = None

    return ImpactReport(
        architectural_impact_score=float(ais),
        coherence_delta=float(coherence_delta),
        risk_vector=risk_vector,
        regression_detected=bool(regression),
        human_summary=summary,
        cumulative_drift=cumulative_drift,
        cold_start=bool(cold_start),
        file_kind=kind,
        cd_threshold=float(t["cd"]),
    )


# ---------------------------------------------------------------------------
# HTTP service (FastAPI)
# ---------------------------------------------------------------------------

def create_app():
    """Construct the FastAPI app. Loads the SSM backbone eagerly via lifespan."""
    # Imports are hoisted into module globals so FastAPI's get_type_hints()
    # can resolve `Request` in the route signature even with
    # `from __future__ import annotations` active at the top of this module.
    import fastapi as _fastapi
    import fastapi.responses as _fastapi_responses
    from contextlib import asynccontextmanager
    globals().setdefault("Request", _fastapi.Request)
    globals().setdefault("JSONResponse", _fastapi_responses.JSONResponse)
    FastAPI = _fastapi.FastAPI
    Request = _fastapi.Request
    JSONResponse = _fastapi_responses.JSONResponse

    @asynccontextmanager
    async def _lifespan(_app):  # pragma: no cover - exercised via boot
        try:
            load_backbone()
            logger.info("backbone pre-warmed at startup")
        except BackboneUnavailableError as exc:
            # Log + keep server alive so /health honestly reports model_loaded:false.
            logger.error("backbone load failed at startup: %s", exc)
        yield
        # No teardown work — the SSM handle is process-lifetime.

    app = FastAPI(title="reasoning-core S2 sidecar", version="1.0", lifespan=_lifespan)

    @app.get("/health")
    async def health():
        return JSONResponse(
            {
                "status": "ok",
                "model_loaded": bool(is_loaded()),
                "languages": list(SUPPORTED_LANGUAGES),
                "backbone": dict(BACKBONE_INFO),
            }
        )

    @app.post("/score")
    async def score(request: Request):
        t0 = time.monotonic()
        try:
            raw = await request.body()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as exc:
            _record_latency((time.monotonic() - t0) * 1000.0, error=True)
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "detail": f"invalid JSON: {exc}"},
            )
        if not isinstance(payload, dict):
            _record_latency((time.monotonic() - t0) * 1000.0, error=True)
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "detail": "expected JSON object"},
            )

        path = payload.get("path")
        before_src = payload.get("before_src", "")
        after_src = payload.get("after_src", "")
        session_id = payload.get("session_id")
        if not isinstance(session_id, str):
            session_id = None
        if not isinstance(path, str) or not path:
            _record_latency((time.monotonic() - t0) * 1000.0, error=True)
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "detail": "missing or invalid 'path'"},
            )
        if not isinstance(before_src, str) or not isinstance(after_src, str):
            _record_latency((time.monotonic() - t0) * 1000.0, error=True)
            return JSONResponse(
                status_code=400,
                content={
                    "error": "bad_request",
                    "detail": "'before_src' and 'after_src' must be strings",
                },
            )

        try:
            report = score_change(path, before_src, after_src, session_id=session_id)
        except UnsupportedLanguageError as exc:
            _record_latency((time.monotonic() - t0) * 1000.0, error=False)
            return JSONResponse(
                status_code=415,
                content={"error": "unsupported_language", "extension": exc.extension},
            )
        except BackboneUnavailableError as exc:
            _record_latency((time.monotonic() - t0) * 1000.0, error=True)
            return JSONResponse(
                status_code=503,
                content={"error": "backbone_unavailable", "detail": str(exc)},
            )
        except Exception as exc:
            _record_latency((time.monotonic() - t0) * 1000.0, error=True)
            logger.exception("scoring failed")
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", "detail": str(exc)},
            )

        _record_latency((time.monotonic() - t0) * 1000.0, error=False)
        return JSONResponse(status_code=200, content=report.to_dict())

    @app.get("/metrics")
    async def metrics():
        return JSONResponse(status_code=200, content=_metrics_snapshot())

    @app.post("/baseline")
    async def baseline(request: Request):
        try:
            raw = await request.body()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as exc:
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "detail": f"invalid JSON: {exc}"},
            )
        if not isinstance(payload, dict):
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "detail": "expected JSON object"},
            )
        session_id = payload.get("session_id")
        files = payload.get("files")
        if not isinstance(session_id, str) or not session_id:
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "detail": "missing or invalid 'session_id'"},
            )
        if not isinstance(files, list) or not files:
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "detail": "'files' must be a non-empty list"},
            )
        try:
            import torch

            vecs = []
            for f in files:
                if not isinstance(f, dict):
                    continue
                src = f.get("src")
                fpath = f.get("path") or "/tmp/baseline.txt"
                if not isinstance(src, str):
                    continue
                # Parse for AST tokens when grammar known; else fall back to raw.
                try:
                    pr = parse_source(fpath, src)
                    tokens = ast_to_tokens(pr.tree, src)
                except UnsupportedLanguageError:
                    tokens = src
                except Exception:  # noqa: BLE001
                    tokens = src
                vec = embed(tokens)
                vecs.append(vec)
            if not vecs:
                return JSONResponse(
                    status_code=400,
                    content={"error": "bad_request", "detail": "no embeddable files"},
                )
            mean = torch.stack(vecs).mean(dim=0)
        except BackboneUnavailableError as exc:
            return JSONResponse(
                status_code=503,
                content={"error": "backbone_unavailable", "detail": str(exc)},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("baseline failed")
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", "detail": str(exc)},
            )
        _set_session_baseline(session_id, mean)
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "session_id": session_id,
                "n_files": len(vecs),
                "hidden_size": int(mean.shape[0]) if hasattr(mean, "shape") else 0,
            },
        )

    return app


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _run() -> int:
    """Run the HTTP service on 127.0.0.1:8765 (loopback only)."""
    host = "127.0.0.1"
    port = int(os.environ.get("S2_PORT", "8765"))

    # Pre-warm the backbone synchronously BEFORE binding the port so callers
    # who probe /health immediately get model_loaded:true.
    try:
        load_backbone()
    except BackboneUnavailableError as exc:
        logger.error("backbone unavailable at startup: %s", exc)
        # Do not exit -- /health will reflect the degraded state. The contract
        # says startup must not crash the sidecar on backbone failure.

    try:
        import uvicorn
    except Exception as exc:  # pragma: no cover - dep missing
        print(f"ERROR: uvicorn import failed: {exc}", file=sys.stderr)
        return 1

    app = create_app()
    try:
        # uvicorn.run blocks. log_level is left as default; we already have
        # logging configured.
        uvicorn.run(app, host=host, port=port, log_level=os.environ.get("S2_LOG_LEVEL", "info"))
    except OSError as exc:
        # Address already in use, etc.
        print(f"ERROR: could not bind {host}:{port}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via boot
    logging.basicConfig(level=os.environ.get("S2_LOG_LEVEL", "INFO").upper())
    sys.exit(_run())


__all__ = [
    "ImpactReport",
    "ParseResult",
    "RISK_LABELS",
    "UnsupportedLanguageError",
    "build_call_graph",
    "create_app",
    "parse_source",
    "score_change",
    "select_grammar",
]
