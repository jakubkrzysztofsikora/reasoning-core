import { expect, test } from "bun:test";
import { mkdtempSync, writeFileSync, readFileSync, rmSync, realpathSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
const upstream = process.env.RC_OMP_SOURCE;
if (!upstream) throw Error("RC_OMP_SOURCE must name the pinned patched source tree");
const compat = JSON.parse(readFileSync(join(process.cwd(), "src/data/templates/omp.compatibility.json"), "utf8"));
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
const { createGate, runBridge } = await import("../integrations/omp/reasoning_core_gate");
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
  const oldClosed = process.env.S2_FAIL_CLOSED; process.env.S2_FAIL_CLOSED = "1";
  const output = await new EditTool(session, "apply_patch").execute("bridge-call", {
    input: "*** Begin Patch\n*** Update File: hello.py\n@@\n-x = 1\n+" + next + "\n*** End Patch",
  });
  if (oldOracle === undefined) delete process.env.RC_ORACLE_BLOCK; else process.env.RC_ORACLE_BLOCK = oldOracle;
  if (oldMode === undefined) delete process.env.RC_MODE; else process.env.RC_MODE = oldMode;
  if (oldClosed === undefined) delete process.env.S2_FAIL_CLOSED; else process.env.S2_FAIL_CLOSED = oldClosed;
  expect(output.isError === true).toBe(mode === "deny");
  if (mode === "deny") expect(JSON.stringify(output.content)).toMatch(/rc explain [a-z0-9]{12}/);
  expect(readFileSync(file, "utf8")).toBe(mode === "allow" ? "x = 2\n" : "x = 1\n");
 } finally {
  if (audit === undefined) delete process.env.RC_AUDIT_ROOT; else process.env.RC_AUDIT_ROOT = audit;
  rmSync(cwd, { recursive: true, force: true });
 }
}, 45000);
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

test("guarded host serializes concurrent sessions in the same project", async () => {
 const cwd = mkdtempSync(join(tmpdir(), "rc serial "));
 let release!: () => void; let started!: () => void;
 const pending = new Promise<void>(resolve => { release = resolve; });
 const ready = new Promise<void>(resolve => { started = resolve; });
 const calls: string[] = [];
 try {
  writeFileSync(join(cwd, "a.py"), "x = 1\n");
  writeFileSync(join(cwd, "b.py"), "x = 1\n");
  const make = () => ({ cwd, hasUI: false, enableLsp: false, settings: Settings.isolated({}),
   getSessionFile: () => null, getSessionSpawns: () => "*", getArtifactsDir: () => null,
   getSessionId: () => crypto.randomUUID(), getPlanModeState: () => undefined,
   hasNativeEditGuard: () => true,
   prepareNativeEdit: async (event: any) => {
    calls.push(event.toolCallId);
    if (event.toolCallId === "first") { started(); await pending; }
    return { allowed: true, finalInputSha256: event.batch.finalInputSha256 };
   }, resultNativeEdit: async () => {},
  });
  const input = (file: string) => ({ input: "*** Begin Patch\n*** Update File: " + file + "\n@@\n-x = 1\n+x = 2\n*** End Patch" });
  const first = new EditTool(make(), "apply_patch").execute("first", input("a.py"));
  await ready;
  const second = new EditTool(make(), "apply_patch").execute("second", input("b.py"));
  await new Promise(resolve => setTimeout(resolve, 30));
  const beforeRelease = [...calls];
  release();
  const outputs = await Promise.all([first, second]);
  expect(beforeRelease).toEqual(["first"]);
  expect(outputs.every(result => !result.isError)).toBe(true);
 } finally { release(); rmSync(cwd, { recursive: true, force: true }); }
});
test("guarded host refuses internal URL resolution and disables automatic model repair", async () => {
 const cwd = mkdtempSync(join(tmpdir(), "rc routes "));
 let repairCalls = 0; let approvals = 0;
 try {
  writeFileSync(join(cwd, "hello.py"), "x = 1\n");
  const session = { cwd, hasUI: false, enableLsp: false, settings: Settings.isolated({ "edit.autoRepair.enabled": true }),
   getSessionFile: () => null, getSessionSpawns: () => "*", getArtifactsDir: () => null,
   getSessionId: () => "session", getPlanModeState: () => undefined,
   hasNativeEditGuard: () => true,
   modelRegistry: { getAvailable: () => { repairCalls++; throw Error("unqualified provider"); } },
   prepareNativeEdit: async (event: any) => { approvals++; return { allowed: true, finalInputSha256: event.batch.finalInputSha256 }; },
   resultNativeEdit: async () => {},
  };
  const tool = new EditTool(session, "apply_patch");
  const url = await tool.execute("url", { input: "*** Begin Patch\n*** Add File: artifact://escaped.py\n+x = 1\n*** End Patch" });
  expect(url.isError).toBe(true);
  expect(approvals).toBe(0);
  const invalid = await tool.execute("repair", { input: "*** Begin Patch\n*** Update File: hello.py\n@@\n-x = 1\n+def broken(:\n*** End Patch" });
  expect(invalid.isError).not.toBe(true);
  expect(repairCalls).toBe(0);
 } finally { rmSync(cwd, { recursive: true, force: true }); }
});

