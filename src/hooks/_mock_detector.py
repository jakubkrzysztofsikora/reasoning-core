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

import re
from pathlib import Path
from typing import Iterable

# --- Signal 1: Cypress wildcard-intercept ratio ----------------------------
_CY_INTERCEPT_RE = re.compile(
    r"cy\.intercept\s*\(\s*(?:[\"'`])([^\"'`]*?)[\"'`]",
    re.MULTILINE,
)
# A handler is "stub-shaped" if it's an inline object literal or fixture
# reference — i.e. the test pre-defines the response without calling
# req.continue() / req.passThrough().
_CY_STUB_HANDLER_RE = re.compile(
    r"cy\.intercept\s*\([^)]*?,\s*\{[^}]*?(?:fixture|statusCode|body)\s*:",
    re.DOTALL,
)


def wildcard_intercept_ratio(content: str) -> float:
    """Ratio of wildcard cy.intercept stubs to total intercepts. 1.0 = all wildcard."""
    if not content:
        return 0.0
    routes = _CY_INTERCEPT_RE.findall(content)
    if not routes:
        return 0.0
    wildcards = sum(1 for r in routes if "**" in r or r.strip() in ("*", "/"))
    return wildcards / len(routes)


# --- Signal 2: Mock-import-to-real-client ratio ----------------------------
_MOCK_LIBS = (
    "jest.mock", "jest/globals", "sinon", "unittest.mock", "Moq",
    "NSubstitute", "msw", "nock", "WireMock", "@mswjs/", "FakeItEasy",
)
_REAL_CLIENT_LIBS = (
    "axios", "node-fetch", "global.fetch", "HttpClient", "RestSharp",
    "pg ", "amqplib", "@azure/service-bus", "requests", "httpx",
    "WebApplicationFactory", "TestServer",
)


def _count_substr_imports(content: str, needles: Iterable[str]) -> int:
    if not content:
        return 0
    count = 0
    for n in needles:
        count += content.count(n)
    return count


def mock_to_real_client_ratio(content: str) -> float:
    """Ratio of mock imports to real-client imports. 1.0 = all mocks, no real clients."""
    if not content:
        return 0.0
    mock = _count_substr_imports(content, _MOCK_LIBS)
    real = _count_substr_imports(content, _REAL_CLIENT_LIBS)
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


def _gather_seed_ids(repo_root: Path) -> set:
    seed_ids: set = set()
    seed_globs = ("seeds", "fixtures/db", "test_data", "factories", "Migrations")
    for glob_dir in seed_globs:
        for path in repo_root.rglob(f"{glob_dir}/**/*"):
            if not path.is_file():
                continue
            try:
                if path.stat().st_size > 256_000:
                    continue
                seed_ids.update(_extract_ids(path.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    return seed_ids


def mystery_guest_score(content: str, repo_root: Path) -> float:
    """1.0 if no fixture IDs overlap any seed-file IDs. 0.0 if full overlap."""
    fixture_ids = _extract_ids(content)
    if not fixture_ids:
        return 0.0
    seed_ids = _gather_seed_ids(repo_root)
    if not seed_ids:
        # No seeds to compare against — can't make a claim either way.
        return 0.0
    overlap = fixture_ids & seed_ids
    if not overlap:
        return 1.0
    return 1.0 - (len(overlap) / len(fixture_ids))


# --- Composite ------------------------------------------------------------

def composite_mock_score(content: str, repo_root: Path) -> float:
    """Max of the three signals — a single bad signal is enough to flag."""
    return max(
        wildcard_intercept_ratio(content),
        mock_to_real_client_ratio(content),
        mystery_guest_score(content, repo_root),
    )


def integration_authenticity(content: str, repo_root: Path) -> float:
    """1.0 = authentic integration; 0.0 = fully mocked. Inverse of composite."""
    return 1.0 - composite_mock_score(content, repo_root)
