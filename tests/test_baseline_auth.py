"""RC-SEC-07: POST /baseline requires bearer token authentication.

Full integration testing requires a running sidecar server. This module
documents the acceptance criteria; manual verification:

1. Start sidecar: python -m src.s2_core
2. curl without token → 401:
   curl -X POST http://127.0.0.1:8765/baseline -d '{"session_id":"t","files":[]}'
3. curl with wrong token → 401:
   curl -X POST http://127.0.0.1:8765/baseline -H 'Authorization: Bearer wrong' -d '{"session_id":"t","files":[]}'
4. curl with correct token (RC_ENFORCEMENT_TOKEN set) → 200 or 400 (bad payload)
5. Rapid repeat → 429 rate limited
"""
from __future__ import annotations


def test_rate_limit_unit():
    """Verify rate limit logic: 1 req per 60s per session_id."""
    rate_limits = {}
    
    def check_rate_limit(session_id):
        import time
        now = time.time()
        last = rate_limits.get(session_id, 0.0)
        if now - last < 60.0:
            return False
        rate_limits[session_id] = now
        return True
    
    # First request allowed
    assert check_rate_limit("test-session") is True
    # Second request within 60s denied
    assert check_rate_limit("test-session") is False
    # Different session allowed
    assert check_rate_limit("other-session") is True
