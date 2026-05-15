# reasoning-core Production Code Quality Review

## Findings Summary (by Severity)

| Severity | Count | Categories |
|----------|-------|------------|
| CRITICAL | 4 | Correctness (3), Resource Mgmt (1) |
| HIGH | 7 | Correctness (2), Resource Mgmt (2), Error Handling (2), Testing (1) |
| MEDIUM | 6 | Resource Mgmt (1), Error Handling (2), Testing (2), Maintainability (1) |
| LOW | 4 | Testing (1), Magic Numbers (3) |

---

## CRITICAL

### CRITICAL-1: sys.exit(2) in threads silently kills the thread, not the process
**FILE:** `src/hooks/_rule_engine.py:360`
**ISSUE:** `_handle_schema_error()` calls `sys.exit(2)`. In Python, `sys.exit()` in a non-main thread raises `SystemExit` in that thread only -- the process continues running. When called from async code (e.g., FastAPI request handler), the task dies but the event loop continues. The `_parse_and_validate` -> `_handle_schema_error` -> `sys.exit(2)` path is reachable from `gate_rule_engine` which is called from request handlers. In multi-threaded contexts, this leaks a dead thread and leaves the process in a potentially corrupted state (partially initialized rule engine). The `gate_rule_engine` in `_dispatch.py` does catch `SystemExit` (line 594), but `load_rules()` is also called directly from other contexts.
**EVIDENCE:**
```python
# _rule_engine.py:358-360
    else:
        sys.stderr.write(f"{full_msg}\n")
        sys.exit(2)
```
**FIX:** Replace `sys.exit(2)` with a dedicated exception (`RuleEngineSchemaError`) that ALL callers catch. The `_TEST_MODE` flag already has this pattern -- promote it to always-on and require top-level callers to handle it.

---

### CRITICAL-2: Per-rule "timeout" is fake -- rule always runs to completion
**FILE:** `src/hooks/_rule_engine.py:159-176`
**ISSUE:** The 5ms per-rule budget is NOT a timeout. The rule runs to completion (`_check_rule(after_src, rule)` at line 161), THEN elapsed time is checked. A regex that catastrophically backtracks will run for seconds/minutes before the budget check fires. This is a DOS vector: a single malicious rule pattern can hang the entire scoring pipeline. The `* 1000.0` arithmetic at each sample is also numerically suboptimal vs `(t1 - t0) * 1000.0`.
**EVIDENCE:**
```python
# _rule_engine.py:159-176
    t0 = time.perf_counter() * 1000.0
    try:
        after_hits = _check_rule(after_src, rule)   # <-- ALWAYS runs to completion
    except Exception:
        after_hits = []

    elapsed = (time.perf_counter() * 1000.0) - t0   # <-- checked AFTER
    if elapsed > _PER_RULE_BUDGET_MS:
        hits.append(RuleHit(...))  # shadow hit
        continue
```
**FIX:** Use `signal.alarm()` (Unix) or `threading.Timer` with a separate worker thread that can actually interrupt long-running work. Document that the 5ms is a best-effort soft budget, not a hard timeout. At minimum, apply `* 1000.0` only to the delta: `(t1 - t0) * 1000.0`.

---

### CRITICAL-3: `break` after FIRST dim breach loses all subsequent breached dims
**FILE:** `src/s2_core.py:995-1001`
**ISSUE:** The dim ceiling check loop has an unconditional `break` after the first breach. `fired_dims` is a `list[str]` designed to capture ALL dimensions that breached, but only the first one is recorded. Multiple risk dimensions can breach simultaneously (e.g., cyclomatic + coupling both spike), but the operator only sees one in `fired_dims`. This makes root-cause analysis impossible for multi-dim regressions. The `fired_margins` dict also loses data since only one margin is captured.
**EVIDENCE:**
```python
# s2_core.py:995-1001
    for i, rv_val in enumerate(risk_vector):
        if rv_val > dim_ceiling:
            fired_conditions.append("dim_ceiling_breached")
            if i < len(RISK_LABELS):
                fired_dims.append(RISK_LABELS[i])
                fired_margins[f"{RISK_LABELS[i]}_ceiling"] = float(rv_val - dim_ceiling)
        break  # <-- EXITS AFTER FIRST BREACH: intentional information loss
```
**FIX:** Remove the `break`. Collect ALL breached dims. `fired_conditions.append("dim_ceiling_breached")` should also be deduplicated (use a set, convert to list at the end).

