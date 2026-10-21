"""Score-hook authentication stays on the loopback sidecar boundary."""

import pytest

from src.hooks import pre_edit_guard


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"regression_detected":false}'


def test_score_token_only_sent_to_loopback(monkeypatch):
    import src.sidecar_auth as sidecar_auth

    monkeypatch.setattr(sidecar_auth.sys, "platform", "linux")
    seen = []

    class _Opener:
        def open(self, request, timeout):
            seen.append((request.full_url, request.get_header("Authorization")))
            return _Response()

    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", "test-token")
    monkeypatch.setattr(pre_edit_guard.urllib.request, "build_opener", lambda *_: _Opener())
    for endpoint in ("http://127.0.0.1:8877/score", "http://example.com/score"):
        monkeypatch.setattr(pre_edit_guard, "SCORE_ENDPOINT", endpoint)
        pre_edit_guard._post_score("sample.py", "x=1", "x=2")
    assert seen == [
        ("http://127.0.0.1:8877/score", "Bearer test-token"),
        ("http://example.com/score", None),
    ]


def test_keychain_time_consumes_the_score_request_deadline(monkeypatch):
    class _Clock:
        now = 100.0

        @classmethod
        def monotonic(cls):
            return cls.now

    observed_budgets = []

    def slow_auth_headers(_endpoint, *, timeout_s):
        observed_budgets.append(timeout_s)
        _Clock.now += 0.2
        return {}

    class _ForbiddenOpener:
        def open(self, *_args, **_kwargs):
            raise AssertionError("HTTP request started after its deadline")

    monkeypatch.setattr(pre_edit_guard, "time", _Clock)
    monkeypatch.setattr(pre_edit_guard, "_effective_score_timeout", lambda: 0.1)
    monkeypatch.setattr(pre_edit_guard, "_score_auth_headers", slow_auth_headers)
    monkeypatch.setattr(pre_edit_guard.urllib.request, "build_opener", lambda *_: _ForbiddenOpener())

    try:
        pre_edit_guard._post_score("sample.py", "x=1", "x=2")
    except pre_edit_guard.SidecarUnavailable as exc:
        assert str(exc) == "hard_cap_exceeded:100ms"
    else:
        raise AssertionError("credential lookup should exhaust the request budget")

    assert observed_budgets == pytest.approx([0.1])


def test_score_uses_remaining_default_budget_after_auth_lookup(monkeypatch):
    class _Clock:
        now = 100.0

        @classmethod
        def monotonic(cls):
            return cls.now

    seen = []

    def auth_headers(_endpoint, *, timeout_s):
        seen.append(("auth", timeout_s))
        _Clock.now += 0.9
        return {"Authorization": "Bearer test-token"}

    class _Opener:
        def open(self, request, timeout):
            seen.append(("http", request.get_header("Authorization"), timeout))
            _Clock.now += timeout
            return _Response()

    monkeypatch.setattr(pre_edit_guard, "time", _Clock)
    monkeypatch.setattr(pre_edit_guard, "_effective_score_timeout", lambda: 1.5)
    monkeypatch.setattr(pre_edit_guard, "_score_auth_headers", auth_headers)
    monkeypatch.setattr(pre_edit_guard.urllib.request, "build_opener", lambda *_: _Opener())

    report = pre_edit_guard._post_score("sample.py", "x=1", "x=2")

    assert report == {"regression_detected": False}
    assert seen[0] == ("auth", pytest.approx(1.5))
    assert seen[1][0:2] == ("http", "Bearer test-token")
    assert seen[1][2] == pytest.approx(0.6)
