# Verification

This document is the *exact* command list a reviewer runs to validate
the quality bar for the reasoning-core prototype. Run them in order
from a clean checkout.

> All commands assume the working directory is the repo root:
> `/Users/jakubsikora/Repos/personal/reasoning-core`.

## 1. Create venv + install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Expected:** `pip install` exits 0. Heavy dependencies (`torch`,
`transformers`) download successfully. Tree-sitter grammar wheels for
Python, JavaScript, TypeScript, C#, and SQL resolve.

## 2. Run the unit test suite

```bash
pytest -q
```

**Expected:** exit code 0. Live-only tests (marked
`@pytest.mark.live`) are skipped unless `RC_LIVE=1` is set; that is
correct behavior for offline CI.

## 3. Compile every Python file

```bash
python3 -m py_compile $(git ls-files '*.py')
```

**Expected:** exit code 0. Catches syntax errors that pytest would
otherwise hide behind import failures.

## 4. Validate `.claude/settings.json`

```bash
python3 -c "import json; json.load(open('.claude/settings.json'))"
```

**Expected:** exit code 0, no output. The hook + MCP server registration
file must remain valid JSON.

## 5. End-to-end prototype smoke

```bash
bash scripts/start-sidecar.sh &  bash scripts/test-prototype.sh
```

(`start-sidecar.sh` runs in the background; `test-prototype.sh` boots
its own sidecar instance, so the background one is a warm-up only and
can be killed afterwards if desired.)

The simpler invocation that the test script supports directly:

```bash
bash scripts/test-prototype.sh
```

**Expected:** prints `PASS` and exits 0. Internally it asserts:
- the sidecar `/health` endpoint reports `model_loaded:true` within 60s,
- the bad Python refactor (drops guard clause + introduces unbounded
  recursion) is **blocked** with hook exit code 2,
- a benign JavaScript rename **passes** with exit 0,
- a benign C# method rename **passes** with exit 0,
- a benign SQL `CREATE PROCEDURE` addition **passes** with exit 0,
- a `.rb` payload **passes** with exit 0 and `unsupported_language` in
  stderr.

## 6. Live Scaleway + y-router probe

This step requires real credentials. Ensure the Scaleway CLI profile
`newprofile` is configured (`scw config set --profile newprofile
secret-key=...`) and that the y-router is running locally on
`http://localhost:8787`.

```bash
RC_LIVE=1 bash scripts/configure-scaleway.sh
```

**Expected:** exit code 0. The script:
- reads the secret key via `scw config get secret-key --profile
  newprofile` (legacy `secret_key` accepted),
- exports `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`,
  `ANTHROPIC_MODEL`, `ANTHROPIC_SMALL_FAST_MODEL`,
- POSTs to `https://api.scaleway.ai/v1/chat/completions` with
  `devstral-2-123b-instruct-2512` and asserts a non-empty
  `.choices[0].message.content`,
- POSTs an Anthropic-shape body to
  `${ANTHROPIC_BASE_URL}/v1/messages` and asserts a non-empty
  completion (warns + skips if the y-router is not reachable, but does
  not fail the whole script).

To run offline (skip both probes):

```bash
bash scripts/configure-scaleway.sh --skip-live
```

The script's negative path can be exercised by unsetting the key and
stubbing `scw`:

```bash
( unset SCALEWAY_API_KEY
  PATH="/tmp/empty-scw:$PATH" bash scripts/configure-scaleway.sh )
```

with a `/tmp/empty-scw/scw` shim that prints nothing — the script must
exit 1 within 2s and print `ERROR: missing Scaleway secret key (scw
profile=newprofile)` to stderr.

## 7. Live Scaleway pytest smoke

```bash
RC_LIVE=1 pytest -q tests/test_scaleway_smoke.py
```

**Expected:** exit code 0 with the live POSTs landing 200s. Without
`RC_LIVE=1`, the live tests are skipped and only the negative
fail-fast assertion runs.

## Quality bar summary

A clean checkout passes the quality bar when, on a developer laptop:

