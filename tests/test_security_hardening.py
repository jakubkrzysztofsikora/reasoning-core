"""Security regression tests guarding the three hardenings applied to
mcp_gate / mcp_reasoner (S2_URL allowlist), gen_client (RC_GEN_URL Bearer
scoping + redirect block), and ssm_backbone (S2_SSM_CHECKPOINT allowlist).

These tests pin the security invariant *and* the legitimate functionality:
each module must still work end-to-end on its default loopback / default
checkpoint config.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Shared httpx fakes (mirrors test_mcp_reasoner.py patterns)
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status_code: int = 200, payload: Optional[Dict[str, Any]] = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> Dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, *, captured: List[Dict[str, Any]], response: _FakeResp, **kwargs):
        self._captured = captured
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, **kwargs):
        self._captured.append({"url": url, "json": json})
        return self._response


def _install_httpx_stub(monkeypatch, module, payload: Optional[Dict[str, Any]] = None):
    captured: List[Dict[str, Any]] = []
    resp = _FakeResp(200, payload or {"regression_detected": False, "human_summary": "ok"})

    def _factory(*args, **kwargs):
        return _FakeClient(captured=captured, response=resp, **kwargs)

    monkeypatch.setattr(module.httpx, "Client", _factory)
    return captured


# ---------------------------------------------------------------------------
# #2 — S2_URL allowlist (mcp_gate + mcp_reasoner)
# ---------------------------------------------------------------------------


def test_mcp_gate_allows_default_loopback(monkeypatch, tmp_path):
    """Backward-compat: default 127.0.0.1 sidecar still works."""
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path))
    monkeypatch.delenv("S2_URL", raising=False)
    monkeypatch.delenv("S2_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("S2_FAIL_CLOSED", raising=False)
    from src import mcp_gate

    captured = _install_httpx_stub(monkeypatch, mcp_gate)
    out = mcp_gate.gate_edit("/x.py", "before", "after")
    assert out["decision"] == "allow"
    assert captured and captured[0]["url"] == "http://127.0.0.1:8765/score"


def test_mcp_gate_allows_alternate_loopback_port(monkeypatch, tmp_path):
    """Operator running sidecar on alt port (still loopback) must work."""
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path))
    monkeypatch.setenv("S2_URL", "http://127.0.0.1:9999")
    monkeypatch.delenv("S2_FAIL_CLOSED", raising=False)
    from src import mcp_gate

    captured = _install_httpx_stub(monkeypatch, mcp_gate)
    out = mcp_gate.gate_edit("/x.py", "before", "after")
    assert out["decision"] == "allow"
    assert captured[0]["url"] == "http://127.0.0.1:9999/score"


def test_mcp_gate_allows_localhost_hostname(monkeypatch, tmp_path):
    """`localhost` is treated as loopback."""
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path))
    monkeypatch.setenv("S2_URL", "http://localhost:8765")
    monkeypatch.delenv("S2_FAIL_CLOSED", raising=False)
    from src import mcp_gate

    captured = _install_httpx_stub(monkeypatch, mcp_gate)
    out = mcp_gate.gate_edit("/x.py", "before", "after")
    assert out["decision"] == "allow"
    assert captured[0]["url"].startswith("http://localhost:")


def test_mcp_gate_rejects_remote_url(monkeypatch, tmp_path):
    """SECURITY: attacker-controlled S2_URL must not receive source contents."""
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path))
    monkeypatch.setenv("S2_URL", "https://attacker.tld")
    monkeypatch.delenv("S2_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("S2_FAIL_CLOSED", raising=False)
    from src import mcp_gate

    captured = _install_httpx_stub(monkeypatch, mcp_gate)
    out = mcp_gate.gate_edit("/x.py", "secret_source_before", "secret_source_after")
    # Hard security invariant: no exfil occurred.
    assert captured == [], "source contents must NOT be sent to attacker URL"
    assert out.get("sidecar_unavailable") is True


def test_mcp_gate_rejects_non_http_scheme(monkeypatch, tmp_path):
    """file:// / gopher:// schemes must not route the Bearer-bearing payload."""
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path))
    monkeypatch.setenv("S2_URL", "file:///etc/passwd")
    monkeypatch.delenv("S2_FAIL_CLOSED", raising=False)
    from src import mcp_gate

    captured = _install_httpx_stub(monkeypatch, mcp_gate)
    out = mcp_gate.gate_edit("/x.py", "b", "a")
    assert captured == []
    assert out.get("sidecar_unavailable") is True


