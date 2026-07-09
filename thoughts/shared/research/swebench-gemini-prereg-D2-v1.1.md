---
date: 2026-05-10
commit: n/a
branch: main
tags: [prereg, amendment]
status: complete
---
# Amendment D2‑v1.1 – Pin Gemini model alias

## Change
- D2 inherits model_id from D1 (prereg clause: "Setup B's model_id MUST equal Setup A's model_id").
- Both arms now use **`gemini-2.5-flash-preview-09-2025`** per D1-v1.1 amendment.

## Why
- SHOWSTOPPER #3 required a concrete, version‑pinned model for reproducibility and audit‑trail compliance.

## Applied
- No direct edit to `swebench-gemini-prereg-D2.json` needed — model_id resolved through D1 inheritance.
- Sweep scripts pass the same `--model gemini-2.5-flash-preview-09-2025` flag to both A and B arms.
