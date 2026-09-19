"""Resolve where reasoning-core is installed and what to put in templates.

Callers used to read ``$RC_REPO`` from the environment (set by ``.envrc``
to the path of a git checkout). After ``pip install reasoning-core[full]``,
there is no checkout — the framework lives inside site-packages. This
module produces the values that ``rc init`` substitutes into per-repo
templates:

- :func:`python_executable` returns the interpreter to invoke hooks with.
- :func:`framework_root` returns the on-disk location of the source code,
  or ``None`` if the framework is a true wheel with no editable install.
- :func:`rc_repo_path` returns the legacy ``$RC_REPO`` value for
  back-compat with ``install.sh`` templates that still reference it.

The rule is intentionally permissive: prefer the editable checkout when
one exists, fall back to the wheel installation directory, and never
fail outright — every caller has a sensible default that doesn't depend
on the framework location.
```
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def python_executable() -> str:
    """Return the absolute path of the interpreter to invoke hooks with.

    Defaults to ``sys.executable`` — the interpreter that ran ``rc init``,
    which is by definition the one with reasoning-core installed. Setting
    ``RC_PYTHON`` in the environment overrides this (used by ``rc init``
    itself when invoked from a venv inside a checkout).
    """
    env = os.environ.get("RC_PYTHON")
    if env:
        return env
    return sys.executable


def _package_root_path() -> Optional[Path]:
    """Best-effort location of the ``src`` package on disk.

    Returns the directory containing ``__init__.py`` for the ``src``
    package, or None if it cannot be resolved (e.g. a zip-imported
    distribution with no on-disk representation). Used by :func:`rc_repo_path`
    so legacy templates that interpolate ``<RC_REPO>`` still get a
    sensible absolute path.
    """
    try:
        import src  # noqa: PLC0415  -- intentional runtime import
    except ImportError:
        return None
    file = getattr(src, "__file__", None)
    if not file:
        return None
    return Path(file).resolve().parent


def framework_root() -> Optional[Path]:
    """Return the on-disk framework root, or None for an unresolvable install.

    For a git checkout this is the parent directory of the ``src``
    package (i.e. the repo root). For a wheel it is the site-packages
    directory containing the ``src`` package.
    """
    pkg = _package_root_path()
    if pkg is None:
        return None
    parent = pkg.parent
    # Heuristic: a checkout has ``pyproject.toml`` next to ``src/``;
    # a wheel install has site-packages — distinct shapes, both fine to
    # return. Callers that care about the distinction check
    # ``(parent / "pyproject.toml").exists()`` themselves.
    return parent


def rc_repo_path() -> str:
    """Return the value to substitute for ``$RC_REPO`` in legacy templates.

    - Editable checkout (``pip install -e .``): the repo root.
    - Wheel install: ``site-packages`` (best we can do; legacy templates
      that ran ``python3 $RC_REPO/src/hooks/pre_edit_guard.py`` should
      be using module form ``python3 -m src.hooks.pre_edit_guard`` instead).
    - Unresolvable: empty string, so a stray ``<RC_REPO>`` placeholder
      does not silently expand to ``None``.
    """
    root = framework_root()
    if root is None:
        return ""
    return str(root)


def is_wheel_install() -> bool:
    """Return True if the framework is running from a wheel install
    (no editable checkout detected).
    """
    root = framework_root()
    if root is None:
        return True
    return not (root / "pyproject.toml").exists()


__all__ = [
    "python_executable",
    "framework_root",
    "rc_repo_path",
    "is_wheel_install",
]
