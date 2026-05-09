# SWE-bench iter-1 D2 Setup-B Malformed-Diff RCA

**Date**: 2026-05-09
**Author**: jakub.sikora@circit.io (assisted by Claude)
**Scope**: 2/10 cells in `d2-sweep-v2` Setup-B arm produced unified diffs
that `git apply --check` accepts but BSD `patch -p1` rejects. Setup A on
the same instances produced structurally valid diffs.
**Constraint**: Investigation conducted from local code-read evidence
only (VM transcripts not retrieved this pass — see "Verification status"
below). Fix is therefore proposed at the harness extraction layer
because that layer is **causally downstream of every candidate root
cause**, so the patch is robust under all three hypotheses.

---

## 1. Problem restatement

Two cells:

| Cell | Hunk header | Grader error |
|---|---|---|
| `B/pylint-dev__pylint-4661/run-01` | `@@ -32,7 +32,7 @@` | `malformed patch at line 29:  def _get_pdata_path(base_name, recurs):` |
| `B/scikit-learn__scikit-learn-13439/run-01` | `@@ -194,6 +194,11 @@` | `patch unexpectedly ends in middle of line` |

Both errors are BSD-`patch`-strict mode. Hunk-header counts, recounted by
hand from the rendered diff, **match** content-line counts. The user's
chat-rendered display nevertheless shows lines that have visibly
hard-wrapped onto a second line (`".pylint.d")`, `Pipeline"""`) without
a `+`/`-`/space prefix on the wrapped portion. That is the structural
defect BSD `patch` is reporting: a line in the hunk body lacks the
leading line-class character → BSD `patch` treats it as end-of-hunk →
the next legitimate context line (`def _get_pdata_path…`) is read
outside-of-hunk → "malformed".

Underlying corruption mode (with high confidence from grader text):
**a long content line in the hunk body has been split into 2 physical
lines, and the prefix character is present only on line 1.**

## 2. Why only Setup B

Both arms run through `eval/agent_loop.run_one_instance_via_cli` and
both extract via `eval/patch_extractor.extract_via_regex`. The
extraction path is identical. The differences are:

- B installs `~/eval-setups-gemini/B/gemini.json` as the worktree's
  `.gemini/settings.json`, which registers:
  - `PreToolUse` hooks on `write_file|edit_file|str_replace_editor`
    (`pre_edit_guard.py`) that can **exit 2** with a multi-line stderr
    block ("BLOCKED: architectural regression detected …") when the
    sidecar reports `regression_detected`, drift over `RC_DRIFT_DENY`,
    plan-grounding violation, or guard-file edit. (See
    `src/hooks/pre_edit_guard.py:765–800` and surrounding gates.)
  - `PostToolUse` advisory text via `additionalContext` from
    `post_batch_lang_audit.py` and `session_resume_inject.py` (the
    latter only fires under `RC_LANG_LOCK=1` with an anchor file).
  - MCP server `hybrid-reasoner` providing extra tools/resources.
- Setup A loads no hooks and no MCP server.

## 3. Hypotheses

### H1 (primary, ~60%): blocked-edit forces inline-diff fallback

When `pre_edit_guard.py` exits 2 on a `write_file`/`edit_file` call,
Gemini CLI surfaces the stderr to the model as a tool-failure message.
The agent loop in `eval/agent_loop.py` is text-driven (terminates only
when the model emits a turn with no `function_call` parts — see
`run_one_instance_via_cli` text-accumulation around `agent_loop.py:497`).
After repeated tool-block messages, gemini-2.5-flash falls back to
emitting the patch as a fenced ```` ```diff ```` block in its final
assistant text rather than via the tool call.

When the model **reconstructs** a unified diff from working memory it is
plausibly miscounting / silently wrapping long content lines in the
hunk body. Setup A doesn't trigger any blocks, the tool succeeds, and
the diff is emitted via the tool with prefixes intact (or the model is
under less context pressure when it does fence the diff).

- Evidence FOR: B-only failure; pre_edit_guard has multiple exit-2
  branches that don't exist in A; both failing instances are exactly
  the sort of multi-line / odd-formatting code (long string concat,
  triple-quoted docstring) where reconstruction-from-memory can wrap.
- Evidence AGAINST: cannot confirm without the transcript count of
  blocked edits per cell; the hooks could have been silent on these
  cells.

### H2 (~25%): stream-json delta accumulation duplicates content

`agent_loop.py:497-501`:

```python
if ev_type == "message" and ev.get("role") == "assistant":
    # Final text accumulates across delta=true events
    t = ev.get("content") or ""
    if t:
        final_text += t