- (1) clean venv + install: green
- (2) `pytest -q`: green
- (3) `py_compile` of all `.py`: green
- (4) settings.json parses: green
- (5) `bash scripts/test-prototype.sh`: prints `PASS`, exits 0
- (6) `RC_LIVE=1 bash scripts/configure-scaleway.sh`: exits 0 with
      Scaleway 200 + y-router 200
- (7) `RC_LIVE=1 pytest -q tests/test_scaleway_smoke.py`: green

Capture the output of (6) (redacting the secret key) and the SSM
checkpoint hash from `state-spaces/mamba-130m-hf` into the verification
log when QA signs the release.

## QA Run Log

### 2026-05-01 — Senior QA pass (offline subset)

- **Commit:** `1c5517a9d2b55ba3f05229a7e8ed09304a9a0209`
- **Python interpreter:** `Python 3.13.11` (no project `.venv`; ran against
  the user's pre-existing site-packages — torch 2.10.0, transformers 5.5.3,
  fastapi 0.135.1, httpx 0.28.1, pytest 9.0.2; `tree_sitter_c_sharp` only;
  `tree_sitter_languages` / `tree_sitter_python` / `tree_sitter_javascript` /
  `tree_sitter_typescript` / `tree_sitter_sql` **NOT INSTALLED**; HuggingFace
  cache empty → no Mamba checkpoint reachable).
- **Test files written:**
  - `tests/test_s2_core.py` (408 LOC, 33 tests — RC-009)
  - `tests/test_mcp_reasoner.py` (263 LOC, 9 tests — RC-010)
  - `tests/test_hook_block.py` (302 LOC, 10 tests — RC-011)
  - `tests/test_scaleway_smoke.py` (201 LOC, 3 tests — RC-013)
- **Collection sanity:** `python3 -m pytest -q --collect-only` → 55 tests
  collected, exit 0.
- **Offline run:** `python3 -m pytest -q -m "not live" --maxfail=20`
  → **29 passed, 24 skipped, 2 deselected (live), 0 failed** in 5.96 s.

#### Skips by reason (env gap, not bugs)

| Reason                                                                    | Count |
| ------------------------------------------------------------------------- | ----- |
| `tree-sitter grammar for 'python' not installed`                          | 3     |
| `tree-sitter grammar for 'javascript' not installed`                      | 3     |
| `tree-sitter grammar for 'typescript' not installed`                      | 3     |
| `tree-sitter grammar for 'sql' not installed`                             | 3     |
| `backbone unavailable: state-spaces/mamba-130m-hf + fallbacks` (Mamba)    | 12    |

C# parse + call-graph cases pass (the only grammar wheel that resolved).
The `live` Scaleway tests are deselected (`RC_LIVE` not set); their
**negative** counterpart `test_configure_scaleway_missing_key_fails_fast`
**PASSES** offline — confirms the script exits non-zero in <2 s with the
exact `ERROR: missing Scaleway secret key` stderr.

#### Per-task QA verdict

| Task   | Stage transition         | Verdict notes                                                                                                                |
| ------ | ------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| RC-002 | test → **test** (BLOCKED) | Negative test PASSES; live probe needs creds + y-router (lead).                                                              |
| RC-003 | test → **test** (BLOCKED) | Tree-sitter wheels missing in QA env (env, not source). See RC-101.                                                          |
| RC-004 | test → **test** (BLOCKED) | Mamba checkpoint not cached. See RC-102.                                                                                     |
| RC-005 | test → **test** (BLOCKED) | HTTP TestClient gated on backbone load. See RC-102.                                                                          |
| RC-006 | test → **done**          | All 9 mcp_reasoner tests green (happy path, fail-open, fail-closed via S2_FAIL_CLOSED, 415 unsupported, 5xx, write-no-file). |
| RC-007 | test → **done**          | All 10 hook tests green (block, allow, unsupported, fail-open, fail-closed, MultiEdit, malformed stdin, settings.json schema). |
| RC-008 | test → **test** (BLOCKED) | Static review of SKILL.md + ARCHITECTURE.md + VERIFICATION.md PASSES; e2e blocked on Mamba. See RC-103.                       |
| RC-009 | todo → **done**          | Test file written, suite collects clean.                                                                                     |
| RC-010 | todo → **done**          | Test file written, all 9 tests PASS.                                                                                         |
| RC-011 | todo → **done**          | Test file written, all 10 tests PASS.                                                                                        |
| RC-012 | todo → **todo** (BLOCKED) | Final gate; pending RC-008 closure.                                                                                          |
| RC-013 | todo → **done**          | Test file written; offline negative test passes; live tests gated on RC_LIVE=1.                                              |
| RC-014 | test → **test** (BLOCKED) | Static review clean; runtime gated on RC-102.                                                                                |

