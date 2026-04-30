"""Shared pytest fixtures for reasoning-core."""
from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from contextlib import closing

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session")
def repo_root() -> str:
    return REPO_ROOT


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def free_port() -> int:
    return _free_port()


@pytest.fixture(scope="session")
def hf_cache_dir(tmp_path_factory) -> Iterator[str]:
    """Pin HF_HOME to a session-scoped tmp dir unless caller overrode it.

    Lets the SSM backbone tests share a single download across the session.
    """
    if os.environ.get("HF_HOME"):
        yield os.environ["HF_HOME"]
        return
    p = tmp_path_factory.mktemp("hf")
    os.environ["HF_HOME"] = str(p)
    try:
        yield str(p)
    finally:
        os.environ.pop("HF_HOME", None)
