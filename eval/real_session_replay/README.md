# Real-session historical patch replay

This pilot reconstructs three real repository fixes from exact Git commits. It
runs each commit's regression test in two isolated local clones of the parent:
one with parent source and one with only the historical source patch. Both arms
receive the **same post-commit test file**. The case manifest is frozen in
`cases.json`; the prepilot evaluation context is
`baseline-2026-09-27-real-session-replay-prepilot`.

Run with Python, pytest, and Ruff installed:

```bash
python eval/real_session_replay/run.py \
  --output ~/.local/share/reasoning-core/real-session-replay/runs/my-new-run
```

The output directory must be new. The runner writes local test and lint logs
plus `run_manifest.json` with revisions, hashes, commands, exit codes, and
durations. Logs can contain local paths or test output; keep them local. A
nonzero exit means at least one focused test lacks a red/green result.

## 2026-09-27 pilot

Local run: `~/.local/share/reasoning-core/real-session-replay/runs/2026-09-27-pilot-v1`.

| Historical fix | Focused test | Full test file | Python parse | Ruff after patch |
| --- | --- | --- | --- | --- |
| Session-scoped reconciliation | fail -> pass | fail -> pass | pass | fail; same 2 preexisting errors |
| Stop-hook session identity | fail -> pass | fail -> pass | pass | pass |
| Exact verification parent link | fail -> pass | fail -> pass | pass | pass |

Focused patched-arm test runtime was 0.571 s, 0.615 s, and 0.284 s,
respectively (single runs). These are **replay test times**, not historical
agent decision, coding, or task completion times. They are not a speed
comparison.

## Interpretation

The results support a narrow solution claim: each historical source patch
resolves its commit-coupled regression test in an isolated replay, without
breaking that commit's full test file. The test authorship and implementation
are from the same commits, so this is not an independent benchmark or a blinded
quality assessment. The suite does not measure architectural quality, real-user
satisfaction, or a causal effect of reasoning-core/Laya.

A fresh 90-day, synthetic-filtered `rc real-session-eval` ledger for this repo
found 414 sessions and 3,487 edit decisions. Only one decision has an exact
verification link, one has a human label, and none has an exact commit link.
The ledger remains local beside the replay logs. It cannot identify these
three commits as outcomes of specific audited edits. It also lacks an exact
transcript-to-edit join for these cases. Historical agent runtime
and usability remain unknown. A future prospective run should capture exact
task, edit, verification, outcome, and user-acceptance IDs before scoring those
dimensions. Do not infer them from commit timestamps or session-level counts.
