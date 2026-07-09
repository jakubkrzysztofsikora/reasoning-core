---
date: 2026-05-10
commit: n/a
branch: main
tags: [prereg, amendment]
status: complete
---
# Amendment D1‑v1.1 – Pin Gemini model alias

## Change
- Replace floating model alias `gemini-2.5-flash` with the dated variant **`gemini-2.5-flash-preview-09-2025`**.
- Updated `thoughts/shared/research/swebench-gemini-prereg-D1.json` `model_id` field.
- Updated `scripts/smoke-gemini-sdk.sh` default MODEL_ID.
- Added `gemini-2.5-flash` and `gemini-2.5-flash-latest` to `prereg_validation.py` ALIAS_FORBIDDEN_EXACT set.

## Why
- SHOWSTOPPER #3 required a concrete, version‑pinned model for reproducibility and to satisfy the prereg freeze.
- Using a dated preview model ensures the same weights across runs and complies with the audit‑trail SHA‑pinning requirement.

## Applied
- `swebench-gemini-prereg-D1.json` model_id set to `gemini-2.5-flash-preview-09-2025`.
- `scripts/smoke-gemini-sdk.sh` default changed from `gemini-2.5-pro` to `gemini-2.5-flash-preview-09-2025`.
- `eval/prereg_validation.py` now rejects `gemini-2.5-flash` and `gemini-2.5-flash-latest` as floating aliases.
