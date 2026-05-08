---
date: 2026-05-08
commit: 5b1599a4b556b070fe08e94f0662cb212a1b7988
branch: main
ticket: n/a
status: draft-v4-post-verification
---
# Plan: Replicate the Claude Code reasoning-core flow on Gemini CLI, GitHub Copilot, and Mistral Vibe CLI

## Summary
Port the reasoning-core integration surface (lifecycle hooks -> S2 sidecar, SKILL.md, MCP server, one-command repo enablement) from Claude Code to Gemini CLI, `gh copilot` interactive CLI, and Mistral Vibe CLI. Best-effort parity: hooks where the host supports them, MCP-as-hook-proxy where it doesn't, with explicit gap notes per host.

## Verification gate — RESOLVED 2026-05-08

Hands-on probes against the installed binaries:

- **Gemini CLI v0.37.1** (`/opt/homebrew/bin/gemini`) — CONFIRMED. Has `gemini hooks`, `gemini skills`, `gemini mcp` subcommands. Critically: `gemini hooks migrate` is "Migrate hooks from Claude Code to Gemini CLI" — Gemini's hook surface is Claude-compatible by design. Original research's `BeforeTool`/`AfterTool`/`PreCompress` rename set was WRONG; use Claude event names directly. `--yolo` / `--approval-mode yolo` for headless trust bypass.
- **GitHub Copilot CLI v1.0.29** (`/opt/homebrew/bin/copilot`) — CONFIRMED as the standalone `copilot` binary, NOT `gh copilot`. The `gh copilot` command is a wrapper that downloads/executes the standalone binary. `~/.copilot/mcp-config.json` is the real config path (referenced by `--additional-mcp-config` flag). `--allow-all-tools` (env: `COPILOT_ALLOW_ALL`) for headless. `--config-dir` overrides config root. Phase 3 targets `copilot`, not `gh copilot`.
- **Mistral Vibe v2.9.4** (`/Users/jakubsikora/.local/bin/vibe`) — CONFIRMED. Headless `--prompt`, `--trust` (not persisted to `trusted_folders.toml`), `--enabled-tools`, `--max-turns`, `--max-price`, `--agent` (builtin: default/plan/accept-edits/auto-approve, custom from `~/.vibe/agents/<name>.toml`), `--workdir`. Product exists despite power-user's hallucination concern.

Pre-Phase-1a stdin audit (also resolved 2026-05-08):
- Direct `sys.stdin.read()` + `json.loads` in 7 hooks. There is **no `_parse_stdin` function** to monkeypatch — senior dev's concern about preserving monkeypatch contracts is moot.
- `pre_edit_guard.py:169` mutates `payload["session_id"]` before the sidecar POST. Adapters must thread `session_id` back through the envelope's `raw` dict (or set on a typed field) for round-trip.
- Field surface in `pre_edit_guard.py`: `payload["tool_name"]`, `payload["tool_input"]`, `payload["session_id"]`. Other hooks may pull `transcript_path`, `cwd`, `hook_event_name`, `permission_mode` — to be enumerated in the Phase-1a PR description (NOT a separate research doc — power-user reviewer flagged that as process theatre).

## Research References
- Gemini CLI parity matrix (research subagent 2026-05-08): gemini-cli docs, hooks reference, GEMINI.md spec, MCP docs.
- GitHub Copilot parity matrix (research subagent 2026-05-08): VS Code hooks Preview Feb 2026, coding-agent hooks reference, MCP docs, agent-skills docs, enterprise-managed-plugins changelog 2026-05-06.
- Vibe CLI parity matrix (research subagent 2026-05-08): github.com/mistralai/mistral-vibe v2.9.5 2026-05-07, docs.mistral.ai, issues #250 and #531.
- Existing Claude integration: `.claude/settings.json`, `.claude/skills/reasoning/SKILL.md`, `src/hooks/*.py`, `src/mcp_reasoner.py`, `scripts/enable-in-repo.sh`.

## Cross-cutting Architecture Decisions

1. Single sidecar. All hosts hit `http://127.0.0.1:8765/score`. No new transport.
2. Per-CLI top-level dirs. `.gemini/`, `.github/`, `.vibe/` as siblings to `.claude/`. The existing `.claude/` stays put.
3. Shared hook bodies, host-specific adapters. Keep all gating logic in `src/hooks/_dispatch.py` and helpers. Host-specific entry points live in `src/hooks/adapters/{gemini,copilot,vibe}.py` and only translate the host's stdin JSON envelope into the shared `(tool_name, tool_input, file_path, before_src, after_src)` shape that the existing dispatch consumes. No business logic duplicated.
4. Env-var convention. `RC_REPO` stays the source of truth. Each host gets its own project-dir env in adapters: `CLAUDE_PROJECT_DIR`, `GEMINI_PROJECT_DIR`, `GITHUB_WORKSPACE` (Copilot), `VIBE_PROJECT_DIR`. Adapter resolves to a single `_project_dir()`.
5. Skill content reused verbatim. The SKILL.md body is host-agnostic. Template-copy the reasoning skill into each host's skill dir; adjust frontmatter only.
6. MCP server is host-agnostic. `python3 -m src.mcp_reasoner` registers identically in every host's MCP config; only the field name (`mcpServers` vs `servers` vs `[[mcp_servers]]`) and config path change.
7. Audit log unified. Every adapter writes to the same `audit_log.py` sink so eval comparisons stay apples-to-apples per host.

## Phase 0.5: Host-agnostic env + concurrency cleanup (NEW — review-driven)

Reviews surfaced three latent couplings that block any multi-host work. Land this phase before Phase 1.

### Changes

#### File: `src/hooks/_host_env.py` (new)
- What: Single shim with `project_dir() -> Path`, `session_id() -> str`, `host() -> str`. **Host detected via explicit `RC_HOST` env (set by each `enable-in-repo-*.sh` in `.envrc`)**, NOT via "which `*_SESSION_ID` is set first" (v3 review correction — that was spoofable when a Vibe child shell inherited `CLAUDE_SESSION_ID` from a prior session). Priority chain: `RC_PROJECT_DIR` > `<RC_HOST>_PROJECT_DIR` > `os.getcwd()`. **`envelope.cwd` is NOT in the priority chain.** For `session_id()`: same priority. If no host env is set, synthesize a stable per-launch UUID via `RC_SESSION_ID` and export it for child processes.
- Detection contract: `host()` returns `os.environ["RC_HOST"]` if set; else falls back to the single host whose `*_PROJECT_DIR` env is set (one match → that host; zero matches → `"unknown"`; multiple matches → ERROR with clear message instructing user to set `RC_HOST` explicitly).
- Rationale: Today `audit_log.py:85,89,95,100`, `_guard_paths.py:60-63`, `_session_manifest.py`, `_calibration_gate.py:69`, `pre_edit_guard.py:162` read `os.environ.get("CLAUDE_*")` directly. On non-Claude hosts those return `""`, collapsing all sessions and corrupting retry-after-block detection. Cross-host fallback (`CLAUDE_*` ahead of `GEMINI_*`) breaks when a user runs `gemini` from a shell that exported `CLAUDE_PROJECT_DIR` from a prior Claude session.

