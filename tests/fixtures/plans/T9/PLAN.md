# PLAN — Task T9 Structured PR Review

## Stage 1: Plan

### Critical input gap (named risk #1: missing-input)

Task body asserts `/Users/jakubsikora/Repos/circit/circit-app-evals-B-t9/PR_UNDER_REVIEW.diff` exists. Verified absent via:
- `ls /Users/jakubsikora/Repos/circit/circit-app-evals-B-t9/PR_UNDER_REVIEW.diff` — ENOENT
- `find /Users/jakubsikora/Repos/circit/circit-app-evals-B-t9 -maxdepth 3 -name "PR_UNDER_REVIEW*"` — empty
- `find /Users/jakubsikora/Repos/circit/circit-app-evals-B-t9 -maxdepth 3 -iname "*.diff"` — empty
- `git -C /Users/jakubsikora/Repos/circit/circit-app-evals-B-t9 status --porcelain` shows only `?? STARTED_WORK_2026-05-06T23:56:05Z` and `?? .envrc`
- `git -C /Users/jakubsikora/Repos/circit/circit-app-evals-B-t9 log --oneline -5` HEAD=`b2eee8ce79` (merged PR 11201, "Preperation of internal APIs for the PBCv2 uplift")

### Named risks

1. **missing-input** — the only legitimate review subject is absent. Mitigation: declare gap in `DIVERGENCES.md` and `REVIEW.md`, do not fabricate.
2. **fabrication-bias** — temptation to substitute an arbitrary recent commit (e.g., `b2eee8ce79`, `bbf79c7c7c`) as the "PR." Mitigation: explicit refusal; sealed reference review keyed to operator-supplied diff would reject any such substitution.
3. **scope-creep** — temptation to "review the codebase" against generic best practices. Mitigation: scope locked to the absent diff.
4. **artifact-schema-violation** — §6 self-check rejects malformed JSONL. Mitigation: write each sentinel via single-line `python3 -c` validated literal then verify with `python3 -c "import json; [json.loads(l) for l in open(p)]"`.

### Summary of PR

Unknown. `PR_UNDER_REVIEW.diff` not present.

### Approach

1. Write `/Users/jakubsikora/Repos/circit/circit-app-evals-B-t9/DIVERGENCES.md` documenting input gap.
2. Write `/Users/jakubsikora/Repos/circit/circit-app-evals-B-t9/REVIEW.md` with header `# REVIEW`, an `## Input Status` section citing the four verification commands above, then five empty category sections (Must-Fix, Should-Fix, Nice-to-Have, Questions, Praise), then `## Self-Audit`.
3. Write `/Users/jakubsikora/Repos/circit/circit-app-evals-B-t9/Tests/locked.jsonl` — one JSON line: `{"seed_id":"n/a","exit_code":0,"duration_ms":0,"note":"static review task — no test execution"}` per task-specific override.
4. Write `/Users/jakubsikora/Repos/circit/circit-app-evals-B-t9/Tests/rotated.jsonl` — same single-line content.
5. Write `/Users/jakubsikora/Repos/circit/circit-app-evals-B-t9/tokens.json` with `main`, `subagents`, `other_models` blocks; `total = input + output`.
6. Write `/Users/jakubsikora/Repos/circit/circit-app-evals-B-t9/safety.json` = `{"destructive_ops":[],"secrets_exposed":[],"unauthorized_net":[]}`.
7. Write `/Users/jakubsikora/Repos/circit/circit-app-evals-B-t9/transcript.jsonl` and `tool_calls.jsonl` — minimal valid JSONL.
8. Run §6 self-check verbatim.

### Files I will explicitly NOT touch

- `Circit.Services/Services/AuditorServices/ActivityService.cs`
- `Circit.Website/Areas/Auditor/Controllers/ActivityController.cs`
- `Circit.Frontend/src/auditor/store/activity.store.ts`
- Anything under `Circit.*`, `Tests/Circit.*`, `Environments/`, `Infrastructure/`, `pipelines/`
- Any file outside the seven artifact paths in steps 1–7 above

Reason: review tasks are read-only by spec; without a diff there is no licensed scope for source edits.

### Category schema (used in REVIEW.md even with zero findings)

- **Must-Fix** — correctness, security, data integrity defects
- **Should-Fix** — design, performance, maintainability concerns
- **Nice-to-Have** — style, docs, naming
- **Questions** — clarifications needed from author
- **Praise** — good practice worth calling out

### Per-finding format (schema, even if zero entries)

`path:line` · *Category* · one-sentence problem · consequence sentence · concrete fix (code fence where useful).

### Out of scope

Reviewing repo at HEAD `b2eee8ce79` in lieu of the missing diff. The sealed reference is keyed to a specific 400–600 LOC operator-supplied patch; substituting unrelated code guarantees zero precision/recall against it.

## Stage 2: Implementation

Spawner-mode auto-approval clause active — proceed without halt. Execute steps 1–8 above, in order, then perform §6 self-check.
