import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { existsSync, realpathSync, readFileSync, lstatSync } from "node:fs";
import { dirname, basename, join, isAbsolute } from "node:path";

// Project-scoped installs substitute these literals at delivery time.
// Marketplace/plugin installs ship them unsubstituted and resolve the
// runtime from <project>/.reasoning-core/omp-runtime.json instead.
const PYTHON = "__RC_PYTHON__";
const ROOT = "__RC_ROOT__";
const BOOTSTRAP = "import sys; sys.path.insert(0,sys.argv[1]); from src.hooks.omp_bridge import main; raise SystemExit(main())";
const MAX_BYTES = 32 * 1024 * 1024;
const sha = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");

interface Runtime {
  python: string;
  root: string;
  active: boolean;
}

const strField = (value: unknown, key: string): string => {
  if (value && typeof value === "object" && key in value) {
    const field = (value as Record<string, unknown>)[key];
    if (typeof field === "string") return field;
  }
  return "";
};

const objField = (value: unknown, key: string): unknown => {
  if (value && typeof value === "object" && key in value) return (value as Record<string, unknown>)[key];
  return undefined;
};

export function runtimeConfig(cwd: string): Runtime {
  if (!PYTHON.startsWith("__") && !ROOT.startsWith("__"))
    return { python: PYTHON, root: ROOT, active: true };
  if (!isAbsolute(cwd)) throw Error("rc runtime unresolved");
  const rc = join(cwd, ".reasoning-core");
  if (!existsSync(join(rc, "omp-runtime.json")) && !existsSync(join(rc, "omp-owned.json")))
    return { python: "", root: "", active: false };
  try {
    const config: unknown = JSON.parse(readFileSync(join(rc, "omp-runtime.json"), "utf8"));
    const python = strField(config, "python");
    const root = strField(config, "repo_root");
    if (python && root && isAbsolute(python) && isAbsolute(root))
      return { python, root, active: true };
  } catch {}
  throw Error("rc runtime unresolved");
}

function canonical(value: any): string {
  const sorted = (item: any): any => Array.isArray(item) ? item.map(sorted)
    : item && typeof item === "object"
      ? Object.fromEntries(Object.keys(item).sort().map(key => [key, sorted(item[key])])) : item;
  return JSON.stringify(sorted(value)).replace(/[\u007f-\uffff]/g,
    char => "\\u" + char.charCodeAt(0).toString(16).padStart(4, "0"));
}

function resolved(path: string): string {
  if (!isAbsolute(path)) throw Error("absolute candidate path required");
  try { lstatSync(path); return realpathSync(path); }
  catch (error: any) {
    if (error.code !== "ENOENT" || dirname(path) === path) throw error;
    return join(resolved(dirname(path)), basename(path));
  }
}

export function bindings(batch: any) {
  return {
    final_input_sha256: batch.final_input_sha256,
    candidate_sha256: sha(canonical(batch.candidates)),
    batch_sha256: sha(canonical({ ...batch, resolved_project: resolved(batch.project_dir),
      resolved_paths: batch.candidates.map((candidate: any) => resolved(candidate.path)) })),
  };
}

export async function runBridge(event: any, options: { argv?: string[]; timeoutMs?: number } = {}) {
  const wire = JSON.stringify(event);
  if (Buffer.byteLength(wire) > MAX_BYTES) throw Error("bridge input limit");
  const argv = options.argv ?? [PYTHON, "-I", "-c", BOOTSTRAP, ROOT];
  if (!argv.length || !argv.every(item => typeof item === "string" && item.length)) throw Error("invalid bridge argv");
  return await new Promise<any>((resolve, reject) => {
    const child = spawn(argv[0], argv.slice(1), { stdio: ["pipe", "pipe", "pipe"], shell: false });
    const stdout: Buffer[] = []; let bytes = 0; let settled = false;
    const finish = (error?: Error, value?: any) => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      if (error) { child.kill("SIGKILL"); reject(error); } else resolve(value);
    };
    const timer = setTimeout(() => finish(Error("bridge timeout")), options.timeoutMs ?? 35000);
    child.on("error", () => finish(Error("bridge unavailable")));
    child.stdin.on("error", () => finish(Error("bridge stdin failed")));
    child.stdout.on("data", chunk => {
      bytes += chunk.length;
      if (bytes > MAX_BYTES) return finish(Error("bridge output limit"));
      stdout.push(Buffer.from(chunk));
    });
    // Bound diagnostics without forwarding credentials or exception text to the host.
    child.stderr.on("data", chunk => {
      bytes += chunk.length;
      if (bytes > MAX_BYTES) finish(Error("bridge diagnostics limit"));
    });
    child.on("close", code => {
      if (code !== 0) return finish(Error("bridge process failed"));
      try { finish(undefined, JSON.parse(Buffer.concat(stdout).toString('utf8'))); }
      catch { finish(Error("invalid bridge output")); }
    });
    child.stdin.end(wire);
  });
}

