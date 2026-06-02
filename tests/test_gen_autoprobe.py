"""Auto-detect gen sidecar via /v1/models probe (audit 2026-06-02 follow-up)."""
from __future__ import annotations

import http.server
import socket
import socketserver
import threading
from contextlib import closing
from typing import Iterator, Optional

import pytest

from src import gen_client


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _StubModels:
    """/v1/models stub — answers 200 with a minimal model list."""

    def __init__(self) -> None:
        self.port = _free_port()
        self._server: Optional[socketserver.TCPServer] = None

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/chat/completions"

    def start(self) -> None:
        body = b'{"data":[{"id":"test-model"}]}'

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a, **kw):
                return

            def do_GET(self):
                if self.path.endswith("/v1/models"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
                else:
                    self.send_response(404)
                    self.end_headers()

        self._server = socketserver.TCPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()


@pytest.fixture
def stub_models() -> Iterator[_StubModels]:
    s = _StubModels()
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.fixture(autouse=True)
def _clear_autoprobe(monkeypatch):
    gen_client._reset_autoprobe_cache_for_tests()
    monkeypatch.delenv("RC_REASONER_BACKEND", raising=False)
    monkeypatch.delenv("RC_GEN_URL", raising=False)
    yield
    gen_client._reset_autoprobe_cache_for_tests()


def test_hard_optout_short_circuits(stub_models, monkeypatch):
    """RC_REASONER_BACKEND=off forces False even with a live sidecar."""
    monkeypatch.setenv("RC_GEN_URL", stub_models.url())
    monkeypatch.setenv("RC_REASONER_BACKEND", "off")
    assert gen_client._backend_active() is False


def test_explicit_optin_keeps_existing_behaviour(monkeypatch):
    monkeypatch.delenv("RC_GEN_URL", raising=False)
    monkeypatch.setenv("RC_REASONER_BACKEND", "mlx")
    # No sidecar running — explicit opt-in still returns True (matches old
    # contract; gen_client._post handles the actual network failure path).
    assert gen_client._backend_active() is True


def test_auto_probe_picks_up_live_sidecar(stub_models, monkeypatch):
    """Sidecar reachable → backend active without RC_REASONER_BACKEND set."""
    monkeypatch.setenv("RC_GEN_URL", stub_models.url())
    assert gen_client._backend_active() is True


def test_auto_probe_no_sidecar_returns_false(monkeypatch):
    """No process on the URL → False, and cached so we don't repeat the probe."""
    monkeypatch.setenv("RC_GEN_URL", f"http://127.0.0.1:1/v1/chat/completions")
    assert gen_client._backend_active() is False
    # Second call uses the cache — exercised by simply calling again.
    assert gen_client._backend_active() is False


def test_cache_per_url(stub_models, monkeypatch):
    """Flipping the URL invalidates the probe cache."""
    monkeypatch.setenv("RC_GEN_URL", f"http://127.0.0.1:1/v1/chat/completions")
    assert gen_client._backend_active() is False
    monkeypatch.setenv("RC_GEN_URL", stub_models.url())
    assert gen_client._backend_active() is True
