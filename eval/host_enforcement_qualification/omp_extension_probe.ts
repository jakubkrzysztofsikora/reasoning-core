import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import reasoningCoreGate from "../../integrations/omp-plugin/extensions/reasoning_core_gate.ts";

const root = process.argv[2];
if (!root) throw new Error("usage: bun omp_extension_probe.ts <project-root>");

type Handler = (event: unknown, ctx: unknown) => unknown;
const handlers = new Map<string, Handler[]>();
const api = {
  nativeEditProtocol: 0,
  logger: { debug: () => {} },
  on: (name: string, fn: Handler) => {
    handlers.set(name, [...(handlers.get(name) ?? []), fn]);
  },
};
reasoningCoreGate(api);

const ctx = {
  cwd: root,
  sessionManager: { getSessionId: () => "rc-qualify-ext" },
  agent: { id: "main", parentId: null },
};
const toolCall = handlers.get("tool_call")![0];
const toolResult = handlers.get("tool_result")![0];
const spawn = handlers.get("before_subagent_spawn")![0];

const blocked = (value: unknown) =>
  typeof value === "object" && value !== null && "block" in value && value.block === true;

const bashAllowed = (await toolCall(
  { type: "tool_call", toolCallId: "q1", toolName: "bash", input: { command: "echo hi" } },
  ctx,
)) === undefined;
const bashBlocked = blocked(await toolCall(
  { type: "tool_call", toolCallId: "q2", toolName: "bash", input: { command: "git push --force origin main" } },
  ctx,
));

const prepared = await toolCall(
  { type: "tool_call", toolCallId: "q3", toolName: "write", input: { path: "rc-qualify-ext-target.txt", content: "qualify\n" } },
  ctx,
);
let writeRoundtrip = prepared === undefined;
if (writeRoundtrip) {
  writeFileSync(path.join(root, "rc-qualify-ext-target.txt"), "qualify\n");
  writeRoundtrip = (await toolResult(
    { type: "tool_result", toolName: "write", toolCallId: "q3", input: {}, content: [], isError: false },
    ctx,
  )) === undefined;
}

const subagentRecorded = (await spawn(
  { type: "before_subagent_spawn", agent: "scout" },
  ctx,
)) === undefined;

const advisorCost = handlers.get("advisor_cost_changed")![0];
const advisorYielded = handlers.get("advisor_yielded")![0];
await advisorCost({ type: "advisor_cost_changed", cost: 0.25 }, ctx);
await advisorYielded({ type: "advisor_yielded" }, ctx);

interface AuditRow {
  tool_name?: unknown;
  decision?: unknown;
  reason?: unknown;
  signal_source?: unknown;
  cost?: unknown;
}
const isAuditRow = (value: unknown): value is AuditRow =>
  typeof value === "object" && value !== null && "tool_name" in value;

const auditRoot = process.env.RC_AUDIT_ROOT ?? "";
const rows: AuditRow[] = [];
for (const file of new Bun.Glob("*/*.jsonl").scanSync({ cwd: auditRoot })) {
  for (const line of readFileSync(path.join(auditRoot, file), "utf8").split("\n")) {
    if (!line.trim()) continue;
    const parsed: unknown = JSON.parse(line);
    if (isAuditRow(parsed)) rows.push(parsed);
  }
}
const audited = (tool: string, check: (row: AuditRow) => boolean) =>
  rows.some((row) => row.tool_name === tool && check(row));

const summary = {
  bash_allowed: bashAllowed && audited("omp:bash", (row) => row.decision === "allowed"),
  bash_blocked: bashBlocked && audited("omp:bash", (row) => row.decision === "blocked"),
  write_roundtrip: writeRoundtrip
    && audited("omp:prepare", (row) => row.decision === "allowed")
    && audited("omp:result", (row) => row.reason === "receipt_verified"),
  subagent_recorded: subagentRecorded && audited("omp:subagent", (row) => row.decision === "allowed"),
  advisor_recorded: audited("omp:advisor",
    (row) => row.signal_source === "omp-advisor" && row.reason === "advisor_cost_changed" && row.cost === 0.25)
    && audited("omp:advisor",
      (row) => row.signal_source === "omp-advisor" && row.reason === "advisor_yielded" && row.cost === null),
};
console.log(JSON.stringify(summary));
if (!Object.values(summary).every(Boolean)) process.exit(1);
