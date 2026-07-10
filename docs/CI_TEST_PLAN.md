# CI/CD integration test plan

This plan verifies that the reasoning-core PR scorer works correctly as a
script, as a GitHub Action/Marketplace action, and as an Azure DevOps pipeline
template.

---

## 1. Unit tests (`scripts/score_pr.py`)

Run in a Python 3.11 virtualenv with the repo's `requirements.txt` installed.

| # | Test | Command / Assertion |
|---|---|---|
| 1.1 | Changed-file detection | `python3 scripts/score_pr.py --base-ref HEAD~1 --head-ref HEAD` only scores files in the diff. |
| 1.2 | Extension filter | `--include "*.py"` skips `.md`, `.yml`, etc. |
| 1.3 | No matching files | Empty diff prints `No matching changed files to score.` and exits `0`. |
| 1.4 | Symbolic pass | Default symbolic mode returns `regression_detected: false` for a safe edit. |
| 1.5 | Symbolic block with rules | `RC_RULE_ENGINE=1` + a `deny` rule in `.reasoning-core/rules.yaml` blocks the file and exits `1` when `--fail-on-block` is set. |
| 1.6 | Plan grounding warn | `RC_PLAN_GROUNDING=1` + `PLAN.md` that omits the changed file emits a warning in the report. |
| 1.7 | JSON output | `--json scores.json` produces valid JSON with the same row count as the Markdown table. |
| 1.8 | Markdown report shape | Report contains `# reasoning-core PR Score Report`, per-file table, blocked/warn/clean sections. |
| 1.9 | External repo | From another git repo, run `python3 /path/to/reasoning-core/scripts/score_pr.py` and confirm it scores the external repo's diff. |
| 1.10 | `rc score-pr` CLI | `bin/rc score-pr --base-ref HEAD~1 --head-ref HEAD` exits `0` and produces a report. |

---

## 2. Local integration tests

| # | Test | Steps |
|---|---|---|
| 2.1 | No sidecar fallback | With no process on `:8765`, `--mode sidecar` prints a fallback warning and runs symbolic mode. |
| 2.2 | Healthy sidecar | Boot `scripts/start-sidecar.sh BACKGROUND=1`, then `--mode sidecar` hits `/score` and returns risk-vector scores (not `n/a`). |
| 2.3 | Sidecar unreachable + fail-closed | `S2_FAIL_CLOSED=1` + no sidecar should not crash the scorer; symbolic fallback still runs. |
| 2.4 | `fail-on-block` behavior | A blocked file with `--fail-on-block` exits non-zero; without the flag the report is printed and exit is `0`. |
| 2.5 | Large diff | A PR with 50+ changed files completes within the action's 10-minute budget in symbolic mode. |

---

## 3. GitHub Actions tests

Create a test repo (or use a fork) and add `.github/workflows/rc-score-test.yml`:

```yaml
on:
  pull_request:
    branches: [main]
jobs:
  score:
    permissions:
      contents: read
      pull-requests: write
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: jakubkrzysztofsikora/reasoning-core@main
        with:
          base-ref: origin/main
          mode: symbolic
          post-comment: "true"
```

| # | Test | Expected result |
|---|---|---|
| 3.1 | Symbolic mode default | Action completes in under 1 minute; report artifact uploaded. |
| 3.2 | PR comment posted | A comment appears on the PR with the Markdown report. |
| 3.3 | Reusable workflow | `uses: jakubkrzysztofsikora/reasoning-core/.github/workflows/reasoning-core-pr-score.yml@main` runs and posts a comment. |
| 3.4 | `fail-on-block: true` | A PR that violates a rule fails the check. |
| 3.5 | No comment when disabled | `post-comment: false` skips the comment step. |
| 3.6 | Sidecar mode | `sidecar: true` installs deps, boots the sidecar, and scores with neural risk vector. |
| 3.7 | Marketplace action from release | After publishing `v1.0.0`, `uses: jakubkrzysztofsikora/reasoning-core@v1.0.0` works identically to `@main`. |
| 3.8 | Permissions failure | Running without `pull-requests: write` produces a clear 403 in the comment step but does not mask scorer exit code. |

### Running GitHub tests locally with `act`

If `act` is installed:

```bash
act pull_request -W .github/workflows/reasoning-core-pr-score.yml \
  -s GITHUB_TOKEN="$(gh auth token)"
```

`act` will not fully exercise PR comments, but it validates action metadata,
step execution, and artifact upload.

---

## 4. Azure DevOps tests

In a test Azure DevOps project, create a pipeline from the repo's
`azure-pipelines-reasoning-core.yml` (or a project YAML that references it as a
template).

| # | Test | Expected result |
|---|---|---|
| 4.1 | Symbolic mode | Build succeeds; `reasoning-core-pr-report` artifact contains `.md` and `.json`. |
| 4.2 | Build summary | The Markdown report appears in the build summary tab. |
| 4.3 | PR comment | A thread is added to the pull request with the report. |
| 4.4 | `failOnBlock: true` | Build fails when a rule or contract is violated. |
| 4.5 | Sidecar mode | `sidecar: true` installs Python deps and boots the sidecar; neural scores appear. |
| 4.6 | Template from GitHub resource | Referencing the template from another repo via `resources.repositories` works. |
| 4.7 | No PR | A manual run without `System.PullRequest.PullRequestId` skips the comment step gracefully. |

---

## 5. Security / privacy tests

| # | Test | Expected result |
|---|---|---|
| 5.1 | Symbolic mode sends no network traffic | No outbound connections in symbolic mode. |
| 5.2 | Sidecar binds loopback only | `S2_URL` outside `127.0.0.1`/`localhost` is rejected unless `S2_ALLOW_REMOTE=1`. |
| 5.3 | Token scope | PR comment only requires `pull-requests: write`; no `contents: write` needed for scoring. |
| 5.4 | No repo pollution | The action leaves no `.reasoning-core-tool` directory in the target repo. |

---

## 6. Compatibility matrix

Run the symbolic-mode action at minimum on:

| Runner / agent | Python | Result |
|---|---|---|
| `ubuntu-latest` GitHub runner | 3.11 | Must pass |
| `ubuntu-latest` Azure DevOps agent | 3.11 | Must pass |
| macOS local dev | 3.11 | Must pass |
| Windows local dev (with Git Bash) | 3.11 | Best effort; report any path issues |

---

## 7. Release / Marketplace regression checklist

Before tagging a new release:

- [ ] `action.yml` at repo root is valid YAML and has `author` + `branding`.
- [ ] `scripts/score_pr.py` passes unit tests (section 1).
- [ ] Full offline pytest suite is green: `pytest -m "not live"`.
- [ ] Reusable workflow syntax is valid (GitHub shows no warnings).
- [ ] Azure pipeline YAML syntax is valid.
- [ ] Test the action from a release tag in a throwaway repo.
- [ ] Confirm Marketplace release shows the correct category and README.

---

## 8. Automated CI gate proposal

Add a workflow to reasoning-core itself that exercises the action on every PR
touching `action.yml`, `scripts/score_pr.py`, or the workflow:

```yaml
name: ci-action-smoke
on:
  pull_request:
    paths:
      - action.yml
      - scripts/score_pr.py
      - .github/workflows/reasoning-core-pr-score.yml
jobs:
  smoke:
    permissions:
      contents: read
      pull-requests: write
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: ./
        with:
          base-ref: origin/${{ github.base_ref }}
          mode: symbolic
          post-comment: "false"
```

This dogfoods the action without spamming PR comments on every framework PR.
