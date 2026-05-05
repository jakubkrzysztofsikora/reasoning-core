"""P1 mock-detector heuristic tests."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src" / "hooks"))


def test_wildcard_intercept_full_stub_scores_one():
    import _mock_detector as md
    content = """
    cy.intercept('/**', { fixture: 'auth.json' });
    cy.intercept('*', { statusCode: 200, body: {} });
    """
    assert md.wildcard_intercept_ratio(content) == 1.0


def test_wildcard_intercept_real_passthrough_scores_zero():
    import _mock_detector as md
    content = """
    cy.intercept('/api/auth/login', (req) => req.continue());
    cy.intercept('/api/users/*', (req) => req.continue());
    """
    assert md.wildcard_intercept_ratio(content) == 0.0


def test_wildcard_intercept_no_intercepts_returns_zero():
    import _mock_detector as md
    assert md.wildcard_intercept_ratio("// no cypress here") == 0.0


def test_mock_to_real_callsites_mock_heavy():
    import _mock_detector as md
    content = """
    jest.mock('../service');
    sinon.stub(client, 'get').returns({});
    nock('https://api.example.com').get('/x').reply(200);
    """
    assert md.mock_to_real_client_ratio(content) == 1.0


def test_mock_to_real_callsites_real_only():
    import _mock_detector as md
    content = """
    const c = await new PostgreSQLContainer().start();
    const res = await axios.get('https://real.api/users');
    """
    assert md.mock_to_real_client_ratio(content) == 0.0


def test_mock_to_real_callsites_balanced():
    import _mock_detector as md
    content = """
    jest.mock('../service');
    const res = await axios.get('https://real.api/users');
    """
    # 1 mock callsite, 1 real callsite → 0.5
    r = md.mock_to_real_client_ratio(content)
    assert abs(r - 0.5) < 1e-9


def test_imports_alone_no_longer_count_as_mock():
    """Reviewer Goodhart fix: imports are free; only call-sites count."""
    import _mock_detector as md
    content = "import sinon from 'sinon';\nimport nock from 'nock';\n"
    # Imports only, no call sites → 0.0 (was 1.0 before the fix)
    assert md.mock_to_real_client_ratio(content) == 0.0


def test_mystery_guest_no_seed_overlap():
    import _mock_detector as md
    with tempfile.TemporaryDirectory() as td:
        seeds = Path(td) / "seeds"
        seeds.mkdir()
        (seeds / "users.sql").write_text("INSERT INTO users (id) VALUES ('user-real-aaa111');\n")
        fixture_content = '{"id": "user-fake-zzz999"}'
        score = md.mystery_guest_score(fixture_content, Path(td))
        assert score == 1.0


def test_mystery_guest_full_overlap():
    import _mock_detector as md
    with tempfile.TemporaryDirectory() as td:
        seeds = Path(td) / "seeds"
        seeds.mkdir()
        (seeds / "users.sql").write_text("VALUES ('user-real-aaa111');")
        fixture_content = '{"id": "user-real-aaa111"}'
        score = md.mystery_guest_score(fixture_content, Path(td))
        assert score == 0.0


def test_mystery_guest_no_seeds_returns_zero():
    import _mock_detector as md
    with tempfile.TemporaryDirectory() as td:
        fixture_content = '{"id": "anything-here"}'
        # No seed dirs → can't make a claim → 0.0
        assert md.mystery_guest_score(fixture_content, Path(td)) == 0.0


def test_integration_authenticity_inverse_of_composite():
    import _mock_detector as md
    with tempfile.TemporaryDirectory() as td:
        content = "cy.intercept('/**', { fixture: 'a.json' });"
        repo = Path(td)
        composite = md.composite_mock_score(content, repo)
        auth = md.integration_authenticity(content, repo)
        assert abs((1.0 - composite) - auth) < 1e-9


def test_passthrough_wildcard_does_not_flag():
    """cy.intercept('/api/**', req => req.continue()) is legitimate spy."""
    import _mock_detector as md
    content = "cy.intercept('/api/**', (req) => req.continue());"
    assert md.wildcard_intercept_ratio(content) == 0.0


def test_is_likely_mocked_requires_mean_and_peak():
    """Reviewer fix: single noisy 0.9 alone is not enough to flag."""
    import _mock_detector as md
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        # single high signal (wildcard) alone → mean ~0.33, peak 1.0 → not flagged
        single = "cy.intercept('/**', { fixture: 'a' });"
        assert md.is_likely_mocked(single, repo) is False
        # all three signals high → flagged
        (Path(td) / "seeds").mkdir()
        (Path(td) / "seeds" / "users.sql").write_text("VALUES ('user-real-aaa111');")
        many = """
        cy.intercept('/**', { fixture: 'a' });
        jest.mock('../svc');
        """
        # mean ~0.5, peak 1.0; mystery_guest=0 because no fixture IDs in this content
        # so we test the corroboration: just two high signals lifts mean above 0.4
        assert md.is_likely_mocked(many, repo) is True


def test_seed_id_cache_memoized():
    """Reviewer latency fix: _gather_seed_ids caches per repo_root."""
    import _mock_detector as md
    md._gather_seed_ids.cache_clear()
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "seeds").mkdir()
        (Path(td) / "seeds" / "x.sql").write_text("VALUES ('user-aaa-real');")
        a = md._gather_seed_ids(td)
        b = md._gather_seed_ids(td)
        assert a is b  # same frozenset object → cache hit
        # cache info reports a hit
        info = md._gather_seed_ids.cache_info()
        assert info.hits >= 1
