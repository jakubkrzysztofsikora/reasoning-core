"""Generative critic client (P5).

Async OpenAI-compatible client wrapping the local generative endpoint
(Qwen2.5-Coder-1.5B-Instruct via mlx_lm.server on Apple, llama.cpp GGUF
on Linux, or remote endpoint). Used by P2 CDGS plan-grounding and P3
Invariant 5 plan analyzer.

Server-side iteration capped at 3 critic passes / 6s wall (per Anthropic
hook docs review — exposing iteration to the agent is anti-pattern).

Per v2 plan reviewer corrections folded in:
- Pin temperature=0 for gate-affecting paths (deterministic).
- Hard-budget RC_GEN_BUDGET_MS=2500 per call → fail-open to BM25 on
  timeout / 5xx.
- Cross-platform via RC_REASONER_BACKEND={mlx, llama, remote}.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


def _default_url() -> str:
    return os.environ.get("RC_GEN_URL", "http://127.0.0.1:8766/v1/chat/completions")


def _is_loopback_host(host: str) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _allowed_remote_hosts() -> frozenset[str]:
    """Hosts permitted to receive the Bearer token. Empty by default.

    Operators using a hosted critic (e.g. api.scaleway.ai) must opt in by
    listing the host in RC_GEN_ALLOWED_HOSTS (CSV).
    """
    raw = os.environ.get("RC_GEN_ALLOWED_HOSTS", "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def _bearer_safe_for(url: str) -> bool:
    """Return True when it is safe to attach Authorization to this URL.

    Loopback always safe. Remote hosts must be explicitly allowlisted via
    RC_GEN_ALLOWED_HOSTS to prevent leaking the API key to an attacker who
    redirects RC_GEN_URL through dotfile / .envrc / IDE-config injection.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if _is_loopback_host(host):
        return True
    if parsed.scheme != "https":
        return False
    return host in _allowed_remote_hosts()


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """urllib does not strip Authorization on cross-origin 30x redirects
    (unlike requests' rebuild_auth). Refuse redirects to keep the Bearer
    token from leaving the originally-validated host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _default_budget_ms() -> int:
    return int(os.environ.get("RC_GEN_BUDGET_MS", "2500"))


# Per-process cache for the auto-probe so /v1/models isn't hit on every Edit.
# Keyed on the resolved URL so flipping RC_GEN_URL invalidates the cache.
_AUTOPROBE_CACHE: Dict[str, bool] = {}


def _backend_active() -> bool:
    """Audit 2026-06-02 follow-up: auto-detect a running gen sidecar.

    Resolution order:
      1. RC_REASONER_BACKEND in {off, 0, no, false, disabled} → hard opt-out.
      2. RC_REASONER_BACKEND in {mlx, llama, remote}           → explicit on.
      3. else (auto/unset) → probe /v1/models once per (URL) per process.
         Cached so the gate doesn't pay the probe on every Edit.
    """
    raw = os.environ.get("RC_REASONER_BACKEND", "").lower()
    if raw in ("off", "0", "no", "false", "disabled"):
        return False
    if raw in ("mlx", "llama", "remote"):
        return True
    url = _default_url()
    key = f"auto::{url}"
    cached = _AUTOPROBE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        active = health_ok(url)
    except Exception:  # noqa: BLE001 — never raise into the gate
        active = False
    _AUTOPROBE_CACHE[key] = active
    return active


def _reset_autoprobe_cache_for_tests() -> None:
    """Test hook — clear the per-process probe cache."""
    _AUTOPROBE_CACHE.clear()


def _audit_emit(reason: str, **fields: Any) -> None:
    """Emit a JSONL event so CDGS BM25 fallback is visible in audit."""
    try:
        default_path = os.path.expanduser("~/.local/share/reasoning-core/events/gen_fallback.jsonl")
        path = os.environ.get("RC_GEN_FALLBACK_LOG", default_path)
        allowed_prefixes = [
            os.path.realpath(os.path.expanduser("~/.local/share/reasoning-core")),
            os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".cache")),
        ]
        resolved = os.path.realpath(path)
        if not any(resolved == p or resolved.startswith(p + os.sep) for p in allowed_prefixes):
            return
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(resolved, flags, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "signal_source": "bm25_fallback",
                "reason": reason,
                **fields,
            }) + "\n")
    except OSError:
        pass


def health_ok(url: Optional[str] = None) -> bool:
    """Liveness probe. mlx_lm.server has no /health — probe /v1/models.
    Hosted endpoints (Scaleway etc.) require auth — pass Bearer token."""
    target = url or _default_url()
    parsed = urllib.parse.urlsplit(target)
    base = f"{parsed.scheme}://{parsed.netloc}"
    probe_url = f"{base}/v1/models"
    headers = {}
    api_key = os.environ.get("RC_GEN_API_KEY") or os.environ.get("SCALEWAY_API_KEY")
    if api_key and _bearer_safe_for(probe_url):
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        req = urllib.request.Request(probe_url, headers=headers)
        with _NO_REDIRECT_OPENER.open(req, timeout=5.0) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _post(url: str, body: Dict[str, Any], budget_ms: int) -> Optional[Dict[str, Any]]:
    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("RC_GEN_API_KEY") or os.environ.get("SCALEWAY_API_KEY")
    if api_key and _bearer_safe_for(url):
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url,
        data=raw,
        headers=headers,
        method="POST",
    )
    try:
        with _NO_REDIRECT_OPENER.open(req, timeout=budget_ms / 1000.0) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data)
    except urllib.error.HTTPError as exc:
        _audit_emit("gen_5xx" if exc.code >= 500 else "gen_4xx", code=exc.code)
        return None
    except (urllib.error.URLError, OSError) as exc:
        # Round-3 fix (SD-H1): never log str(exc) — defense-in-depth against
        # future redirect/URL-with-key paths leaking auth into JSONL.
        _audit_emit("gen_timeout_or_unreachable", exc_type=type(exc).__name__)
        return None
    except ValueError as exc:
        _audit_emit("gen_decode_error", exc_type=type(exc).__name__)
        return None


def _default_model() -> str:
    return os.environ.get(
        "RC_GEN_MODEL", "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
    )


def critic_call(prompt: str, *, model: Optional[str] = None,
                budget_ms: Optional[int] = None,
                max_tokens: int = 512) -> Optional[str]:
    if not _backend_active():
        return None
    body = {
        "model": model or _default_model(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    res = _post(_default_url(), body, budget_ms or _default_budget_ms())
    if not res:
        return None
    try:
        return res["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


_GROUNDING_PROMPT = (
    "You audit whether a plan claim is implemented by a code diff.\n"
    "Answer 3 rubric questions on separate lines. Each line MUST be exactly\n"
    "'Q1: Y' or 'Q1: N' (and so on). Code computes the verdict from your\n"
    "Y/N answers — do NOT emit a VERDICT line.\n\n"
    "PLAN CLAIM:\n{claim}\n\n"
    "DIFF HUNK:\n{hunk}\n\n"
    "RUBRIC:\n"
    "Q1: Does the diff change a file or symbol that the plan claim names?\n"
    "Q2: Does the diff introduce or modify behavior matching the claim's intent\n"
    "    (not just whitespace, comments, or unrelated edits)?\n"
    "Q3: Could a reviewer reading only the diff conclude the claim is delivered?\n\n"
    "Respond with EXACTLY three lines, in order: 'Q1: Y' or 'Q1: N', then Q2,\n"
    "then Q3. Nothing else.\n"
)

_QLINE_RE = re.compile(r"^\s*Q([123])\s*[:\-]\s*([YN])\b", re.IGNORECASE | re.MULTILINE)


def _parse_verdict(text: str) -> Optional[int]:
    """Round-3 fix (LLM-sci H2 + AH H2): parse the 3 rubric Y/N answers from
    the model output and recompute the verdict in code (≥2 of 3 = YES).
    Ignores any model-emitted VERDICT line. Falls back to legacy bare YES/NO
    only when zero Q-lines are detected (legacy prompt compat)."""
    if not text:
        return None
    matches = _QLINE_RE.findall(text)
    if matches:
        seen: Dict[str, str] = {}
        for q, ans in matches:
            seen.setdefault(q, ans.upper())
        ys = sum(1 for v in seen.values() if v == "Y")
        ns = sum(1 for v in seen.values() if v == "N")
        if ys + ns == 0:
            return None
        # Need at least 2 answers to make a confident call; fewer = unparseable.
        if ys + ns < 2:
            return None
        return 1 if ys >= 2 else 0
    return _parse_yesno(text)


_YES_WORD = re.compile(r"\bYES\b")
_NO_WORD = re.compile(r"\bNO\b")


def _parse_yesno(text: str) -> Optional[int]:
    """Robust YES/NO extraction. Returns 1, 0, or None (unparseable).

    Uses word boundaries to avoid false-positives like 'know' matching NO.
    """
    if not text:
        return None
    upper = text.strip().upper()
    yes_m = _YES_WORD.search(upper)
    no_m = _NO_WORD.search(upper)
    if yes_m and not no_m:
        return 1
    if no_m and not yes_m:
        return 0
    if yes_m and no_m:
        return 1 if yes_m.start() < no_m.start() else 0
    return None


def score_plan_grounding(plan_claim: str, diff_hunk: str) -> Dict[str, int]:
    """CDGS support. Returns {"supported": 0|1, "total": 1} on success,
    {"supported": 0, "total": 0} on failure (caller falls back to BM25).

    P5 round-2 fix: hunk truncated to ~3000 chars (mlx_lm prefill p99 budget),
    max_tokens=8 (not 512 — single classification token + slack), stop on \n.
    """
    if not _backend_active():
        return {"supported": 0, "total": 0}
    hunk = (diff_hunk or "")[:3000]
    claim = (plan_claim or "")[:500]
    out = critic_call(
        _GROUNDING_PROMPT.format(claim=claim, hunk=hunk),
        max_tokens=64,  # 3 short Q-lines = ~30 tokens
    )
    parsed = _parse_verdict(out or "")
    if parsed is None:
        return {"supported": 0, "total": 0}
    return {"supported": parsed, "total": 1}