#### Sweep: replace direct `os.environ.get("CLAUDE_PROJECT_DIR")` / `CLAUDE_SESSION_ID` reads
- Where: `src/hooks/audit_log.py`, `src/hooks/_guard_paths.py`, `src/hooks/_session_manifest.py`, `src/hooks/_calibration_gate.py`, `src/hooks/_dispatch.py:155`, `src/hooks/_dispatch.py:198`, `src/hooks/pre_edit_guard.py:162`, plus any others surfaced by `grep -rn 'CLAUDE_PROJECT_DIR\|CLAUDE_SESSION_ID' src/`.
- What: Replace each call with `_host_env.project_dir()` / `_host_env.session_id()`.
- Rationale: Keeps `_dispatch.py` host-agnostic by construction. The adapter layer in Phase 1 cannot fix this from the outside.

#### File: `src/hooks/audit_log.py` (modify)
- What: Wrap append paths in `portalocker.Lock` (POSIX flock + Windows msvcrt unified). Add `host` column to schema (default `"claude"` for back-compat). Bump `SCHEMA_VERSION` to 2.
- `portalocker` becomes a hard dependency in `requirements.txt`. Power-user reviewer rejected the hand-rolled msvcrt fallback (locks byte ranges, easy to get wrong); `portalocker` is MIT, used by pip, zero deps.

#### Reader+writer JSONL audit (review-driven, was missing)
- All JSONL streams in `/tmp/rc-events/` and replay readers must be enumerated and updated:
  - **Writers**:
    - `src/hooks/post_batch_lang_audit.py:70` — writes `{sid}.jsonl` directly. MUST be routed through `audit_log.append_event` (or get its own portalocker wrapper). Today bypasses the new lock.
    - `src/_supervisor_recalibrate.py:78` — writes a separate JSONL stream; mark "out of scope, no host column needed" explicitly.
    - `src/gen_client.py:47` — `~/.local/share/reasoning-core/events/gen_fallback.jsonl`; out of scope.
  - **Readers** (must tolerate v1 rows in-place — no migration required):
    - `eval/aggregate.py:87` (`audit_dir.rglob("*.jsonl")`).
    - `src/rc_cli.py:116,156`.
- Rationale: Senior dev v3 flagged that the v2 reader-tolerance promise was abstract; concrete file list closes the gap.

#### File: `src/hooks/_audit_migrate.py` (new — review-driven, was missing from ownership table)
- What: One-shot migration helper. Reads existing `audit_log.jsonl`, defaults missing `host` to `"claude"`, writes back with `SCHEMA_VERSION=2` header. Reader code in `audit_log.py` MUST also tolerate v1 rows in-place (no migration required) so eval replay scripts survive.
- Rationale: Senior dev flagged migration spec was prose-only.

#### File: `src/hooks/_dispatch.py` (modify)
- What: Thread `envelope.cwd` through `gate_lang_lock` and `_resolve_plan_path` so they no longer read `CLAUDE_PROJECT_DIR` directly.
- Rationale: The "shared dispatch chain" promise breaks if it reaches into Claude-only env vars.

#### File: `tests/test_host_env.py` (new — placed next to existing tests, NOT under `tests/hooks/`)
- What: Per-host env-var resolution table. Concurrency test: spawn ≥16 processes appending to the same `audit_log.jsonl`, with `fsync` between writes; assert no row corruption, total row count matches, no torn JSON. Skipped on Windows if cross-platform lock falls back to no-op.
- Rationale: Both reviewers flagged 4-process test is too lax under load.

### Success Criteria

#### Automated Verification
- [ ] `pytest tests/ -q` passes (entire suite — existing audit-log replay tests must still pass with `host="claude"` default).
- [ ] `tests/test_host_env.py` concurrency test passes 100 runs.
- [ ] `grep -rn 'CLAUDE_PROJECT_DIR\|CLAUDE_SESSION_ID' src/hooks/` returns ZERO hits (only `_host_env.py` may read those, and only inside the priority chain).

#### Manual Verification
- [ ] Existing `claude` workflow unchanged: run a session, verify audit-log entries still tagged with the same Claude session_id format.

### Dependencies
- Requires: nothing.
- Blocks: Phase 1a, 1b, every per-host phase.

## Phase 1a: Adapter canary — single hook, byte-identical (was Phase 1, split per review)

### Changes

#### File: `src/hooks/_envelope.py` (new)
- What: Frozen dataclass `HookEnvelope` with the shared fields the dispatch chain needs (`event`, `tool_name`, `tool_input`, `file_path`, `before_src`, `after_src`, `cwd`, `session_id`, `prompt`, `host`).
- Where: New file.
- Rationale: Today `pre_edit_guard.py` parses Claude's stdin JSON inline. Other hosts use different envelopes. Centralising the shape lets adapters do the only work that differs.
- Code sketch:
  ```python
  @dataclass(frozen=True)
  class HookEnvelope:
      event: str
      host: str
      tool_name: str | None
      tool_input: dict
      file_path: str | None
      before_src: str | None
      after_src: str | None
      cwd: str
      session_id: str | None
      prompt: str | None
  ```

#### File: `src/hooks/adapters/__init__.py` (new) and `claude.py` (new)
- What: Move the Claude-specific stdin parser out of `pre_edit_guard.py` into `adapters/claude.py::parse_stdin(event)` returning `HookEnvelope`.
- Rationale: Existing tests must remain byte-identical - extract, don't rewrite.

#### File: `src/hooks/pre_edit_guard.py` (modify)
- What: Replace inline stdin parsing with `envelope = adapters.claude.parse_stdin("PreToolUse")`. Pass envelope into the dispatch chain.
- Where: `main()`.

#### Files: `pre_plan_guard.py`, `pre_bash_guard.py`, `pre_task_guard.py`, `post_bash_revive.py`, `post_batch_lang_audit.py`, `session_start_manifest.py`, `session_resume_inject.py`, `pre_compact_guard.py`
- What: Same refactor as `pre_edit_guard.py`.
- Rationale: All hooks must be adapter-agnostic before phase 2 starts.

### Phase-1a scope (canary)

