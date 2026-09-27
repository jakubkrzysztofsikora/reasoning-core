# Autonomous coding harness

The bounded intake and reasoning-core integration is `rc autonomous`.
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

`triage_brief` is the only task text sent to the decision adapter. Write it as
a redacted summary. The default adapter is local Laya with automatic language
routing; it also receives the allowed path names. If the server is unavailable
or the brief is empty, the decision is uncertain and the coding model still
receives the full task. `--adapter local` uses simple rules without a model.
`--adapter jev` explicitly selects the hosted TypeSafe API and requires its
key. See [LAYA_LOCAL_EVAL.md](LAYA_LOCAL_EVAL.md) for local setup and evidence.

Each check is an argument array, never a shell command. Checks run in a
disposable copy of the clone. macOS uses `sandbox-exec` to deny network,
signals, child processes, and writes outside that copy. Check output is bounded
and captured as bytes. Linux currently fails closed: a root bind with `bwrap`
exposed host Unix control sockets, and a narrower backend is not qualified.
Without a supported sandbox, the check fails closed. The sandbox still permits reads of
host dependencies, so do not use this runner for hostile code that must not
read host files. Check output is hashed locally and is not sent to the coding
model on retry.

## Qualification and run

The qualification must match the exact installed Claude Code version, model,
permission mode, offered mutation tools, and enforcement source digest. A
result for an older host or guard build cannot authorize the current host.
Run the qualification suite after a host or enforcement-code upgrade:

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
  --model claude-sonnet-4-5 \
  --max-budget-usd 1 \
  --max-attempts 3 \
  --out ~/.local/share/reasoning-core/autonomous/task-001
```

Start the loopback Laya server first to get the default advisory hint. The
server is optional: without it, the run proceeds with an uncertain hint. Use
`--adapter local` for deterministic intake or `--adapter jev` for hosted Jev.
No intake response can grant path access or relax a deterministic deny.
The run refuses an unqualified host, an unhealthy sidecar, a preexisting output
directory, an existing contract that it cannot merge safely, or an output
directory inside the source repo.

The harness clones the committed source revision; uncommitted source changes
are not copied. It writes task policy only in the clone. It allows at most two
repair turns after the first attempt and allocates the declared provider budget
across attempts. Every repair turn repeats the original task and scope. It
stops on an out-of-scope final change, a symlink escape,
failed checks, a final file whose bytes lack a matching allowed gate audit
receipt, an exported patch that fails byte-for-byte verification, or exhaustion.
Successful runs produce `final.patch`;
all runs retain `workspace/` and `report.json` for review. No source-branch
merge or deployment is performed.

## Current limits

- Only the qualified Claude Code `Edit`/`Write` lane is enabled. Bash,
  delegation, notebook and worktree mutation tools are disabled.
- The agent's separate clone and qualified Edit/Write lane are not an OS
  container. The macOS check sandbox denies external writes and network, but
  it does not provide full read isolation from host files. Checks that need
  subprocesses will fail under the current macOS profile; Linux check
  execution is unavailable until a socket-isolating backend is qualified.
- The current runner checks final paths and tests. It is not the independent
  transient-write observer used by the 10x evaluation, so its report is not a
  new containment, speed, or quality claim.
- The one-task Laya + reasoning-core FeatureBench pilot is exploratory. See
  [the four-arm report](../eval/featurebench_four_arm/README.md); it does not
  establish a general quality, speed, or causal benefit.