test("real SDK retains native gate in isolated child and restricts automatic parent merges", async () => {
 const { createAgentSession } = await import(join(upstream, "packages/coding-agent/src/sdk.ts"));
 const { ModelRegistry } = await import(join(upstream, "packages/coding-agent/src/config/model-registry.ts"));
 const { createInMemoryAuthStorage } = await import(join(upstream, "packages/coding-agent/test/helpers/agent-session-setup.ts"));
 const { default: adapter } = await import("../integrations/omp/reasoning_core_gate");
 const cwd = mkdtempSync(join(tmpdir(), "rc SDK parent "));
 const childCwd = mkdtempSync(join(tmpdir(), "rc SDK isolated "));
 const storage = createInMemoryAuthStorage();
 const registry = new ModelRegistry(storage);
 const captured: any[] = [];
 const model = { id: "no-provider-call", name: "qualification", api: "openai-completions", provider: "qualification",
   baseUrl: "http://127.0.0.1:1", identity: { class: "unknown" }, reasoning: false, input: ["text"],
   cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 8192, maxTokens: 1024 };
 const factory = (api: any) => {
  adapter(api, async (wire: any) => {
   captured.push(wire);
   if (wire.event !== "prepare") return { decision: "allowed" };
   const { bindings } = await import("../integrations/omp/reasoning_core_gate");
   return { decision: "blocked", reason: "deterministic fixture", decision_id: "child-denial", ...bindings(wire.batch) };
  });
 };
 const options = (root: string) => ({ cwd: root, agentDir: join(root, "agent"), authStorage: storage,
  modelRegistry: registry, model, settings: Settings.isolated({ "tools.approvalMode": "yolo", "edit.mode": "apply_patch" }),
  sessionManager: SessionManager.inMemory(root), disableExtensionDiscovery: true,
  skills: [], contextFiles: [], promptTemplates: [], slashCommands: [], enableMCP: false,
  enableLsp: false, skipPythonPreflight: true, rules: [], preloadedCustomToolPaths: [],
  toolNames: ["read", "edit", "task"] });
 let parent: any; let child: any;
 try {
  writeFileSync(join(cwd, "hello.py"), "x = 1\n"); writeFileSync(join(childCwd, "hello.py"), "x = 1\n");
  ({ session: parent } = await createAgentSession({ ...options(cwd), agentId: "parent", extensions: [factory] }));
  ({ session: child } = await createAgentSession({ ...options(childCwd), agentId: "child", parentAgentId: "parent",
    taskDepth: 1, restrictToolNames: true, preloadedPreparedExtensions: parent.preparedExtensions }));
  const edit = child.getToolByName("edit");
  expect(edit).toBeDefined();
  const result = await edit.execute("child-edit", { input: "*** Begin Patch\n*** Update File: hello.py\n@@\n-x = 1\n+x = 2\n*** End Patch" });
  expect(result.isError).toBe(true);
  const preparation = captured.find(wire => wire.event === "prepare");
  expect(preparation.batch.agent_id).toBe("child");
  expect(preparation.batch.parent_agent_id).toBe("parent");
  expect(preparation.batch.session_id).toBe(child.sessionId);
  expect(readFileSync(join(childCwd, "hello.py"), "utf8")).toBe("x = 1\n");
  expect(readFileSync(join(cwd, "hello.py"), "utf8")).toBe("x = 1\n");
  const task = parent.getToolByName("task");
  expect(task).toBeDefined();
  await expect(task.execute("merge", { agent: "task", task: "modify file", isolated: true })).rejects.toThrow("no qualified prepared-mutation boundary");
  expect(readFileSync(join(cwd, "hello.py"), "utf8")).toBe("x = 1\n");
 } finally {
  await child?.dispose(); await parent?.dispose(); storage.close();
  rmSync(cwd, { recursive: true, force: true }); rmSync(childCwd, { recursive: true, force: true });
 }
}, 45000);

