import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { realpathSync, readFileSync, lstatSync } from "node:fs";
import { dirname, basename, join, isAbsolute } from "node:path";

// Installer replaces these literals with the delivered interpreter and package root.
const PYTHON = "__RC_PYTHON__";
const ROOT = "__RC_ROOT__";
const BOOTSTRAP = "import sys; sys.path.insert(0,sys.argv[1]); from src.hooks.omp_bridge import main; raise SystemExit(main())";
const MAX_BYTES = 32 * 1024 * 1024;
const sha = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");

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
  const key = (ctx: any, call: string) => canonical({ ...identity(ctx), call });
  return {
    async prepare(event: any, ctx: any) {
      const denied = { allowed: false, finalInputSha256: event.batch?.finalInputSha256 ?? "" };
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
        if (!reply || reply.decision !== "allowed" || typeof reply.reason !== "string" || !reply.reason.trim()
          || typeof reply.decision_id !== "string" || !reply.decision_id
          || Object.entries(expected).some(([name, value]) => reply[name] !== value)
          || Object.keys(reply).sort().join(",") !==
            "batch_sha256,candidate_sha256,decision,decision_id,final_input_sha256,reason")
          return denied;
        pending.set(id, { batch, approval: reply });
        return { allowed: true, finalInputSha256: batch.final_input_sha256 };
      } catch { return denied; }
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
      const reply = await bridge({ event, ...identity(ctx) });
      if (reply?.decision !== "allowed") throw Error("reasoning-core session audit unavailable");
    },
  };
}

export default function reasoningCoreGate(api: any, bridge: (event: any) => Promise<any> = runBridge) {
  const gate = createGate(bridge);
  api.on("native_edit_prepare", (event: any, ctx: any) => gate.prepare(event, ctx));
  api.on("native_edit_result", (event: any, ctx: any) => gate.result(event, ctx));
  api.on("session_start", (_event: any, ctx: any) => gate.lifecycle("session_start", ctx));
  api.on("session_shutdown", (_event: any, ctx: any) => gate.lifecycle("session_stop", ctx));
  const restriction = "reasoning-core: this route has no qualified prepared-mutation boundary";
  api.on("tool_call", (event: any) => {
    if (!["edit", "read", "grep", "glob", "find"].includes(event.toolName))
      return { block: true, reason: restriction };
    if (event.toolName === "edit" && api.nativeEditProtocol !== 1)
      return { block: true, reason: "reasoning-core requires the qualified native precommit build" };
  });
  api.on("before_subagent_spawn", () => ({ block: true, reason: restriction }));
  const refusedExecution = () => ({ result: { output: restriction, exitCode: 1, cancelled: false,
    truncated: false, totalLines: 1, totalBytes: Buffer.byteLength(restriction),
    outputLines: 1, outputBytes: Buffer.byteLength(restriction), displayOutputs: [] } });
  api.on("user_bash", refusedExecution);
  api.on("user_python", refusedExecution);
}
