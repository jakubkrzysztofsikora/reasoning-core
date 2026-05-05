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


def test_mock_to_real_ratio_all_mocks():
    import _mock_detector as md
    content = """
    import sinon from 'sinon';
    import { rest } from 'msw';
    jest.mock('../service');
    """
    assert md.mock_to_real_client_ratio(content) > 0.5


def test_mock_to_real_ratio_balanced():
    import _mock_detector as md
    content = """
    import axios from 'axios';
    import { setupServer } from 'msw/node';
    """
    r = md.mock_to_real_client_ratio(content)
    assert 0.3 < r < 0.7  # roughly even


def test_mock_to_real_ratio_real_only():
    import _mock_detector as md
    content = "import { HttpClient } from '@angular/common/http';"
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
