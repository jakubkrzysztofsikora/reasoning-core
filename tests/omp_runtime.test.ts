import { describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createGate, bindings, runBridge } from "../integrations/omp/reasoning_core_gate";

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
 const { default: extension } = await import("../integrations/omp/reasoning_core_gate");
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

test.each(["policy", "timeout", "malformed"])("safe denial diagnostic: %s", async mode => {
 const f = fixture();
 try {
  const gate = createGate(async wire => {
   if (mode === "timeout") throw Error("bridge timeout");
   if (mode === "malformed") return {};
   return { decision: "blocked", reason: "guard_file_locked", decision_id: "audit123", ...bindings(wire.batch) };
  });
  const result = await gate.prepare(f.event, f.ctx);
  expect(result.allowed).toBe(false);
  expect(result.reason).toBe(mode === "policy" ? "guard_file_locked" :
    mode === "timeout" ? "bridge_timeout" : "invalid_bridge_response");
  if (mode === "policy") expect(result.decisionId).toBe("audit123");
 } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test.each(["scoped", "plugin"])("%s adapter blocks session stop with the review reason", async kind => {
 const f = fixture();
 try {
  mkdirSync(join(f.cwd, ".reasoning-core"));
  writeFileSync(join(f.cwd, ".reasoning-core", "omp-runtime.json"), JSON.stringify({
   python: join(process.cwd(), ".venv/bin/python"), repo_root: process.cwd(),
  }));
  const extension = kind === "scoped"
   ? (await import("../integrations/omp/reasoning_core_gate")).default
   : (await import("../integrations/omp-plugin/extensions/reasoning_core_gate")).default;
  const handlers = new Map<string, any>();
  let reply: any = { decision: "blocked", reason: "open duplicate finding", decision_id: "audit123" };
  let unavailable = false;
  extension({ on: (name: string, handler: any) => handlers.set(name, handler) }, async () => {
   if (unavailable) throw Error("bridge down");
   return reply;
  });
  const stop = handlers.get("session_stop");
  expect(typeof stop).toBe("function");
  expect(await stop({}, f.ctx)).toEqual({ decision: "block", reason: "open duplicate finding" });
  reply = { decision: "allowed", reason: "session_stop", decision_id: "audit123" };
  expect(await stop({}, f.ctx)).toBeUndefined();
  reply = {};
  expect(await stop({}, f.ctx)).toEqual({ decision: "block", reason: "reasoning-core session audit unavailable" });
  unavailable = true;
  expect(await stop({}, f.ctx)).toEqual({ decision: "block", reason: "reasoning-core session audit unavailable" });
 } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test("plugin stays dormant for non-omp rc repos and blocks stop for broken omp installs", async () => {
 const f = fixture();
 try {
  const { default: extension } = await import("../integrations/omp-plugin/extensions/reasoning_core_gate");
  const handlers = new Map<string, any>();
  extension({ on: (name: string, handler: any) => handlers.set(name, handler) });
  const stop = handlers.get("session_stop");
  const call = handlers.get("tool_call");
  expect(await stop({}, f.ctx)).toBeUndefined();
  mkdirSync(join(f.cwd, ".reasoning-core"));
  writeFileSync(join(f.cwd, ".reasoning-core", "install.manifest"), "");
  expect(await stop({}, f.ctx)).toBeUndefined();
  expect(await call({ toolName: "task", input: {} }, f.ctx)).toBeUndefined();
  writeFileSync(join(f.cwd, ".reasoning-core", "omp-owned.json"), "{}");
  expect(await stop({}, f.ctx)).toEqual({
   decision: "block", reason: "reasoning-core runtime unresolved (run `rc init --host omp`)",
  });
  expect(await call({ toolName: "task", input: {} }, f.ctx)).toBeUndefined();
  expect(await call({ toolName: "bash", input: { command: "ls" } }, f.ctx)).toEqual({
   block: true, reason: "reasoning-core runtime unresolved (run `rc init --host omp`)",
  });
 } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test("plugin screens a relative bash cwd against its project", async () => {
 const f = fixture();
 const priorOverride = process.env.RC_ALLOW_GUARD_EDIT;
 const priorAudit = process.env.RC_AUDIT_ROOT;
 try {
  delete process.env.RC_ALLOW_GUARD_EDIT;
  process.env.RC_AUDIT_ROOT = join(f.cwd, "audit");
  mkdirSync(join(f.cwd, ".reasoning-core"));
  writeFileSync(join(f.cwd, ".reasoning-core", "omp-runtime.json"), JSON.stringify({
   python: join(process.cwd(), ".venv/bin/python"), repo_root: process.cwd(),
  }));
  const guarded = join(f.cwd, "build/nested/src/hooks/pre_edit_guard.py");
  mkdirSync(join(f.cwd, "build/nested/src/hooks"), { recursive: true });
  writeFileSync(guarded, "guard\n");
  const { default: extension } = await import("../integrations/omp-plugin/extensions/reasoning_core_gate");
  const handlers = new Map<string, any>();
  extension({ on: (name: string, handler: any) => handlers.set(name, handler) });
  const result = await handlers.get("tool_call")({
   toolName: "bash", input: { command: "rm -rf nested", cwd: "build" },
  }, f.ctx);
  expect(result?.block).toBe(true);
  expect(result?.reason).toContain("guarded file");
 } finally {
  if (priorOverride === undefined) delete process.env.RC_ALLOW_GUARD_EDIT;
  else process.env.RC_ALLOW_GUARD_EDIT = priorOverride;
  if (priorAudit === undefined) delete process.env.RC_AUDIT_ROOT;
  else process.env.RC_AUDIT_ROOT = priorAudit;
  rmSync(f.cwd, { recursive: true, force: true });
 }
});

function activePlugin(cwd: string) {
 mkdirSync(join(cwd, ".reasoning-core"));
 writeFileSync(join(cwd, ".reasoning-core", "omp-runtime.json"), JSON.stringify({
  python: join(process.cwd(), ".venv/bin/python"), repo_root: process.cwd(),
 }));
 return import("../integrations/omp-plugin/extensions/reasoning_core_gate").then(({ default: extension }) => {
  const handlers = new Map<string, any>();
  extension({ on: (name: string, handler: any) => handlers.set(name, handler) }, async () => {
   throw Error("bridge must not be called");
  });
  return handlers.get("tool_call");
 });
}

test.each(["[.envrc.local]", "[.envrc.local#ABCD]", "[notes.txt#1A2B] ", "file:///tmp/x"])("plugin refuses unbindable write target %s", async path => {
 const f = fixture();
 try {
  const call = await activePlugin(f.cwd);
  expect(await call({ toolName: "write", toolCallId: "uri", input: { path, content: "{}" } }, f.ctx))
   .toEqual({ block: true, reason: "reasoning-core: non-file write targets are refused" });
 } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test("plugin leaves non-mutation routes ungated while active", async () => {
 const f = fixture();
 try {
  const call = await activePlugin(f.cwd);
  for (const toolName of ["eval", "task", "todo", "ask", "web_search", "context_notes", "read", "grep"])
   expect(await call({ toolName, input: {} }, f.ctx)).toBeUndefined();
  for (const path of ["xd://mcp__service_change", "ssh://host/path", "local://notes.md"])
   expect(await call({ toolName: "write", toolCallId: "uri", input: { path, content: "{}" } }, f.ctx)).toBeUndefined();
 } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test("plugin stop gate blocks a real duplicate until it is resolved", async () => {
 const f = fixture();
 const names = ["AccountMenu", "BasemapControl", "SortMenu"];
 const keys = ["RC_DUP_ORACLE", "RC_CACHE_DIR", "RC_AUDIT_ROOT", "RC_MODE"] as const;
 const previous = Object.fromEntries(keys.map(key => [key, process.env[key]]));
 try {
  process.env.RC_DUP_ORACLE = "1";
  process.env.RC_CACHE_DIR = join(f.cwd, "cache");
  process.env.RC_AUDIT_ROOT = join(f.cwd, "audit");
  process.env.RC_MODE = "copilot";
  mkdirSync(join(f.cwd, ".reasoning-core"));
  const python = join(process.cwd(), ".venv/bin/python");
  writeFileSync(join(f.cwd, ".reasoning-core", "omp-runtime.json"), JSON.stringify({
   python, repo_root: process.cwd(),
  }));
  const setup = Bun.spawnSync([python, "-c",
   "import sys; from pathlib import Path; from tests.test_dup_review import _menus; " +
   "from src.duplicate_detection import findings; from src.duplicate_detection.review import stop_decision; " +
   "root=Path(sys.argv[1]); path=findings.ledger_path(str(root)); " +
   "findings.record(path, _menus(root)); print(stop_decision(str(root), path, count_blocks=False).block)", f.cwd],
   { env: { ...process.env } });
  expect(setup.exitCode).toBe(0);
  expect(setup.stdout.toString().trim()).toBe("True");
  const { default: extension } = await import("../integrations/omp-plugin/extensions/reasoning_core_gate");
  const handlers = new Map<string, any>();
  extension({ on: (name: string, handler: any) => handlers.set(name, handler) });
  const stop = handlers.get("session_stop");
  const denied = await stop({}, f.ctx);
  expect(denied?.decision).toBe("block");
  expect(denied?.reason).toContain("DUPLICATE CHECK");
  for (const name of names) writeFileSync(join(f.cwd, `${name}.ts`), `export const ${name} = 1;\n`);
  expect(await stop({}, f.ctx)).toBeUndefined();
 } finally {
  for (const key of keys) {
   if (previous[key] === undefined) delete process.env[key];
   else process.env[key] = previous[key];
  }
  rmSync(f.cwd, { recursive: true, force: true });
 }
});
