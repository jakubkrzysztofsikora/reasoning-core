"""The CLI status command checks authenticated sidecar readiness."""

from urllib import request as urllib_request

from src import rc_cli


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_sidecar_auth_status_reports_missing_token_without_network(monkeypatch):
    monkeypatch.setattr(rc_cli, "score_headers", lambda _url: {})
    calls = []
    monkeypatch.setattr(urllib_request, "urlopen", lambda *args, **kwargs: calls.append(args))

    assert rc_cli._sidecar_auth_status() == "missing_token"
    assert calls == []


def test_sidecar_auth_status_probes_auth_without_scoring(monkeypatch):
    seen = []

    class _Opener:
        def open(self, req, timeout):
            seen.append((req.full_url, req.get_header("Authorization"), req.get_method(), timeout, req.data))
            return _Response()

    monkeypatch.setattr(rc_cli, "score_headers", lambda _url: {"Authorization": "Bearer test-token"})
    monkeypatch.setattr(urllib_request, "build_opener", lambda *_handlers: _Opener())

    assert rc_cli._sidecar_auth_status() == "ok"
    assert seen[0][0] == "http://127.0.0.1:8765/auth"
    assert seen[0][1] == "Bearer test-token"
    assert seen[0][2:] == ("GET", 3, None)


def test_sidecar_auth_status_classifies_rejected_credentials(monkeypatch):
    import urllib.error

    monkeypatch.setattr(rc_cli, "score_headers", lambda _url: {"Authorization": "Bearer bad-token"})

    class _Opener:
        def open(self, _request, timeout):
            raise urllib.error.HTTPError("http://127.0.0.1:8765/auth", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib_request, "build_opener", lambda *_handlers: _Opener())

    assert rc_cli._sidecar_auth_status() == "rejected_token"


def test_sidecar_auth_status_classifies_auth_unavailable(monkeypatch):
    import urllib.error

    monkeypatch.setattr(rc_cli, "score_headers", lambda _url: {"Authorization": "Bearer test-token"})

    class _Opener:
        def open(self, _request, timeout):
            raise urllib.error.HTTPError("http://127.0.0.1:8765/auth", 503, "Unavailable", {}, None)

    monkeypatch.setattr(urllib_request, "build_opener", lambda *_handlers: _Opener())

    assert rc_cli._sidecar_auth_status() == "sidecar_auth_unavailable"


def test_sidecar_auth_status_rejects_redirects(monkeypatch):
    import urllib.error

    monkeypatch.setattr(rc_cli, "score_headers", lambda _url: {"Authorization": "Bearer secret"})
    handlers = []

    class _Opener:
        def open(self, _request, timeout):
            assert handlers[0]().redirect_request(None, None, 302, "Found", {}, "https://evil.example/") is None
            raise urllib.error.HTTPError("http://127.0.0.1:8765/auth", 302, "Found", {}, None)

    def build_opener(*items):
        handlers.extend(items)
        return _Opener()

    monkeypatch.setattr(urllib_request, "build_opener", build_opener)

    assert rc_cli._sidecar_auth_status() == "http_302"
    assert len(handlers) == 1
