"""The enforcement credential is scoped to loopback score calls."""

from src.sidecar_auth import score_headers


def test_score_headers_only_include_token_for_loopback(monkeypatch):
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", "test-token")
    assert score_headers("http://127.0.0.1:8877/score") == {
        "Authorization": "Bearer test-token",
    }
    assert score_headers("http://example.com/score") == {}