```

The accumulator concatenates **every** assistant `message` event
without checking the `delta` flag the comment claims to honor. If
Gemini CLI v0.41+ emits both per-token delta chunks **and** a final
consolidated message in the same turn, `final_text` ends up with the
diff content twice — and `extract_via_regex` (non-greedy
`.+?` in `_FENCED_DIFF_RE`) returns whichever closing fence it sees
first, which can be the **partial** delta-accumulated diff if the
consolidated copy carries its own opening fence. The visible artifact
would be a truncated hunk that ends mid-line.

- Evidence FOR: code is genuinely buggy (no delta filter despite the
  comment); `final_text unexpectedly ends in middle of line` matches a
  truncation symptom exactly.
- Evidence AGAINST: this would also affect Setup A; we'd expect ~0%
  divergence between the two if it were the sole cause. (Could still
  contribute on top of H1 — e.g. B has more turns due to retries,
  giving more chances to hit the duplication boundary.)

### H3 (~15%): `additionalContext` from B-arm hooks pollutes agent text

`post_batch_lang_audit.py:43-50` and
`session_resume_inject.py:61-69` write
`{"hookSpecificOutput":{"hookEventName":"PostToolUse",
"additionalContext":"<text>"}}` to stdout. If Gemini CLI v0.41
surfaces this content as a `message`/`role: assistant` stream event
(rather than as next-turn user-prompt context), the accumulator picks
it up and inserts non-diff prose in the middle of `final_text`.

- Evidence FOR: B-only path; B has these hooks wired.
- Evidence AGAINST: `RC_LANG_LOCK=1` is conditional, and
  `post_batch_lang_audit` requires ≥5 file events + cross-language
  threshold to fire — unlikely on a 1–2 file SWE-bench task.

## 4. Recommended fix (harness-layer, hypothesis-agnostic)

Add a structural normalizer to `eval/patch_extractor.py` that runs on
the regex-extracted patch BEFORE it is written to `patch.diff`. The
normalizer:

1. Walks every hunk between `@@ … @@` markers.
2. For each line that does **not** start with one of `{' ', '+', '-',
   '\\'}`, treats it as a continuation of the previous hunk-body line
   and rejoins it inline (preserving the original line's leading prefix
   character).
3. Recounts the hunk-body lines and rewrites the `@@ -a,b +c,d @@`
   header so `b` = (context+removed) lines and `d` = (context+added)
   lines — matching what the body actually contains.
4. Refuses to write `patch.diff` if recount produces a `b` or `d` of
   `0` (defends against catastrophic truncation; surface
   `extractor_disagreement = True` instead so the cell is excluded
   from the resolved-rate denominator per D2 prereg).

Why this fix:

- It is **causally downstream** of H1, H2, and H3 — all three produce
  artifacts that the normalizer repairs.
- It has **no effect on Setup A**'s output, because A's clean diffs
  already satisfy the prefix invariant; rejoin and recount are
  idempotent on well-formed input.
- It does **not** alter `model_id`, sampling, `max_turns`, hook
  payloads, MCP surface, or any treatment knob → D2 prereg
  `TREATMENT_INTEGRITY_CONSTRAINTS` are unaffected (verified against
  `thoughts/shared/research/swebench-gemini-prereg-D2.json`).
- It does **not** touch `gemini` itself.
- It treats catastrophic truncation as `extractor_disagreement` rather
  than silently producing a wrong patch (matches Path-2-disagreement
  semantics already in the prereg).

### Minimal patch sketch

```python
# eval/patch_extractor.py — new helper
import re

