"""Plan-vocabulary path extraction shared between pre_plan_guard and the
plan-grounding gate.

Single source of truth for "what file paths does this PLAN.md mention?".
Three extraction strategies merged:

  1. Bulleted items with LOC annotation, e.g. ``- src/foo.py — ~120 LOC``.
  2. Bare backticked paths, e.g. `` `src/bar.ts` ``.
  3. Inline ``path:line`` refs, e.g. `` `src/foo.py:42` ``.

Plus two extension-less allowlists (word-boundary matched) so common
extension-less source files surface in the plan vocabulary:

  4. Well-known extension-less filenames (Dockerfile, Makefile, LICENSE,
     README, CHANGELOG, Rakefile, Gemfile, setup.py, ...).
  5. Common dotfiles (.gitignore, .dockerignore, .envrc, .eslintrc, ...).

Consumers:
  - ``pre_plan_guard._count_distinct_file_paths`` — wired (existing semantics preserved).
  - ``_dispatch.gate_plan_grounding`` — Phase 3, not yet wired in this commit.

Self-contained — no imports from other hook modules — to avoid cycles.
"""
from __future__ import annotations

import re
from typing import List, Tuple

# Match bulleted lines like "- src/foo.py — ~120 LOC" or "* `src/foo.py`: ~80 lines".
# Mirrors pre_plan_guard._FILE_LINE_RE exactly to preserve semantics.
_FILE_LINE_RE = re.compile(
    r"""
    [\-\*\+]\s*                          # bullet
    `?(?P<path>[A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,8})`?
    .*?                                  # any text in between
    (?:~?\s*(?P<loc>\d{2,5})\s*(?:LOC|lines?|locs?))
    """,
    re.IGNORECASE | re.VERBOSE,
)

_BARE_PATH_RE = re.compile(r"`([A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,8})`")

# Match inline code references like `Circit.Data/Model/Authorisation.cs:11`
# or bare `src/foo.py:42`.
_LINE_REF_RE = re.compile(
    r"`?([A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,8}):\d+`?"
)


# ---------------------------------------------------------------------------
# Extension-less allowlists
# ---------------------------------------------------------------------------
# The original regexes require a ``.ext`` suffix. That excludes a long tail of
# first-class source files -- Dockerfile, Makefile, LICENSE, README,
# CHANGELOG, Rakefile, Gemfile, setup.py, ... -- and every dotfile
# (.gitignore, .envrc, .dockerignore, .eslintrc, ...). Without a PLAN.md
# entry for these the plan-grounding gate hard-blocks edits to them, even
# when the user explicitly listed them. The allowlists below are matched as
# whole words with path/word boundary lookarounds so they don't false-match
# substrings (e.g. "MyDockerfile" or "the docker").
#
# Two design choices worth knowing about:
# 1. The lookaround excludes ``/`` so a token mid-path still matches
#    ("src/Dockerfile", "src/.gitignore" both work).
# 2. Longer alternates come first in the joined regex so ".env.example"
#    wins over ".env" and ".eslintrc.js" wins over ".eslintrc".

# Well-known filenames with NO extension (or with one but commonly cited
# bare). Case-sensitive — "Dockerfile" ≠ "dockerfile". Order does not matter
# here because lookarounds anchor the match; the alternation order only
# affects which alternative wins on overlapping matches (none of these
# overlap, so plain sort is fine).
_EXTENSIONLESS_NAMES: tuple[str, ...] = (
    # Build / packaging
    "Dockerfile", "Dockerfile.dev", "Dockerfile.prod", "Dockerfile.test",
    "Makefile", "GNUmakefile", "Makefile.am", "Makefile.in",
    "Rakefile", "Gemfile", "Gemfile.lock", "Vagrantfile", "Procfile",
    "Brewfile", "SConstruct", "Sakefile",
    # Python tooling entry points
    "setup.py", "setup.cfg", "conftest.py", "manage.py", "fabfile.py",
    # Top-level repo metadata
    "LICENSE", "LICENSE.md", "LICENSE.txt",
    "README", "README.md", "README.txt",
    "CHANGELOG", "CHANGELOG.md",
    "AUTHORS", "CONTRIBUTORS", "NOTICE", "COPYING",
    # Common Node / JS manifests (have an "extension" but cited bare often)
    "package.json", "tsconfig.json", "pnpm-workspace.yaml",
)