---

### CRITICAL-4: cumulative_drift at risk_vector[8] CAN spuriously trigger dim_ceiling_breached
**FILE:** `src/s2_core.py:973-975` (set) before `s2_core.py:995-1001` (check)
**ISSUE:** `cumulative_drift` is promoted into `risk_vector[8]` at line 975, and the dim ceiling loop at line 995 iterates the full vector including index 8. Since `cumulative_drift` uses the same normalization as `coherence_delta` (divided by sqrt(hidden_size)), it can exceed the dim_ceiling threshold (0.9 default). When it does, it triggers `dim_ceiling_breached` with `fired_dims = ["session_centroid_drift"]`. The dim ceiling check was designed for structural risk dimensions (cyclomatic, coupling, etc.), NOT for cumulative drift which has its own separate semantic meaning. This conflates two independent signals.
**EVIDENCE:**
```python
# s2_core.py:973-975
    if cumulative_drift is not None and len(risk_vector) > 8:
        risk_vector = list(risk_vector)
        risk_vector[8] = float(cumulative_drift)   # <-- sets BEFORE ceiling check

# ... dim ceiling check at 995 iterates ALL risk_vector indices including 8 ...
    for i, rv_val in enumerate(risk_vector):
        if rv_val > dim_ceiling:
            ...  # cumulative_drift can trigger this
```
**FIX:** Either (a) exclude index 8 from the dim ceiling loop (check `i != 8`), or (b) check structural dims (0-7) separately from cumulative_drift (8), or (c) apply a separate threshold for cumulative_drift (e.g., `RC_DRIFT_DENY`).

---

## HIGH

### HIGH-1: Module-level `_load_cache` dict is an unbounded memory leak
**FILE:** `src/hooks/_rule_engine.py:81`
**ISSUE:** `_load_cache: Dict[str, Tuple[float, list[Rule]]] = {}` is a module-level dictionary with no size limit, no TTL, and no eviction. In CI environments or long-running processes that scan many different project roots, this dict grows without bound. Cache invalidation is only mtime-based -- stale entries for deleted projects are never removed. There is no `maxsize` or `lru_cache` wrapper.
**EVIDENCE:**
```python
# _rule_engine.py:81
_load_cache: Dict[str, Tuple[float, list[Rule]]] = {}

# Populated at line 263, never deleted:
_load_cache[cache_key] = (mtime, rules)
```
**FIX:** Use `functools.lru_cache(maxsize=128)` or implement manual eviction (max 100 entries, LRU). Add a `clear_cache()` function for test teardown.

---

### HIGH-2: `_CONSENSUS_HANDLE` singleton is never released -- permanent VRAM leak
**FILE:** `src/s2_core.py:751-772`
**ISSUE:** The bge-code consensus embedder handle is cached in a module-level `_CONSENSUS_HANDLE` variable and NEVER released. When `RC_CONSENSUS=1` and bge-code loads on GPU, it permanently consumes VRAM for the process lifetime. There is no `unload_consensus()` or `__del__` method. The FastAPI lifespan at line 1054 explicitly says "No teardown work" for the primary handle, and the consensus handle is equally neglected.
**EVIDENCE:**
```python
# s2_core.py:751
_CONSENSUS_HANDLE = None

# s2_core.py:771 (cached forever)
        _CONSENSUS_HANDLE = _try_load(ckpt, device, embedder_name="bge-code")
        return _CONSENSUS_HANDLE
```
**FIX:** Add an `unload_consensus()` function that sets `_CONSENSUS_HANDLE = None` and calls `torch.cuda.empty_cache()`. Wire it into the FastAPI lifespan `yield` cleanup block.

---

