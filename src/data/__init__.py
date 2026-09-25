"""Bundled templates and CLI scaffold assets for reasoning-core.

Every template that `rc init` or `install.sh` writes into a target repo is
shipped here as package data. Whether the framework is running from a
git checkout or a `pip install reasoning-core[full]` wheel, callers
read these files via :func:`read_text` (a thin wrapper over
:mod:`importlib.resources`). Treat the dot-dir templates at the repo
root (``.codex/settings.json.template`` etc.) as the source of truth
that the wheel build copies into this package.
```
"""
from __future__ import annotations

import os
from functools import lru_cache
from importlib import resources
from typing import Union

PackageRoot = Union[str, "os.PathLike[str]"]


@lru_cache(maxsize=1)
def package_root() -> PackageRoot:
    """Return the on-disk directory holding the bundled templates.

    Resolves to a real filesystem path even when running from a wheel
    (``importlib.resources`` extracts to a temp dir on older Pythons).
    Cached because the result is process-stable.
    """
    return resources.files("src.data.templates")  # type: ignore[return-value]


def read_text(relative_path: str) -> str:
    """Read a bundled template as text.

    Raises :class:`FileNotFoundError` if the resource is missing — this
    is almost always a packaging bug (the file was added to the source
    tree but not listed in ``pyproject.toml`` ``package-data``).
    """
    parts = relative_path.split("/")
    base = resources.files("src.data.templates")
    for part in parts:
        base = base.joinpath(part)
    resource = base
    if not resource.exists():
        raise FileNotFoundError(
            f"reasoning-core template not found in wheel data: {relative_path!r}. "
            "Add it to pyproject.toml [tool.setuptools.package-data] "
            "under 'src.data' or move it out of the bundle."
        )
    return resource.read_text(encoding="utf-8")


def exists(relative_path: str) -> bool:
    """Return True if the bundled template exists."""
    parts = relative_path.split("/")
    base = resources.files("src.data.templates")
    for part in parts:
        base = base.joinpath(part)
    return base.exists()


__all__ = ["package_root", "read_text", "exists"]
