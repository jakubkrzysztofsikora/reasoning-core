"""Runtime contract of the Stop hook (src/hooks/stop_duplicate_check.py).

The review logic is covered in tests/test_dup_review.py; this covers main():
opt-in, the block payload, RC_MODE=advise, fail-open, and the audit event.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.duplicate_detection import findings as ledger  # noqa: E402
from src.duplicate_detection.check import EXTRACT  # noqa: E402
from src.hooks import audit_log  # noqa: E402
from src.hooks import stop_duplicate_check as hook  # noqa: E402

DISMISS = (
    "export function {name}(ref, onClose) {{\n"
    "  function onDoc(e) {{ if (ref.current && !ref.current.contains(e.target)) onClose(); }}\n"
    "  function onKey(e) {{ if (e.key === 'Escape') onClose(); }}\n"
    "  document.addEventListener('mousedown', onDoc);\n"
    "  document.addEventListener('keydown', onKey);\n"
    "  return () => {{ document.removeEventListener('mousedown', onDoc); "
    "document.removeEventListener('keydown', onKey); }};\n"
    "}}\n"
)
MENUS = ("AccountMenu", "BasemapControl", "SortMenu")


@pytest.fixture
def env(tmp_path, monkeypatch):
    audit = tmp_path / "audit"
    monkeypatch.setattr(audit_log, "_AUDIT_ROOT", str(audit))
    monkeypatch.setenv("RC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("RC_DUP_ORACLE", "1")
    monkeypatch.delenv("RC_MODE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo, audit


def _open_extract(repo: Path) -> dict:
    for m in MENUS:
        (repo / f"{m}.ts").write_text(DISMISS.format(name=f"dismiss{m}"))
        (repo / f"{m}.test.ts").write_text(f"import {{ {m} }} from './{m}'")
    f = ledger.make_finding(
        added_path="AccountMenu.ts", added_name="dismissAccountMenu",
        matches=[{"path": f"{m}.ts", "name": f"dismiss{m}", "lineno": 1, "logic": 1.0} for m in MENUS[1:]],
        verdict=EXTRACT, in_diff=False, likely_move=False, test_covered=True,
    )
    ledger.record(ledger.ledger_path(str(repo)), f)
    return f


def test_stop_from_a_subdirectory_reviews_the_whole_repo(env, monkeypatch, capsys):
    # Paths in the ledger are relative to the repo's top level; reviewing them
    # from a subdirectory must not mistake the copies for gone and resolve them.
    repo, _ = env
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    f = _open_extract(repo)
    (repo / "src").mkdir()
    out, _ = _run(monkeypatch, capsys, repo / "src")
    assert out and out["decision"] == "block"
    assert ledger.load(ledger.ledger_path(str(repo)))[f["id"]]["status"] == "open"


def _run(monkeypatch, capsys, repo: Path, **extra):
    payload = {"hook_event_name": "Stop", "cwd": str(repo), "stop_hook_active": False, **extra}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        hook.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    return (json.loads(captured.out) if captured.out else None), captured.err


def _events(audit: Path) -> list[dict]:
    rows = []
    for f in sorted(audit.glob("*/*.jsonl")):
        rows += [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    return [r for r in rows if r.get("event_type") == "dup_stop"]


def test_opt_out_is_silent(env, monkeypatch, capsys):
    repo, audit = env
    monkeypatch.delenv("RC_DUP_ORACLE")
    _open_extract(repo)
    out, err = _run(monkeypatch, capsys, repo)
    assert out is None and err == ""
    assert _events(audit) == []


def test_open_extract_finding_blocks(env, monkeypatch, capsys):
    repo, audit = env
    f = _open_extract(repo)
    out, _ = _run(monkeypatch, capsys, repo)
    assert out["decision"] == "block"
    assert f["id"] in out["reason"]
    [event] = _events(audit)
    assert event["decision"] == "blocked"
    assert event["extract_ids"] == [f["id"]]


def test_blocks_even_on_a_retry_until_the_cap(env, monkeypatch, capsys):
    # stop_hook_active is set on the stop after a block; this hook keeps
    # blocking an open EXTRACT finding (capped), unlike stop_reconcile.
    repo, audit = env
    _open_extract(repo)
    decisions = []
    for i in range(4):
        out, _ = _run(monkeypatch, capsys, repo, stop_hook_active=i > 0)
        decisions.append(out["decision"] if out else None)
    assert decisions == ["block", "block", "block", None]
    assert [e["decision"] for e in _events(audit)][-1] == "escalated"


def test_explicit_advise_mode_never_blocks_but_is_audited(env, monkeypatch, capsys):
    repo, audit = env
    monkeypatch.setenv("RC_MODE", "advise")
    f = _open_extract(repo)
    out, err = _run(monkeypatch, capsys, repo)
    assert out is None
    assert f["id"] in err  # still visible to the operator
    [event] = _events(audit)
    assert event["decision"] == "advise"


def test_advise_mode_does_not_use_up_the_block_cap(env, monkeypatch, capsys):
    repo, audit = env
    monkeypatch.setenv("RC_MODE", "advise")
    _open_extract(repo)
    for _ in range(4):
        _run(monkeypatch, capsys, repo)
    assert [e["decision"] for e in _events(audit)] == ["advise"] * 4


def test_nothing_open_approves(env, monkeypatch, capsys):
    repo, audit = env
    out, _ = _run(monkeypatch, capsys, repo)
    assert out is None
    assert [e["decision"] for e in _events(audit)] == ["approved"]


def test_failure_is_fail_open_but_traced(env, monkeypatch, capsys):
    repo, audit = env

    def boom(*_a, **_k):
        raise RuntimeError("ledger exploded")

    monkeypatch.setattr(hook, "stop_decision", boom)
    out, err = _run(monkeypatch, capsys, repo)
    assert out is None
    assert "fail-open" in err
    [event] = _events(audit)
    assert event["decision"] == "fail-open"
    assert "ledger exploded" in event["error"]