Only refactor `pre_edit_guard.py` + introduce `_envelope.py` + `adapters/__init__.py` + `adapters/claude.py`. Do not touch the other 7 hooks. The existing `tests/test_hook_block.py`, `tests/test_pre_bash_guard.py`, `tests/test_iter3_wiring_smoke.py`, `tests/test_lang_invariants.py`, `tests/test_plan_grounding.py`, `tests/test_guard_paths.py` all run subprocess-style and MUST stay green byte-identical.

#### Adapter parse-failure contract (review-required, was missing)
- `adapters.claude.parse_stdin(event)` MUST NEVER raise. On malformed JSON, missing fields, or unknown event: return `HookEnvelope(event=event, host="claude", tool_name=None, tool_input={}, file_path=None, before_src=None, after_src=None, cwd=_host_env.project_dir(), session_id=_host_env.session_id(), prompt=None, raw={})` and emit an `audit_log` row with `decision="malformed_payload"`.
- Equivalent contract applies to `gemini.py`, `copilot.py`, `vibe.py` in their respective phases.
- Per-adapter test fixture: `garbage_stdin.txt` (random bytes) → expect HookEnvelope with `tool_name=None`, exit 0, audit row written.
- `cwd` defaults to `_host_env.project_dir()`, NOT `os.getcwd()` — review-driven correction (Claude launches hooks from subdirs).

#### `HookEnvelope` shape — `raw` escape hatch (review-driven)
- The frozen dataclass MUST include `raw: types.MappingProxyType` (immutable view over a captured dict — power-user v3 flagged that a bare `dict` on a frozen dataclass is mutable, breaking the freeze guarantee).
- Existing hooks read fields not yet enumerated in the envelope (`transcript_path`, `permission_mode`, `hook_event_name`); freezing without an escape hatch breaks them silently.

#### Pre-Phase-1a audit (resolved 2026-05-08)
- Probe completed; key findings:
  - **No `_parse_stdin` function exists.** All 7 hooks read `sys.stdin.read()` then `json.loads`. There is nothing to monkeypatch — senior dev v2 concern moot.
  - `pre_edit_guard.py:169` mutates `payload["session_id"] = sid` before sidecar POST. Adapter must round-trip session_id through `HookEnvelope.raw` (or set on the typed `session_id` field).
  - Field surface confirmed: `tool_name`, `tool_input`, `session_id` (line 310-311). Other hooks may pull `transcript_path`, `cwd`, `hook_event_name`, `permission_mode`. Phase-1a PR description (not a separate `.md` doc — power-user reviewer flagged process theatre) MUST include the full field-usage matrix with a yes/no column "covered by typed envelope field" for each, plus an explicit "no other fields used" assertion. PR is blocked on this matrix being complete.
- Acceptance criterion: PR description has the matrix; reviewer can sign off without further investigation.

#### Back-compat shim
- Keep `pre_edit_guard._parse_stdin` as a thin wrapper. Whether it returns `dict` or `HookEnvelope` depends on the audit above.

### Success Criteria

#### Automated Verification
- [ ] Existing test suite passes byte-identical: `pytest tests/ -q` (existing tests live at `tests/test_*.py`, NOT `tests/hooks/`).
- [ ] New: `pytest tests/test_adapter_claude.py -q` (claude adapter unit tests, including garbage-stdin fixture).
- [ ] No new `mypy` errors in `src/hooks/`.

#### Manual Verification
- [ ] Run `claude` in a small repo. Sample 3 edits, audit-log entries are bit-identical to a pre-Phase-1a capture.

### Dependencies
- Requires: Phase 0.5.
- Blocks: Phase 1b.

## Phase 1b: Adapter sweep — remaining 7 hooks

### Changes

Apply the same refactor as 1a to: `pre_plan_guard.py`, `pre_bash_guard.py`, `pre_task_guard.py`, `post_bash_revive.py`, `post_batch_lang_audit.py`, `session_start_manifest.py`, `session_resume_inject.py`, `pre_compact_guard.py`. Each gets a back-compat shim.

`pre_compact_guard.py` was previously duplicated in 1a's scope text — it lives in 1b only (review-driven dedup).

#### SessionStart vs PreToolUse envelope divergence (review-driven)
- `session_start_manifest.py` and `session_resume_inject.py` fire on SessionStart and have no `tool_name`/`tool_input`/`file_path` fields. The adapter layer must emit a separate `HookEnvelope` shape for these events: `tool_name=None`, `tool_input={}`, `file_path=None`, but `transcript_path` and `cwd` are populated from the host's SessionStart payload.
- `transcript_path: str | None` is required on `HookEnvelope`. Claude provides it; Gemini/Vibe/Copilot may not. Adapter sets `None` if absent. `session_resume_inject.py` must handle `None` gracefully (skip resume injection on hosts without transcripts).
- Rationale: Power-user flagged session_resume_inject's transcript dependency; senior dev flagged the boot-ordering / SessionStart-vs-PreToolUse divergence.

### Success Criteria

#### Automated Verification
- [ ] `pytest tests/ -q` passes.
- [ ] `grep -rn 'json.load(sys.stdin)' src/hooks/` returns hits ONLY inside `src/hooks/adapters/`.

#### Manual Verification
- [ ] Full Claude session — all hook events fire, audit log compares clean against pre-Phase-1 capture.

### Dependencies
- Requires: Phase 1a.
- Blocks: Phases 2, 3, 4.

## Phase 2: Gemini CLI integration

Verification (2026-05-08) confirmed: Gemini v0.37.1 uses **Claude-compatible event names** (`gemini hooks migrate` is literally "Migrate hooks from Claude Code"). Hook schema, MCP schema, skills directory layout all map directly. Headless trust: `--yolo` or `--approval-mode yolo`.

### Changes

#### Files: `.gemini/settings.json.template` (new, committed) + `.gemini/settings.json` (gitignored, generated by install script)
- What: Template ships absolute path placeholders (`<RC_REPO>`); install script substitutes the real path into the gitignored `.gemini/settings.json`. Power-user v3 flagged that committing a per-machine absolute path breaks portability.
- Mirror `.claude/settings.json` event names directly; only `tool_name` strings differ (Gemini lowercases to `write_file`/`edit_file`/`run_shell_command` — adapter normalises).
- `.gitignore` entries: `.gemini/settings.json` (NOT the `.template`).
- Code sketch (event names assume Claude-compatible per reviewer; abbreviated; absolute path placeholder `<RC_REPO>` resolved by install script):
  ```json
  {
    "hooks": {
      "PreToolUse": [
        { "matcher": "write_file|edit_file", "hooks": [{ "type": "command", "command": "python3 <RC_REPO>/src/hooks/pre_edit_guard.py", "timeout": 60000 }] },
        { "matcher": "run_shell_command",    "hooks": [{ "type": "command", "command": "python3 <RC_REPO>/src/hooks/pre_bash_guard.py" }] }
      ],
      "Stop": [{ "hooks": [{ "type": "command", "command": "python3 <RC_REPO>/src/hooks/pre_compact_guard.py" }] }]
    },
    "mcpServers": {
      "hybrid-reasoner": {
        "command": "python3",
        "args": ["-m", "src.mcp_reasoner"],
        "cwd": "<RC_REPO>",
        "env": {}
      }
    }
  }
  ```
  Note: `cwd` is REQUIRED — without it, `python3 -m src.mcp_reasoner` raises `ModuleNotFoundError` when the MCP server is launched from `$HOME` (per power-user reviewer).

