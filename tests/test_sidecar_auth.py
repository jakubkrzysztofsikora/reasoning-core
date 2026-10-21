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
    monkeypatch.setattr(sidecar_auth, "keychain_account", lambda _timeout_s: "operator")
    commands = []

    def find_password(command, **_kwargs):
        commands.append(command)
        account = command[command.index("-a") + 1] if "-a" in command else None
        if account == "operator":
            return SimpleNamespace(returncode=44, stdout="")
        assert account == ""
        return SimpleNamespace(returncode=0, stdout="keychain-token\n")

    monkeypatch.setattr(sidecar_auth.subprocess, "run", find_password)

    assert sidecar_auth.score_headers("http://127.0.0.1:8765/score") == {
        "Authorization": "Bearer keychain-token",
    }
    assert len(commands) == 2
    assert commands[0][-3:] == ["-a", "operator", "-w"]
    assert commands[1][-3:] == ["-a", "", "-w"]


def test_score_headers_falls_back_to_null_account_after_named_lookup(monkeypatch):
    from types import SimpleNamespace

    import src.sidecar_auth as sidecar_auth

    monkeypatch.delenv("RC_ENFORCEMENT_TOKEN", raising=False)
    monkeypatch.setenv("USER", "operator")
    monkeypatch.setattr(sidecar_auth.sys, "platform", "darwin")
    monkeypatch.setattr(sidecar_auth, "keychain_account", lambda _timeout_s: "operator")
    commands = []

    def find_password(command, **_kwargs):
        commands.append(command)
        account = command[command.index("-a") + 1] if "-a" in command else None
        if account == "operator":
            return SimpleNamespace(returncode=44, stdout="")
        assert account == ""
        return SimpleNamespace(returncode=0, stdout="keychain-token\n")

    monkeypatch.setattr(sidecar_auth.subprocess, "run", find_password)

    assert sidecar_auth.score_headers("http://127.0.0.1:8765/score") == {
        "Authorization": "Bearer keychain-token",
    }
    assert len(commands) == 2
    assert commands[0][-3:] == ["-a", "operator", "-w"]
    assert commands[1][-3:] == ["-a", "", "-w"]


def test_keychain_lookup_subprocesses_share_one_deadline(monkeypatch):
    from types import SimpleNamespace

    import src.sidecar_auth as sidecar_auth

    monkeypatch.delenv("RC_ENFORCEMENT_TOKEN", raising=False)
    monkeypatch.setattr(sidecar_auth.sys, "platform", "darwin")
    monkeypatch.setattr(sidecar_auth, "keychain_account", lambda _timeout_s: "operator")
    now = [10.0]

    class _Clock:
        @staticmethod
        def monotonic():
            return now[0]

    monkeypatch.setattr(sidecar_auth, "time", _Clock, raising=False)
    timeouts = []

    def find_password(command, *, timeout, **_kwargs):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            now[0] += 0.8
            return SimpleNamespace(returncode=44, stdout="")
        assert command[-3:] == ["-a", "", "-w"]
        return SimpleNamespace(returncode=0, stdout="keychain-token\n")

    monkeypatch.setattr(sidecar_auth.subprocess, "run", find_password)

    assert sidecar_auth.keychain_token(timeout_s=1.0) == "keychain-token"
    assert timeouts[0] == 1.0
    assert 0 < timeouts[1] <= 0.2


def test_default_keychain_budget_allows_normal_slow_keychain(monkeypatch):
    from types import SimpleNamespace

    import src.sidecar_auth as sidecar_auth

    monkeypatch.delenv("RC_ENFORCEMENT_TOKEN", raising=False)
    monkeypatch.setattr(sidecar_auth.sys, "platform", "darwin")
    monkeypatch.setattr(sidecar_auth, "keychain_account", lambda _timeout_s: "operator")
    now = [10.0]
    monkeypatch.setattr(sidecar_auth.time, "monotonic", lambda: now[0])
    timeouts = []

    def find_password(_command, *, timeout, **_kwargs):
        timeouts.append(timeout)
        now[0] += 1.2
        return SimpleNamespace(returncode=0, stdout="keychain-token\n")

    monkeypatch.setattr(sidecar_auth.subprocess, "run", find_password)

    assert sidecar_auth.keychain_token() == "keychain-token"
    assert timeouts[0] >= 1.2