### HIGH-3: Regex fallback matches commented-out JS/TS imports as false positives
**FILE:** `src/hooks/_rule_engine.py:584-638`
**ISSUE:** The regex-based JS/TS import detection (`_check_forbid_import_js_ts_regex`) iterates source line-by-line and applies regex patterns WITHOUT checking if the match occurs inside a comment. A commented-out import like `// import X from "forbidden-module"` or `/* import X from "forbidden-module" */` will produce a false positive RuleHit. The regex also does not handle template literals or strings containing import-like text.
**EVIDENCE:**
```python
# _rule_engine.py:608-622
    for i, line in enumerate(src.split("\n"), 1):
        for m in import_re.finditer(line):
            mod = m.group(1)
            ...
        for m in require_re.finditer(line):
            mod = m.group(1)
            ...
```
**FIX:** Add a cheap comment-detection heuristic: skip lines where the match starts after `//` (line comment) or is between `/* */` pairs. Even a simple `if line.strip().startswith("//"): continue` would eliminate the most common false positive.

---

### HIGH-4: `--smoke` mode in run_ablation.py does NOT set `RC_EVAL_STUB_CLAUDE=1`
**FILE:** `eval/run_ablation.py:359-360`
**ISSUE:** The `--smoke` flag discovers smoke task IDs but does NOT set the `RC_EVAL_STUB_CLAUDE=1` environment variable that `run_task.sh:193` requires. In `RC_LIVE=1` mode, `run_ablation.py` calls `run_task.sh` which checks `RC_EVAL_STUB_CLAUDE` -- if unset, it tries to invoke the `claude` CLI, which fails. The `--smoke` flag is therefore broken for live runs. It only works in dry-run mode (`RC_LIVE != 1`) because the synthetic path bypasses `run_task.sh` entirely.
**EVIDENCE:**
```python
# run_ablation.py:359-360
    if args.smoke:
        tasks = _discover_smoke_tasks(args.n, args.seed)
# No RC_EVAL_STUB_CLAUDE=1 set anywhere in the file
```
**FIX:** Set `os.environ["RC_EVAL_STUB_CLAUDE"] = "1"` when `args.smoke` is true, OR document the requirement explicitly. Better: make `--smoke` imply stub mode automatically.

---

### HIGH-5: `gate_consensus()` function is completely untested
**FILE:** `src/hooks/_dispatch.py:522-557`
**ISSUE:** The `gate_consensus()` function -- which decides whether to emit warnings based on embedder disagreement (threshold: 0.2 AIS delta, Spearman rho >= 0.7 gating) -- has ZERO test coverage. There is no `test_consensus.py`. The function contains non-trivial logic: absolute difference check, spearman rho threshold comparison, and honest wording selection ("secondary_score_disagree" vs "consensus_disagree"). A bug in the rho threshold direction (e.g., `>` vs `<`) would go undetected.
**EVIDENCE:**
```bash
$ grep -rn "gate_consensus\|test_consensus" tests/
# No results
```
**FIX:** Add `tests/test_consensus.py` covering: (a) no consensus when RC_CONSENSUS != 1, (b) pass when agreement within 0.2, (c) stderr_only when disagreement > 0.2 with rho >= 0.7, (d) stderr_only when disagreement > 0.2 with rho < 0.7, (e) pass when consensus_score or AIS is None.

---

### HIGH-6: `_randomize_mamba_weights()` uses unseeded global RNG -- non-deterministic
**FILE:** `src/ssm_backbone.py:258-281`
**ISSUE:** The random-mamba control experiment calls `torch.randn_like(p.data)` without setting a random seed. The randomization is non-deterministic across process restarts, making A/B comparisons and regression tests impossible to reproduce. Additionally, the model is loaded by `AutoModel.from_pretrained()` (CPU), randomized on CPU, then moved to target device (line 342-350). For large models, this means CPU memory pressure during load that could be avoided by randomizing on the target device.
**EVIDENCE:**
```python
# ssm_backbone.py:271-275
    import torch
    randomized = 0
    for p in model.parameters():
        p.data = torch.randn_like(p.data)   # <-- unseeded, non-deterministic
```
**FIX:** Accept a seed parameter (default 42), call `torch.manual_seed(seed)` before randomization. Randomize AFTER `model.to(device)` to avoid CPU memory pressure.

---