#### File: `src/hooks/adapters/gemini.py` (new)
- What: `parse_stdin(event)` mapping Gemini's payload (`tool_name`, `tool_input`, `session_id`, `cwd`) into `HookEnvelope(host="gemini", ...)`. Tool-name normalisation: `write_file -> Write`, `edit_file -> Edit`, `run_shell_command -> Bash`, `task -> Task`.

#### File: `.gemini/skills/reasoning/SKILL.md` (new)
- What: Copy the body of `.claude/skills/reasoning/SKILL.md`. Drop Claude-specific frontmatter fields not in the agentskills.io spec; keep `name` and `description`.

#### File: `GEMINI.md` (new, committed at repo root)
- What: Project-level context file. Brief: this repo runs the System 2 sidecar on :8765; hooks wired in `.gemini/settings.json`; use the `reasoning` skill to interpret risk vectors.
- Rationale: Gemini's auto-loaded project context is `GEMINI.md`.

#### File: `scripts/enable-in-repo-gemini.sh` (new)
- What: Same idempotent pattern as `enable-in-repo.sh` but writes `.gemini/settings.json`, `GEMINI.md`, `.envrc`. Refuses to overwrite. Adds entries to `.gitignore`.

#### File: `tests/test_adapter_gemini.py` (new)
- What: Replay 6 fixtures matching whatever event names verification confirms. If Claude-compatible: PreToolUse (write_file, edit_file, run_shell_command), PostToolUse, SessionStart, Stop. If the rename set proves correct: BeforeTool / AfterTool / PreCompress equivalents. **Fixture file names mirror confirmed event names** — internal contradiction (was: fixtures listed BeforeTool/PreCompress while body assumed Claude-compatible names) is fixed by deferring to verification output.

#### Run-shell-command timeout (review-driven)
- The `run_shell_command` matcher MUST set explicit `timeout: 30000` (30s) — Gemini's default tool timeout is 5s in some versions, which kills `pre_bash_guard.py` if httpx cold-starts. Power-user flagged from daily use.

#### `<RC_REPO>` substitution mechanism (review-driven)
- The install script (`enable-in-repo-gemini.sh`) MUST resolve `<RC_REPO>` to the absolute path of the user's reasoning-core checkout at write time — `.gemini/settings.json` is committed with absolute paths. The script aborts with a clear error if `RC_REPO` env is unset or points to a stale checkout.
- Snapshot tests in Phase 5 MUST normalise these absolute paths before hashing (e.g. via `sed 's|<RC_REPO>|/path/to/reasoning-core|g' | sha256sum` then revert) to avoid per-machine snapshot drift.

### Success Criteria

#### Automated Verification
- [ ] `pytest tests/test_adapter_gemini.py -q` passes.
- [ ] `bash scripts/enable-in-repo-gemini.sh` in a fresh tmp dir produces the expected files; rerun fails with the existing-files error.
- [ ] JSON parses: `python3 -c "import json; json.load(open('.gemini/settings.json'))"`.

#### Manual Verification
- [ ] `gemini` (v0.40+) launched in a test repo loads the `reasoning` skill (visible via `/skills`).
- [ ] Edit a Python file with a known bad diff (matches Phase-1 fixture); `BeforeTool` blocks with the same human-readable message Claude produces.
- [ ] Sidecar at :8765 must be running; confirm with `curl -fsS http://127.0.0.1:8765/health`.

### Dependencies
- Requires: Phase 1.
- Blocks: Phase 6.

### Gaps recorded
- No regex-piped matchers. Replicated via multiple matcher entries.
- No Task-specific event. Wired into `BeforeAgent` instead; sub-agent gating is coarser on Gemini.
- **Trust prompt on first MCP server load** (`Trust this MCP server? y/N`) blocks headless eval. Phase 6 spawner pre-populates `~/.gemini/trusted_mcp.json` (or passes a trust-all flag per Gemini version).
- `GEMINI.md` is per-cwd: running `gemini` from a parent dir does not pick it up. Documented in `docs/CLI_PARITY.md`.

## Phase 3: GitHub Copilot CLI integration (standalone `copilot` binary)

Verification (2026-05-08) resolved: `gh copilot` is a wrapper that downloads/execs the **standalone `copilot` binary** (v1.0.29 confirmed). Phase 3 targets `copilot` directly, not `gh copilot`. Real surface: `~/.copilot/mcp-config.json` (MCP), `--allow-all-tools` / `COPILOT_ALLOW_ALL` env (headless), `--config-dir` (config root override), `--additional-mcp-config` (per-session MCP merge), `--add-dir` (path allowlist), `--allow-tool=` / `--deny-tool=` (per-tool gate). **Hook system existence is still TBD** — `copilot --help` did not surface hook subcommands in the probe; needs deeper inspection (`copilot hooks --help`, install-tree grep). If no hook system, Phase 3 collapses to MCP + skill + instructions; runtime gate is `gate_edit` MCP tool only.

VS Code Copilot Chat and Copilot coding agent remain out of scope.

### Changes

#### File: `.copilot/hooks.json` (new, project root)
- What: Single combined hooks file in cwd that `gh copilot` reads. All 6 events (`sessionStart`, `userPromptSubmitted`, `preToolUse`, `postToolUse`, `errorOccurred`, `sessionEnd`) shelling out to `python3 src/hooks/<name>.py` resolved via `RC_REPO`.
- Rationale: `gh copilot` only loads one file in cwd; consolidate.
- Code sketch:
  ```json
  {
    "version": 1,
    "hooks": {
      "preToolUse": [
        { "matcher": "write|edit|str_replace_editor",
          "bash": "python3 \"$RC_REPO/src/hooks/pre_edit_guard.py\"" },
        { "matcher": "bash|run",
          "bash": "python3 \"$RC_REPO/src/hooks/pre_bash_guard.py\"" }
      ],
      "postToolUse": [
        { "matcher": "bash|run",
          "bash": "python3 \"$RC_REPO/src/hooks/post_bash_revive.py\"" },
        { "matcher": "write|edit|str_replace_editor",
          "bash": "python3 \"$RC_REPO/src/hooks/post_batch_lang_audit.py\"" }
      ],
      "sessionStart": [
        { "bash": "python3 \"$RC_REPO/src/hooks/session_start_manifest.py\"" },
        { "bash": "python3 \"$RC_REPO/src/hooks/session_resume_inject.py\"" }
      ],
      "userPromptSubmitted": [
        { "bash": "python3 \"$RC_REPO/src/hooks/session_resume_inject.py\"" }
      ]
    }
  }
  ```

