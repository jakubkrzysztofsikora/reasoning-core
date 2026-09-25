"""Regression: /score FastAPI handler runs score_change off the event loop.

Audit-hostile/2026-09-19-fixes: the previous async def score() handler
called score_change() directly on the asyncio event loop, freezing it for
2-4.5s during a Mamba CPU forward pass. The supervisor's /health probe
then timed out and killed the sidecar. After the fix, score_change runs
inside asyncio.to_thread, so /health stays responsive during a /score call.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from src import s2_core

_AUTH_TOKEN = "ci-test-token-for-baseline-offload-test-minimum-32-chars!!!"
_AUTH_HEADER = {"Authorization": f"Bearer {_AUTH_TOKEN}"}


@pytest.fixture(scope="module")
def app():
    return s2_core.create_app()


@pytest.mark.asyncio
async def test_health_responds_while_score_in_flight(app):
    """Fire /health while a 2s /score is in flight; health must respond promptly."""
    from fastapi.testclient import TestClient

    # Replace score_change with a slow stub to simulate inference latency.
    original = s2_core.score_change
    started = threading.Event()
    release = threading.Event()

    def slow_score_change(*args, **kwargs):
        started.set()
        release.wait(timeout=5.0)
        return original("a.py", "print('a')\n", "print('b')\n", session_id="test")

    s2_core.score_change = slow_score_change
    try:
        client = TestClient(app)
        # Start /score in a background thread (TestClient is sync).
        score_result = {}
        score_thread_done = threading.Event()

        def fire_score():
            try:
                score_result["response"] = client.post(
                    "/score",
                    json={
                        "path": "a.py",
                        "before_src": "print('a')\n",
                        "after_src": "print('b')\n",
                        "session_id": "test",
                    },
                    headers={"Authorization": "Bearer test-token-for-baseline-offload-test-minimum-32-chars!!!"},
                )
            finally:
                score_thread_done.set()

        thread = threading.Thread(target=fire_score)
        thread.start()
        # Wait until score_change has actually started.
        assert started.wait(timeout=2.0), "score_change was never called"
        # Now fire /health; it must respond while the slow score_change is
        # still holding the worker thread.
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        health_resp = await loop.run_in_executor(None, client.get, "/health")
        elapsed = loop.time() - t0
        assert health_resp.status_code == 200
        assert elapsed < 1.0, f"/health took {elapsed:.3f}s while /score was in flight"
    finally:
        release.set()
        s2_core.score_change = original