### HIGH-7: `gate_id` parameter is not validated against allowed set
**FILE:** `src/hooks/audit_log.py:189-211`
**ISSUE:** The `new_event()` function accepts an arbitrary `gate_id: Optional[str]` and writes it directly to the audit event without validation. A buggy or malicious caller can inject any gate_id string. The `GATE_IDS` list exists in `run_ablation.py:46` but is not shared with `audit_log.py`. There is no single source of truth for valid gate IDs.
**EVIDENCE:**
```python
# audit_log.py:189
    gate_id: Optional[str] = None,  # which gate emitted this event

# audit_log.py:210-211
    if gate_id is not None:
        base["gate_id"] = gate_id   # <-- no validation
```
**FIX:** Define `ALLOWED_GATE_IDS = frozenset({...})` in a shared constants module, validate `gate_id in ALLOWED_GATE_IDS` before writing, log a warning and drop invalid values.

---

## MEDIUM

### MEDIUM-1: Codestral-Mamba (14.6GB FP16) has no GPU memory pressure handling
**FILE:** `src/ssm_backbone.py:284-407`
**ISSUE:** Loading a 14.6GB model on a GPU with insufficient VRAM will raise a CUDA OOM. The `_try_load()` function catches generic `Exception` at line 405 and returns None, but this catch happens AFTER `AutoModel.from_pretrained()` has already attempted to load the full model into memory. On systems with limited VRAM, the process may be killed by the OOM killer before the exception handler fires. There is no `torch.cuda.get_device_properties()` check, no `torch.cuda.empty_cache()` call, no `accelerate` library integration, and no `device_map="auto"` for model sharding across multiple GPUs.
**EVIDENCE:**
```python
# ssm_backbone.py:333-335
        model = AutoModel.from_pretrained(
            ckpt, revision=revision, trust_remote_code=False,
        )  # <-- loads FULL model into memory, no VRAM check
```
**FIX:** Add a pre-load VRAM check: `torch.cuda.get_device_properties(device).total_memory - torch.cuda.memory_allocated() > estimated_model_size`. Use `accelerate.init_empty_weights()` for large models. Consider `device_map="auto"` for multi-GPU setups.

---

### MEDIUM-2: `_minimal_yaml_parse()` has zero dedicated test coverage
**FILE:** `src/hooks/_rule_engine.py:745-984`
**ISSUE:** The minimal YAML parser is the critical fallback path when PyYAML is not installed (security-constrained environments). It is 240 lines of complex hand-rolled state machine logic with nested loops, indentation tracking, and stack manipulation -- but zero dedicated tests. The existing tests all use PyYAML (which is installed in the test environment). A regression in the minimal parser would only be discovered in production environments where PyYAML is absent.
**EVIDENCE:**
```python
# _rule_engine.py:745
def _minimal_yaml_parse(text: str) -> Optional[dict]:
    """Parse a minimal subset of YAML..."""
    # ~240 lines of hand-rolled parsing logic

# test_rule_engine.py uses PyYAML for all fixtures -- no test_minimal_yaml_parser.py exists
```
**FIX:** Add `tests/test_minimal_yaml_parser.py` with fixtures covering: nested mappings, lists of scalars, lists of mappings, mixed nesting, quoted strings, comment handling, and malformed input.

---

### MEDIUM-3: `compare_embedders.py` synthetic pairwise Cohen's d uses non-independent groups
**FILE:** `eval/compare_embedders.py:303-322`
**ISSUE:** The pairwise Cohen's d calculation generates synthetic group data using `rng.gauss()` with per-arm means/stds (line 305-308). The two groups are independently sampled random data, not paired samples from the same underlying edits. This means the Cohen's d is computed on uncorrelated noise rather than correlated paired observations. For embedder comparison, the correct statistical approach is paired differences (same edit pair evaluated by both embedders), not independent groups.
**EVIDENCE:**
```python
# compare_embedders.py:304-308
            group_i = [rng.gauss(results[arm_i].get("raw_l2_mean", 0.3),
                                  results[arm_i].get("raw_l2_std", 0.1)) for _ in range(n)]
            group_j = [rng.gauss(results[arm_j].get("raw_l2_mean", 0.3),
                                  results[arm_j].get("raw_l2_std", 0.1)) for _ in range(n)]
```
**FIX:** Use paired sampling: generate a shared latent vector of edit difficulties, then produce each arm's scores as correlated draws around that latent vector. This preserves the expected positive correlation between embedders on the same edits.

---