#### New bug / env-fix tasks opened by QA

- **RC-101** — install tree-sitter language wheels in a clean QA venv to
  unblock RC-003.
- **RC-102** — resolve Mamba checkpoint cache (HF download or
  `S2_SSM_CHECKPOINT` override) to unblock RC-004 / RC-005 / RC-014.
- **RC-103** — once RC-101 + RC-102 are green, run
  `bash scripts/test-prototype.sh` end-to-end and capture the output here
  to close RC-008 and RC-012.

#### Environment caveats

The offline-only subset is not a full release gate. The fully-green
quality bar in §"Quality bar summary" above requires:

1. A clean `.venv` populated from `requirements.txt` (lifts the 12
   tree-sitter skips → pass and the 12 Mamba-skipped scoring/HTTP cases).
2. A reachable HuggingFace mirror **or** a pre-cached
   `state-spaces/mamba-130m-hf` checkpoint (lifts all 12 backbone skips
   and unblocks `bash scripts/test-prototype.sh`).
3. `RC_LIVE=1` plus a real Scaleway profile and a y-router on
   `localhost:8787` (lifts the 2 deselected live tests and closes RC-002).

When (1) + (2) land, the expected pytest summary is **53 passed, 0
skipped (offline)** — and `bash scripts/test-prototype.sh` prints `PASS`.
That is the bar for closing the remaining BLOCKED tasks.

## Cross-Verification — QA Pass 2

- **Timestamp:** 2026-05-01T00:00:00Z
- **Commit:** `1c5517a9d2b55ba3f05229a7e8ed09304a9a0209` (unchanged)
- **Python interpreter:** Python 3.13.11 (venv; torch 2.10.0, transformers 5.5.3)

### Step 1 — pip install attempt

`tree-sitter-languages` is not available for Python 3.13 on macOS arm64 — no matching distribution found.

Per-language wheels installed successfully:
- `tree-sitter-python 0.25.0`
- `tree-sitter-javascript 0.25.0`
- `tree-sitter-typescript 0.23.2`
- `tree-sitter-sql 0.3.11`
- `tree-sitter-c-sharp` was already present

`--break-system-packages` rejected (running inside a virtualenv); used plain `pip install` instead.

### Step 2 — pytest delta

**Before install:** `29 passed, 24 skipped, 2 deselected, 0 failed` in 5.99s

**After install:**
```
2 failed, 39 passed, 12 skipped, 2 deselected, 2 warnings in 6.68s
```

