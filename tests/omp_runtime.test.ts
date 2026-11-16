import { describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync, existsSync } from "node:fs";
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
    batch: { finalInputSha256: hash("args"), batchSha256: hash("batch"), candidates: [{ path: file,
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
      expect(result).toEqual({ allowed: true, finalInputSha256: f.event.batch.finalInputSha256,
        batchSha256: f.event.batch.batchSha256 });
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
 const ungated = oldHandlers.get("tool_call")({ toolName: "edit" });
 expect(ungated?.block).toBeUndefined();
 expect(ungated?.additionalContext).toContain("native_edit_unavailable");
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

test.each(["scoped", "plugin"])("%s adapter blocks session stop only for a policy refusal", async kind => {
 const f = fixture();
 try {
  mkdirSync(join(f.cwd, ".reasoning-core"));
  writeFileSync(join(f.cwd, ".reasoning-core", "omp-runtime.json"), JSON.stringify({
   python: join(process.cwd(), ".venv/bin/python"), repo_root: process.cwd(),
  }));
  let reply: any = { decision: "blocked", reason: "open duplicate finding", decision_id: "audit123" };
  let unavailable = false;
  const handlers = await withMode("enforce", async () => {
   const extension = kind === "scoped"
    ? (await import("../integrations/omp/reasoning_core_gate")).default
    : (await import("../integrations/omp-plugin/extensions/reasoning_core_gate")).default;
   const registered = new Map<string, any>();
   extension({ on: (name: string, handler: any) => registered.set(name, handler) }, async () => {
    if (unavailable) throw Error("bridge down");
    return reply;
   });
   return registered;
  });
  const stop = handlers.get("session_stop");
  expect(typeof stop).toBe("function");
  expect(await stop({}, f.ctx)).toEqual({ decision: "block", reason: "open duplicate finding" });
  reply = { decision: "allowed", reason: "session_stop", decision_id: "audit123" };
  expect(await stop({}, f.ctx)).toBeUndefined();
  reply = {};
  expect(await stop({}, f.ctx)).toBeUndefined();
  unavailable = true;
  expect(await stop({}, f.ctx)).toBeUndefined();
 } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

const loadGate = async (mode: string, onError: string) => {
  process.env.RC_GATE = mode;
  process.env.RC_GATE_ON_ERROR = onError;
  const { default: extension } = await import("../integrations/omp-plugin/extensions/reasoning_core_gate");
  const handlers = new Map<string, any>();
  extension({ on: (name: string, handler: any) => handlers.set(name, handler) });
  return handlers;
};

test.each(["advisory", "enforce"])("%s mode warns and never blocks when the runtime is missing", async mode => {
 const f = fixture();
 const prior = [process.env.RC_GATE, process.env.RC_GATE_ON_ERROR];
 try {
  const handlers = await loadGate(mode, "open");
  const stop = handlers.get("session_stop");
  const start = handlers.get("session_start");
  const call = handlers.get("tool_call");
  expect(await stop({}, f.ctx)).toBeUndefined();
  mkdirSync(join(f.cwd, ".reasoning-core"), { recursive: true });
  writeFileSync(join(f.cwd, ".reasoning-core", "install.manifest"), "");
  expect(await stop({}, f.ctx)).toBeUndefined();
  expect(await call({ toolName: "task", input: {} }, f.ctx)).toBeUndefined();
  writeFileSync(join(f.cwd, ".reasoning-core", "omp-owned.json"), "{}");
  expect(await start({}, f.ctx)).toBeUndefined();
  expect(await stop({}, f.ctx)).toBeUndefined();
  expect(await call({ toolName: "bash", input: { command: "ls" } }, f.ctx)).toEqual({
   additionalContext: expect.stringContaining("reasoning-core advisory (runtime_unresolved)"),
  });
  expect(await call({ toolName: "edit", input: {} }, f.ctx)).toEqual({
   additionalContext: expect.stringContaining("reasoning-core advisory (native_edit_unavailable)"),
  });
 } finally {
  if (prior[0] === undefined) delete process.env.RC_GATE; else process.env.RC_GATE = prior[0];
  if (prior[1] === undefined) delete process.env.RC_GATE_ON_ERROR; else process.env.RC_GATE_ON_ERROR = prior[1];
  rmSync(f.cwd, { recursive: true, force: true });
 }
});

test("an unresolved runtime blocks when errors are closed", async () => {
 const f = fixture();
 const prior = [process.env.RC_GATE, process.env.RC_GATE_ON_ERROR];
 try {
  const handlers = await loadGate("enforce", "closed");
  await handlers.get("session_start")({}, f.ctx);
  expect(await handlers.get("tool_call")({ toolName: "bash", toolCallId: "r1", input: { command: "ls" } }, f.ctx)).toEqual({
   block: true,
   reason: "reasoning-core: runtime_unresolved",
  });
  expect(await handlers.get("session_stop")({}, f.ctx)).toEqual({
   decision: "block",
   reason: "reasoning-core session audit unavailable",
  });
 } finally {
  if (prior[0] === undefined) delete process.env.RC_GATE; else process.env.RC_GATE = prior[0];
  if (prior[1] === undefined) delete process.env.RC_GATE_ON_ERROR; else process.env.RC_GATE_ON_ERROR = prior[1];
  rmSync(f.cwd, { recursive: true, force: true });
 }
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
  const handlers = await withMode("enforce", async () => {
   const { default: extension } = await import("../integrations/omp-plugin/extensions/reasoning_core_gate");
   const registered = new Map<string, any>();
   extension({ on: (name: string, handler: any) => registered.set(name, handler) });
   await registered.get("session_start")({}, f.ctx);
   return registered;
  });
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

test("plugin reports a policy block without blocking in advisory mode", async () => {
 const f = fixture();
 const priorAudit = process.env.RC_AUDIT_ROOT;
 const priorOverride = process.env.RC_ALLOW_GUARD_EDIT;
 try {
  delete process.env.RC_ALLOW_GUARD_EDIT;
  process.env.RC_AUDIT_ROOT = join(f.cwd, "audit");
  mkdirSync(join(f.cwd, ".reasoning-core"));
  writeFileSync(join(f.cwd, ".reasoning-core", "omp-runtime.json"), JSON.stringify({
   python: join(process.cwd(), ".venv/bin/python"), repo_root: process.cwd(),
  }));
  mkdirSync(join(f.cwd, "build/nested/src/hooks"), { recursive: true });
  writeFileSync(join(f.cwd, "build/nested/src/hooks/pre_edit_guard.py"), "guard\n");
  const call = await withMode("advisory", async () => {
   const { default: extension } = await import("../integrations/omp-plugin/extensions/reasoning_core_gate");
   const registered = new Map<string, any>();
   extension({ on: (name: string, handler: any) => registered.set(name, handler) });
   return registered.get("tool_call");
  });
  const result = await call({
   toolName: "bash", input: { command: "rm -rf nested", cwd: "build" },
  }, f.ctx);
  expect(result?.block).toBeUndefined();
  expect(result?.additionalContext).toContain("would have blocked");
  expect(result?.additionalContext).toContain("guarded file");
 } finally {
  if (priorAudit === undefined) delete process.env.RC_AUDIT_ROOT;
  else process.env.RC_AUDIT_ROOT = priorAudit;
  if (priorOverride === undefined) delete process.env.RC_ALLOW_GUARD_EDIT;
  else process.env.RC_ALLOW_GUARD_EDIT = priorOverride;
  rmSync(f.cwd, { recursive: true, force: true });
 }
});

async function withEnv<T>(vars: Record<string, string | undefined>, run: () => Promise<T>): Promise<T> {
 const prior = Object.fromEntries(Object.keys(vars).map(key => [key, process.env[key]]));
 for (const [key, value] of Object.entries(vars)) {
  if (value === undefined) delete process.env[key];
  else process.env[key] = value;
 }
 try { return await run(); } finally {
  for (const [key, value] of Object.entries(prior)) {
   if (value === undefined) delete process.env[key];
   else process.env[key] = value;
  }
 }
}

function withMode<T>(mode: string, run: () => Promise<T>): Promise<T> {
 return withEnv({ RC_GATE: mode, RC_GATE_ON_ERROR: undefined }, run);
}

function activePlugin(cwd: string, mode = "enforce", extra: Record<string, string | undefined> = {},
  bridge: (event: any) => Promise<any> = async () => { throw Error("bridge must not be called"); }) {
 mkdirSync(join(cwd, ".reasoning-core"), { recursive: true });
 writeFileSync(join(cwd, ".reasoning-core", "omp-runtime.json"), JSON.stringify({
  python: join(process.cwd(), ".venv/bin/python"), repo_root: process.cwd(),
 }));
 return withEnv({ RC_GATE: mode, RC_GATE_ON_ERROR: undefined, RC_GATE_SETTINGS: join(cwd, "gate-settings.json"), ...extra }, async () => {
  const { default: extension } = await import("../integrations/omp-plugin/extensions/reasoning_core_gate");
  const handlers = new Map<string, any>();
  extension({ on: (name: string, handler: any) => handlers.set(name, handler) }, bridge);
  return handlers;
 });
}

test.each(["[.envrc.local]", "[.envrc.local#ABCD]", "[notes.txt#1A2B] ", "file:///tmp/x"])("plugin leaves unbindable write target %s ungoverned", async path => {
 const f = fixture();
 try {
  const call = (await activePlugin(f.cwd)).get("tool_call");
  const result = await call({ toolName: "write", toolCallId: "uri", input: { path, content: "{}" } }, f.ctx);
  expect(result?.block).toBeUndefined();
  expect(result?.additionalContext).toContain("non-file destination");
 } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test("plugin leaves writes outside the project root ungoverned", async () => {
 const f = fixture();
 try {
  mkdirSync(join(f.cwd, ".reasoning-core"), { recursive: true });
  writeFileSync(join(f.cwd, ".reasoning-core", "omp-runtime.json"), JSON.stringify({
   python: join(process.cwd(), ".venv/bin/python"), repo_root: process.cwd(),
  }));
  let calls = 0;
  const handlers = await withMode("enforce", async () => {
   const { default: extension } = await import("../integrations/omp-plugin/extensions/reasoning_core_gate");
   const registered = new Map<string, any>();
   extension({ on: (name: string, handler: any) => registered.set(name, handler) },
     async () => { calls += 1; throw Error("bridge unavailable"); });
   return registered;
  });
  const result = await handlers.get("tool_call")(
   { toolName: "write", toolCallId: "outside", input: { path: "/tmp/rc-outside-target.txt", content: "x\n" } }, f.ctx);
  expect(result?.block).toBeUndefined();
  expect(result?.additionalContext).toContain("outside the project root");
  expect(calls).toBe(0);
  const large = await handlers.get("tool_call")(
   { toolName: "write", toolCallId: "large", input: { path: join(f.cwd, "big.txt"), content: "x".repeat(4 * 1024 * 1024 + 1) } }, f.ctx);
  expect(large?.block).toBeUndefined();
  expect(large?.additionalContext).toContain("exceeds the governed text limit");
  expect(calls).toBe(0);
 } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test("plugin leaves non-mutation routes ungated while active", async () => {
 const f = fixture();
 try {
  const call = (await activePlugin(f.cwd)).get("tool_call");
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
  const handlers = await withMode("enforce", async () => {
  const { default: extension } = await import("../integrations/omp-plugin/extensions/reasoning_core_gate");
  const registered = new Map<string, any>();
  extension({ on: (name: string, handler: any) => registered.set(name, handler) });
  await registered.get("session_start")({}, f.ctx);
  return registered;
 });
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

test.each(["open", "closed"])("plugin fails %s when the bridge cannot answer", async onError => {
 const f = fixture();
 try {
  const call = (await activePlugin(f.cwd, "enforce", { RC_GATE_ON_ERROR: onError })).get("tool_call");
  const result = await call({ toolName: "bash", toolCallId: "boom", input: { command: "ls" } }, f.ctx);
  if (onError === "open") {
   expect(result?.block).toBeUndefined();
   expect(result?.additionalContext).toContain("could not evaluate");
  } else {
   expect(result?.block).toBe(true);
   expect(result?.reason).toBe("reasoning-core: bridge_unavailable");
  }
 } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

const workerBlocked = (reason: string) => async () => ({ decision: "blocked", reason, decision_id: "id123456" });

test.each(["open", "closed"])("plugin fails %s when the worker reports its own infrastructure failure", async onError => {
  const f = fixture();
  try {
    const call = (await activePlugin(f.cwd, "enforce", { RC_GATE_ON_ERROR: onError },
      workerBlocked("bridge_audit_unavailable"))).get("tool_call");
    const result = await call({ toolName: "bash", toolCallId: "audit", input: { command: "ls" } }, f.ctx);
    if (onError === "open") {
      expect(result?.block).toBeUndefined();
      expect(result?.additionalContext).toContain("bridge_audit_unavailable");
    } else {
      expect(result?.block).toBe(true);
      expect(result?.reason).toBe("reasoning-core: bridge_audit_unavailable");
    }
  } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test("plugin keeps a worker policy refusal blocking when errors fail open", async () => {
  const f = fixture();
  try {
    const call = (await activePlugin(f.cwd, "enforce", { RC_GATE_ON_ERROR: "open" },
      workerBlocked("bash_screen_blocked"))).get("tool_call");
    const result = await call({ toolName: "bash", toolCallId: "screen", input: { command: "ls" } }, f.ctx);
    expect(result?.block).toBe(true);
    expect(result?.reason).toBe("bash_screen_blocked");
  } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test.each(["open", "closed"])("plugin degrades to %s after a failed session start", async onError => {
  const f = fixture();
  try {
    const handlers = await activePlugin(f.cwd, "enforce", { RC_GATE_ON_ERROR: onError },
      workerBlocked("bridge_audit_unavailable"));
    await handlers.get("session_start")({}, f.ctx);
    const result = await handlers.get("tool_call")({ toolName: "bash", toolCallId: "after", input: { command: "ls" } }, f.ctx);
    if (onError === "open") {
      expect(result?.block).toBeUndefined();
      expect(result?.additionalContext).toContain("bridge_audit_unavailable");
    } else {
      expect(result?.block).toBe(true);
      expect(result?.reason).toBe("reasoning-core: bridge_audit_unavailable");
    }
  } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test("operator /rc flips mode and on-error, persists them, and takes live effect", async () => {
 const f = fixture();
 try {
  const settings = join(f.cwd, "gate-settings.json");
  await withEnv({ RC_GATE: undefined, RC_GATE_ON_ERROR: undefined, RC_GATE_SETTINGS: settings }, async () => {
   const { default: extension } = await import("../integrations/omp-plugin/extensions/reasoning_core_gate");
   const handlers = new Map<string, any>();
   const commands = new Map<string, any>();
   extension({ on: (name: string, handler: any) => handlers.set(name, handler),
    registerCommand: (name: string, command: any) => commands.set(name, command) },
    async () => { throw Error("bridge unavailable"); });
   mkdirSync(join(f.cwd, ".reasoning-core"), { recursive: true });
   writeFileSync(join(f.cwd, ".reasoning-core", "omp-runtime.json"), JSON.stringify({
    python: join(process.cwd(), ".venv/bin/python"), repo_root: process.cwd(),
   }));
   const notes: string[] = [];
   const ctx = { ...f.ctx, ui: { notify: (message: string) => notes.push(message) } };
   const rc = commands.get("rc");
   const last = () => notes[notes.length - 1];
   expect(rc).toBeDefined();
   await rc.handler("", ctx);
   expect(last()).toContain("mode=advisory [default]");
   expect(last()).toContain("on-error=open [default]");
   await rc.handler("mode enable", ctx);
   expect(last()).toContain("mode=enforce [session]");
   expect(last()).toContain("session env: RC_GATE");
   expect(process.env.RC_GATE).toBe("enforce");
   expect(JSON.parse(readFileSync(settings, "utf8")).mode).toBe("enforce");
   await rc.handler("RC_GATE_ON_ERROR closed", ctx);
   expect(last()).toContain("on-error=closed [session]");
   expect(process.env.RC_GATE_ON_ERROR).toBe("closed");
   expect(JSON.parse(readFileSync(settings, "utf8")).onError).toBe("closed");
   const call = handlers.get("tool_call");
   expect((await call({ toolName: "bash", toolCallId: "a", input: { command: "ls" } }, f.ctx))?.block).toBe(true);
   await rc.handler("on-error open", ctx);
   const soft = await call({ toolName: "bash", toolCallId: "b", input: { command: "ls" } }, f.ctx);
   expect(soft?.block).toBeUndefined();
   expect(soft?.additionalContext).toContain("could not evaluate");
   await rc.handler("disable", ctx);
   expect(await call({ toolName: "bash", toolCallId: "c", input: { command: "ls" } }, f.ctx)).toBeUndefined();
   await rc.handler("bogus", ctx);
   expect(last()).toContain("rc: (no args) status");
   await rc.handler("reset", ctx);
   expect(JSON.parse(readFileSync(settings, "utf8"))).toEqual({});
   expect(process.env.RC_GATE).toBeUndefined();
   expect(process.env.RC_GATE_ON_ERROR).toBeUndefined();
   expect(last()).toContain("mode=advisory");
   expect(last()).toContain("on-error=open");
  });
 } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test("operator /rc exports the gate env for the session and reset restores the inherited value", async () => {
 const f = fixture();
 try {
  const settings = join(f.cwd, "gate-settings.json");
  await withEnv({ RC_GATE: "advisory", RC_GATE_ON_ERROR: undefined, RC_GATE_SETTINGS: settings }, async () => {
   const { default: extension } = await import("../integrations/omp-plugin/extensions/reasoning_core_gate");
   const commands = new Map<string, any>();
   extension({ on: () => {}, registerCommand: (name: string, command: any) => commands.set(name, command) },
     async () => { throw Error("bridge unavailable"); });
   const notes: string[] = [];
   const ctx = { ...f.ctx, ui: { notify: (message: string) => notes.push(message) } };
   const rc = commands.get("rc");
   const last = () => notes[notes.length - 1];
   await rc.handler("mode enforce", ctx);
   expect(process.env.RC_GATE).toBe("enforce");
   expect(last()).toContain("mode=enforce [session]");
   await rc.handler("reset", ctx);
   expect(process.env.RC_GATE).toBe("advisory");
   expect(last()).toContain("mode=advisory [env]");
  });
 } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});

test("operator /rc lists, sets and unsets any RC_* variable for the session only", async () => {
 const f = fixture();
 try {
  const settings = join(f.cwd, "gate-settings.json");
  await withEnv({ RC_GATE: undefined, RC_GATE_ON_ERROR: undefined, RC_GATE_SETTINGS: settings,
    RC_PYTHON: "/usr/bin/python3", RC_API_TOKEN: "hunter2" }, async () => {
   const { default: extension } = await import("../integrations/omp-plugin/extensions/reasoning_core_gate");
   const commands = new Map<string, any>();
   extension({ on: () => {}, registerCommand: (name: string, command: any) => commands.set(name, command) },
     async () => { throw Error("bridge unavailable"); });
   const notes: string[] = [];
   const ctx = { ...f.ctx, ui: { notify: (message: string) => notes.push(message) } };
   const rc = commands.get("rc");
   const last = () => notes[notes.length - 1];
   await rc.handler("env", ctx);
   expect(last()).toContain("RC_PYTHON=/usr/bin/python3");
   expect(last()).toContain("RC_API_TOKEN=***");
   expect(last()).not.toContain("hunter2");
   await rc.handler("env RC_MODEL sonnet", ctx);
   expect(process.env.RC_MODEL).toBe("sonnet");
   expect(last()).toContain("RC_MODEL=sonnet [session]");
   await rc.handler("env RC_ARGS a b c", ctx);
   expect(process.env.RC_ARGS).toBe("a b c");
   expect(existsSync(settings)).toBe(false);
   await rc.handler("env RC_MODEL", ctx);
   expect(last()).toContain("RC_MODEL=sonnet [session]");
   await rc.handler("env RC_GATE enforce", ctx);
   expect(process.env.RC_GATE).toBe("enforce");
   expect(last()).toContain("mode=enforce [session]");
   expect(existsSync(settings)).toBe(false);
   await rc.handler("unset RC_MODEL", ctx);
   expect(process.env.RC_MODEL).toBeUndefined();
   expect(last()).toContain("RC_MODEL reverted (unset)");
   await rc.handler("unset RC_PYTHON", ctx);
   expect(process.env.RC_PYTHON).toBe("/usr/bin/python3");
   await rc.handler("env PATH nope", ctx);
   expect(last()).toContain("is not an RC_* variable name");
   await rc.handler("unset", ctx);
   expect(last()).toContain("rc unset <RC_NAME>");
   await rc.handler("reset", ctx);
   expect(process.env.RC_GATE).toBeUndefined();
   expect(process.env.RC_MODEL).toBeUndefined();
   expect(process.env.RC_PYTHON).toBe("/usr/bin/python3");
   expect(last()).toContain("mode=advisory");
  });
 } finally { rmSync(f.cwd, { recursive: true, force: true }); }
});
