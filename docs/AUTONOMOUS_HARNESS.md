# Autonomous coding harness

The first runnable integration of Jev and reasoning-core is `rc autonomous`.
It takes a bounded task, makes one advisory intake decision, and runs Claude
Code unattended in a separate clone. The only enabled mutation tools are
the exact `Edit`/`Write` lane in a matching host-enforcement qualification.
Reasoning-core's pre-write hook controls those edits; deterministic checks
decide whether the final patch is accepted.

## Task file

Create a JSON file outside the source repository:

```json
{
  "prompt": "Update src/service.py so normalize_name trims surrounding spaces.",
  "triage_brief": "Small Python string-normalization change.",
  "allowed_paths": ["src/service.py"],
  "protected_paths": ["src/config.py"],
  "checks": [["python", "-m", "pytest", "-q", "tests/test_service.py"]]
}
```

`triage_brief` is the only task text sent to hosted Jev. Write it as a
redacted summary. Without it, or without `TYPESAFE_API_KEY`, Jev returns an
uncertain advisory decision and the coding model still receives the full task.
The rules adapter uses no decision model. For a local typed decision model,
use Laya as described in [LAYA_LOCAL_EVAL.md](LAYA_LOCAL_EVAL.md).

Each check is an argument array, never a shell command. The checks run on the
host in the separate clone, with a reduced environment. Supply checks you
trust. This version does not put the agent or tests in an OS container.

## Qualification and run

The qualification must match the exact installed Claude Code version, model,
permission mode, and offered mutation tools. A result for an older version
cannot authorize the current host. Run the existing qualification suite after
a host upgrade:

```bash
.venv/bin/python -m eval.host_enforcement_qualification.run \
  --model claude-sonnet-4-5 --no-gateway \
  --tools Edit,Write,MultiEdit \
  --out-dir ~/.local/share/reasoning-core/qualifications/claude-current
```

Use a fresh output directory for each qualification attempt. Then run:

```bash
rc autonomous \
  --repo /path/to/source-repo \
  --task /path/to/task.json \
  --qualification /path/to/qualification/report.json \
  --adapter jev \
  --model claude-sonnet-4-5 \
  --max-budget-usd 1 \
  --max-attempts 3 \
  --out ~/.local/share/reasoning-core/autonomous/task-001
```

Use `--adapter local` to run entirely without Jev. A Jev response is
advisory task data. It cannot grant path access or relax a deterministic deny.
The run refuses an unqualified host, an unhealthy sidecar, a preexisting output
directory, an existing contract that it cannot merge safely, or an output
directory inside the source repo.

The harness clones the committed source revision; uncommitted source changes
are not copied. It writes task policy only in the clone. It allows at most two
repair turns after the first attempt and allocates the declared provider budget
across attempts. It stops on an out-of-scope final change, a symlink escape,
failed checks, a final file whose bytes lack a matching allowed gate audit
receipt, or exhaustion. Successful runs produce `final.patch`;
all runs retain `workspace/` and `report.json` for review. No source-branch
merge or deployment is performed.

## Current limits

- Only the qualified Claude Code `Edit`/`Write` lane is enabled. Bash,
  delegation, notebook and worktree mutation tools are disabled.
- A separate clone limits where ordinary file-tool edits land, but it is not
  OS-level filesystem or network containment. Trusted acceptance commands can
  execute arbitrary code on the host.
- The current runner checks final paths and tests. It is not the independent
  transient-write observer used by the 10x evaluation, so its report is not a
  new containment, speed, or quality claim.
- Jev's confidence floor is provisional. The proposed four-arm, frozen-task
  pilot in [JEV_AUTONOMOUS_HARNESS_DESIGN.md](JEV_AUTONOMOUS_HARNESS_DESIGN.md)
  is still required before claiming combined value.
