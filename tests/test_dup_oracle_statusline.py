"""Unit tests for the statusline segment (src/hooks/dup_oracle_statusline.py).

Offline: stubbed stdin + a stubbed counter, no torch. Covers the opt-in gate,
the session-keyed lookup, and fail-open.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.dup_oracle_flagcount import render  # noqa: E402
from src.hooks import dup_oracle_statusline as sl  # noqa: E402


def _feed(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def test_silent_when_oracle_disabled(monkeypatch, capsys):
    # Even with a live count, no RC_DUP_ORACLE=1 -> print nothing (don't imply
    # the oracle is running when it isn't).
    monkeypatch.delenv("RC_DUP_ORACLE", raising=False)
    monkeypatch.setattr(sl, "read_count", lambda s: 5)
    _feed(monkeypatch, {"session_id": "x"})
    sl.main()
    assert capsys.readouterr().out == ""


def test_renders_the_sessions_count_when_enabled(monkeypatch, capsys):
    monkeypatch.setenv("RC_DUP_ORACLE", "1")
    monkeypatch.setattr(sl, "read_count", lambda s: 3)
    _feed(monkeypatch, {"session_id": "sess-abc"})
    sl.main()
    assert capsys.readouterr().out == render(3)


def test_looks_up_the_count_by_this_sessions_id(monkeypatch, capsys):
    # The count shown must be for the payload's session, not a global one.
    monkeypatch.setenv("RC_DUP_ORACLE", "1")
    seen = {}

    def record(session_id):
        seen["id"] = session_id
        return 0

    monkeypatch.setattr(sl, "read_count", record)
    _feed(monkeypatch, {"session_id": "the-real-session"})
    sl.main()
    assert seen["id"] == "the-real-session"


def test_end_to_end_real_counter_writer_and_reader_agree(monkeypatch, capsys, tmp_path):
    # No stubbed count. Drive the REAL writer (flagcount.bump -- exactly what the
    # hook calls with payload["session_id"]) and the REAL reader (statusline.main,
    # which extracts payload["session_id"] and looks it up), against a shared
    # base. This is the test that fails if the two sides ever derive the session
    # key differently -- the reader would look up an empty/other file and render 0.
    import src.dup_oracle_flagcount as fc

    real_read = fc.read_count
    monkeypatch.setattr(sl, "read_count", lambda s: real_read(s, base=str(tmp_path)))
    monkeypatch.setenv("RC_DUP_ORACLE", "1")

    sid = "e2e-session"
    fc.bump(sid, base=str(tmp_path))
    fc.bump(sid, base=str(tmp_path))
    fc.bump(sid, base=str(tmp_path))

    # this session's three flags render...
    _feed(monkeypatch, {"session_id": sid})
    sl.main()
    assert capsys.readouterr().out == render(3)

    # ...while a different session is independent (truly per-session, end to end).
    _feed(monkeypatch, {"session_id": "some-other-session"})
    sl.main()
    assert capsys.readouterr().out == render(0)


def test_fails_open_on_bad_input(monkeypatch, capsys):
    monkeypatch.setenv("RC_DUP_ORACLE", "1")

    def boom(_s):
        raise RuntimeError("counter blew up")

    monkeypatch.setattr(sl, "read_count", boom)
    _feed(monkeypatch, {"session_id": "x"})
    sl.main()  # must not raise
    assert capsys.readouterr().out == ""
