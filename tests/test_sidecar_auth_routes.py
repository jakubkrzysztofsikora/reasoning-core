"""Credential lookups must not block the sidecar event loop."""

import asyncio
import concurrent.futures
import threading

from starlette.requests import Request

from src import s2_core


def _request(method: str, path: str) -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer test-token")],
        "server": ("127.0.0.1", 8765),
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope, receive)


def test_auth_routes_resolve_credentials_off_event_loop_thread(monkeypatch):
    app = s2_core.create_app()
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
    event_loop_thread = threading.get_ident()
    lookup_threads = []

    def resolve_token():
        lookup_threads.append(threading.get_ident())
        return "test-token"

    monkeypatch.delenv("RC_ENFORCEMENT_TOKEN", raising=False)
    monkeypatch.setattr(s2_core, "operator_token", resolve_token)

    async def exercise_routes():
        auth_result = await endpoints["/auth"](_request("GET", "/auth"))
        score_result = await endpoints["/score"](_request("POST", "/score"))
        baseline_result = await endpoints["/baseline"](_request("POST", "/baseline"))
        return auth_result, score_result.status_code, baseline_result.status_code

    auth_result, score_status, baseline_status = asyncio.run(exercise_routes())

    assert auth_result == {"status": "ok"}
    assert score_status == 400  # Empty JSON is rejected before scoring.
    assert baseline_status == 400  # Empty JSON is rejected before baseline work.
    assert len(lookup_threads) == 3
    assert all(thread_id != event_loop_thread for thread_id in lookup_threads)


def test_auth_env_token_bypasses_a_busy_default_executor(monkeypatch):
    app = s2_core.create_app()
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", "test-token")

    async def exercise():
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        asyncio.get_running_loop().set_default_executor(pool)
        occupied = threading.Event()
        release = threading.Event()

        def block_score_worker():
            occupied.set()
            release.wait(2.0)

        worker = asyncio.get_running_loop().run_in_executor(None, block_score_worker)
        assert occupied.wait(1.0)
        try:
            result = await asyncio.wait_for(
                endpoints["/auth"](_request("GET", "/auth")), timeout=0.2
            )
        finally:
            release.set()
        await worker
        return result

    assert asyncio.run(exercise()) == {"status": "ok"}


def test_concurrent_keychain_auth_requests_share_one_lookup(monkeypatch):
    app = s2_core.create_app()
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
    monkeypatch.delenv("RC_ENFORCEMENT_TOKEN", raising=False)
    started = threading.Event()
    release = threading.Event()
    lookups = []

    def resolve_token():
        lookups.append(True)
        started.set()
        release.wait(2.0)
        return "test-token"

    monkeypatch.setattr(s2_core, "operator_token", resolve_token)

    async def exercise():
        tasks = [
            asyncio.create_task(endpoints["/auth"](_request("GET", "/auth")))
            for _ in range(5)
        ]
        assert await asyncio.to_thread(started.wait, 1.0)
        await asyncio.sleep(0.05)
        release.set()
        return await asyncio.gather(*tasks)

    results = asyncio.run(exercise())

    assert results == [{"status": "ok"}] * 5
    assert lookups == [True]


def test_timed_out_keychain_lookup_can_resume_after_loop_shutdown(monkeypatch):
    monkeypatch.delenv("RC_ENFORCEMENT_TOKEN", raising=False)
    monkeypatch.setattr(s2_core, "KEYCHAIN_LOOKUP_BUDGET_S", 0.02)
    started = threading.Event()
    release = threading.Event()
    lookups = []

    def slow_token():
        lookups.append(True)
        started.set()
        release.wait(2.0)
        return "test-token"

    monkeypatch.setattr(s2_core, "operator_token", slow_token)
    app = s2_core.create_app()
    auth = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/auth")

    assert asyncio.run(auth(_request("GET", "/auth"))).status_code == 503
    assert started.is_set()
    assert asyncio.run(auth(_request("GET", "/auth"))).status_code == 503

    release.set()
    s2_core._AUTH_EXECUTOR.submit(lambda: None).result(timeout=1.0)
    monkeypatch.setattr(s2_core, "operator_token", lambda: "test-token")

    assert asyncio.run(auth(_request("GET", "/auth"))) == {"status": "ok"}
    assert lookups == [True]