function identity(ctx: any) {
  const value = { project_dir: ctx.cwd, session_id: ctx.sessionManager.getSessionId(),
    agent_id: ctx.agent.id, parent_agent_id: ctx.agent.parentId ?? null };
  for (const key of ["project_dir", "session_id", "agent_id"]) {
    if (typeof value[key as keyof typeof value] !== "string" || !value[key as keyof typeof value])
      throw Error("missing host identity");
  }
  return value;
}

export function createGate(bridge: (event: any) => Promise<any> = runBridge) {
  const pending = new Map<string, { batch: any; approval: any }>();
  const writePending = new Map<string, { batch: any; approval: any }>();
  const key = (ctx: any, call: string) => canonical({ ...identity(ctx), call });
  const clean = (reason: string) => reason.replace(/[\x00-\x1f\x7f]/g, " ").slice(0, 512);
  return {
    async prepare(event: any, ctx: any) {
      const denied = { allowed: false, finalInputSha256: event.batch?.finalInputSha256 ?? "", reason: "invalid_bridge_response" };
      try {
        const id = key(ctx, event.toolCallId);
        if (pending.has(id)) throw Error("duplicate prepared call");
        const batch = { protocol_version: 1, ...identity(ctx), tool_call_id: event.toolCallId,
          final_input_sha256: event.batch.finalInputSha256,
          candidates: event.batch.candidates.map((item: any) => ({
            path: item.path, operation: item.operation, before_src: item.beforeSrc,
            after_src: item.afterSrc, expected_before_sha256: item.expectedBeforeSha256,
          })) };
        const expected = bindings(batch);
        const reply = await bridge({ event: "prepare", batch });
        if (!reply || !["allowed", "blocked"].includes(reply.decision) || typeof reply.reason !== "string" || !reply.reason.trim()
          || typeof reply.decision_id !== "string" || !reply.decision_id
          || Object.entries(expected).some(([name, value]) => reply[name] !== value)
          || Object.keys(reply).sort().join(",") !==
            "batch_sha256,candidate_sha256,decision,decision_id,final_input_sha256,reason")
          return denied;
        if (reply.decision === "blocked") return { ...denied,
          reason: reply.reason.replace(/[\x00-\x1f\x7f]/g, " ").slice(0, 512),
          decisionId: /^[a-zA-Z0-9_-]{1,128}$/.test(reply.decision_id) ? reply.decision_id : undefined };
        pending.set(id, { batch, approval: reply });
        return { allowed: true, finalInputSha256: batch.final_input_sha256 };
      } catch (error: any) { return { ...denied, reason: error?.message === "bridge timeout"
        ? "bridge_timeout" : error?.message === "invalid bridge output"
          ? "invalid_bridge_response" : "bridge_unavailable" }; }
    },
    async result(event: any, ctx: any) {
      const id = key(ctx, event.toolCallId);
      const prepared = pending.get(id);
      if (!prepared) return;
      pending.delete(id);
      const persisted_digests = Object.fromEntries(prepared.batch.candidates.map((item: any) => {
        try { return [item.path, sha(readFileSync(item.path))]; }
        catch (error: any) { if (error.code === "ENOENT") return [item.path, null]; throw error; }
      }));
      const reply = await bridge({ event: "result", ...prepared, success: event.success,
        error: event.error, persisted_digests });
      if (reply?.decision !== "allowed") throw Error("reasoning-core receipt unavailable");
    },
    async lifecycle(event: string, ctx: any) {
      try {
        const reply = await bridge({ event, ...identity(ctx) });
        if (reply?.decision === "allowed" && typeof reply.reason === "string"
          && typeof reply.decision_id === "string" && reply.decision_id) return;
        if (event === "session_stop" && reply?.decision === "blocked"
          && typeof reply.reason === "string" && reply.reason.trim()
          && typeof reply.decision_id === "string" && reply.decision_id)
          return { decision: "block", reason: clean(reply.reason) };
      } catch {}
      if (event === "session_stop")
        return { decision: "block", reason: "reasoning-core session audit unavailable" };
      throw Error("reasoning-core session audit unavailable");
    },
    async bash(event: any, ctx: any) {
      const input = objField(event, "input");
      const command = strField(input, "command");
      if (!command.trim()) return { block: true, reason: "reasoning-core: missing bash command" };
      const cwd = strField(input, "cwd");
      const reply = await bridge({ event: "bash", ...identity(ctx), command,
        cwd: cwd ? (isAbsolute(cwd) ? cwd : join(ctx.cwd, cwd)) : null });
      if (reply && reply.decision === "allowed" && typeof reply.reason === "string"
        && typeof reply.decision_id === "string" && reply.decision_id) return;
      if (reply && reply.decision === "blocked" && typeof reply.reason === "string" && reply.reason.trim()
        && typeof reply.decision_id === "string" && reply.decision_id)
        return { block: true, reason: clean(reply.reason) };
      throw Error("invalid bridge output");
    },
    async writePrepare(event: any, ctx: any) {
      const input = objField(event, "input");
      const path = strField(input, "path");
      const content = strField(input, "content");
      if (!path.trim()) return { block: true, reason: "reasoning-core: missing write path" };
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(path) || /^\[.*\]\s*$/s.test(path))
        return { block: true, reason: "reasoning-core: non-file write targets are refused" };
      const absolute = isAbsolute(path) ? path : join(ctx.cwd, path);
      let before: string | null = null;
      try { before = readFileSync(absolute, "utf8"); }
      catch (error: any) { if (error.code !== "ENOENT") throw error; }
      const batch = { protocol_version: 1, ...identity(ctx), tool_call_id: event.toolCallId,
        final_input_sha256: sha(canonical({ path: absolute, content })),
        candidates: [{ path: resolved(absolute), operation: "write", before_src: before,
          after_src: content,
          expected_before_sha256: before === null ? null : sha(before) }] };
      const expected = bindings(batch);
      const reply = await bridge({ event: "prepare", batch });
      if (!reply || !["allowed", "blocked"].includes(reply.decision) || typeof reply.reason !== "string" || !reply.reason.trim()
        || typeof reply.decision_id !== "string" || !reply.decision_id
        || Object.entries(expected).some(([name, value]) => reply[name] !== value)
        || Object.keys(reply).sort().join(",") !==
          "batch_sha256,candidate_sha256,decision,decision_id,final_input_sha256,reason")
        return { block: true, reason: "reasoning-core: invalid bridge response" };
      if (reply.decision === "blocked") return { block: true, reason: clean(reply.reason) };
      writePending.set(key(ctx, event.toolCallId), { batch, approval: reply });
    },
    async writeResult(event: any, ctx: any) {
      const id = key(ctx, event.toolCallId);
      const prepared = writePending.get(id);
      if (!prepared) return;
      writePending.delete(id);
      const persisted_digests = Object.fromEntries(prepared.batch.candidates.map((item: any) => {
        try { return [item.path, sha(readFileSync(item.path))]; }
        catch (error: any) { if (error.code === "ENOENT") return [item.path, null]; throw error; }
      }));
      const reply = await bridge({ event: "result", ...prepared, success: !event.isError,
        error: null, persisted_digests });
      if (reply?.decision !== "allowed") throw Error("reasoning-core receipt unavailable");
    },
    async subagent(event: any, ctx: any) {
      const name = strField(event, "agent");
      if (!name.trim()) return { block: true, reason: "reasoning-core: missing subagent name" };
      try {
        const reply = await bridge({ event: "subagent", ...identity(ctx), name });
        if (reply && reply.decision === "allowed"
          && typeof reply.decision_id === "string" && reply.decision_id) return;
        if (reply && reply.decision === "blocked" && typeof reply.reason === "string" && reply.reason.trim()
          && typeof reply.decision_id === "string" && reply.decision_id)
          return { block: true, reason: clean(reply.reason) };
      } catch {}
      return { block: true, reason: "reasoning-core: subagent audit unavailable" };
    },
    async advisor(event: any, ctx: any) {
      try {
        const kind = strField(event, "type") === "advisor_yielded" ? "yielded" : "cost_changed";
        const cost = typeof event.cost === "number" && Number.isFinite(event.cost) ? event.cost : null;
        await bridge({ event: "advisor", ...identity(ctx), kind, cost });
      } catch {}
    },
  };
}