### MEDIUM-4: `test_backbone_swap.py` -- only actual load test is unconditionally skipped
**FILE:** `tests/test_backbone_swap.py:92-103`
**ISSUE:** The `@pytest.mark.skip(reason="requires cached HF weights")` decorator unconditionally skips the ONLY test that validates actual backbone loading (`test_load_backbone_codestral_mamba`). All other tests in the file only test constants and resolution logic without ever loading weights. This means there is NO CI coverage for: weight download, tokenizer initialization, `embed()` output shape verification, or the randomization path. The skip should be `pytest.importorskip`-style based on cache availability, not unconditional.
**EVIDENCE:**
```python
# test_backbone_swap.py:92
@pytest.mark.skip(reason="requires cached HF weights")
def test_load_backbone_codestral_mamba():
    """Skipped by default -- requires 14.6GB FP16 weights in HF cache."""
```
**FIX:** Use conditional skip: check if weights are cached via `huggingface_hub.try_to_load_from_cache()`, skip only if not present. Run the test on CI runners that have the weights.

---

### MEDIUM-5: `gate_rule_engine` integration tests miss shadow, warn, SystemExit, and Exception paths
**FILE:** `tests/test_rule_engine.py:745-813`
**ISSUE:** Tests 19-21 cover disabled, enabled-deny, and enabled-clean paths. Missing coverage: (a) `shadow_hits` path (rule timeout), (b) `warn_hits` path (severity="warn" rules), (c) `SystemExit` propagation path when schema error + strict mode, (d) generic `Exception` handling path (line 605-614), (e) lenient-mode SystemExit catch (lines 594-604).
**EVIDENCE:**
```python
# test_rule_engine.py:745-813 -- only tests 19-21 cover gate_rule_engine
# No test for shadow_hits (lines 642-649 of _dispatch.py)
# No test for warn_hits (lines 651-659)
# No test for SystemExit catch (lines 594-604)
# No test for generic Exception catch (lines 605-614)
```
**FIX:** Add tests for each path: (a) monkeypatch `_check_rule` to return a shadow hit, (b) create a "warn" severity rule and verify `stderr_only` action, (c) trigger a schema error via corrupted rules.yaml with lenient=0 and verify SystemExit is raised, (d) mock `evaluate_edit` to raise Exception and verify lenient/graceful handling.

---

### MEDIUM-6: `run_ablation.py` dry-run mode generates correlated synthetic data
**FILE:** `eval/run_ablation.py:400-416`
**ISSUE:** The synthetic token counts in dry-run mode use `synthetic_tokens = 1000 - arm_idx * 50` which creates perfectly deterministic, perfectly correlated data across arms. The bootstrap CI computation on this data will produce artificially narrow CIs because the synthetic data has zero variance within each arm. This makes dry-run mode useless for validating the statistical pipeline -- it tests the plumbing but not the math.
**EVIDENCE:**
```python
# run_ablation.py:403-404
                arm_idx = int(arm, 2)
                synthetic_tokens = 1000 - arm_idx * 50  # vanishing savings as more gates
```
**FIX:** Add controlled randomness to synthetic data: `synthetic_tokens = 1000 - arm_idx * 50 + rng.gauss(0, 20)` to validate that the bootstrap pipeline handles variance correctly.

---

## LOW

### LOW-1: Magic numbers are not centrally documented or env-configurable
**FILE:** Multiple files
**ISSUE:** The following magic numbers are hardcoded and not centrally documented or made configurable via environment variables:

