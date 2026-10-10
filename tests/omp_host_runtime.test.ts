import { expect, test } from "bun:test";
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
const upstream = process.env.RC_OMP_SOURCE;
if (!upstream) throw Error("RC_OMP_SOURCE must name the pinned patched source tree");
const { EditTool } = await import(join(upstream, "packages/coding-agent/src/edit/index.ts"));
const { Settings } = await import(join(upstream, "packages/coding-agent/src/config/settings.ts"));
test("real EditTool requests whole batch before any write and publishes failure result", async () => {
 const cwd = mkdtempSync(join(tmpdir(), "rc host "));
 try {
  writeFileSync(join(cwd, "first.py"), "a\n");
  writeFileSync(join(cwd, "last.py"), "b\n");
  let prepared: any; let result: any;
  const session = { cwd, hasUI: false, enableLsp: false, settings: Settings.isolated({}),
    getSessionFile: () => null, getSessionSpawns: () => "*", getArtifactsDir: () => null,
    getSessionId: () => "session", getAgentId: () => "agent", getPlanModeState: () => undefined,
    hasNativeEditGuard: () => true,
    prepareNativeEdit: async (event: any) => {
      prepared = event;
      expect(readFileSync(join(cwd, "first.py"), "utf8")).toBe("a\n");
      return { allowed: false, finalInputSha256: event.batch.finalInputSha256 };
    },
    resultNativeEdit: async (event: any) => { result = event; },
  };
  const input = "*** Begin Patch\n*** Update File: first.py\n@@\n-a\n+A\n*** Update File: last.py\n@@\n-b\n+B\n*** End Patch";
  const output = await new EditTool(session, "apply_patch").execute("call", { input });
  expect(output.isError).toBe(true);
  expect(prepared.batch.candidates).toHaveLength(2);
  expect(prepared.toolCallId).toBe("call");
  expect(result.success).toBe(false);
  expect(readFileSync(join(cwd, "first.py"), "utf8")).toBe("a\n");
  expect(readFileSync(join(cwd, "last.py"), "utf8")).toBe("b\n");
 } finally { rmSync(cwd, { recursive: true, force: true }); }
});

const { ExtensionRunner } = await import(join(upstream, "packages/coding-agent/src/extensibility/extensions/runner.ts"));
const { SessionManager } = await import(join(upstream, "packages/coding-agent/src/session/session-manager.ts"));
const { createGate, runBridge } = await import("../.omp/extensions/reasoning_core_gate");
test.each(["allow", "deny"])("real native host and isolated Python bridge: %s", async mode => {
 const cwd = mkdtempSync(join(tmpdir(), "rc host bridge "));
 const audit = process.env.RC_AUDIT_ROOT;
 process.env.RC_AUDIT_ROOT = join(cwd, "audit");
 try {
  const file = join(cwd, "hello.py"); writeFileSync(file, "x = 1\n");
  const gate = createGate(event => runBridge(event, { argv: [join(process.cwd(), ".venv/bin/python"), "-I", "-c",
    "import sys; sys.path.insert(0,sys.argv[1]); from src.hooks.omp_bridge import main; raise SystemExit(main())", process.cwd()] }));
  const manager = SessionManager.inMemory(cwd);
  const handlers = new Map([
    ["native_edit_prepare", [(event: any, ctx: any) => gate.prepare(event, ctx)]],
    ["native_edit_result", [(event: any, ctx: any) => gate.result(event, ctx)]],
  ]);
  const runner = new ExtensionRunner([{ path: "guard", handlers }], { flagValues: new Map(), pendingProviderRegistrations: [] },
    cwd, manager, {}, undefined, undefined, undefined, undefined,
    { kind: "sub", id: "child", name: "child", depth: 1, parentId: "parent" });
  const session = { cwd, hasUI: false, enableLsp: false, settings: Settings.isolated({}),
    getSessionFile: () => null, getSessionSpawns: () => "*", getArtifactsDir: () => null,
    getSessionId: () => manager.getSessionId(), getAgentId: () => "child", getPlanModeState: () => undefined,
    hasNativeEditGuard: () => true,
    prepareNativeEdit: (event: any, signal: any) => runner.emitNativeEdit(event, manager.getSessionId(), "child", signal),
    resultNativeEdit: (event: any) => runner.emitNativeEdit(event, manager.getSessionId(), "child"),
  };
  const next = mode === "allow" ? "x = 2" : "def broken(:";
  const oldOracle = process.env.RC_ORACLE_BLOCK; process.env.RC_ORACLE_BLOCK = "1";
  const oldMode = process.env.RC_MODE; process.env.RC_MODE = "copilot";
  const output = await new EditTool(session, "apply_patch").execute("bridge-call", {
    input: "*** Begin Patch\n*** Update File: hello.py\n@@\n-x = 1\n+" + next + "\n*** End Patch",
  });
  if (oldOracle === undefined) delete process.env.RC_ORACLE_BLOCK; else process.env.RC_ORACLE_BLOCK = oldOracle;
  if (oldMode === undefined) delete process.env.RC_MODE; else process.env.RC_MODE = oldMode;
  expect(output.isError === true).toBe(mode === "deny");
  expect(readFileSync(file, "utf8")).toBe(mode === "allow" ? "x = 2\n" : "x = 1\n");
 } finally {
  if (audit === undefined) delete process.env.RC_AUDIT_ROOT; else process.env.RC_AUDIT_ROOT = audit;
  rmSync(cwd, { recursive: true, force: true });
 }
});
test.each(["missing", "throw", "malformed", "stale"])("runner fails closed for %s handler and preserves advisor identity", async mode => {
 const manager = SessionManager.inMemory("/tmp");
 let identity: any;
 const handler = (event: any, ctx: any) => {
  identity = { agent: ctx.agent, session: ctx.sessionManager.getSessionId() };
  if (mode === "throw") throw Error("failed");
  if (mode === "missing") return;
  return { allowed: true, finalInputSha256: mode === "stale" ? "other" : undefined };
 };
 const runner = new ExtensionRunner([{ path: "guard", handlers: new Map([["native_edit_prepare", [handler]]]) }],
  { flagValues: new Map(), pendingProviderRegistrations: [] }, "/tmp", manager, {},
  undefined, undefined, undefined, undefined, { kind: "main", id: "parent", name: "parent", depth: 0 });
 const result = await runner.emitNativeEdit({ type: "native_edit_prepare", toolCallId: "call",
  batch: { finalInputSha256: "bound", candidates: [] } }, "advisor-session", "advisor");
 expect(result.allowed).toBe(false);
 expect(identity.agent.id).toBe("advisor");
 expect(identity.agent.parentId).toBe("parent");
 expect(identity.session).toBe("advisor-session");
 expect(manager.getSessionId()).not.toBe("advisor-session");
});