#### File: `~/.copilot/mcp-config.json` (template only — merged in by enable script)
- What: Register `hybrid-reasoner` MCP server in the user-level Copilot config (CLI does not support per-repo MCP today). Stdio transport, `command: python3`, `args: ["-m", "src.mcp_reasoner"]`, `cwd: $RC_REPO`.
- Rationale: `gh copilot` reads MCP servers from `~/.copilot/mcp-config.json`. Enable script **merges** (does not overwrite) so existing user MCP servers survive.

#### File: `src/hooks/adapters/copilot.py` (new)
- What: `parse_stdin(event)` mapping `gh copilot` payload -> `HookEnvelope(host="copilot")`. Maps `write`, `edit`, `str_replace_editor` -> canonical `Write`, `Edit`. Emits `permissionDecision: "deny"` JSON on stdout when dispatch decides block.
- Rationale: Copilot's blocking contract is `permissionDecision` JSON, not exit code 2.

#### File: `.copilot/skills/reasoning/SKILL.md` (new — copy of `.claude/skills/reasoning/SKILL.md`)
- What: Same skill body. Frontmatter with `name`, `description` per agentskills.io.
- Rationale: `gh copilot` reads skills from `~/.copilot/skills/` (user) and project-local paths. Project-local copy keeps the skill versioned with the repo.

#### File: `scripts/enable-in-repo-copilot.sh` (new)
- What: Writes `.copilot/hooks.json` and `.copilot/skills/reasoning/SKILL.md`. Merges the `hybrid-reasoner` entry into `~/.copilot/mcp-config.json` (preserves existing servers). Acquires a file lock (`flock`) on `~/.copilot/mcp-config.json.lock` for the merge — power-user/senior-dev flagged that concurrent enable runs of two repos race. Backs up to `~/.copilot/mcp-config.json.bak.<ts>` before merging.
- Rationale: User-level MCP file is shared between projects; merge, don't clobber, don't race.
- **All home-dir writes (`~/.copilot/*`) are gated behind successful verification** (the bash script aborts before any `~/.copilot/*` mutation if `gh copilot` / target binary fails the hook-surface probe). Power-user reviewer flagged that writing to `$HOME` before confirming the host exists is destructive.

#### File: `tests/test_adapter_copilot.py` (new)
- What: Fixtures for `gh copilot` stdin envelope. Assert dispatch produces the right `permissionDecision` JSON on stdout.

### Success Criteria

#### Automated Verification
- [ ] `pytest tests/test_adapter_copilot.py -q` passes.
- [ ] `.copilot/hooks.json` validates against the Copilot hooks schema.
- [ ] `bash scripts/enable-in-repo-copilot.sh` in tmp `$HOME` produces expected files; rerun is safe; existing entries in `~/.copilot/mcp-config.json` survive.

#### Manual Verification
- [ ] `gh copilot` interactive: trigger an edit on a known-bad diff fixture; agent surfaces the sidecar's `human_summary`.
- [ ] Sidecar at :8765 must be running; confirm with `curl -fsS http://127.0.0.1:8765/health`.

### Dependencies
- Requires: Phase 1.
- Blocks: Phase 6.

### Gaps recorded
- VS Code Copilot Chat and coding agent are out of scope by user decision; not ported.
- `gh copilot` MCP config is user-level only (no per-repo override). Documented in README.

## Phase 4: Mistral Vibe CLI integration