| Number | File | Line | Meaning |
|--------|------|------|---------|
| 50 | `_rule_engine.py` | 30 | Max rules cap |
| 5.0 | `_rule_engine.py` | 31 | Per-rule budget (ms) -- NOT a real timeout |
| 0.2 | `_dispatch.py` | 539 | AIS disagreement threshold for consensus warning |
| 0.7 | `_dispatch.py` | 541 | Spearman rho threshold for "honest wording" gate |
| 8192 | `ssm_backbone.py` | 59 | codestral-mamba max sequence length |
| 512 | `ssm_backbone.py` | 60-64 | All other embedders max sequence length |
| 0xC0DEC0DE | `ssm_backbone.py` | 508 | Embedding determinism seed |
| 4096 | `ssm_backbone.py` | 622 | Max AST nodes in tokenization |
| 4096 | `s2_core.py` | 230 | Max parse error walk steps |
| 1000 | `s2_core.py` | 142 | Metrics ring buffer size |
| 20.0, 8.0, 12.0, 40.0, 200.0, 40.0, 1.0 | `s2_core.py` | 621-668 | Risk dimension normalization scales |
| 32 | `s2_core.py` | 921 | Cold-start minimum source length |
| 0.4, 1.5, 0.9 | `s2_core.py` | 699-704 | Regression threshold defaults (env-overridable) |
| 2.0, 0.3, 0.95 | `s2_core.py` | 731 | test_code per-kind thresholds |
| 3.0, 0.3, 1.0 | `s2_core.py` | 732-733 | plan_md / doc_md thresholds |
| 1.2, 0.5, 0.9 | `s2_core.py` | 734 | config thresholds |
| 10000 | `run_ablation.py` | 64 | Bootstrap resample count |
| 0.95 | `run_ablation.py` | 137 | Bootstrap CI level |
| 900 | `run_ablation.py` | 63 | Task timeout (seconds) |
| 3.0 | `compare_embedders.py` | 58 | Sigma separation threshold |
| 4.0 | `_dispatch.py` | 387 | RC_DRIFT_WARN default |
| 6.0 | `_dispatch.py` | 388 | RC_DRIFT_DENY default |
| 90 | `audit_log.py` | 68 | Audit retention days |
| 5GB | `audit_log.py` | 69 | Audit disk cap |
| 120 | `audit_log.py` | 287 | Retry window seconds |
| 3600 | `audit_log.py` | 342, 378 | Marker GC cutoff (seconds) |

**The following ARE already env-configurable (good practice):**
- `S2_AIS_THRESHOLD`, `S2_COHERENCE_THRESHOLD`, `S2_RISK_DIM_THRESHOLD` (s2_core.py)
- `RC_DRIFT_WARN`, `RC_DRIFT_DENY` (_dispatch.py)
- `RC_AUDIT_RETENTION_DAYS`, `RC_AUDIT_CAP_BYTES` (audit_log.py)

**The following should be env-configurable:**
- Max rules cap (50)
- Per-rule budget (5.0ms) -- once it becomes a real timeout
- Bootstrap resample count (10000) -- needed for faster CI runs
- Max sequence lengths per embedder
- Embedding seed (0xC0DEC0DE)
- Metrics ring buffer size (1000)
- Risk dimension normalization scales

---

### LOW-2: `evaluate_edit` uses mutable default slice `rules[:_MAX_RULES]`
**FILE:** `src/hooks/_rule_engine.py:149`
**ISSUE:** The `rules[:_MAX_RULES]` slice creates a shallow copy of the list but the check is inside the loop. If a caller passes a mutable list that is modified during iteration, the behavior is undefined. More importantly, the slice is recomputed on every call even though `_MAX_RULES` is a constant.
**EVIDENCE:**
```python
# _rule_engine.py:149
    for rule in rules[:_MAX_RULES]:
```
**FIX:** Document that `rules` is expected to be pre-truncated by `load_rules()`. Add an assertion or explicit truncation at the top of the function.

---

### LOW-3: `_walk()` generator silently truncates at 8192 steps for depth calculation
**FILE:** `src/s2_core.py:568-586`
**ISSUE:** `_max_depth()` silently caps the DFS walk at 8192 steps, returning a potentially incorrect depth for deeply nested ASTs. A file with >8192 nodes will report a truncated depth, leading to an under-estimated depth delta and a missed regression signal.
**EVIDENCE:**
```python
# s2_core.py:575
    while stack and steps < 8192:
```
**FIX:** Log a warning when the step limit is reached. Make the limit configurable via env var.

---

### LOW-4: `run_task.sh` hardcodes `bash` instead of using `/usr/bin/env bash`
**FILE:** `eval/run_task.sh:1`
**ISSUE:** The shebang is `#!/usr/bin/env bash` (good), but `run_ablation.py:175` hardcodes `"bash"` as the command which may resolve to a different bash than intended on systems with multiple bash installations.
**EVIDENCE:**
```python
# run_ablation.py:175
    cmd = ["bash", str(script), task_id, arm_code, str(out_dir)]
```
**FIX:** Use `sys.executable` pattern or `"/usr/bin/env", "bash"` for consistency.
