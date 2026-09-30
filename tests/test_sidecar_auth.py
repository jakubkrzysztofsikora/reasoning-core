"""The enforcement credential is scoped to loopback score calls."""

from src.sidecar_auth import score_headers


def test_score_headers_only_include_token_for_loopback(monkeypatch):
    import src.sidecar_auth as sidecar_auth

    monkeypatch.setattr(sidecar_auth.sys, "platform", "linux")
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", "test-token")
    assert score_headers("http://127.0.0.1:8877/score") == {
        "Authorization": "Bearer test-token",
    }
    assert score_headers("http://example.com/score") == {}


def test_score_headers_uses_null_account_keychain_item_when_user_is_unset(monkeypatch):
    from types import SimpleNamespace

    import src.sidecar_auth as sidecar_auth

    monkeypatch.delenv("RC_ENFORCEMENT_TOKEN", raising=False)
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setattr(sidecar_auth.sys, "platform", "darwin")
    monkeypatch.setattr(sidecar_auth, "keychain_account", lambda: "operator")
    commands = []

    def find_password(command, **_kwargs):
        commands.append(command)
        if "-a" in command:
            return SimpleNamespace(returncode=44, stdout="")
        return SimpleNamespace(returncode=0, stdout="keychain-token\n")

    monkeypatch.setattr(sidecar_auth.subprocess, "run", find_password)

    assert sidecar_auth.score_headers("http://127.0.0.1:8765/score") == {
        "Authorization": "Bearer keychain-token",
    }
    assert len(commands) == 2
    assert commands[0][-3:] == ["-a", "operator", "-w"]
    assert commands[1][-1] == "-w"


def test_score_headers_falls_back_to_null_account_after_named_lookup(monkeypatch):
    from types import SimpleNamespace

    import src.sidecar_auth as sidecar_auth

    monkeypatch.delenv("RC_ENFORCEMENT_TOKEN", raising=False)
    monkeypatch.setenv("USER", "operator")
    monkeypatch.setattr(sidecar_auth.sys, "platform", "darwin")
    monkeypatch.setattr(sidecar_auth, "keychain_account", lambda: "operator")
    commands = []

    def find_password(command, **_kwargs):
        commands.append(command)
        if "-a" in command:
            return SimpleNamespace(returncode=44, stdout="")
        return SimpleNamespace(returncode=0, stdout="keychain-token\n")

    monkeypatch.setattr(sidecar_auth.subprocess, "run", find_password)

    assert sidecar_auth.score_headers("http://127.0.0.1:8765/score") == {
        "Authorization": "Bearer keychain-token",
    }
    assert len(commands) == 2
    assert commands[0][-3:] == ["-a", "operator", "-w"]
    assert commands[1][-1] == "-w"


def test_score_headers_uses_shared_operator_token_resolver(monkeypatch):
    import src.sidecar_auth as sidecar_auth

    monkeypatch.delenv("RC_ENFORCEMENT_TOKEN", raising=False)
    monkeypatch.setattr(sidecar_auth.sys, "platform", "linux")
    monkeypatch.setattr(sidecar_auth, "operator_token", lambda: "resolved-token", raising=False)

    assert sidecar_auth.score_headers("http://127.0.0.1:8765/score") == {
        "Authorization": "Bearer resolved-token",
    }