test("actual OS failure publishes the landed prefix through runtime filesystem receipt", async () => {
 const { mkdirSync, chmodSync, existsSync } = await import("node:fs");
 const { bindings } = await import("../integrations/omp/reasoning_core_gate");
 const cwd = mkdtempSync(join(tmpdir(), "rc OS prefix "));
 const blockedDir = join(cwd, "blocked");
 const events: any[] = [];
 try {
  mkdirSync(blockedDir); writeFileSync(join(cwd, "first.py"), "x = 1\n");
  const gate = createGate(async wire => {
   events.push(wire);
   return wire.event === "prepare" ? { decision: "allowed", reason: "fixture", decision_id: "approved", ...bindings(wire.batch) }
     : { decision: "allowed" };
  });
  const ctx = { cwd, agent: { id: "agent" }, sessionManager: { getSessionId: () => "session" } };
  const session = { cwd, hasUI: false, enableLsp: true, settings: Settings.isolated({}),
   getSessionFile: () => null, getSessionSpawns: () => "*", getArtifactsDir: () => null,
   getSessionId: () => "session", getPlanModeState: () => undefined, hasNativeEditGuard: () => true,
   prepareNativeEdit: (event: any) => gate.prepare(event, ctx),
   resultNativeEdit: (event: any) => gate.result(event, ctx),
   bumpFileMutationVersion: (file: string) => { if (file === join(cwd, "first.py")) chmodSync(blockedDir, 0o555); },
  };
  const output = await new EditTool(session, "apply_patch").execute("prefix", {
   input: "*** Begin Patch\n*** Update File: first.py\n@@\n-x = 1\n+x = 2\n*** Add File: blocked/last.py\n+y = 3\n*** End Patch" });
  expect(output.isError).toBe(true);
  expect(readFileSync(join(cwd, "first.py"), "utf8")).toBe("x = 2\n");
  expect(existsSync(join(blockedDir, "last.py"))).toBe(false);
  const receipt = events.find(wire => wire.event === "result");
  expect(receipt.success).toBe(false);
  expect(receipt.persisted_digests[join(cwd, "first.py")]).toBe(new Bun.CryptoHasher("sha256").update("x = 2\n").digest("hex"));
  expect(receipt.persisted_digests[join(blockedDir, "last.py")]).toBeNull();
 } finally { chmodSync(blockedDir, 0o755); rmSync(cwd, { recursive: true, force: true }); }
});

test("normal CLI probe discovers installed guard without invoking a model", async () => {
 const { mkdirSync } = await import("node:fs");
 const { spawnSync } = await import("node:child_process");
 const cwd = mkdtempSync(join(tmpdir(), "rc CLI probe "));
 const profile = join(cwd, "profile"); mkdirSync(profile);
 try {
  const installed = spawnSync(join(process.cwd(), ".venv/bin/python"), ["-c",
    "import sys; from pathlib import Path; from src._init import InitResult; from src._omp_install import install_omp; p=Path(sys.argv[1]); m=p/'.reasoning-core/install.manifest'; m.parent.mkdir(); m.touch(); install_omp(p,m,{'RC_PYTHON':sys.executable,'RC_REPO':sys.argv[2]},InitResult(p),strict=True)", cwd, process.cwd()], { encoding: "utf8" });
  expect(installed.status).toBe(0);
  const probeEnv = { ...process.env };
  delete probeEnv.RC_ALLOW_GUARD_EDIT;
  delete probeEnv.RC_LANG_OVERRIDE;
  const probe = spawnSync(process.execPath, [join(upstream, "packages/coding-agent/src/cli.ts"), "--rc-native-probe"], {
    cwd, input: JSON.stringify({ project_dir: cwd, qualify: true }), encoding: "utf8", timeout: 40000,
    env: { ...probeEnv, PI_CODING_AGENT_DIR: profile, OMP_PROFILE: "qualification" },
  });
  expect(probe.status).toBe(0);
  const result = JSON.parse(probe.stdout);
  expect(result.loaded).toBe(true);
  expect(result.native_protocol).toBe(1);
  expect(result.version).toBe(compat.upstream_version);
  expect(result.model_invoked).toBe(false);
  expect(result.adapter_path).toBe(join(realpathSync(cwd), ".omp/extensions/reasoning_core_gate.ts"));
  expect(result.native_sha256).toMatch(/^[0-9a-f]{64}$/);
  expect(result.evidence.allowed_write).toBe(true);
  expect(result.evidence.denied_unchanged).toBe(true);
  expect(result.evidence.child_guard_retained).toBe(true);
  expect(result.evidence.restrictions_verified).toBe(true);
 } finally { rmSync(cwd, { recursive: true, force: true }); }
}, 45000);
