"""RC-013: live Scaleway + y-router smoke test.

Live tests are gated behind ``@pytest.mark.live`` and the ``RC_LIVE=1`` env
variable. Without ``RC_LIVE=1`` only the negative ``configure-scaleway.sh``
fail-fast assertion runs (and that's intentional -- it's the path CI exercises
without credentials).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "configure-scaleway.sh"

httpx = pytest.importorskip("httpx")


SCALEWAY_DIRECT_URL = "https://api.scaleway.ai/v1/chat/completions"
DEFAULT_MODEL = "devstral-2-123b-instruct-2512"


def _scw_secret_key() -> Optional[str]:
    """Read the Scaleway secret key via the CLI; return None if unavailable."""
    if os.environ.get("SCALEWAY_API_KEY"):
        return os.environ["SCALEWAY_API_KEY"]
    if not shutil.which("scw"):
        return None
    try:
        out = subprocess.run(
            ["scw", "config", "get", "secret-key", "--profile", "newprofile"],
            check=False, capture_output=True, text=True, timeout=4,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    val = (out.stdout or "").strip()
    if not val:
        # Try legacy snake_case key.
        try:
            out = subprocess.run(
                ["scw", "config", "get", "secret_key", "--profile", "newprofile"],
                check=False, capture_output=True, text=True, timeout=4,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        val = (out.stdout or "").strip()
    return val or None


# ---------------------------------------------------------------------------
# Live tests (gated)
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_scaleway_direct_chat_completions_live():
    if os.environ.get("RC_LIVE") != "1":
        pytest.skip("RC_LIVE=1 not set; skipping live Scaleway probe")
    key = _scw_secret_key()
    if not key:
        pytest.skip("no Scaleway secret key available (set SCALEWAY_API_KEY or configure scw profile=newprofile)")

    payload = {
        "model": DEFAULT_MODEL,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 16,
    }
    t0 = time.time()
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            SCALEWAY_DIRECT_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    elapsed_ms = int((time.time() - t0) * 1000)
    assert resp.status_code == 200, f"unexpected status {resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
    assert isinstance(content, str) and content, f"empty completion: {body}"
    usage = body.get("usage") or {}
    print(
        json.dumps({
            "scaleway_direct": {
                "model": DEFAULT_MODEL,
                "latency_ms": elapsed_ms,
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            },
        })
    )


@pytest.mark.live
def test_yrouter_messages_live():
    if os.environ.get("RC_LIVE") != "1":
        pytest.skip("RC_LIVE=1 not set; skipping live y-router probe")
    key = _scw_secret_key()
    if not key:
        pytest.skip("no Scaleway secret key available")
    base = os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:8787").rstrip("/")
    url = f"{base}/v1/messages"

    # Bail early if the y-router isn't actually up.
    try:
        with httpx.Client(timeout=2.0) as ping:
            ping.get(base)
    except Exception as exc:
        pytest.skip(f"y-router at {base} not reachable: {exc}")

    payload = {
        "model": DEFAULT_MODEL,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "ping"}],
    }
    t0 = time.time()
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    elapsed_ms = int((time.time() - t0) * 1000)
    assert resp.status_code == 200, f"unexpected status {resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    # Anthropic-shape OR OpenAI-shape (the y-router may pass-through either).
    text = None
    if isinstance(body.get("content"), list) and body["content"]:
        text = body["content"][0].get("text")
    if not text:
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content")
    assert isinstance(text, str) and text, f"empty completion: {body}"
    print(
        json.dumps({
            "yrouter": {
                "endpoint": url,
                "latency_ms": elapsed_ms,
                "len_chars": len(text),
            },
        })
    )


# ---------------------------------------------------------------------------
# Negative test: the script must fail fast when no key is available.
# Runs offline, always.
# ---------------------------------------------------------------------------

def test_configure_scaleway_missing_key_fails_fast(tmp_path: Path):
    if not SCRIPT.exists():
        pytest.skip(f"configure-scaleway.sh not present at {SCRIPT}")
    if not shutil.which("bash"):
        pytest.skip("bash not on PATH")

    # Build a stub `scw` that prints nothing on any invocation.
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    stub = stub_dir / "scw"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # Build a tiny PATH that contains the stub plus the bare-essentials needed
    # by bash itself (env, bash, jq, curl). We keep the original PATH suffix so
    # the script still finds curl/jq -- we are only stubbing `scw`.
    new_path = f"{stub_dir}:{os.environ.get('PATH', '')}"
    env = os.environ.copy()
    env["PATH"] = new_path
    env.pop("SCALEWAY_API_KEY", None)

    t0 = time.time()
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=10.0,
    )
    elapsed = time.time() - t0
    assert result.returncode != 0, (
        f"script unexpectedly succeeded; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert elapsed < 5.0, f"script took too long ({elapsed:.2f}s) -- expected <2s budget"
    assert "missing Scaleway secret key" in result.stderr, (
        f"expected 'missing Scaleway secret key' in stderr; got: {result.stderr!r}"
    )