type Mode = "active" | "dormant" | "failclosed";

// Protocol body above mirrors the qualified project-scoped gate
// (integrations/omp/reasoning_core_gate.ts) verbatim; only the plugin
// boundary below adds runtime resolution and dormancy.
export default function reasoningCoreGate(api: any, bridge: (event: any, options?: { argv?: string[]; timeoutMs?: number }) => Promise<any> = runBridge) {
  const gate = createGate((event: any) => {
    const dir = strField(event.batch, "project_dir") || strField(event, "project_dir");
    if (!dir) throw Error("rc runtime unresolved");
    const runtime = runtimeConfig(dir);
    if (!runtime.active) throw Error("rc runtime unresolved");
    return bridge(event, { argv: [runtime.python, "-I", "-c", BOOTSTRAP, runtime.root] });
  });
  const mode = (ctx: unknown): Mode => {
    const cwd = strField(ctx, "cwd");
    try { return runtimeConfig(cwd).active ? "active" : "dormant"; }
    catch { return "failclosed"; }
  };
  api.logger?.debug?.("reasoning-core gate loaded", { mode: mode({ cwd: process.cwd() }) });
  const unresolved = "reasoning-core runtime unresolved (run `rc init --host omp`)";
  api.on("native_edit_prepare", (event: unknown, ctx: unknown) => {
    const state = mode(ctx);
    const finalInput = strField(objField(event, "batch"), "finalInputSha256");
    if (state === "dormant") return { allowed: true, finalInputSha256: finalInput };
    if (state === "failclosed") return { allowed: false, finalInputSha256: finalInput, reason: unresolved };
    return gate.prepare(event, ctx);
  });
  api.on("native_edit_result", (event: unknown, ctx: unknown) => {
    if (mode(ctx) !== "active") return;
    return gate.result(event, ctx);
  });
  api.on("session_start", (_event: unknown, ctx: unknown) => {
    if (mode(ctx) !== "active") return;
    return gate.lifecycle("session_start", ctx);
  });
  api.on("session_stop", (_event: unknown, ctx: unknown) => {
    const state = mode(ctx);
    if (state === "dormant") return;
    if (state === "failclosed") return { decision: "block", reason: unresolved };
    return gate.lifecycle("session_stop", ctx);
  });
  api.on("tool_call", (event: unknown, ctx: unknown) => {
    const toolName = strField(event, "toolName");
    if (!["bash", "write", "edit"].includes(toolName)) return;
    const state = mode(ctx);
    if (state === "dormant") return;
    const path = strField(objField(event, "input"), "path");
    if (toolName === "write" && /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(path) && !/^file:/i.test(path)) return;
    if (state === "failclosed") return { block: true, reason: unresolved };
    if (toolName === "bash") return gate.bash(event, ctx);
    if (toolName === "write") return gate.writePrepare(event, ctx);
    if (api.nativeEditProtocol !== 1)
      return { block: true, reason: "reasoning-core requires the qualified native precommit build" };
  });
  api.on("tool_result", (event: unknown, ctx: unknown) => {
    if (mode(ctx) !== "active") return;
    if (strField(event, "toolName") === "write") return gate.writeResult(event, ctx);
  });
  api.on("before_subagent_spawn", (event: unknown, ctx: unknown) => {
    if (mode(ctx) === "dormant") return;
    return gate.subagent(event, ctx);
  });
  api.on("advisor_cost_changed", (event: unknown, ctx: unknown) => {
    if (mode(ctx) !== "active") return;
    return gate.advisor(event, ctx);
  });
  api.on("advisor_yielded", (event: unknown, ctx: unknown) => {
    if (mode(ctx) !== "active") return;
    return gate.advisor(event, ctx);
  });
}
