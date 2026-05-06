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


def _default_budget_ms() -> int:
    return int(os.environ.get("RC_GEN_BUDGET_MS", "2500"))


def _backend_active() -> bool:
    return os.environ.get("RC_REASONER_BACKEND", "").lower() in ("mlx", "llama", "remote")


def _audit_emit(reason: str, **fields: Any) -> None:
    """Emit a JSONL event so CDGS BM25 fallback is visible in audit."""
    try:
        path = os.environ.get(
            "RC_GEN_FALLBACK_LOG",
            os.path.expanduser("~/.local/share/reasoning-core/events/gen_fallback.jsonl"),
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
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
    headers = {}
    api_key = os.environ.get("RC_GEN_API_KEY") or os.environ.get("SCALEWAY_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        req = urllib.request.Request(f"{base}/v1/models", headers=headers)
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _post(url: str, body: Dict[str, Any], budget_ms: int) -> Optional[Dict[str, Any]]:
    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("RC_GEN_API_KEY") or os.environ.get("SCALEWAY_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url,
        data=raw,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=budget_ms / 1000.0) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data)
    except urllib.error.HTTPError as exc:
        _audit_emit("gen_5xx" if exc.code >= 500 else "gen_4xx", code=exc.code)
        return None
    except (urllib.error.URLError, OSError) as exc:
        _audit_emit("gen_timeout_or_unreachable", error=str(exc))
        return None
    except ValueError as exc:
        _audit_emit("gen_decode_error", error=str(exc))
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
    "You are auditing whether a plan claim is implemented by a code diff.\n"
    "Answer the 3 rubric questions, then give a final verdict.\n\n"
    "PLAN CLAIM:\n{claim}\n\n"
    "DIFF HUNK:\n{hunk}\n\n"
    "RUBRIC (answer Y or N):\n"
    "1. Does the diff change a file or symbol that the plan claim names?\n"
    "2. Does the diff introduce or modify behavior that matches the claim's intent\n"
    "   (not just whitespace, comments, or unrelated edits)?\n"
    "3. Could a reviewer reading only the diff conclude the claim is delivered?\n\n"
    "If at least 2 of 3 are Y → VERDICT: YES. Otherwise → VERDICT: NO.\n"
    "Respond with the rubric answers (one line each) then a final line\n"
    "starting with 'VERDICT:' followed by exactly YES or NO. Nothing else.\n"
)


def _parse_verdict(text: str) -> Optional[int]:
    """Parse the rubric-prompt 'VERDICT: YES|NO' line. Falls back to bare
    YES/NO scan if no VERDICT line found (legacy prompt compat)."""
    if not text:
        return None
    upper = text.strip().upper()
    for line in upper.splitlines()[::-1]:
        line = line.strip().lstrip("-* ").rstrip(".:,")
        if line.startswith("VERDICT"):
            tail = line.split(":", 1)[-1].strip() if ":" in line else line
            if "YES" in tail and "NO" not in tail:
                return 1
            if "NO" in tail and "YES" not in tail:
                return 0
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
        max_tokens=128,  # rubric needs ~3 lines + verdict
    )
    parsed = _parse_verdict(out or "")
    if parsed is None:
        return {"supported": 0, "total": 0}
    return {"supported": parsed, "total": 1}