Verification (2026-05-08) confirmed: `vibe` v2.9.4 installed. Headless: `--prompt` + `--trust` (per-invocation, not persisted). Tool gating: `--enabled-tools`. Cost ceilings: `--max-turns`, `--max-price`. Custom agents via `--agent NAME` (builtins: `default`, `plan`, `accept-edits`, `auto-approve`; custom from `~/.vibe/agents/<name>.toml`). Power-user v2 hallucination concern was wrong on existence (right on issue numbers, which we don't depend on). Real config schema must still be read from the live install before authoring `.vibe/config.toml`.

### Changes

#### File: `src/mcp_gate.py` (new — review-required relocation)
- What: New module exposing `gate_edit(path, before_src, after_src) -> {decision, message}` that calls the HTTP sidecar at `127.0.0.1:8765/score` (NOT in-process scoring). Returns `{decision: "block"|"allow", message: str}`.
- Rationale: Senior dev reviewer flagged that adding `gate_edit` directly into `mcp_reasoner.py` creates a circular-import risk (`mcp_reasoner.py` would need `_dispatch.py` from `src/hooks/`) and breaks the codebase's layering (sidecar = scoring, hooks = gating). Power-user flagged that two scoring paths (HTTP `/score` and in-process `gate_edit`) race on the same audit log. Both reviewers converge: route `gate_edit` through HTTP like every other adapter.
- **MCP error contract decision (review-driven)**: `gate_edit` MUST match the existing `reason_over_edit` contract — return a dict, do NOT raise `McpError`. Senior dev v3 caught that two FastMCP tools on the same server with opposite error contracts (one returns dict, one raises) will confuse agents. To still get host-visible block surfacing (which `McpError` was supposed to provide), the post-turn hook is responsible: it inspects the audit log for `gate_edit` blocks within the turn and emits a user-visible `BLOCKED:` line via stderr. Belt-and-braces — power-user v3 also recommended this fallback pattern.
- **Audit-row durability (review-driven correction)**: Senior dev v3 caught that the previous "sidecar /score must fsync before response" was wrong — `s2_core.py:937` does NOT write the audit log; scoring is pure compute, audit writes happen on the hook side AFTER the HTTP response. Real requirement: `mcp_gate.gate_edit` MUST call `audit_log.append_event(..., fsync=True)` BEFORE returning, since the host may exit before deferred I/O lands. `audit_log.append_event` gains a `fsync: bool = False` kwarg in Phase 0.5.
- Code sketch (corrected):
  ```python
  # src/mcp_gate.py
  import httpx
  from src.hooks._host_env import host as _host, session_id as _sid
  from src.hooks import audit_log

  def gate_edit(path: str, before_src: str, after_src: str) -> dict:
      """Synthetic PreToolUse for hosts without hooks. HTTP sidecar only."""
      r = httpx.post(
          "http://127.0.0.1:8765/score",
          json={"path": path, "before_src": before_src, "after_src": after_src,
                "host": _host(), "session_id": _sid()},
          timeout=30.0,
      )
      report = r.json()
      decision = "block" if report.get("regression_detected") else "allow"
      audit_log.append_event(
          event="gate_edit", decision=decision, host=_host(),
          session_id=_sid(), file_path=path, summary=report.get("human_summary", ""),
          fsync=True,  # host may exit before deferred I/O lands
      )
      return {"decision": decision, "message": report.get("human_summary", ""),
              "regression_detected": report.get("regression_detected", False)}
  ```

#### File: `src/mcp_reasoner.py` (modify — minimal)
- What: One-liner — `from src.mcp_gate import gate_edit; mcp.tool()(gate_edit)`. No business logic added here.
- Rationale: Same FastMCP server, clean separation.

#### File: `.vibe/config.toml` (new)
- What: Register MCP server, post-agent-turn hook, skill paths.
- Code sketch:
  ```toml
  [[mcp_servers]]
  name = "hybrid-reasoner"
  transport = "stdio"
  command = "python3"
  args = ["-m", "src.mcp_reasoner"]
  startup_timeout_sec = 20
  tool_timeout_sec = 60

  [hooks]
  post_agent_turn = "python3 ${VIBE_PROJECT_DIR}/src/hooks/post_batch_lang_audit.py"

  [skills]
  paths = [".vibe/skills", "~/.vibe/skills"]
  ```

#### File: `.vibe/AGENTS.md` (new — Vibe-scoped, NOT repo-root)
- What: Vibe-only instructions to the agent: "Before any write or edit, you MUST call `hybrid_reasoner_gate_edit(path, before_src, after_src)`. If `decision == 'block'`, do not proceed; surface the message verbatim. The sidecar runs at 127.0.0.1:8765; if unreachable, fail-closed."
- Rationale: **Reviewer-driven correction.** The original plan put this at repo-root `AGENTS.md`, but power-user flagged that AGENTS.md is also read by Claude (recently added support), Cursor, Codex CLI, and others. A repo-root file would cause Claude to start prompting `gate_edit` calls — duplicated with the existing PreToolUse hook = double-scoring + double audit entries. Scope to `.vibe/AGENTS.md` only.
- Enforcement strategy: best-effort. AGENTS.md instruction + skill description. **Drop `safety = "ask"`** — senior dev reviewer flagged it blocks headless eval (Phase 6). Use `safety = "auto"` and rely on the post-turn audit to retroactively flag missed `gate_edit` calls. Persistent non-compliance is logged, not a blocker.

#### File: `.vibe/skills/reasoning/SKILL.md` (new)
- What: Copy of `.claude/skills/reasoning/SKILL.md`. Vibe's frontmatter takes `name`, `description`, `allowed-tools`, `user-invocable` - set the latter two explicitly.

#### File: `src/hooks/adapters/vibe.py` (new)
- What: `parse_stdin("PostAgentTurn")` adapter, plus a tiny `mcp_to_envelope(args)` helper used by `gate_edit` so the dispatch chain consumes an MCP tool call as if it were a PreToolUse hook.

#### File: `scripts/enable-in-repo-vibe.sh` (new)
- What: Writes `.vibe/config.toml`, `AGENTS.md`, `.vibe/skills/...`. Patches `~/.vibe/trusted_folders.toml` (vibe requires the project be marked trusted before AGENTS.md auto-loads). Refuses to overwrite existing files.

#### File: `tests/test_adapter_vibe.py` (new)
- What: Two fixtures: post-agent-turn payload, and an MCP `gate_edit` call. Assert dispatch returns identical decisions to Claude/Gemini for the same diff.

### Success Criteria

#### Automated Verification
- [ ] `pytest tests/test_adapter_vibe.py -q` passes.
- [ ] `vibe --version` >= 2.9.5 (recorded in script preflight).
- [ ] `python3 -c "import tomllib; tomllib.load(open('.vibe/config.toml','rb'))"` parses.

#### Manual Verification
- [ ] `vibe` in a test repo; ask it to make a known-bad edit. Confirm it calls `gate_edit` first (visible in vibe's tool log) and surfaces the block.
- [ ] When the sidecar is down with `S2_FAIL_CLOSED=1`, Vibe refuses the edit per the AGENTS.md contract.

### Dependencies
- Requires: Phase 1.
- Blocks: Phase 6.

### Gaps recorded
- Real PreToolUse hook does not exist. The gate is enforced via prompt (`AGENTS.md`) + MCP tool with `McpError` on block (so the host surfaces a tool error, not a "successful" allow text). Susceptible to model non-compliance. Mitigation: include the gate-edit instruction in `.vibe/AGENTS.md` AND in the skill description AND rely on the post-turn audit to flag missed calls retroactively. **`safety = "auto"`** (decision 12 — `safety = "ask"` is dropped as it blocks headless eval).
- No SessionStart, UserPromptSubmit, PreCompact equivalents. Document as known limitations.

## Phase 5: Repo-template factoring + docs + drift guards

### Changes

#### Dir: `scripts/templates/{claude,gemini,copilot,vibe}/` (new)
- What: Move all template content (config files, SKILL.md, AGENTS.md, instructions.md) here. The four `enable-in-repo-*.sh` scripts copy from the corresponding subdir.
- Rationale: Cuts duplication. SKILL.md content lives once; per-host frontmatter shims are tiny separate files.

#### File: `scripts/enable-in-repo.sh` (modify)
- What: Add `--host=claude|gemini|copilot|vibe|all` flag. Default = `claude` (back-compat). `all` runs every host's enable in sequence.
- Rationale: One entry point; preserves the current CLI signature.

#### File: `README.md` (modify)
- What: New section "Multi-CLI support" with a parity matrix (rows = host, cols = hook events / MCP / skill / one-command-enable / known gaps). Link to per-host docs.

#### File: `docs/CLI_PARITY.md` (new)
- What: Long-form per-host docs lifted from the research outputs (post-verification, not the original hallucinated drafts). Each section has install, config layout, env vars, known gaps, debugging tips.

#### File: `tests/test_skill_drift.py` (new — review-driven)
- What: Compute SHA-256 of the body section (post-frontmatter) of every `scripts/templates/<host>/SKILL.md` and assert all four hashes are identical. Frontmatter may legitimately differ per host; body must not silently drift.
- Rationale: Senior dev reviewer flagged that copy-not-symlink invites silent skill-content drift across hosts.

#### File: `tests/test_enable_in_repo_backcompat.py` (new — review-driven)
- What: Snapshot the file set produced by today's `bash scripts/enable-in-repo.sh` (Claude-only). After Phase 5 lands, assert `bash scripts/enable-in-repo.sh --host=claude` in a tmp dir produces a byte-identical file set + identical contents. **Path normalisation**: before hashing, replace any absolute `<RC_REPO>` substitution with the literal placeholder string so snapshots are machine-independent.
- Rationale: Senior dev flagged back-compat untested. Power-user flagged that absolute-path embedding breaks naive byte-equality snapshots.

#### File: `.github/workflows/multi-cli.yml` (new — review-driven, name corrected)
- What: New workflow file. Existing `.github/workflows/lint-and-test.yml` runs offline with `S2_FAIL_CLOSED=1` / `RC_LIVE=0` and a 10-min budget — host-CLI installs + live sidecar can't share that budget. New workflow has TWO jobs:
  - `verified`: hard-fail matrix for hosts whose verification gate has cleared (today: gemini, vibe, copilot — all confirmed 2026-05-08). Each cell installs the CLI, runs `pytest tests/test_adapter_<host>.py -q` + the enable-script smoke test. **No `continue-on-error`**.
  - `experimental`: informational matrix for hosts pending deeper verification (e.g. `copilot` hook subcommand existence). `continue-on-error: true`. Posts a status comment but does NOT gate merge.
- A host moves from `experimental` to `verified` in a single PR. Power-user v3 flagged that mixing both modes in one matrix hides regressions.

#### `--host=all` semantics (review-driven)
- `bash scripts/enable-in-repo.sh --host=all` MUST hard-fail if any host's CLI is missing on the machine. To opt into best-effort multi-host, pass `--skip-missing`. Power-user flagged silent skips would drop CI coverage.

### Success Criteria

#### Automated Verification
- [ ] `bash scripts/enable-in-repo.sh --host=all` in a tmp dir writes the host file sets that have passed their verification gates (skip hosts whose verification failed).
- [ ] `pytest tests/test_skill_drift.py tests/test_enable_in_repo_backcompat.py -q` passes.
- [ ] Markdown link-check on `docs/CLI_PARITY.md`.

#### Manual Verification
- [ ] README parity matrix renders correctly on GitHub.

### Dependencies
- Requires: Phases 2, 3, 4.
- Blocks: Phase 6.

## Phase 6: Eval extension - per-CLI parity verification

### Changes

#### Dir: `eval/setups/` (new; mirrors ~/eval-setups/)
- What: Add four setup definitions (`claude-with-rc`, `gemini-with-rc`, `copilot-with-rc`, `vibe-with-rc`). Each lists the required config files + env.
- Rationale: Today the eval framework only spawns Claude Code agents. Need to spawn the same task against each CLI and compare per-task success.

#### File: `eval/spawner.py` (modify, if eval flow lives here)
- What: Dispatch on `setup.host` to invoke the right CLI binary (`claude`, `gemini`, `gh copilot`, `vibe`) with the right `--settings`/`--config` flag.

#### File: `eval/datasets/sidecar_block_pairs.jsonl` (new)
- What: ~30 known-good and known-bad edits with expected sidecar decisions. Run each through every CLI; compare blocked/allowed/decision-text.

#### Trust-prompt automation (review-driven)
- Every host's spawner-side setup MUST pre-clear interactive trust prompts before launching the agent, otherwise headless runs hang on first invocation.
  - **Gemini**: write `~/.gemini/trusted_mcp.json` (or pass `--trust-all`/`--yolo` per version) trusting `hybrid-reasoner`.
  - **Vibe**: append project to `~/.vibe/trusted_folders.toml`.
  - **Copilot**: TBD pending verification.
- Spawner must FAIL FAST with a clear error if a host's trust file can't be pre-populated.

#### Sidecar concurrency budget (review-driven)
- 4 hosts × 3 runs each = up to 12 simultaneous `/score` calls. Document expected QPS and required worker count in `docs/EVAL_DESIGN.md`. Pre-Phase-6 task: load-test the sidecar at 2× expected QPS; tune worker count if needed.

### Success Criteria

#### Automated Verification
- [ ] `python3 -m eval.cli spawn --setup gemini-with-rc --task P0-login-dashboard --runs 3` completes.
- [ ] Per-host agreement on `sidecar_block_pairs.jsonl`: threshold derived from a **dry-run baseline** (do not set 95% by fiat). Baseline scope is **per-host single number, not per-(host,fixture) matrix** — senior dev v3 flagged the matrix variant is 4 × 30 × 30 = 3,600 runs and gets cut at execution time. With per-host single number: 5-run pilot per host = 20 LLM-driven sessions total, defensible. Threshold = `baseline_mean - 2 * baseline_stddev` (or descriptive-not-statistical if the 5-run sample variance is too high). Record baseline + threshold + cost in `docs/EVAL_DESIGN.md`. Re-baseline only when sidecar scoring logic changes (not on every threshold-affecting plan change).
- [ ] Per-fixture failure breakdown: spawner emits a JSON report with one row per (host, fixture) showing decision + decision-text, so disagreements are diagnosable not just countable.

#### Manual Verification
- [ ] Compare audit-log entries per host for the same diff; human-summary text identical (sidecar is the single source).
- [ ] User-visible block message reads naturally in each CLI's UX.

### Dependencies
- Requires: Phases 2, 3, 4, 5.
- Blocks: nothing.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Copilot coding agent / VS Code Copilot Chat | n/a | n/a | Out of scope by user decision (2026-05-08). Only `gh copilot` interactive CLI supported. |
| Vibe hooks API changes (issues #250/#531 unmerged at v2.9.5) | Med | Med | Pin a Vibe version in install script; fall back to MCP-only path on version mismatch. |
| Gemini's BeforeTool matcher syntax differs from Claude's regex pipes | Low | Low | Multiple matcher entries instead of one piped regex; test fixtures cover all canonical tools. |
| VS Code Copilot hooks are Preview (Feb 2026) and may break | Med | Low | Coding-agent hooks are GA - port stays usable even if VS Code surface churns. Document Preview status in README. |
| Blocking contract differs (Claude exit-2 vs Copilot `permissionDecision` JSON vs Gemini `decision` JSON) | High | Low | Adapter layer (Phase 1) owns wire-format translation; dispatch chain stays unchanged. |
| Skills are model-selected on Copilot (not force-loaded) | Med | Med | Repeat skill content in `copilot-instructions.md` (always loaded) as a backstop. |
| AGENTS.md agent-compliance for Vibe's MCP-as-PreToolUse | High | Med | Best-effort enforcement. `.vibe/AGENTS.md` instruction + skill description + `McpError` on block + post-turn audit that retroactively warns when `gate_edit` was skipped. `safety = "auto"` (NOT "ask"). Persistent non-compliance = logged gap, not a blocker. |
| Eval comparison confounded by per-CLI prompt/system differences | Med | Med | Treat per-CLI eval as descriptive, not causal. State this in `docs/EVAL_DESIGN.md`. |

## Rollback Strategy

- Phase 1: revert is one commit; `pre_edit_guard.py` and friends become standalone again. Tests gate this.
- Phases 2-5: each phase touches a disjoint dir (`.gemini/`, `.github/`, `.vibe/`, `scripts/templates/`). Delete the dir + revert the corresponding commit.
- Phase 6: eval changes are additive; revert the new setup files.
- No DB, no schema, no shared-infra changes - all rollbacks are local-tree git reverts.

## File Ownership Summary

| File | Phase | Change Type |
|------|-------|-------------|
| `src/hooks/_host_env.py` | 0.5 | Create |
| `src/hooks/_audit_migrate.py` | 0.5 | Create (one-shot JSONL migration helper) |
| `src/hooks/audit_log.py` | 0.5 | Modify (cross-platform lock + host column + schema bump) |
| `src/hooks/_dispatch.py` | 0.5 | Modify (thread `envelope.cwd`, drop CLAUDE_* reads) |
| `src/hooks/_guard_paths.py`, `_session_manifest.py`, `_calibration_gate.py` | 0.5 | Modify (CLAUDE_* sweep) |
| `tests/test_host_env.py` | 0.5 | Create |
| `src/hooks/_envelope.py` | 1a | Create |
| `src/hooks/adapters/__init__.py` | 1a | Create |
| `src/hooks/adapters/claude.py` | 1a | Create |
| `src/hooks/pre_edit_guard.py` | 1a | Modify |
| `tests/test_adapter_claude.py` | 1a | Create |
| Remaining 7 hooks (`pre_plan_guard.py` etc.) | 1b | Modify |
| `src/mcp_gate.py` | 4 | Create |
| `.gemini/settings.json.template` | 2 | Create (committed) |
| `.gemini/settings.json` | 2 | Generate at install (gitignored) |
| `.gitignore` | 2,3,4 | Modify (add per-host generated files) |
| `.gemini/skills/reasoning/SKILL.md` | 2 | Create |
| `GEMINI.md` | 2 | Create |
| `src/hooks/adapters/gemini.py` | 2 | Create |
| `scripts/enable-in-repo-gemini.sh` | 2 | Create |
| `.copilot/hooks.json` | 3 | Create |
| `.copilot/skills/reasoning/SKILL.md` | 3 | Create |
| `~/.copilot/mcp-config.json` | 3 | Merge (user-level) |
| `src/hooks/adapters/copilot.py` | 3 | Create |
| `scripts/enable-in-repo-copilot.sh` | 3 | Create |
| `.vibe/config.toml` | 4 | Create |
| `.vibe/skills/reasoning/SKILL.md` | 4 | Create |
| `.vibe/AGENTS.md` | 4 | Create (Vibe-scoped, NOT repo-root) |
| `src/mcp_reasoner.py` | 4 | Modify (one-liner registers `gate_edit` from `mcp_gate.py`) |
| `tests/test_skill_drift.py` | 5 | Create |
| `tests/test_enable_in_repo_backcompat.py` | 5 | Create |
| `.github/workflows/multi-cli.yml` | 5 | Create (verified + experimental matrix) |
| `requirements.txt` | 0.5 | Modify (add `portalocker`) |
| `src/hooks/adapters/vibe.py` | 4 | Create |
| `scripts/enable-in-repo-vibe.sh` | 4 | Create |
| `scripts/templates/{claude,gemini,copilot,vibe}/` | 5 | Create |
| `scripts/enable-in-repo.sh` | 5 | Modify (add `--host` flag) |
| `README.md` | 5 | Modify |
| `docs/CLI_PARITY.md` | 5 | Create |
| `eval/setups/*` | 6 | Create |
| `eval/spawner.py` | 6 | Modify (if eval lives here) |
| `eval/datasets/sidecar_block_pairs.jsonl` | 6 | Create |

## Decisions locked

### 2026-05-08 (initial)
1. Cloud agents skipped. Only interactive Copilot CLI supported (which binary TBD by verification).
2. Vibe enforcement = best-effort.
3. SKILL.md = copy across (no symlinks).
4. ~~Single shared repo-root AGENTS.md.~~ **Reverted by review**: scope to `.vibe/AGENTS.md` to avoid double-scoring on Claude/Cursor/Codex.
5. Gemini `BeforeAgent` accepted as Task substitute. **Now deferred** until Gemini event names are verified hands-on.

### 2026-05-08 (post-review revisions)
6. **Phase 0.5 inserted** — host-env shim, audit-log file-locking + `host` column, `_dispatch.py` decoupling from `CLAUDE_*` envs. Lands before any per-host phase.
7. **Phase 1 split** into 1a (canary, `pre_edit_guard.py` only) + 1b (sweep remaining 7 hooks). Each lands as its own PR with byte-identical Claude tests.
8. **`gate_edit` lives in `src/mcp_gate.py`**, NOT in `src/mcp_reasoner.py`. Routes through HTTP sidecar — never in-process scoring. Single audit-log writer at all times.
9. **Adapter parse-failure contract**: every `*.parse_stdin` MUST NEVER raise. Garbage stdin → HookEnvelope with `tool_name=None` + audit row `decision="malformed_payload"` + exit 0.
10. **MCP `cwd` is mandatory** in every host's MCP config block.
11. **Test paths corrected**: tests live at `tests/test_*.py`, not `tests/hooks/*`. Original Phase-1 success criteria pointed at a non-existent directory and would have trivially passed.
12. **Drop `safety = "ask"` for Vibe** — blocks headless eval. Use post-turn audit instead.
13. **Phases 2/3/4 are verification-gated**: Gemini event names, `gh copilot` hook surface existence, Mistral Vibe product existence must all be confirmed hands-on before code. Original research is suspect.

## Missing phases / pieces flagged by review

- **CI matrix update**: GitHub Actions must install `gemini`, target Copilot binary, and Vibe (or skip those hosts gracefully) for integration tests. Without it, manual verification is the only signal.
- **Audit-log schema versioning**: Phase 0.5 bumps schema; eval aggregators (`tests/test_eval_aggregate.py` if it exists, otherwise per-iteration replay scripts in `thoughts/shared/research/scripts/`) need a compat note + migration helper.
- **`session_resume_inject.py` UserPromptSubmit on Gemini**: Gemini's UserPromptSubmit equivalent must be confirmed by verification gate; if absent, the resume-inject behaviour is silently lost on Gemini — flag explicitly in `docs/CLI_PARITY.md`.
- **Concurrency QPS doc**: 4 hosts each running their own MCP stdio server + all hammering `:8765` — document expected QPS and sidecar worker count.

## Open questions (deferrable)

1. Cursor / Codex CLI follow-ups: the Phase-1 adapter layer makes them cheap (~1 day each). Worth a placeholder note in `docs/CLI_PARITY.md`? Defer until after Phase 6 ships.
