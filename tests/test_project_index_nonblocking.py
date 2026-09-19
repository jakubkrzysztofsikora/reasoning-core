"""Regression: project_index get_or_build_index() must not block on fresh build.

Audit-hostile/2026-09-19-fixes: the previous code called fut.result(timeout=300)
on the fresh-build branch, which synchronously blocked the event-loop thread for
up to 5 minutes during the repo walk. The new contract is: returns None
immediately so the caller falls back to intra-file scoring; subsequent calls
see the cached future on the fast path.
"""
from __future__ import annotations

import threading
import time

from src import project_index


def test_fresh_build_returns_none_immediately(tmp_path, monkeypatch):
    """First call returns None without waiting for the build to finish."""
    # Make the build slow so any blocking would be visible.
    started = threading.Event()
    release = threading.Event()

    def slow_build(repo_root, session_id):
        started.set()
        release.wait(timeout=10.0)
        return project_index.ProjectIndex(
            session_id=session_id,
            repo_root=repo_root,
            symbol_index={},
            import_index={},
        )

    monkeypatch.setattr(project_index, "_build_index", slow_build)
    monkeypatch.setattr(project_index, "_iter_repo_files", lambda root: ["a.py"])

    t0 = time.monotonic()
    result = project_index.get_or_build_index("nonblocking-test", repo_root=str(tmp_path))
    elapsed = time.monotonic() - t0
    assert result is None
    assert elapsed < 0.5, f"get_or_build_index blocked for {elapsed:.3f}s"
    assert started.is_set(), "background build was not kicked off"
    release.set()


def test_subsequent_call_sees_cached_result(tmp_path, monkeypatch):
    """A second call after the build completes returns the cached index."""
    def fast_build(repo_root, session_id):
        return project_index.ProjectIndex(
            session_id=session_id,
            repo_root=repo_root,
            symbol_index={"foo": [("a.py", 1)]},
            import_index={},
        )

    monkeypatch.setattr(project_index, "_build_index", fast_build)
    monkeypatch.setattr(project_index, "_iter_repo_files", lambda root: ["a.py"])

    project_index.clear_index("cache-test")
    project_index.get_or_build_index("cache-test", repo_root=str(tmp_path))
    # Give the background thread a moment to finish.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        result = project_index.get_or_build_index("cache-test", repo_root=str(tmp_path))
        if result is not None:
            break
        time.sleep(0.05)
    assert result is not None
    assert result.symbol_index == {"foo": [("a.py", 1)]}
    project_index.clear_index("cache-test")