_HUNK_HEADER = re.compile(r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@")

def repair_unified_diff_structure(patch: str) -> str | None:
    """Repair line-wrap artifacts in unified diffs and recount hunk headers.
    Returns None if a hunk has zero content after repair (catastrophic)."""
    if not patch:
        return patch
    out_lines: list[str] = []
    in_hunk = False
    body: list[str] = []
    header_idx = -1
    pending_prefix: str | None = None

    def _flush_hunk(header_line: str, body_lines: list[str]) -> str | None:
        m = _HUNK_HEADER.match(header_line)
        if not m:
            return None
        old_start = int(m.group(1)); new_start = int(m.group(3))
        old = sum(1 for ln in body_lines if ln[:1] in (" ", "-"))
        new = sum(1 for ln in body_lines if ln[:1] in (" ", "+"))
        if old == 0 or new == 0:
            return None
        return f"@@ -{old_start},{old} +{new_start},{new} @@"

    lines = patch.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("@@"):
            # flush previous
            if in_hunk and header_idx >= 0:
                fixed = _flush_hunk(out_lines[header_idx], body)
                if fixed is None:
                    return None
                out_lines[header_idx] = fixed
                out_lines.extend(body)
                body = []
            in_hunk = True
            out_lines.append(line)
            header_idx = len(out_lines) - 1
            pending_prefix = None
        elif in_hunk and line[:1] in (" ", "+", "-", "\\"):
            body.append(line)
            pending_prefix = line[:1]
        elif in_hunk and line == "":
            # BSD-patch fatal: bare empty line. Promote to space-prefixed
            # context iff previous hunk-body line was context.
            body.append(" ")
            pending_prefix = " "
        elif in_hunk and pending_prefix is not None:
            # Wrapped-line continuation — rejoin onto previous body line.
            body[-1] = body[-1] + line
        else:
            in_hunk = False
            out_lines.append(line)
            pending_prefix = None
        i += 1
    # flush trailing hunk
    if in_hunk and header_idx >= 0:
        fixed = _flush_hunk(out_lines[header_idx], body)
        if fixed is None:
            return None
        out_lines[header_idx] = fixed
        out_lines.extend(body)
    return "\n".join(out_lines) + ("\n" if patch.endswith("\n") else "")
```

Wired in `extract_with_agreement` between the regex extract and the
`git apply --check` step:

```python
p1 = extract_via_regex(text)
if p1 is None:
    return (None, False)
repaired = repair_unified_diff_structure(p1)
if repaired is None:
    return (None, True)        # catastrophic — mark disagreement
p1 = repaired
# … existing git apply --check, Path-2 compare, etc.
```

## 5. Regression test (in `tests/test_patch_extractor.py`)

```python
def test_repair_rejoins_wrapped_long_line(self):
    """B-arm pylint-4661 fixture: long replacement line wrapped onto
    two physical lines without prefix on continuation."""
    bad = (
        "diff --git a/p/c/__init__.py b/p/c/__init__.py\n"
        "--- a/p/c/__init__.py\n+++ b/p/c/__init__.py\n"
        "@@ -32,7 +32,7 @@\n"
        " USER_HOME = os.path.expanduser(\"~\")\n"
        " if \"PYLINTHOME\" in os.environ:\n"
        "     PYLINT_HOME = os.environ[\"PYLINTHOME\"]\n"
        "-    PYLINT_HOME = os.path.join(USER_HOME,  \n"
        "\".pylint.d\")\n"
        "+    PYLINT_HOME =                        \n"
        "appdirs.user_cache_dir(\"pylint\")\n"
        " \n \n def _get_pdata_path(base_name, recurs):\n"
    )
    repaired = repair_unified_diff_structure(bad)
    self.assertIsNotNone(repaired)
    # No body line lacks a prefix character
    in_hunk = False
    for ln in repaired.splitlines():
        if ln.startswith("@@"): in_hunk = True; continue
        if ln.startswith(("diff --git","---","+++")): in_hunk = False; continue
        if in_hunk and ln and ln[0] not in " +-\\":
            self.fail(f"unprefixed body line: {ln!r}")

def test_repair_idempotent_on_clean_diff(self):
    repaired = repair_unified_diff_structure(SIMPLE_DIFF)
    self.assertEqual(repaired, SIMPLE_DIFF)

def test_repair_catastrophic_returns_none(self):
    """Truncation that leaves a hunk with 0 added lines — extractor
    must signal disagreement, not silently emit empty hunk."""
    truncated = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        "@@ -1,2 +1,3 @@\n a\n b\n"
    )
    self.assertIsNone(repair_unified_diff_structure(truncated))
