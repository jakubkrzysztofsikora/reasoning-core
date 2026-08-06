# date-fns dup-oracle fixture

Frozen snapshot for the offline near-duplicate integration test (`tests/test_dup_oracle_integration.py`). Not a live dependency.

- Source: date-fns/date-fns @ `4098115cf705e3af7f663d8e5b0686e39a9f478a`
- Scope: `pkgs/core/src/**/index.ts`, excluding /locale/, /fp/, /_lib/
- Functions: 265
- Embedder: `unixcoder-base` (CLS pooling), vectors L2-normalised, shape (265, 768)

Regenerate:

    RC_EMBEDDER=unixcoder-base .venv/bin/python tests/fixtures/dup_oracle/_generate.py