def test_mcp_gate_remote_allowed_with_explicit_opt_in(monkeypatch, tmp_path):
    """S2_ALLOW_REMOTE=1 escape hatch for legitimate remote-sidecar topologies."""
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path))
    monkeypatch.setenv("S2_URL", "https://sidecar.internal.example.com")
    monkeypatch.setenv("S2_ALLOW_REMOTE", "1")
    monkeypatch.delenv("S2_FAIL_CLOSED", raising=False)
    from src import mcp_gate

    captured = _install_httpx_stub(monkeypatch, mcp_gate)
    out = mcp_gate.gate_edit("/x.py", "b", "a")
    assert out["decision"] == "allow"
    assert captured and "sidecar.internal.example.com" in captured[0]["url"]


def test_mcp_reasoner_rejects_remote_url(monkeypatch):
    """SECURITY: same guard on the reason_over_edit bridge."""
    monkeypatch.setenv("S2_URL", "https://attacker.tld")
    monkeypatch.delenv("S2_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("S2_FAIL_CLOSED", raising=False)
    if "src.mcp_reasoner" in sys.modules:
        del sys.modules["src.mcp_reasoner"]
    pytest.importorskip("mcp.server.fastmcp")
    from src import mcp_reasoner

    captured = _install_httpx_stub(
        monkeypatch, mcp_reasoner,
        payload={"regression_detected": False, "human_summary": "ok"},
    )
    # Resolve the underlying callable — FastMCP may wrap.
    fn = mcp_reasoner.reason_over_edit
    if not hasattr(fn, "__call__") or hasattr(fn, "fn"):
        fn = getattr(fn, "fn", fn)
    out = fn("/tmp/foo.py", "def f(): pass", "edit")
    assert captured == []
    assert out["regression_detected"] in (False, True)  # contract: always dict
    assert out.get("degraded") is True


def test_mcp_reasoner_default_loopback_still_works(monkeypatch):
    """Backward-compat for the bridge's default config."""
    monkeypatch.delenv("S2_URL", raising=False)
    monkeypatch.delenv("S2_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("S2_FAIL_CLOSED", raising=False)
    if "src.mcp_reasoner" in sys.modules:
        del sys.modules["src.mcp_reasoner"]
    pytest.importorskip("mcp.server.fastmcp")
    from src import mcp_reasoner

    impact = {
        "architectural_impact_score": 0.9,
        "coherence_delta": 0.1,
        "risk_vector": [0.0] * 8,
        "risk_labels": ["a"] * 8,
        "regression_detected": False,
        "human_summary": "ok",
    }
    captured = _install_httpx_stub(monkeypatch, mcp_reasoner, payload=impact)
    fn = mcp_reasoner.reason_over_edit
    fn = getattr(fn, "fn", fn)
    out = fn("/tmp/foo.py", "def f(): pass", "edit")
    assert captured and captured[0]["url"] == "http://127.0.0.1:8765/score"
    assert out["regression_detected"] is False


# ---------------------------------------------------------------------------
# #6 — RC_GEN_URL Bearer scoping (gen_client)
# ---------------------------------------------------------------------------


@pytest.fixture
def gen_client_module():
    """Load gen_client fresh."""
    spec = importlib.util.spec_from_file_location(
        "_gc_sec_test", REPO_ROOT / "src" / "gen_client.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_bearer_safe_for_loopback(gen_client_module):
    gc = gen_client_module
    assert gc._bearer_safe_for("http://127.0.0.1:8766/v1/chat/completions") is True
    assert gc._bearer_safe_for("http://localhost:8766/v1") is True
    assert gc._bearer_safe_for("http://[::1]:8766/v1") is True


def test_bearer_unsafe_for_remote_without_allowlist(gen_client_module, monkeypatch):
    monkeypatch.delenv("RC_GEN_ALLOWED_HOSTS", raising=False)
    gc = gen_client_module
    assert gc._bearer_safe_for("https://attacker.tld/v1") is False
    assert gc._bearer_safe_for("https://api.scaleway.ai/v1") is False


def test_bearer_safe_for_allowlisted_remote(gen_client_module, monkeypatch):
    """Hosted Scaleway use case still works when operator opts in."""
    monkeypatch.setenv("RC_GEN_ALLOWED_HOSTS", "api.scaleway.ai,api.example.com")
    gc = gen_client_module
    assert gc._bearer_safe_for("https://api.scaleway.ai/v1/chat/completions") is True
    assert gc._bearer_safe_for("https://api.example.com/v1") is True
    # Even on the allowlist, plain http is rejected (no TLS = no auth).
    assert gc._bearer_safe_for("http://api.scaleway.ai/v1") is False
    # Out-of-list host still rejected.
    assert gc._bearer_safe_for("https://attacker.tld/v1") is False


def test_post_attaches_bearer_for_loopback(gen_client_module, monkeypatch):
    """Default loopback critic still receives the Bearer (no regression)."""
    gc = gen_client_module
    monkeypatch.setenv("RC_GEN_API_KEY", "sk-test-loopback-key")
    captured: Dict[str, Any] = {}

    class _Ctx:
        def __init__(self, status: int = 200, body: bytes = b'{"ok":true}'):
            self._status = status
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._body

    def _fake_open(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        return _Ctx()

    monkeypatch.setattr(gc._NO_REDIRECT_OPENER, "open", _fake_open)
    res = gc._post("http://127.0.0.1:8766/v1/chat/completions", {"x": 1}, 1000)
    assert res == {"ok": True}
    auth = {k.lower(): v for k, v in captured["headers"].items()}.get("authorization")
    assert auth == "Bearer sk-test-loopback-key"


def test_post_strips_bearer_for_attacker_url(gen_client_module, monkeypatch):
    """SECURITY: attacker-controlled RC_GEN_URL receives NO Authorization."""
    gc = gen_client_module
    monkeypatch.setenv("RC_GEN_API_KEY", "sk-test-very-secret")
    monkeypatch.delenv("RC_GEN_ALLOWED_HOSTS", raising=False)
    captured: Dict[str, Any] = {}

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok":false}'

    def _fake_open(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _Ctx()

    monkeypatch.setattr(gc._NO_REDIRECT_OPENER, "open", _fake_open)
    gc._post("https://attacker.tld/v1/chat/completions", {"x": 1}, 1000)
    auth_keys = [k for k in captured["headers"] if k.lower() == "authorization"]
    assert auth_keys == [], (
        f"Authorization header MUST NOT be sent to non-allowlisted remote host; "
        f"got headers: {captured['headers']}"
    )


def test_post_attaches_bearer_for_allowlisted_scaleway(gen_client_module, monkeypatch):
    """Hosted critic on Scaleway still works via explicit allowlist."""
    gc = gen_client_module
    monkeypatch.setenv("RC_GEN_API_KEY", "sk-scw-key")
    monkeypatch.setenv("RC_GEN_ALLOWED_HOSTS", "api.scaleway.ai")
    captured: Dict[str, Any] = {}

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok":true}'

    def _fake_open(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _Ctx()

    monkeypatch.setattr(gc._NO_REDIRECT_OPENER, "open", _fake_open)
    gc._post("https://api.scaleway.ai/v1/chat/completions", {"x": 1}, 1000)
    auth = {k.lower(): v for k, v in captured["headers"].items()}.get("authorization")
    assert auth == "Bearer sk-scw-key"


def test_redirect_handler_returns_none(gen_client_module):
    """Redirects MUST be refused so urllib never re-sends Authorization
    to a 30x Location host. urllib does not strip auth on redirect — see
    CPython HTTPRedirectHandler — so we drop redirects entirely.
    """
    gc = gen_client_module
    handler = gc._NoRedirectHandler()
    out = handler.redirect_request(
        req=None, fp=None, code=302, msg="Found", headers={}, newurl="https://attacker.tld",
    )
    assert out is None


# ---------------------------------------------------------------------------
# #5 — S2_SSM_CHECKPOINT allowlist (ssm_backbone)
# ---------------------------------------------------------------------------


def test_resolve_checkpoint_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("S2_SSM_CHECKPOINT", raising=False)
    from src import ssm_backbone

    assert ssm_backbone._resolve_checkpoint(None) == ssm_backbone.DEFAULT_CHECKPOINT


def test_resolve_checkpoint_allows_known_fallback(monkeypatch):
    monkeypatch.setenv("S2_SSM_CHECKPOINT", "state-spaces/mamba2-130m")
    from src import ssm_backbone

    assert ssm_backbone._resolve_checkpoint(None) == "state-spaces/mamba2-130m"


def test_resolve_checkpoint_allows_tiny_gpt2_fallback(monkeypatch):
    monkeypatch.setenv("S2_SSM_CHECKPOINT", "sshleifer/tiny-gpt2")
    from src import ssm_backbone

    assert ssm_backbone._resolve_checkpoint(None) == "sshleifer/tiny-gpt2"


def test_resolve_checkpoint_rejects_arbitrary_repo(monkeypatch):
    """SECURITY: env-controlled checkpoint outside the allowlist must raise.
    trust_remote_code=True against an arbitrary HF repo == RCE on sidecar boot.
    """
    monkeypatch.setenv("S2_SSM_CHECKPOINT", "evil-org/typosquat-mamba")
    from src import ssm_backbone

    with pytest.raises(ssm_backbone.BackboneUnavailableError) as exc:
        ssm_backbone._resolve_checkpoint(None)
    assert "allowlist" in str(exc.value).lower()


def test_resolve_checkpoint_rejects_caller_supplied_evil(monkeypatch):
    """Even an explicit caller arg (non-env) is bounded by the allowlist."""
    monkeypatch.delenv("S2_SSM_CHECKPOINT", raising=False)
    from src import ssm_backbone

    with pytest.raises(ssm_backbone.BackboneUnavailableError):
        ssm_backbone._resolve_checkpoint("attacker-org/payload")


def test_resolve_checkpoint_allows_caller_supplied_known(monkeypatch):
    monkeypatch.delenv("S2_SSM_CHECKPOINT", raising=False)
    from src import ssm_backbone

    out = ssm_backbone._resolve_checkpoint("state-spaces/mamba2-130m")
    assert out == "state-spaces/mamba2-130m"


# ---------------------------------------------------------------------------
# #5 follow-up — SHA-pin revisions (ssm_backbone)
# ---------------------------------------------------------------------------


def test_pinned_revisions_are_40char_hex():
    """Every pinned revision must itself be a valid SHA — no accidental
    branch/tag refs in the table."""
    from src import ssm_backbone

    for repo, sha in ssm_backbone._PINNED_REVISIONS.items():
        assert ssm_backbone._SHA_RE.match(sha), (
            f"pinned revision for {repo} is not a 40-char hex SHA: {sha!r}"
        )


def test_resolve_revision_returns_pin_for_default(monkeypatch):
    """Default checkpoint resolves to its pinned SHA."""
    monkeypatch.delenv("RC_STATE_SPACES_MAMBA_130M_HF_REVISION", raising=False)
    from src import ssm_backbone

    out = ssm_backbone._resolve_revision(ssm_backbone.DEFAULT_CHECKPOINT)
    assert out == ssm_backbone._PINNED_REVISIONS[ssm_backbone.DEFAULT_CHECKPOINT]
    assert ssm_backbone._SHA_RE.match(out)


def test_resolve_revision_returns_pin_for_each_fallback(monkeypatch):
    from src import ssm_backbone

    for ckpt in ssm_backbone.FALLBACK_CHECKPOINTS:
        monkeypatch.delenv(ssm_backbone._revision_env_key(ckpt), raising=False)
        assert ssm_backbone._resolve_revision(ckpt) == ssm_backbone._PINNED_REVISIONS[ckpt]


def test_resolve_revision_rejects_branch_name_via_env(monkeypatch):
    """SECURITY: branch names like 'main' are mutable refs — must be rejected."""
    from src import ssm_backbone

    env_key = ssm_backbone._revision_env_key(ssm_backbone.DEFAULT_CHECKPOINT)
    monkeypatch.setenv(env_key, "main")
    with pytest.raises(ssm_backbone.BackboneUnavailableError) as exc:
        ssm_backbone._resolve_revision(ssm_backbone.DEFAULT_CHECKPOINT)
    assert "40-char" in str(exc.value)


def test_resolve_revision_rejects_short_sha(monkeypatch):
    """A 7-char abbreviated SHA is not immutable enough — full 40 required."""
    from src import ssm_backbone

    env_key = ssm_backbone._revision_env_key(ssm_backbone.DEFAULT_CHECKPOINT)
    monkeypatch.setenv(env_key, "1e76775")
    with pytest.raises(ssm_backbone.BackboneUnavailableError):
        ssm_backbone._resolve_revision(ssm_backbone.DEFAULT_CHECKPOINT)


def test_resolve_revision_rejects_tag_via_env(monkeypatch):
    from src import ssm_backbone

    env_key = ssm_backbone._revision_env_key(ssm_backbone.DEFAULT_CHECKPOINT)
    monkeypatch.setenv(env_key, "v1.0.0")
    with pytest.raises(ssm_backbone.BackboneUnavailableError):
        ssm_backbone._resolve_revision(ssm_backbone.DEFAULT_CHECKPOINT)


def test_resolve_revision_accepts_valid_sha_via_env(monkeypatch):
    """Operator override with a real SHA is honored (e.g. updating the pin)."""
    from src import ssm_backbone

    env_key = ssm_backbone._revision_env_key(ssm_backbone.DEFAULT_CHECKPOINT)
    new_sha = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setenv(env_key, new_sha)
    assert ssm_backbone._resolve_revision(ssm_backbone.DEFAULT_CHECKPOINT) == new_sha


def test_try_load_passes_revision_and_no_trust_remote_code(monkeypatch):
    """SECURITY contract: from_pretrained must receive revision=<sha> AND
    trust_remote_code=False (or absent → False default).
    """
    captured: Dict[str, Any] = {}

    class _FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(ckpt, **kwargs):
            captured["tok_ckpt"] = ckpt
            captured["tok_kwargs"] = kwargs
            tok = type("T", (), {})()
            tok.pad_token = None
            tok.eos_token = "<eos>"
            return tok

    class _FakeAutoModel:
        @staticmethod
        def from_pretrained(ckpt, **kwargs):
            captured["mod_ckpt"] = ckpt
            captured["mod_kwargs"] = kwargs
            cfg = type("C", (), {"hidden_size": 128})()
            mod = type("M", (), {})()
            mod.config = cfg
            mod.eval = lambda: None
            mod.to = lambda d: None
            mod.parameters = lambda: iter([])
            return mod

    fake = types.ModuleType("transformers")
    fake.AutoTokenizer = _FakeAutoTokenizer
    fake.AutoModel = _FakeAutoModel
    monkeypatch.setitem(sys.modules, "transformers", fake)

    # _try_load imports torch even though the stubbed model does not need it.
    class _NoGrad:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    class _FakeTensor:
        def __init__(self, data):
            self._data = data
        def to(self, _device):
            return self
        def dim(self):
            return 1

    fake_torch = types.ModuleType("torch")
    fake_torch.long = int
    fake_torch.float32 = object()
    fake_torch.no_grad = _NoGrad
    fake_torch.manual_seed = lambda _seed: None
    fake_torch.tensor = lambda data, dtype=None: _FakeTensor(data)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    from src import ssm_backbone

    handle = ssm_backbone._try_load(ssm_backbone.DEFAULT_CHECKPOINT, "cpu")
    assert handle is not None, "load failed even with stubbed transformers"

    expected_sha = ssm_backbone._PINNED_REVISIONS[ssm_backbone.DEFAULT_CHECKPOINT]
    for kwargs in (captured["tok_kwargs"], captured["mod_kwargs"]):
        assert kwargs.get("revision") == expected_sha, (
            f"from_pretrained must receive revision={expected_sha}, got {kwargs}"
        )
        assert kwargs.get("trust_remote_code", False) is False, (
            f"trust_remote_code must be False; got {kwargs}"
        )