```

## 6. Verification status

**NOT YET RUN AGAINST THE FAILING CELLS.** This memo's verification is
incomplete because:

1. The two cells' `transcript.jsonl` and `final_text` files live on
   the Scaleway VM (`51.15.95.42:/root/eval-cells/d2-sweep-v2/cells/B/…`)
   and were not pulled to local. Without them I cannot:
   - confirm whether B's run hit any `pre_edit_guard` exit-2 (H1)
   - confirm whether stream-json emitted both delta and consolidated
     messages (H2)
   - count `additionalContext` events per cell (H3)
2. The proposed fix has **only been written, not executed**. Steps to
   complete verification:

   ```bash
   # On VM (or after scp to local)
   cd ~/eval-cells/d2-sweep-v2/cells/B/pylint-dev__pylint-4661/run-01
   python3 -c '
   import sys; sys.path.insert(0, "/Users/jakubsikora/research-gemini-swebench-eval-scripts")
   from eval.patch_extractor import extract_via_regex, repair_unified_diff_structure
   text = open("transcript.jsonl").read()  # or final_text
   p = extract_via_regex(text)
   r = repair_unified_diff_structure(p)
   open("patch.repaired.diff","w").write(r)
   '
   git apply --check patch.repaired.diff && \
     patch -p1 --check < patch.repaired.diff
   # both should exit 0
   ```

   Repeat for `scikit-learn__scikit-learn-13439`. If either step fails,
   the H1 root-cause assumption is wrong (model emitted a
   semantically-broken diff, not just a structurally-broken one) and
   the patch should be promoted to `extractor_disagreement=True`
   instead of attempting repair.

## 7. Cost estimate

| Step | Time |
|---|---|
| Land `repair_unified_diff_structure` + 3 unit tests in `research-gemini-swebench-eval-scripts` | ~30 min |
| Pull VM artifacts and replay 2 failing cells locally | ~15 min |
| Run iter-1 full B-arm re-sweep (10 instances × 1 rep ≈ same as pilot) | depends on per-cell timeout × 10; pilot took ~X min so re-sweep ≈ same |
| Re-run iter-1 D2 full sweep (165 inst × n_reps × 2 arms) | unchanged — fix is in extraction, not execution |

The fix does **not** require re-running anything that already produced
a structurally-valid `patch.diff`. We can re-grade existing transcripts
by re-running only the extractor over saved `final_text`, **without
re-spawning agents**, if the harness exposes a `--re-extract-only` mode
(if it doesn't, that's a 10-min addition).

## 8. Cross-references

- `thoughts/shared/research/swebench-gemini-prereg-D2.json` —
  TREATMENT_INTEGRITY_CONSTRAINTS this fix preserves.
- `thoughts/shared/research/swebench-iter1-meta.md` — design rationale.
- `eval/patch_extractor.py:25-66` — current `extract_via_regex`.
- `eval/agent_loop.py:497-501` — stream-json accumulator (H2 site).
- `src/hooks/pre_edit_guard.py:765-800` — exit-2 block path (H1 site).
- `src/hooks/post_batch_lang_audit.py:43-50` — additionalContext emit
  (H3 site).