def test_keychain_account_timeout_is_bounded_and_reuses_lookup_worker(monkeypatch):
    import threading
    import time as wall_time

    import src.sidecar_auth as sidecar_auth

    monkeypatch.setattr(sidecar_auth.sys, "platform", "darwin")
    monkeypatch.setattr(sidecar_auth, "_ACCOUNT_CACHE", "")
    monkeypatch.setattr(sidecar_auth, "_ACCOUNT_LOOKUP_THREAD", None)
    monkeypatch.setenv("LOGNAME", "stale-launchd-name")
    monkeypatch.delenv("USER", raising=False)
    lookup_started = threading.Event()
    release_lookup = threading.Event()
    lookup_calls = []

    def slow_nss_lookup():
        lookup_calls.append(True)
        lookup_started.set()
        release_lookup.wait(0.3)
        return "canonical-account"

    monkeypatch.setattr(sidecar_auth, "_lookup_macos_account", slow_nss_lookup)
    started = wall_time.monotonic()
    accounts = [sidecar_auth.keychain_account(timeout_s=0.02) for _ in range(6)]
    elapsed = wall_time.monotonic() - started

    assert lookup_started.wait(1.0)
    worker = sidecar_auth._ACCOUNT_LOOKUP_THREAD
    release_lookup.set()
    worker.join(1.0)

    assert accounts == [""] * 6
    assert lookup_calls == [True]
    assert elapsed < 0.2
    assert sidecar_auth.keychain_account() == "canonical-account"


def test_keychain_account_uses_caller_budget_above_250ms(monkeypatch):
    import threading

    import src.sidecar_auth as sidecar_auth

    monkeypatch.setattr(sidecar_auth.sys, "platform", "darwin")
    monkeypatch.setattr(sidecar_auth, "_ACCOUNT_CACHE", "")
    monkeypatch.setattr(sidecar_auth, "_ACCOUNT_LOOKUP_THREAD", None)
    monkeypatch.delenv("LOGNAME", raising=False)
    monkeypatch.delenv("USER", raising=False)
    lookup_started = threading.Event()

    def delayed_lookup():
        lookup_started.set()
        threading.Event().wait(0.3)
        return "canonical-account"

    monkeypatch.setattr(sidecar_auth, "_lookup_macos_account", delayed_lookup)

    assert sidecar_auth.keychain_account(timeout_s=0.6) == "canonical-account"
    assert lookup_started.is_set()


def test_keychain_account_does_not_trust_environment_after_lookup_failure(monkeypatch):
    import src.sidecar_auth as sidecar_auth

    monkeypatch.setattr(sidecar_auth.sys, "platform", "darwin")
    monkeypatch.setattr(sidecar_auth, "_ACCOUNT_CACHE", "")
    monkeypatch.setattr(sidecar_auth, "_ACCOUNT_LOOKUP_THREAD", None)
    monkeypatch.setenv("LOGNAME", "previous-account")
    monkeypatch.setenv("USER", "another-account")
    monkeypatch.setattr(
        sidecar_auth,
        "_lookup_macos_account",
        lambda: (_ for _ in ()).throw(KeyError("uid not found")),
    )

    assert sidecar_auth.keychain_account() == ""


def test_keychain_account_override_supports_explicit_migration(monkeypatch):
    import src.sidecar_auth as sidecar_auth

    monkeypatch.setattr(sidecar_auth.sys, "platform", "darwin")
    monkeypatch.setenv("RC_ENFORCEMENT_KEYCHAIN_ACCOUNT", "previous-account")
    monkeypatch.setattr(
        sidecar_auth, "_lookup_macos_account", lambda: (_ for _ in ()).throw(AssertionError())
    )

    assert sidecar_auth.keychain_account(timeout_s=0.1) == "previous-account"


def test_score_headers_uses_shared_operator_token_resolver(monkeypatch):
    import src.sidecar_auth as sidecar_auth

    monkeypatch.delenv("RC_ENFORCEMENT_TOKEN", raising=False)
    monkeypatch.setattr(sidecar_auth.sys, "platform", "linux")
    monkeypatch.setattr(
        sidecar_auth, "operator_token", lambda _timeout_s: "resolved-token", raising=False
    )

    assert sidecar_auth.score_headers("http://127.0.0.1:8765/score") == {
        "Authorization": "Bearer resolved-token",
    }


def test_operator_token_falls_back_to_file_when_keychain_empty(monkeypatch, tmp_path):
    import src.sidecar_auth as sidecar_auth

    monkeypatch.delenv("RC_ENFORCEMENT_TOKEN", raising=False)
    monkeypatch.setattr(sidecar_auth, "keychain_token", lambda _timeout_s=5.0: "")
    token_dir = tmp_path / ".config" / "reasoning-core"
    token_dir.mkdir(parents=True)
    token_file = token_dir / "token"
    token_file.write_text("file-stored-token\n")

    monkeypatch.setattr(sidecar_auth.Path, "home", lambda: tmp_path)
    assert sidecar_auth.operator_token() == "file-stored-token"

