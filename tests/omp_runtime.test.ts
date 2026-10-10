import { describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createGate, bindings, runBridge } from "../.omp/extensions/reasoning_core_gate";

const hash = (value: string) => new Bun.CryptoHasher("sha256").update(value).digest("hex");
function fixture() {
  const cwd = mkdtempSync(join(tmpdir(), "rc runtime "));
  const file = join(cwd, "hello.py");
  writeFileSync(file, "old\n");
  const ctx = { cwd, agent: { id: "child", parentId: "parent" },
    sessionManager: { getSessionId: () => "session-child" } };
  const event = { type: "native_edit_prepare", toolCallId: "call",
    batch: { finalInputSha256: hash("args"), candidates: [{ path: file,
      operation: "write", beforeSrc: "old\n", afterSrc: "new\n",
      expectedBeforeSha256: hash("old\n") }] } };
  return { cwd, file, ctx, event };
}
describe("session native guard adapter", () => {
  test("converts exact native bytes, authentic identity, and validates all bindings", async () => {
    const f = fixture(); let seen: any;
    try {
      const gate = createGate(async wire => {
        seen = wire;
        return { decision: "allowed", reason: "checks", decision_id: "id", ...bindings(wire.batch) };
      });
      const result = await gate.prepare(f.event, f.ctx);
      expect(result).toEqual({ allowed: true, finalInputSha256: f.event.batch.finalInputSha256 });
      expect(seen.batch.agent_id).toBe("child");
      expect(seen.batch.parent_agent_id).toBe("parent");
      expect(seen.batch.session_id).toBe("session-child");
      expect(seen.batch.candidates[0].after_src).toBe("new\n");
      writeFileSync(f.file, "new\n");
      const receipt = await gate.result({ type: "native_edit_result", toolCallId: "call", success: true, error: null }, f.ctx);
      expect(seen.persisted_digests[f.file]).toBe(hash("new\n"));
      expect(receipt).toBeUndefined();
    } finally { rmSync(f.cwd, { recursive: true, force: true }); }
  });
  test.each(["missing", "input", "candidate", "batch", "blocked", "throw"])("%s response denies without a receipt", async mode => {
    const f = fixture(); let calls = 0;
    try {
      const gate = createGate(async wire => {
        calls++;
        if (mode === "throw") throw Error("unavailable");
        if (mode === "missing") return {};
        const reply = { decision: mode === "blocked" ? "blocked" : "allowed", reason: "checks",
          decision_id: "id", ...bindings(wire.batch) };
        if (mode === "input") reply.final_input_sha256 = hash("other");
        if (mode === "candidate") reply.candidate_sha256 = hash("other");
        if (mode === "batch") reply.batch_sha256 = hash("other");
        return reply;
      });
      expect((await gate.prepare(f.event, f.ctx)).allowed).toBe(false);
      await gate.result({ toolCallId: "call", success: false, error: "deny" }, f.ctx);
      expect(calls).toBe(1);
    } finally { rmSync(f.cwd, { recursive: true, force: true }); }
  });
  test("bridge uses JSON stdin and fixed argv with bounded process failure", async () => {
    const value = { event: "test", text: "spaces ' ; $()" };
    const reply = await runBridge(value, { argv: [process.execPath, "-e",
      'let s=""; for await (const c of process.stdin) s+=c; process.stdout.write(s);'], timeoutMs: 2000 });
    expect(reply).toEqual(value);
    await expect(runBridge(value, { argv: [process.execPath, "-e", "setTimeout(()=>{},10000)"], timeoutMs: 30 })).rejects.toThrow();
    await expect(runBridge(value, { argv: [process.execPath, "-e", 'console.log("not json")'], timeoutMs: 2000 })).rejects.toThrow();
  });
  test("Python canonical hash parity includes unicode and resolved missing parents", async () => {
    const f = fixture();
    try {
      const gate = createGate(async wire => {
        const proc = Bun.spawn([join(process.cwd(), ".venv/bin/python"), "-c",
          "import json,sys; from src.omp_contract import PreparedBatch,GateDecision; b=PreparedBatch.from_dict(json.load(sys.stdin)['batch']); print(json.dumps(GateDecision.for_batch(b,decision='allowed',reason='checks',decision_id='id').to_dict()))"],
          { stdin: new Blob([JSON.stringify(wire)]), stdout: "pipe", stderr: "pipe" });
        return JSON.parse(await new Response(proc.stdout).text());
      });
      f.event.batch.candidates[0].path = join(f.cwd, "missing", "emoji.py");
      f.event.batch.candidates[0].beforeSrc = null as any;
      f.event.batch.candidates[0].expectedBeforeSha256 = null as any;
      f.event.batch.candidates[0].afterSrc = "text = '\u{1f600}\u00e9'\n";
      expect((await gate.prepare(f.event, f.ctx)).allowed).toBe(true);
    } finally { rmSync(f.cwd, { recursive: true, force: true }); }
  });
});

test("qualified adapter restricts unprepared mutation and delegated routes", async () => {
 const { default: extension } = await import("../.omp/extensions/reasoning_core_gate");
 const handlers = new Map<string, any>();
 extension({ nativeEditProtocol: 1, on: (name: string, handler: any) => handlers.set(name, handler) });
 const tool = handlers.get("tool_call");
 for (const toolName of ["bash", "python", "eval", "write", "ast_edit", "lsp", "archive", "sqlite", "custom_mutator", "task"]) {
  expect(tool({ toolName, input: {} })?.block).toBe(true);
 }
 for (const toolName of ["edit", "read", "grep", "glob", "find"]) expect(tool({ toolName, input: {} })).toBeUndefined();
 expect(handlers.get("before_subagent_spawn")({ invocationKind: "eval" })?.block).toBe(true);
 expect(handlers.get("user_bash")({ command: "touch escaped" })?.result.exitCode).toBe(1);
 expect(handlers.get("user_python")({ code: "write()" })?.result.exitCode).toBe(1);
 const oldHandlers = new Map<string, any>();
 extension({ on: (name: string, handler: any) => oldHandlers.set(name, handler) });
 expect(oldHandlers.get("tool_call")({ toolName: "edit" })?.block).toBe(true);
});
