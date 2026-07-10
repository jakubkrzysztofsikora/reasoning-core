# CI / CD integration

Add reasoning-core to every pull request with one file. The scorer compares the
PR's changed source files against the repo's `PLAN.md`, `.reasoning-core/rules.yaml`,
and optional neural sidecar, then posts a Markdown report.

**Default mode is symbolic** — no GPU, no model download, no cloud. It runs in
seconds on a free runner.

---

## GitHub Actions

### Option A — reusable workflow (one line)

Create `.github/workflows/reasoning-core-score.yml` in the repo you want gated:

```yaml
name: reasoning-core score

on:
  pull_request:
    branches: [main]

permissions:
  contents: read
  pull-requests: write

jobs:
  score:
    uses: jakubkrzysztofsikora/reasoning-core/.github/workflows/reasoning-core-pr-score.yml@main
    secrets: inherit
```

The workflow scores the PR and posts a comment. It never fails the build unless
you override `fail-on-block: true`.

### Option B — composite action (drop it into an existing job)

```yaml
- name: Score PR with reasoning-core
  uses: jakubkrzysztofsikora/reasoning-core/.github/actions/reasoning-core-score@main
  with:
    base-ref: origin/main
    mode: symbolic
    fail-on-block: "false"
    post-comment: "true"
```

### Inputs

| Input | Default | Description |
|---|---|---|
| `base-ref` | `origin/main` | Base git ref for the diff |
| `head-ref` | `HEAD` | Head git ref |
| `mode` | `symbolic` | `symbolic` (fast, local) or `sidecar` (neural scorer) |
| `include` | *(all source extensions)* | Comma-separated extension globs, e.g. `*.py,*.ts` |
| `fail-on-block` | `false` | Exit non-zero when a file is blocked |
| `sidecar` | `false` | Boot the SSM sidecar (requires `torch`/`transformers` install) |
| `post-comment` | `true` | Post the report as a PR comment |

### Outputs

- `report` — path to the Markdown report.
- `json` — path to the machine-readable JSON report.
- `blocked` — `true` if at least one file was blocked.

Artifacts are uploaded as `reasoning-core-pr-report` even when the step fails.

---

## Azure DevOps

Add this pipeline file to the repo you want gated:

```yaml
# azure-pipelines.yml
trigger: none
pr:
  - main

resources:
  repositories:
    - repository: reasoningCore
      type: github
      endpoint: <your-github-service-connection>
      name: jakubkrzysztofsikora/reasoning-core

steps:
  - template: azure-pipelines-reasoning-core.yml@reasoningCore
```

Or copy `azure-pipelines-reasoning-core.yml` from this repo and commit it directly.

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `baseRef` | `origin/main` | Base git ref |
| `headRef` | `HEAD` | Head git ref |
| `mode` | `symbolic` | `symbolic` or `sidecar` |
| `include` | `""` | Comma-separated extension globs |
| `failOnBlock` | `false` | Fail if any file is blocked |
| `sidecar` | `false` | Boot the SSM sidecar |
| `postComment` | `true` | Post report as a PR thread |

The report is published as a build artifact and added to the build summary.

---

## Scoring modes

- **symbolic** — evaluates `PLAN.md` path contracts and `.reasoning-core/rules.yaml`
  with zero external dependencies. Fast and free-runner friendly.
- **sidecar** — sends each changed file to the local SSM sidecar for the 8-dim
  neural risk vector. Use this when the sidecar is already running in your
  self-hosted runner, or when you set `sidecar: true` to boot it in CI.

Symbolic mode is the right default for "score every PR" because it gives real
architectural signal without model weights, GPUs, or minutes of startup.

---

## Failing the build

Set `fail-on-block: true` (GitHub) or `failOnBlock: true` (Azure) to make the
gate hard. In symbolic mode this blocks on rule-engine denies and plan-contract
violations. In sidecar mode it also blocks on SSM regression decisions.

For an observe-first rollout, keep the default `false` and let the report train
your team before you turn on enforcement.

---

## Local dry-run

You can run the same scorer locally before pushing:

```bash
# from reasoning-core
python3 scripts/score_pr.py \
  --base-ref origin/main \
  --head-ref HEAD \
  --mode symbolic \
  --output report.md

# or via the rc CLI
rc score-pr --base-ref origin/main --head-ref HEAD --mode symbolic
```

---

## Security / privacy notes

- Default symbolic mode does **not** send code anywhere. It runs inside the CI runner.
- Sidecar mode binds `127.0.0.1` only; the scorer talks to the loopback address.
- The action/workflow clones reasoning-core into `.reasoning-core-tool` inside the
  runner workspace and leaves no state in your repo.
