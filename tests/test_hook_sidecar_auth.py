"""Score-hook authentication stays on the loopback sidecar boundary."""

from src.hooks import pre_edit_guard


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"regression_detected":false}'


def test_score_token_only_sent_to_loopback(monkeypatch):
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
