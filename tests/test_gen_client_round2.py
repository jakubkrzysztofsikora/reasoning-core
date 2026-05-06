"""Round-2 P5 fixes for gen_client."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "gen_client", REPO_ROOT / "src" / "gen_client.py"
)
gc = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(gc)


def test_parse_yesno_explicit():
    assert gc._parse_yesno("YES") == 1
    assert gc._parse_yesno("NO") == 0


def test_parse_yesno_with_preamble():
    assert gc._parse_yesno("The claim is supported, YES") == 1
    assert gc._parse_yesno("Hmm. NO.") == 0


def test_parse_yesno_unparseable():
    assert gc._parse_yesno("") is None
    assert gc._parse_yesno("maybe") is None
    assert gc._parse_yesno("I don't know") is None


def test_parse_yesno_disambiguates_first_occurrence():
    assert gc._parse_yesno("YES, but NO") == 1
    assert gc._parse_yesno("NO, definitely not YES") == 0


def test_default_url_reads_env_at_call_time(monkeypatch):
    monkeypatch.setenv("RC_GEN_URL", "http://example/v1/chat/completions")
    assert gc._default_url() == "http://example/v1/chat/completions"


def test_default_budget_reads_env_at_call_time(monkeypatch):
    monkeypatch.setenv("RC_GEN_BUDGET_MS", "1500")
    assert gc._default_budget_ms() == 1500


def test_score_plan_grounding_inactive_backend(monkeypatch):
    monkeypatch.delenv("RC_REASONER_BACKEND", raising=False)
    res = gc.score_plan_grounding("claim", "hunk")
    assert res == {"supported": 0, "total": 0}


def test_health_url_parse_handles_proxy_paths():
    # urllib.parse.urlsplit correctly extracts scheme://netloc regardless
    # of repeated /v1/ in path
    import urllib.parse
    p = urllib.parse.urlsplit("http://proxy/api/v1/qwen/v1/chat/completions")
    base = f"{p.scheme}://{p.netloc}"
    assert base == "http://proxy"
