"""Mock-instead-of-integrate heuristic signals (P1).

Three cheap signals that flag tests likely to pass against in-process mocks
rather than exercising real cross-workload boundaries. All three return a
[0, 1] score where 1.0 = "very likely mocked".

These are a CHEAP PRE-FILTER. The actual gate is Stryker mutation-score
correlation (P1 follow-up); per the v2 plan we require Spearman r >= 0.6
between heuristic score and mutation score on a 30-file labeled corpus
before promoting to enforcement.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Iterable

# --- Signal 1: Cypress wildcard-intercept ratio ----------------------------
_CY_INTERCEPT_RE = re.compile(
    r"cy\.intercept\s*\(\s*(?:[\"'`])([^\"'`]*?)[\"'`](.*?)\)",
    re.DOTALL,
)


def _is_stub_call(call_text: str) -> bool:
    """A cy.intercept(...) call is "stub-shaped" iff handler is an inline
    object literal with fixture/statusCode/body OR a fixture-string reference
    AND it does NOT call req.continue() / req.passThrough().
    """
    if "req.continue" in call_text or "req.passThrough" in call_text or "passThrough" in call_text:
        return False
    return bool(re.search(r"\{[^}]*?(?:fixture|statusCode|body)\s*:", call_text, re.DOTALL))


def wildcard_intercept_ratio(content: str) -> float:
    """Ratio of wildcard-+-stub cy.intercept calls to total intercepts."""
    if not content:
        return 0.0
    matches = _CY_INTERCEPT_RE.findall(content)
    if not matches:
        return 0.0
    flagged = 0
    for route, rest in matches:
        is_wildcard = ("**" in route) or route.strip() in ("*", "/")
        if is_wildcard and _is_stub_call(rest):
            flagged += 1
    return flagged / len(matches)


# --- Signal 2: Mock CALL-SITES vs real-client call-sites -------------------
# Reviewer-flagged: import counts are trivially Goodhart-able (5 mocks + 5
# real imports = 0.5 ratio = passing). Count actual call sites — the
# load-bearing ops — not just imports.
_MOCK_CALLSITE_RES = (
    re.compile(r"\bjest\.mock\s*\("),
    re.compile(r"\bvi\.mock\s*\("),
    re.compile(r"\bsinon\.(?:stub|fake|mock|spy)\s*\("),
    re.compile(r"\bnock\s*\("),
    re.compile(r"\bMock<[^>]+>\s*\("),
    re.compile(r"\bnew\s+Mock<[^>]+>\s*\("),
    re.compile(r"\.Setup\s*\("),  # Moq
    re.compile(r"\bMockBean\b"),  # Spring
    re.compile(r"\bmock\s*\(\s*[A-Z]\w+::class"),  # MockK
    re.compile(r"\bgomock\.NewController\b"),
    re.compile(r"@patch\s*\("),  # unittest.mock
    re.compile(r"\bmocker\.patch\b"),  # pytest-mock
    re.compile(r"\bresponses\.add\b"),
    re.compile(r"\brespx\.\w+\s*\("),
    re.compile(r"\baioresponses\s*\("),
    re.compile(r"\bsetupServer\s*\("),  # MSW
    re.compile(r"\brest\.(?:get|post|put|delete|patch)\s*\("),  # MSW handlers
)
_REAL_CLIENT_CALLSITE_RES = (
    re.compile(r"\baxios\.(?:get|post|put|delete|patch)\s*\("),
    re.compile(r"\bfetch\s*\(\s*['\"]https?://"),
    re.compile(r"\brequests\.(?:get|post|put|delete|patch)\s*\("),
    re.compile(r"\bhttpx\.(?:get|post|put|delete|patch|Client)\s*\("),
    re.compile(r"\bnew\s+HttpClient\b"),
    re.compile(r"\bWebApplicationFactory<"),
    re.compile(r"\bTestcontainers\."),
    re.compile(r"\bGenericContainer\b"),
    re.compile(r"\bPostgreSQLContainer\b"),
    re.compile(r"\bpsycopg\.\w+\.connect\b"),
    re.compile(r"\basyncpg\.connect\b"),
    re.compile(r"\bcreate_engine\s*\("),  # SQLAlchemy
    re.compile(r"\bredis\.Redis\s*\("),
    re.compile(r"\bgrpc\.insecure_channel\b"),
    re.compile(r"\bboto3\.(?:client|resource)\s*\("),
    re.compile(r"\bamqplib\.Connection\s*\("),
)


def _count_callsites(content: str, patterns: Iterable[re.Pattern[str]]) -> int:
    return sum(len(p.findall(content)) for p in patterns)


def mock_to_real_client_ratio(content: str) -> float:
    """Ratio of mock call-sites to total call-sites. 1.0 = all mocks."""
    if not content:
        return 0.0
    mock = _count_callsites(content, _MOCK_CALLSITE_RES)
    real = _count_callsites(content, _REAL_CLIENT_CALLSITE_RES)
    if mock + real == 0:
        return 0.0
    return mock / (mock + real)


# --- Signal 3: Mystery-Guest fixture-vs-seed reconciliation ---------------
# Detect quoted string IDs in fixture files vs seed-data files. If fixture
# IDs have zero overlap with seed IDs, the fixture is fabricated.
_ID_LITERAL_RE = re.compile(
    r"[\"'`]([A-Za-z0-9_-]{6,40})[\"'`]"
)


def _extract_ids(text: str) -> set:
    if not text:
        return set()
    return set(_ID_LITERAL_RE.findall(text))


_SEED_DIR_NAMES = {
    "seeds", "fixtures", "test_data", "factories", "Migrations",
    "migrations", "supabase", "__fixtures__",
}
_EXCLUDE_DIRS = {".git", "node_modules", "dist", "build", ".venv", "venv",
                 "__pycache__", ".cache", "coverage", "target", "obj", "bin"}


def _persisted_cache_path(repo_root: Path) -> Path:
    """Disk-persistent cache lives at .rc/seed_ids.<repo_hash>.json so it
    survives the per-hook fresh-process problem (lru_cache alone is dead
    weight; each Edit hook is a new Python process)."""
    import hashlib
    digest = hashlib.sha1(str(repo_root).encode("utf-8")).hexdigest()[:12]
    state_dir = Path.home() / ".local" / "state" / "reasoning-core"
    return state_dir / f"seed_ids.{digest}.json"


def _read_seed_cache(cache_path: Path, repo_root: Path) -> frozenset | None:
    """Return cached frozenset if cache is fresh (<24h, repo HEAD unchanged)."""
    import json as _json
    import subprocess as _sp
    if not cache_path.exists():
        return None
    try:
        meta = _json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            return None
        head = _sp.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        if head and head == meta.get("head") and isinstance(meta.get("ids"), list):
            return frozenset(meta["ids"])
    except (OSError, ValueError, _sp.TimeoutExpired, _sp.SubprocessError):
        pass
    return None


def _write_seed_cache(cache_path: Path, repo_root: Path, ids: frozenset) -> None:
    import json as _json
    import subprocess as _sp
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        head = _sp.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        cache_path.write_text(
            _json.dumps({"head": head, "ids": sorted(ids)}),
            encoding="utf-8",
        )
    except (OSError, _sp.TimeoutExpired, _sp.SubprocessError):
        pass


def _walk_seed_files(repo_root: Path) -> set:
    """os.walk with in-place dirnames filtering — does NOT descend into
    _EXCLUDE_DIRS. Path.glob doesn't honor exclusions; this does.
    """
    import os as _os
    seed_ids: set = set()
    for dirpath, dirnames, filenames in _os.walk(repo_root):
        # Filter dirnames in place so os.walk doesn't recurse into them
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
        # Only scan if any segment of dirpath ends with a seed-dir name
        rel_parts = Path(dirpath).relative_to(repo_root).parts if Path(dirpath) != repo_root else ()
        if not any(p in _SEED_DIR_NAMES for p in rel_parts):
            continue
        for fname in filenames:
            fpath = Path(dirpath) / fname
            try:
                if fpath.stat().st_size > 256_000:
                    continue
                seed_ids.update(_extract_ids(fpath.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    return seed_ids


@functools.lru_cache(maxsize=4)
def _gather_seed_ids(repo_root_str: str) -> frozenset:
    """In-process memoization + disk-persistent cache keyed by git HEAD."""
    repo_root = Path(repo_root_str)
    cache_path = _persisted_cache_path(repo_root)
    cached = _read_seed_cache(cache_path, repo_root)
    if cached is not None:
        return cached
    ids = frozenset(_walk_seed_files(repo_root))
    _write_seed_cache(cache_path, repo_root, ids)
    return ids


def mystery_guest_score(content: str, repo_root: Path) -> float:
    """1.0 if no fixture IDs overlap any seed-file IDs. 0.0 if full overlap."""
    fixture_ids = _extract_ids(content)
    if not fixture_ids:
        return 0.0
    seed_ids = _gather_seed_ids(str(repo_root))
    if not seed_ids:
        # No seeds to compare against — can't make a claim either way.
        return 0.0
    overlap = fixture_ids & seed_ids
    if not overlap:
        return 1.0
    return 1.0 - (len(overlap) / len(fixture_ids))


# --- Composite ------------------------------------------------------------
#
# Reviewer-flagged: max(3) is unstable on noisy signals — single false-positive
# at 0.9 trips the gate. Use mean-with-corroboration: flag only if mean >= 0.4
# AND max >= 0.6. Single high signal alone produces a "watch" score (0.4-0.6)
# but doesn't trip enforcement.

def composite_mock_score(content: str, repo_root: Path) -> float:
    """Mean of the 3 signals; reported as the 'how mocked' metric."""
    s1 = wildcard_intercept_ratio(content)
    s2 = mock_to_real_client_ratio(content)
    s3 = mystery_guest_score(content, repo_root)
    return (s1 + s2 + s3) / 3.0


def is_likely_mocked(content: str, repo_root: Path) -> bool:
    """Conservative gate: mean >= 0.4 AND at least one signal >= 0.6."""
    s1 = wildcard_intercept_ratio(content)
    s2 = mock_to_real_client_ratio(content)
    s3 = mystery_guest_score(content, repo_root)
    mean = (s1 + s2 + s3) / 3.0
    peak = max(s1, s2, s3)
    return mean >= 0.4 and peak >= 0.6


def integration_authenticity(content: str, repo_root: Path) -> float:
    """1.0 = authentic integration; 0.0 = fully mocked. 1 - composite mean."""
    return 1.0 - composite_mock_score(content, repo_root)
