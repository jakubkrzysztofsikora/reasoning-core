# Evaluation baselines

Baseline manifests are committed, versioned records of an evaluation context.
They are immutable: `rc baseline capture --id <id>` refuses to overwrite an
existing ID. Raw audit logs and run artifacts remain local at
`~/.local/share/reasoning-core/baselines/` and are referenced by path and
SHA-256 in the manifest where available.

Use the registry before comparing results or changing an enforcement default:

```bash
rc baseline list
rc baseline show baseline-2026-08-09 --verify
rc baseline compare baseline-2026-08-09 <new-baseline-id>
```

The initial reference is `baseline-2026-08-09`. A baseline captures context,
not causal product quality: audit and operational metrics are diagnostics until
they are joined to blinded, human outcome labels. Every suite run writes an
immutable `run_manifest.json` beside its raw results with the task IDs, seed,
arm configuration, hashes, and report digest.

## Containment evaluation baselines

| Baseline | Date | Description |
|----------|------|-------------|
| `baseline-2026-09-15-host-enforcement-qualification` | 2026-09-15 | Host enforcement qualification for Claude Code 2.1.274 (Edit/Write). Schema repaired 2026-09-17. |
| `baseline-2026-09-15-ten-x-feasibility-corpus-v1` | 2026-09-15 | Feasibility corpus (20 tasks). |
| `baseline-2026-09-15-ten-x-confirmatory-corpus-v1` | 2026-09-15 | Superseded by v2 (prompt design defect eliminated control pressure). |
| `baseline-2026-09-17-ten-x-confirmatory-corpus-v2` | 2026-09-17 | **Active confirmatory corpus** (150 tasks, frozen). Used for the confirmatory result. |
