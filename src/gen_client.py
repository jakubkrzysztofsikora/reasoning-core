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
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


_DEFAULT_URL = os.environ.get("RC_GEN_URL", "http://127.0.0.1:8766/v1/chat/completions")
_DEFAULT_BUDGET_MS = int(os.environ.get("RC_GEN_BUDGET_MS", "2500"))


def _backend_active() -> bool:
    """True if a generative backend is configured. Off by default."""
    return os.environ.get("RC_REASONER_BACKEND", "").lower() in ("mlx", "llama", "remote")


def health_ok(url: str = _DEFAULT_URL) -> bool:
    """Quick liveness probe. Substitutes /v1/chat/completions path with /health."""
    base = url.split("/v1/")[0] if "/v1/" in url else url
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=1.0) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _post(url: str, body: Dict[str, Any], budget_ms: int) -> Optional[Dict[str, Any]]:
    """Synchronous POST with hard timeout. Returns parsed JSON or None."""
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=budget_ms / 1000.0) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def critic_call(prompt: str, *, model: str = "qwen2.5-coder-1.5b-instruct",
                budget_ms: int = _DEFAULT_BUDGET_MS) -> Optional[str]:
    """Single generative critic pass. Temperature pinned to 0 for gate paths.

    Returns the assistant message text on success, None on timeout/5xx.
    Caller falls open to BM25 / heuristic on None.
    """
    if not _backend_active():
        return None
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 512,
    }
    res = _post(_DEFAULT_URL, body, budget_ms)
    if not res:
        return None
    try:
        return res["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def score_plan_grounding(plan_claim: str, diff_hunk: str) -> Dict[str, int]:
    """CDGS support: ask critic if claim is grounded in diff hunk.

    Returns {"supported": 0|1, "total": 1}. Caller aggregates over claims.
    Server-side iteration cap = 1 here (per-claim); aggregator runs N claims.
    """
    if not _backend_active():
        return {"supported": 0, "total": 0}
    prompt = (
        "Decide if the plan claim is supported by the diff hunk. "
        "Respond with one word: YES or NO.\n\n"
        f"PLAN CLAIM:\n{plan_claim}\n\n"
        f"DIFF HUNK:\n{diff_hunk}\n\n"
        "ANSWER:"
    )
    out = critic_call(prompt)
    if not out:
        return {"supported": 0, "total": 0}
    head = out.strip().split()[0].upper() if out.strip() else ""
    return {"supported": 1 if head.startswith("YES") else 0, "total": 1}


def score_with_iteration(prompt: str, *, max_iters: int = 3,
                         total_budget_ms: int = 6000) -> Optional[str]:
    """Server-side iteration loop with hard cap.

    Per agent-harness reviewer: do NOT expose iteration to the agent.
    Internal-only. Each iteration consumes proportionally from the total
    wall budget; once budget exhausts, return last-best.
    """
    if not _backend_active():
        return None
    deadline = time.time() + (total_budget_ms / 1000.0)
    last: Optional[str] = None
    for i in range(max_iters):
        remaining_ms = max(int((deadline - time.time()) * 1000), 100)
        if remaining_ms < 200:
            break
        out = critic_call(prompt, budget_ms=remaining_ms)
        if out:
            last = out
        if last and "FINAL" in last.upper():
            break
    return last