# Common dotfiles. Longest first so the alternation prefers the more
# specific match.
_DOTFILES: tuple[str, ...] = (
    ".dockerignore", ".gitattributes", ".gitmodules",
    ".gitignore", ".env.example", ".env.local", ".env.sample",
    ".envrc", ".env",
    ".eslintrc.js", ".eslintrc.json", ".eslintrc.yaml", ".eslintrc.yml",
    ".eslintrc",
    ".prettierrc.js", ".prettierrc.json", ".prettierrc.yaml",
    ".prettierrc.yml", ".prettierrc",
    ".babelrc", ".babelrc.js", ".babelrc.json",
    ".editorconfig", ".npmrc", ".yarnrc", ".yarnrc.yml",
    ".nvmrc", ".node-version", ".tool-versions",
    ".flake8", ".pylintrc", ".ruff.toml", ".python-version",
    ".python-version",
)

# Negative lookarounds block substring matches like "MyDockerfile" or
# "..gitignore". Excludes ``/`` so a token mid-path still matches
# ("src/Dockerfile", "src/.gitignore"). The optional path-prefix capture
# keeps ``src/Dockerfile`` as the full path rather than truncating to just
# "Dockerfile" — otherwise the contract's allowed_paths would silently
# widen from the listed path to its basename.
#
# IMPORTANT: the prefix must wrap EACH alternative in the alternation, not
# just the first one. ``(prefix|alt1|alt2)`` only attaches the prefix to
# ``alt1`` -- ``alt2`` would only match at the start of a path. We want
# ``(prefix|alt1)|(prefix|alt2)``, hence the per-alternative join below.
_PATH_PREFIX = r"(?:[A-Za-z0-9_.\-]+/)?"
_EXTENSIONLESS_RE = re.compile(
    rf"(?<![A-Za-z0-9_.\-])("
    + "|".join(_PATH_PREFIX + re.escape(n) for n in _EXTENSIONLESS_NAMES)
    + r")(?![A-Za-z0-9_.\-])"
)

# Dotfiles: longest-first so the alternation prefers the more specific match.
_DOTFILE_RE = re.compile(
    rf"(?<![A-Za-z0-9_.\-])("
    + "|".join(
        _PATH_PREFIX + re.escape(n)
        for n in sorted(_DOTFILES, key=len, reverse=True)
    )
    + r")(?![A-Za-z0-9_.\-])"
)


def extract_files_with_loc(content: str) -> List[Tuple[str, int]]:
    """Find file_path + estimated LOC pairs in plan body (bulleted lines)."""
    out: List[Tuple[str, int]] = []
    for m in _FILE_LINE_RE.finditer(content):
        try:
            loc = int(m.group("loc"))
        except (TypeError, ValueError):
            continue
        path = m.group("path") or ""
        if path:
            out.append((path, loc))
    return out


def distinct_file_paths(content: str) -> set[str]:
    """Return file paths mentioned in the plan.

    Combines bulleted-with-LOC lines, bare-backticked paths, inline
    ``path:line`` references, well-known extension-less filenames (Dockerfile,
    Makefile, LICENSE, README, ...), and common dotfiles (.gitignore,
    .envrc, .dockerignore, ...).
    """
    paths: set[str] = {p for p, _ in extract_files_with_loc(content)}
    paths.update(_BARE_PATH_RE.findall(content))
    paths.update(_LINE_REF_RE.findall(content))
    paths.update(_EXTENSIONLESS_RE.findall(content))
    paths.update(_DOTFILE_RE.findall(content))
    return paths