Delta: +10 tests moved from skip to pass (Python, JS, TS, C# parser/call-graph cases). 12 skips remain (Mamba backbone unavailable). 2 new failures introduced by SQL grammar dialect mismatch (see RC-003 bug below).

### Step 3 — parser smoke test

```
python3 -c "from src.grammars import get_parser, select_grammar; lang_id, _ = select_grammar('/x.py'); p = get_parser('python'); t = p.parse(b'def f(): return 1'); print(t.root_node.has_error, t.root_node.type)"
# Output: False module
# Exit code: 0
```

PASS — Python parser returns a valid tree with `has_error=False`.

### Step 4 — Per-task verdicts (6 tasks at stage `test`)

| Task   | Verdict  | Evidence |
|--------|----------|----------|
| RC-002 | BLOCKED  | Live probe needs creds + y-router. Offline negative test `test_configure_scaleway_missing_key_fails_fast` PASSES (exit non-zero, <2s, exact stderr). Command: `python3 -m pytest tests/test_scaleway_smoke.py::test_configure_scaleway_missing_key_fails_fast -v` → 1 passed. |
| RC-003 | FAIL     | 4 of 5 language parse+call-graph tests now PASS after wheel install. SQL tests FAIL: `tree_sitter_sql 0.3.11` returns `has_error=True` for `CREATE PROCEDURE A() BEGIN CALL B(); END;` (MySQL BEGIN/END dialect not supported by grammar). Exit: 1. Bug: grammar dialect mismatch in SQL fixture / `build_call_graph` query. Task moved to `inprogress`. |
| RC-004 | BLOCKED  | All 6 scoring/determinism/embed tests skip — Mamba checkpoint not in HF cache. Command: `python3 -m pytest tests/test_s2_core.py -k "score_change or determinism or backbone or embed" -v` → 6 skipped. |
| RC-005 | BLOCKED  | All 5 HTTP TestClient tests skip — `create_app` fixture gated on backbone. Command: `python3 -m pytest tests/test_s2_core.py -k "http" -v` → 5 skipped. |
| RC-008 | BLOCKED (static PASS) | Static review: SKILL.md frontmatter+8 dims+decision matrix+languages all present. ARCHITECTURE.md S1/S2 split, port 8765, SSM Backbone Selection section, mamba-130m-hf, license, unsupported-language contract all present. `test-prototype.sh` executable, `set -euo pipefail`, all fixture types + assertions present. E2e blocked on Mamba checkpoint. |
| RC-014 | BLOCKED  | `load_backbone()` skips — no checkpoint resolves. Static review: singleton/LRU, env overrides, BACKBONE_INFO, BackboneUnavailableError, `ast_to_tokens` all confirmed present. |

### Step 5 — Board changes

- **RC-003** moved `test` → `inprogress`: SQL grammar dialect bug discovered (2 new test failures).
- RC-002, RC-004, RC-005, RC-008, RC-014 remain at `test` — BLOCKED, no regressions, same root causes as QA Pass 1.

### Skips by reason (after install)

| Reason | Count |
|--------|-------|
| Mamba backbone unavailable (score_change, determinism, embed, HTTP TestClient, backbone smoke) | 12 |

### New bug opened

**RC-003 SQL dialect bug:** `tree-sitter-sql 0.3.11` does not parse `CREATE PROCEDURE ... BEGIN ... END;` (MySQL/MariaDB syntax). Grammar returns an `ERROR` node for the entire statement. The SQL test fixture and/or `build_call_graph` SQL query in `src/grammars.py` must be updated to use a supported dialect (e.g. PostgreSQL `CREATE FUNCTION ... LANGUAGE sql` or standard `CREATE PROCEDURE` without a BEGIN/END compound body, depending on which syntax the grammar actually supports). Engineer-1 to fix.

## Cross-Verification — Data Eng Pass

### 2026-05-01 — Math/runtime verification (Data Eng)

- **Goal:** verify the Mamba forward-pass path end-to-end (real load,
  determinism, sensitivity, risk-vector hygiene) per the Data
  Engineering verification brief.
- **Environment:** macOS arm64, Python 3.13.11 (cyberlegion venv),
  torch 2.10.0, transformers 5.5.3.
- **HF cache state:** empty (`~/.cache/huggingface` does not exist).
- **HF reachability:** `curl -I https://huggingface.co/state-spaces/
  mamba-130m-hf/resolve/main/config.json` returns **HTTP 403** via the
  `Cato` corporate proxy. CDN host `cas-bridge.xethub.hf.co` is also
  blocked (CloudFront 403). github.com and pypi.org pass through, so
  the gating is HF-specific. Setting `HF_HUB_OFFLINE=0` cannot help —
  upstream is unreachable.

#### Step 1 — Real Mamba load: BLOCKED

`load_backbone()` cycles through all three documented checkpoints and
fails identically:

```
checkpoint state-spaces/mamba-130m-hf failed to load: We couldn't
  connect to 'https://huggingface.co' to load the files, ...
checkpoint state-spaces/mamba2-130m   failed to load: same.
checkpoint sshleifer/tiny-gpt2        failed to load: same.
BackboneUnavailableError: No SSM backbone could be loaded.
```

- Checkpoint loaded: **none**.
- Hidden size: unknown (`BACKBONE_INFO['hidden_size']` is `None`).
- Cold latency: unmeasurable.
- Warm latency: unmeasurable.
- Fallback chain behavior: **correct**. Primary → mamba2 → tiny-gpt2 →
  typed `BackboneUnavailableError` with the documented remediation
  hint. No code defect — the loader's contract is honored. Same
  RC-102 environmental block as QA's earlier passes.

**Verdict: BLOCKED on environment.** Steps 2 (determinism), 3
(sensitivity), 5 (risk hygiene) all transit the backbone forward
pass and cannot be exercised here.

#### Step 4 — AST → token bridge: PASS

This step does not need the SSM. Per-language wheels are present in
this venv (`tree_sitter_python` 0.25.0, `tree_sitter_javascript`
0.25.0, `tree_sitter_typescript` 0.23.2, `tree_sitter_c_sharp` 0.25.0,
`tree_sitter_sql` 0.3.11). The aggregate `tree_sitter_languages` wheel
is **not** available for Python 3.13 (`No matching distribution
found`), but `src/grammars.py::_load_via_per_lang` is the documented
fallback and resolves all five languages:

```
GRAMMAR_OK a.py python    GRAMMAR_OK a.js javascript
GRAMMAR_OK a.ts typescript GRAMMAR_OK a.tsx tsx
GRAMMAR_OK a.cs csharp     GRAMMAR_OK a.sql sql
```

`ast_to_tokens` exercised on Python fixture
`def f(n): if n<=0: return 0; return f(n-1)+1`:

| Property                                      | Value                                                                |
| --------------------------------------------- | -------------------------------------------------------------------- |
| Output length                                 | 422 chars                                                            |
| Deterministic across two calls (same tree)    | **True**                                                             |
| Deterministic across re-parses of same source | **True**                                                             |
| Head                                          | `<module> <function_definition> <def> def <identifier> f <parameters> <(> ( <identifier> n <)> ) <:> : <block> <if_statement> ...` |
| `tree=None` fallback                          | returns the raw source string verbatim — non-empty                  |

**Verdict: PASS.** Linearisation is structure-aware, non-empty,
deterministic, and falls back gracefully.

#### Steps 2, 3, 5 — Determinism / sensitivity / risk hygiene: NOT EXECUTED

All three require a live forward pass. Static review of the relevant
code (unchanged from earlier QA passes) suggests the contracts are
implementable:

- `embed()` re-seeds `torch.manual_seed(_EMBED_SEED)` on every call,
  wraps the forward in `torch.no_grad()`, pulls the cached singleton
  → bit-identical output is plausible.
- `score_change()` derives AIS from `(cos+1)/2` clamped to `[0,1]`
  and `coherence_delta` from `torch.linalg.norm(emb_before -
  emb_after)`. Threshold logic (AIS<0.4 OR coherence>1.5 OR any risk
  dim >0.9) matches the spec.
- `_compute_risk_vector` clamps every dim through `_norm(x, scale)`
  which returns `0.0` for `scale<=0`, `0.0` for negatives, and `1.0`
  for over-scale → dims are *guaranteed* in `[0, 1]` by construction.
  No NaN/inf source for finite-dtype inputs.

#### Mathematically suspicious items flagged for the lead

1. **Cyclomatic dim is asymmetric on guard-removal.**
   `cyclomatic = clip((max(0, b_after - b_before) + 0.25 * b_after) /
   20, 0, 1)`. A regression that *removes* a guard branch contributes
   only `0.25 * b_after`, which is *strictly smaller* than the
   pre-edit baseline. So the canonical "drops a guard clause + adds
   unbounded recursion" example will only trip the regression flag if
   coupling/novelty/depth absorb the signal — cyclomatic alone won't.
   Worth a unit fixture asserting the spec example actually trips
   `regression_detected` once the backbone is reachable.
2. **`fan_in` excludes external/builtin calls.** `if callee in
   in_counts_after` filters down to intra-module callees, so for
   single-function fixtures the dim is always 0. Defensible for a
   per-file analysis but means it never contributes signal on small
   edits — confirm intentional.
3. **`coherence_delta` is raw L2** of two `hidden_size`-D vectors
   (768 for mamba-130m). The hard-coded threshold `1.5` is sensitive
   to (a) `hidden_size`, (b) the model's typical activation scale, (c)
   the checkpoint identity. If `S2_SSM_CHECKPOINT` is overridden to a
   different model the threshold may be trivially-tripped or
   impossibly-strict. Suggest scaling by `1/sqrt(hidden_size)` or
   normalising embeddings before the diff, and recording the empirical
   distribution on a benign-edit corpus once HF is reachable.
4. **`novelty = 1 - max(cos, 0)`** discards negative cosine similarity.
   Capping at 0 keeps the dim in `[0, 1]` but means
   semantically-opposite edits and merely-different edits both
   saturate to 1.0. Calibration item, not a correctness bug.
5. **`embed()` truncates at `max_length=512`** BPE tokens. For larger
   files the AIS reflects only the first ~512 tokens of the linearised
   AST. Should be documented in `SKILL.md` so reviewers know the score
   is not whole-file beyond ~2KB of token-dense AST output.
6. **AIS contract may be too lenient at the `0.4` regression threshold.**
   `ais = (cos + 1) / 2` maps to `0.4` when `cos = -0.2`. For an SSM
   trained on natural code, two valid functions almost never embed to
   `cos < -0.2`. Effectively the AIS-only branch of the regression
   predicate fires only on truly antithetical edits — coherence_delta
   and the per-dim ceiling do most of the work. Worth measuring AIS
   distribution on the regression corpus before relying on the AIS
   threshold for production gating.

#### Tasks moved to `done`

**None.** The brief required Mamba load + determinism + sensitivity to
all pass before moving RC-004 / RC-014. Only step 4 was runnable.
RC-004 and RC-014 remain at `test` — gated on RC-102 (HF cache).

#### Suggested next action

Cache `state-spaces/mamba-130m-hf` from a host outside the Cato proxy
(or whitelist `huggingface.co` + `cas-bridge.xethub.hf.co`), copy the
`~/.cache/huggingface/hub` tree onto this machine, then rerun this
section. Once the backbone loads, the four pending checks (load,
determinism, sensitivity, risk hygiene) take <2 minutes and we can
close RC-004/014 cleanly.

## Cross-Verification — Dev Pass

### 2026-05-01 — Runtime end-to-end (Dev verification agent)

- **Commit:** `1c5517a9d2b55ba3f05229a7e8ed09304a9a0209`
- **Python:** `Python 3.13.11` (cyberlegion venv); torch 2.10.0, transformers 5.5.3, fastapi 0.135.1, pydantic 2.12.5
- **HF reachability:** `https://huggingface.co` returns HTTP 403 (Cato proxy block — same as Data Eng pass)
- **Port 8765 at start:** free

---

#### Step 1 — Sidecar boot test (RC-005)

Command:
```
PYTHONPATH=/.../ python3 -m src.s2_core &
# poll GET http://127.0.0.1:8765/health
```

Result:
- Server binds on 127.0.0.1:8765 within ~5s. `/health` responds 200.
- `model_loaded` stays `false` throughout the 60s polling window.
- Backbone load attempts all fail with HTTP 403 from Cato proxy (same RC-102 env block).
- Sidecar does NOT crash — it stays alive with `model_loaded:false` as designed.
- **RC-005 verdict: FAIL** — `/health` never reports `model_loaded:true` within 60s.
- Root cause: environmental (HF 403), not a startup-crash source bug.

Sidecar stderr (first startup):
```
INFO:src.ssm_backbone:Loading SSM backbone checkpoint=state-spaces/mamba-130m-hf device=cpu
INFO:httpx:HTTP Request: HEAD https://huggingface.co/state-spaces/mamba-130m-hf/resolve/main/config.json "HTTP/1.1 403 Forbidden"
WARNING:src.ssm_backbone:checkpoint state-spaces/mamba-130m-hf failed to load: ...
WARNING:src.ssm_backbone:checkpoint state-spaces/mamba2-130m failed to load: ...
WARNING:src.ssm_backbone:checkpoint sshleifer/tiny-gpt2 failed to load: ...
ERROR:__main__:backbone unavailable at startup: No SSM backbone could be loaded.
  Remediation: run `huggingface-cli download state-spaces/mamba-130m-hf` ...
INFO:     Application startup complete.
```

**Source bug found (BUG-DEV-001):** `/score` route returns HTTP 422 instead of executing — caused by `-> JSONResponse` return-type annotation on the FastAPI route handlers (`s2_core.py:690` and `s2_core.py:701`). Under pydantic v2 + FastAPI 0.135, FastAPI builds a `TypeAdapter` for the annotated return type; `JSONResponse` is not a pydantic model, which causes a `PydanticUserError` at schema generation time. FastAPI then treats the endpoint as having a required query parameter `request`, producing 422 on every POST. `/openapi.json` returns HTTP 500 for the same reason.

Fix: remove `-> JSONResponse` return annotations from both route handlers (or replace with `-> dict`). The handlers themselves are correct — the bug is in the annotations only.

---

#### Step 2 — Live hook block test

```
echo '{"tool_name":"Edit","tool_input":{"file_path":"/tmp/test_block.py","new_string":"def f(n): return n + f(n+1)"}}' \
  | python3 src/hooks/pre_edit_guard.py
```

Exit code: `0` (expected `2`)
Stderr: `[hybrid-reasoner] sidecar unavailable (http_422); fail-open.`

**FAIL** — hook cannot exercise the block path because BUG-DEV-001 causes every `/score` call to return 422 instead of an ImpactReport. Hook correctly falls through to fail-open.

---

#### Step 3 — Live hook pass test

Exit code: `0` (expected `0`)
Stderr: `[hybrid-reasoner] sidecar unavailable (http_422); fail-open.`

**FAIL** — the pass test exits 0 but for the wrong reason (fail-open on 422, not a clean score). The distinction matters: a genuine pass should produce empty stderr.

---

#### Step 4 — Unsupported-language test (.rb)

```
echo '{"tool_name":"Edit","tool_input":{"file_path":"/tmp/test.rb","new_string":"..."}}' \
  | python3 src/hooks/pre_edit_guard.py
```

Exit code: `0` (correct)
Stderr contains `unsupported_language`: **NO** — stderr shows `http_422 fail-open` because BUG-DEV-001 makes the sidecar return 422 for all requests. A 422 is not a 415, so the hook's unsupported-language branch is never reached.

**FAIL** — exit code correct but for wrong reason; stderr missing required `unsupported_language` token.

---

#### Step 5 — MCP server bootstrap

```
python3 -m src.mcp_reasoner 2>&1 &
# wait 3s, SIGTERM
```

- Import: `from src.mcp_reasoner import mcp, reason_over_edit` succeeds.
- `mcp.name` == `"hybrid-reasoner"` — correct.
- Process exits cleanly when stdin closes (STDIO transport; expected behavior).
- No crash on import or startup.

**PASS** — MCP server imports and initialises cleanly; no crash.

---

#### Step 6 — `bash scripts/test-prototype.sh`

```
bash scripts/test-prototype.sh   # 5-minute timeout
```

Output (truncated):
```
[prototype] booting sidecar...
[prototype] waiting for /health (60s budget, pid=43394)...
[prototype] sidecar exited early; log tail:
  ERROR:__main__:backbone unavailable at startup: ...
  [Errno 48] error while attempting to bind on address ('127.0.0.1', 8765): address already in use
FAIL: sidecar process died before /health came up
```

Exit code: `1`

**FAIL** — test-prototype.sh tries to boot a second sidecar while the one from Step 1 still held port 8765. The script's own sidecar instance exited immediately on EADDRINUSE. Root cause: port occupied by previous test step. Had port been free, the test would still fail at the `model_loaded:true` gate (HF 403, RC-102 env block).

---

#### Step 7 — `bash -n` and shellcheck on all 3 scripts

```
bash -n scripts/test-prototype.sh      # OK
bash -n scripts/start-sidecar.sh       # OK
bash -n scripts/configure-scaleway.sh  # OK
shellcheck scripts/test-prototype.sh scripts/start-sidecar.sh scripts/configure-scaleway.sh
```

shellcheck findings (all `info` severity — no errors or warnings):
- `test-prototype.sh:31`: SC2329 — `cleanup` function never directly invoked (registered via `trap`; false positive).
- `configure-scaleway.sh:35,57,61,64,94,118,214,218`: SC2317 — `exit N` after `return N 2>/dev/null` appears unreachable (intentional `source`-compatible dual-mode pattern).

**PASS** — no errors or warnings. Only info-level style notes.

---

#### py_compile check

```
python3 -m py_compile src/__init__.py src/grammars.py src/hooks/__init__.py \
  src/hooks/pre_edit_guard.py src/mcp_reasoner.py src/s2_core.py \
  src/ssm_backbone.py tests/__init__.py tests/conftest.py \
  tests/test_hook_block.py tests/test_mcp_reasoner.py \
  tests/test_s2_core.py tests/test_scaleway_smoke.py
```

Exit code: `0` — all `.py` files compile cleanly.

---

#### Summary of verdicts

| Check | Status | Details |
|-------|--------|---------|
| Sidecar boot (RC-005) | FAIL | HTTP server starts, /health responds 200; `model_loaded` stays false (HF 403 env block, RC-102). BUG-DEV-001 also found (see below). |
| Hook block test | FAIL | Exit 0 instead of 2. BUG-DEV-001 causes /score to return 422; hook falls through to fail-open. |
| Hook pass test | FAIL | Exit 0 correct but via fail-open path, not clean score. |
| Unsupported (.rb) test | FAIL | Exit 0 correct; stderr missing `unsupported_language` — 422 (not 415) prevents the unsupported-lang branch. |
| MCP server bootstrap | PASS | Import OK, server name `hybrid-reasoner`, no crash on startup or SIGTERM. |
| test-prototype.sh | FAIL | Exit 1; sidecar EADDRINUSE (port held by prior step); underlying HF 403 would fail the health gate regardless. |
| bash -n (3 scripts) | PASS | All 3 scripts pass bash syntax check. |
| shellcheck (3 scripts) | PASS | Info-level notes only; zero errors or warnings. |
| py_compile (all .py) | PASS | All 13 Python files compile cleanly. |

#### Source bug found

**BUG-DEV-001** — `src/s2_core.py:690` and `src/s2_core.py:701`
- **Symptom:** `POST /score` returns HTTP 422 with `{"detail":[{"type":"missing","loc":["query","request"],...}]}` and `GET /openapi.json` returns HTTP 500 with `pydantic.errors.PydanticUserError: TypeAdapter[...JSONResponse...] is not fully defined`.
- **Cause:** FastAPI route handlers annotated `-> JSONResponse`. Under pydantic v2 + FastAPI 0.135, FastAPI attempts to build a `TypeAdapter` for the return type; `JSONResponse` is not a pydantic model, which fails at schema-generation time and confuses parameter resolution.
- **Fix:** remove `-> JSONResponse` from the `health()` and `score()` route-handler signatures (or change to `-> dict`). The handler bodies are correct.

#### Tasks moved to `done`

**None.** RC-005 cannot be promoted because BUG-DEV-001 prevents `/score` from functioning and `model_loaded:true` is blocked by RC-102 (env). RC-007 was already `done`. No other tasks could be definitively verified end-to-end in this environment.

