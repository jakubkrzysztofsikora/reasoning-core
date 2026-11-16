import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { existsSync, realpathSync, readFileSync, lstatSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, basename, join, isAbsolute, sep } from "node:path";

// Project-scoped installs substitute these literals at delivery time.
// Marketplace/plugin installs ship them unsubstituted and resolve the
// runtime from <project>/.reasoning-core/omp-runtime.json instead.
const PYTHON = "__RC_PYTHON__";
const ROOT = "__RC_ROOT__";
const BOOTSTRAP = "import sys; sys.path.insert(0,sys.argv[1]); from src.hooks.omp_bridge import main; raise SystemExit(main())";
const MAX_BYTES = 32 * 1024 * 1024;
// Mirrors src/omp_contract.py::_MAX_TEXT_BYTES; larger candidates are refused
// by the contract, so they are never sent.
const MAX_TEXT_BYTES = 4 * 1024 * 1024;
const sha = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");

interface Runtime {
  python: string;
  root: string;
  active: boolean;
  reason: string;
}

// Must stay well under the host's extensionHandlers.toolCallTimeoutMs (30s default);
// the host fails a tool call closed when a handler outruns that budget.
const BRIDGE_TIMEOUT_MS = 8000;

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
  const absent = (reason: string): Runtime => ({ python: "", root: "", active: false, reason });
  if (!PYTHON.startsWith("__") && !ROOT.startsWith("__"))
    return { python: PYTHON, root: ROOT, active: true, reason: "substituted" };
  if (typeof cwd !== "string" || !isAbsolute(cwd)) return absent("project_dir_unknown");
  const rc = join(cwd, ".reasoning-core");
  const runtimeFile = join(rc, "omp-runtime.json");
  if (!existsSync(runtimeFile) && !existsSync(join(rc, "omp-owned.json"))) return absent("runtime_absent");
  try {
    const config: unknown = JSON.parse(readFileSync(runtimeFile, "utf8"));
    const python = strField(config, "python");
    const root = strField(config, "repo_root");
    if (python && root && isAbsolute(python) && isAbsolute(root))
      return { python, root, active: true, reason: "resolved" };
  } catch {}
  return absent("runtime_invalid");
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
    const timer = setTimeout(() => finish(Error("bridge timeout")), options.timeoutMs ?? BRIDGE_TIMEOUT_MS);
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

export interface SubagentDecision {
  block: boolean;
  reason?: string;
  infra?: boolean;
}

export function createGate(bridge: (event: any) => Promise<any> = runBridge) {
  const pending = new Map<string, { batch: any; approval: any }>();
  const writePending = new Map<string, { batch: any; approval: any }>();
  const key = (ctx: any, call: string) => canonical({ ...identity(ctx), call });
  const clean = (reason: string) => reason.replace(/[\x00-\x1f\x7f]/g, " ").slice(0, 512);
  return {
    async prepare(event: any, ctx: any) {
      const denied = { allowed: false, finalInputSha256: event.batch?.finalInputSha256 ?? "", batchSha256: event.batch?.batchSha256, reason: "invalid_bridge_response", infra: true };
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
        // Echo the native batch digest so the engine can prove the approval
        // binds this exact candidate set.
        return { allowed: true, finalInputSha256: batch.final_input_sha256, batchSha256: event.batch?.batchSha256 };
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
        return { decision: "block", reason: "reasoning-core session audit unavailable", infra: true };
      throw Error("reasoning-core session audit unavailable");
    },
    async bash(event: any, ctx: any) {
      const input = objField(event, "input");
      const command = strField(input, "command");
      if (!command.trim()) return { note: "this bash call carries no command, so reasoning-core does not govern it" };
      const cwd = strField(input, "cwd");
      const reply = await bridge({ event: "bash", ...identity(ctx), command,
        cwd: cwd ? (isAbsolute(cwd) ? cwd : join(ctx.cwd, cwd)) : null });
      if (reply && reply.decision === "allowed" && typeof reply.reason === "string"
        && typeof reply.decision_id === "string" && reply.decision_id) return;
      if (reply && reply.decision === "blocked" && typeof reply.reason === "string" && reply.reason.trim()
        && typeof reply.decision_id === "string" && reply.decision_id)
        return { block: true, reason: clean(reply.reason) };
      return { block: true, reason: "invalid_bridge_response", infra: true };
    },
    async writePrepare(event: any, ctx: any) {
      const input = objField(event, "input");
      const path = strField(input, "path");
      const content = strField(input, "content");
      if (!path.trim()) return { note: "this write call carries no path, so reasoning-core does not govern it" };
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(path) || /^\[.*\]\s*$/s.test(path))
        return { note: "this write targets a non-file destination, so reasoning-core does not govern it" };
      const absolute = isAbsolute(path) ? path : join(ctx.cwd, path);
      const project = resolved(ctx.cwd);
      const target = resolved(absolute);
      if (target !== project && !target.startsWith(project + sep))
        return { note: "this write targets a path outside the project root, so reasoning-core does not govern it" };
      let before: string | null = null;
      try { before = readFileSync(absolute, "utf8"); }
      catch (error: any) { if (error.code !== "ENOENT") throw error; }
      if (Buffer.byteLength(content) > MAX_TEXT_BYTES || (before !== null && Buffer.byteLength(before) > MAX_TEXT_BYTES))
        return { note: "this write exceeds the governed text limit, so reasoning-core does not govern it" };
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
        return { block: true, reason: "invalid_bridge_response", infra: true };
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
    async subagent(event: any, ctx: any): Promise<SubagentDecision> {
      const name = strField(event, "agent");
      if (!name.trim()) return { block: false };
      try {
        const reply = await bridge({ event: "subagent", ...identity(ctx), name });
        if (reply && reply.decision === "allowed"
          && typeof reply.decision_id === "string" && reply.decision_id) return { block: false };
        if (reply && reply.decision === "blocked" && typeof reply.reason === "string" && reply.reason.trim()
          && typeof reply.decision_id === "string" && reply.decision_id)
          return { block: true, reason: clean(reply.reason) };
      } catch {}
      return { block: true, reason: "reasoning-core: subagent audit unavailable", infra: true };
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
export type Mode = "off" | "advisory" | "enforce";
export type OnError = "open" | "closed";
export type SettingSource = "env" | "file" | "default" | "session";

export interface GateSettings {
  mode: Mode;
  onError: OnError;
}

export interface ResolvedSettings extends GateSettings {
  path: string;
  source: { mode: SettingSource; onError: SettingSource };
}

const MODES: Mode[] = ["off", "advisory", "enforce"];
const USAGE = "rc: (no args) status | mode off|advisory|enforce|enable|disable | on-error open|closed | RC_GATE <mode> | RC_GATE_ON_ERROR <open|closed> | env [RC_NAME [value]] | unset RC_NAME | reset | help - mode/on-error persist to the settings file; env/unset change any RC_* variable for this session only";

export const settingsPath = (): string =>
  process.env.RC_GATE_SETTINGS || join(process.env.HOME || ".", ".omp", "agent", "reasoning-core-gate.json");

const envMode = (): Mode | undefined => {
  const value = String(process.env.RC_GATE ?? "").trim().toLowerCase();
  return MODES.includes(value as Mode) ? value as Mode : undefined;
};

const envOnError = (): OnError | undefined => {
  const value = String(process.env.RC_GATE_ON_ERROR ?? "").trim().toLowerCase();
  return value === "open" || value === "closed" ? value : undefined;
};

function readSettingsFile(): Partial<GateSettings> {
  try {
    const raw = JSON.parse(readFileSync(settingsPath(), "utf8"));
    const file: Partial<GateSettings> = {};
    if (MODES.includes(raw?.mode)) file.mode = raw.mode;
    if (raw?.onError === "open" || raw?.onError === "closed") file.onError = raw.onError;
    return file;
  } catch { return {}; }
}

export function resolveSettings(): ResolvedSettings {
  const file = readSettingsFile();
  const fromEnv = envMode();
  const errorFromEnv = envOnError();
  return {
    mode: fromEnv ?? file.mode ?? "advisory",
    onError: errorFromEnv ?? file.onError ?? "open",
    path: settingsPath(),
    source: {
      mode: fromEnv ? "env" : file.mode ? "file" : "default",
      onError: errorFromEnv ? "env" : file.onError ? "file" : "default",
    },
  };
}

export function persistSettings(patch: Partial<GateSettings>): void {
  const next = { ...readSettingsFile(), ...patch };
  mkdirSync(dirname(settingsPath()), { recursive: true });
  writeFileSync(settingsPath(), `${JSON.stringify(next, null, 1)}\n`);
}

export function clearSettings(): void {
  mkdirSync(dirname(settingsPath()), { recursive: true });
  writeFileSync(settingsPath(), "{}\n");
}

// Protocol body above mirrors the qualified project-scoped gate
// (integrations/omp/reasoning_core_gate.ts) verbatim; only the plugin
// boundary below adds runtime resolution, mode negotiation and operator control.
export default function reasoningCoreGate(api: any, bridge: (event: any, options?: { argv?: string[]; timeoutMs?: number }) => Promise<any> = runBridge) {
  const state: ResolvedSettings = resolveSettings();
  api.logger?.debug?.("reasoning-core gate loaded", { mode: state.mode, onError: state.onError, source: state.source });
  const gate = createGate((event: any) => {
    const dir = strField(event.batch, "project_dir") || strField(event, "project_dir");
    const runtime = runtimeConfig(dir);
    if (!runtime.active) throw Error("rc runtime unresolved");
    return bridge(event, { argv: [runtime.python, "-I", "-c", BOOTSTRAP, runtime.root] });
  });
  let announced = "";
  let degraded = false;
  // Captured before any /rc write so `reset` restores what this session inherited.
  const inherited = new Map<string, string>(
    Object.entries(process.env).filter((entry): entry is [string, string] =>
      entry[0].startsWith("RC_") && typeof entry[1] === "string"));
  const sessionEnv = new Set<string>();
  const announce = (reason: string) => {
    if (announced === reason) return;
    announced = reason;
    api.logger?.warn?.(`reasoning-core gate not enforcing (${reason})`);
  };
  const active = () => state.mode !== "off";
  const enforcing = (_ctx: unknown): boolean => {
    // A degraded session must still block under on-error closed: degrading is
    // itself the fail-open answer, so it cannot outrank a fail-closed policy.
    // An unresolved runtime is an infrastructure failure, not a mode: the
    // routes report it as `runtime_unresolved` and on-error decides.
    return state.mode === "enforce" && !(degraded && failOpen());
  };
  const failOpen = () => state.onError === "open";
  const emitted = new Set<string>();
  const advisory = (reason: string, detail: string) => {
    const id = `${reason}:${detail}`;
    if (emitted.has(id)) return;
    emitted.add(id);
    return { additionalContext: `reasoning-core advisory (${reason}): ${detail}`.slice(0, 1024) };
  };
  const unreviewed = (route: string, ctx: unknown, reason: string, detail: string | null) => {
    api.logger?.warn?.(`reasoning-core ${route} unreviewed (${reason})${detail ? `: ${detail}` : ""}`);
    if (enforcing(ctx) && !failOpen()) return { block: true, reason: `reasoning-core: ${reason}` };
    return advisory(reason, "reasoning-core could not evaluate this call.");
  };
  // The worker reports its own failures as plain blocked decisions with no infra
  // flag, so every reason it can emit for a failure that carried no policy
  // verdict belongs here; anything absent stays a policy refusal in both modes.
  const infraDenial = new Set([
    "bridge_unavailable", "bridge_timeout", "bridge_process_failed",
    "invalid_bridge_output", "invalid_bridge_response", "invalid_event",
    "bridge_audit_unavailable", "guard_evaluation_failed", "bash_screen_error",
    "sidecar_unavailable", "sidecar_auth_failed", "invalid_score_report",
    "runtime_unresolved",
  ]);
  // prepare() mirrors the strict protocol and tags every reply infra:true, so
  // policy blocks there are told apart by reason text, not the flag.
  const isInfraReason = (reply: any) => infraDenial.has(String(reply?.reason));
  const isInfra = (reply: any) => reply?.infra === true || isInfraReason(reply);
  if (state.mode === "enforce") api.logger?.debug?.("reasoning-core gate enforcing", { onError: state.onError });
  else announce("rc_gate_advisory");

  const notify = (ctx: any, message: string, level: "info" | "warning" = "info") => {
    if (ctx?.ui?.notify) ctx.ui.notify(message, level);
    else api.logger?.info?.(message);
  };
  const status = () => {
    const names = [...sessionEnv].sort();
    const overrides = names.length
      ? ` (session env: ${names.slice(0, 6).join(", ")}${names.length > 6 ? `, +${names.length - 6}` : ""})`
      : inherited.size
        ? " (env overrides present: /rc persists for later sessions, env wins at load)"
        : "";
    return `reasoning-core gate: mode=${state.mode} [${state.source.mode}] on-error=${state.onError} [${state.source.onError}] settings=${state.path}${overrides}`;
  };
  // /rc is the operator lever, so it also writes the env var the gate reads:
  // child processes and nested sessions inherit the decision without a shell dance.
  const exportEnv = (name: string, value: string) => {
    process.env[name] = value;
    sessionEnv.add(name);
  };
  const restoreEnv = () => {
    for (const name of sessionEnv) {
      const prior = inherited.get(name);
      if (prior === undefined) delete process.env[name];
      else process.env[name] = prior;
    }
    sessionEnv.clear();
  };
  const RC_ENV = /^RC_[A-Z0-9_]+$/;
  const SECRET_ENV = /(TOKEN|SECRET|KEY|PASSWORD|PASSWD|CREDENTIAL)/;
  const shown = (name: string) => `${name}=${SECRET_ENV.test(name) ? "***" : process.env[name] ?? ""}`;
  const envNames = () => [...new Set([...inherited.keys(), ...Object.keys(process.env), ...sessionEnv])]
    .filter((name) => RC_ENV.test(name)).sort();
  const listEnv = () => {
    const names = envNames();
    if (!names.length) return "rc env: no RC_* variables in this session";
    return `rc env (* = set by /rc this session): ${names.map((name) => `${sessionEnv.has(name) ? "*" : ""}${shown(name)}`).join("  ")}`;
  };
  const reload = () => {
    const resolved = resolveSettings();
    state.mode = resolved.mode;
    state.onError = resolved.onError;
    state.source = resolved.source;
    degraded = false;
    emitted.clear();
    announced = "";
    if (state.mode !== "enforce") announce("rc_gate_advisory");
  };
  const setEnv = (ctx: any, rawName: string, value: string | undefined) => {
    const name = rawName.trim().toUpperCase();
    if (!RC_ENV.test(name)) return notify(ctx, `rc: "${rawName}" is not an RC_* variable name`, "warning");
    if (value === undefined) {
      if (process.env[name] === undefined) return notify(ctx, `rc: ${name} is not set`, "warning");
      return notify(ctx, `${shown(name)}${sessionEnv.has(name) ? " [session]" : " [env]"}`);
    }
    if (name === "RC_GATE") {
      const mode = applyMode(value.trim().toLowerCase());
      if (!mode) return notify(ctx, `unknown mode "${value}" - use off, advisory, enforce`, "warning");
      exportEnv(name, mode);
      return notify(ctx, status());
    }
    if (name === "RC_GATE_ON_ERROR") {
      const onError = applyOnError(value.trim().toLowerCase());
      if (!onError) return notify(ctx, `unknown on-error "${value}" - use open or closed`, "warning");
      exportEnv(name, onError);
      return notify(ctx, status());
    }
    exportEnv(name, value);
    return notify(ctx, `rc: ${shown(name)} [session]`);
  };
  const unsetEnv = (ctx: any, rawName: string) => {
    const name = rawName.trim().toUpperCase();
    if (!RC_ENV.test(name)) return notify(ctx, `rc: "${rawName}" is not an RC_* variable name`, "warning");
    if (process.env[name] === undefined && !sessionEnv.has(name)) return notify(ctx, `rc: ${name} is not set`, "warning");
    sessionEnv.add(name);
    const prior = inherited.get(name);
    if (prior === undefined) delete process.env[name];
    else process.env[name] = prior;
    if (name === "RC_GATE" || name === "RC_GATE_ON_ERROR") reload();
    return notify(ctx, `rc: ${name} reverted${prior === undefined ? " (unset)" : " to inherited value"}${sessionEnv.has(name) ? " [session]" : ""}`);
  };
  const applyMode = (raw: string): Mode | null => {
    const value = raw === "enable" || raw === "on" ? "enforce" : raw === "disable" || raw === "none" ? "off" : raw;
    if (!MODES.includes(value as Mode)) return null;
    state.mode = value as Mode;
    state.source = { ...state.source, mode: "session" };
    degraded = false;
    emitted.clear();
    announced = "";
    if (state.mode !== "enforce") announce("rc_gate_advisory");
    return state.mode;
  };
  const applyOnError = (raw: string): OnError | null => {
    if (raw !== "open" && raw !== "closed") return null;
    state.onError = raw;
    state.source = { ...state.source, onError: "session" };
    return raw;
  };
  const setMode = (ctx: any, raw: string) => {
    const value = applyMode(raw);
    if (!value) return notify(ctx, `unknown mode "${raw}" - use off, advisory, enforce, enable or disable`, "warning");
    persistSettings({ mode: value });
    exportEnv("RC_GATE", value);
    return notify(ctx, status());
  };
  const setOnError = (ctx: any, raw: string) => {
    const value = applyOnError(raw);
    if (!value) return notify(ctx, `unknown on-error "${raw}" - use open or closed`, "warning");
    persistSettings({ onError: value });
    exportEnv("RC_GATE_ON_ERROR", value);
    return notify(ctx, status());
  };
  const reset = (ctx: any) => {
    clearSettings();
    restoreEnv();
    reload();
    return notify(ctx, `settings cleared · ${status()}`);
  };

  api.registerCommand?.("rc", {
    description: "reasoning-core gate: show state, set mode (off|advisory|enforce) or on-error (open|closed)",
    getArgumentCompletions: (prefix: string) => {
      const options = ["status", "off", "advisory", "enforce", "enable", "disable",
        "on-error open", "on-error closed", "env", "env RC_GATE ", "unset ", "reset", "help"];
      const matches = options.filter((option) => option.startsWith(String(prefix ?? "").trim()));
      return matches.length ? matches.map((value) => ({ label: value, value })) : null;
    },
    handler: async (args: string, ctx: any) => {
      const raw = String(args ?? "").trim();
      if (!raw) return notify(ctx, status());
      const tokens = raw.split(/\s+/);
      const head = tokens[0].toLowerCase();
      if (head === "env" || head === "set") {
        if (tokens.length < 2) return notify(ctx, listEnv());
        const [rawName, inline] = tokens[1].includes("=") ? tokens[1].split(/=(.*)/s) : [tokens[1], undefined];
        const value = inline ?? (tokens.length > 2 ? tokens.slice(2).join(" ") : undefined);
        return setEnv(ctx, rawName, value);
      }
      if (head === "unset") {
        if (tokens.length < 2) return notify(ctx, "rc unset <RC_NAME>", "warning");
        return unsetEnv(ctx, tokens[1]);
      }
      let name = head;
      let value = (tokens[1] ?? "").toLowerCase();
      if (name.includes("=")) {
        const [inlineName, inlineValue] = name.split("=");
        name = inlineName;
        value = (inlineValue || value).toLowerCase();
      }
      if (name === "help") return notify(ctx, USAGE);
      if (name === "status") return notify(ctx, status());
      if (name === "reset") return reset(ctx);
      if (name === "mode") return setMode(ctx, value);
      if (name === "on-error" || name === "onerror" || name === "rc_gate_on_error") return setOnError(ctx, value);
      if (name === "rc_gate") return setMode(ctx, value);
      if (name === "open" || name === "closed") return setOnError(ctx, name);
      if (MODES.includes(name as Mode) || name === "enable" || name === "disable") return setMode(ctx, name);
      return notify(ctx, USAGE, "warning");
    },
  });

  api.on("native_edit_prepare", async (event: unknown, ctx: unknown) => {
    const finalInput = strField(objField(event, "batch"), "finalInputSha256");
    const batchDigest = strField(objField(event, "batch"), "batchSha256");
    if (!enforcing(ctx)) return { allowed: true, finalInputSha256: finalInput, batchSha256: batchDigest };
    let reply: any;
    try { reply = await gate.prepare(event, ctx); }
    catch (error: any) {
      api.logger?.warn?.(`reasoning-core edit unreviewed: ${String(error?.message ?? error)}`);
      return failOpen() ? { allowed: true, finalInputSha256: finalInput, batchSha256: batchDigest }
        : { allowed: false, finalInputSha256: finalInput, batchSha256: batchDigest, reason: "reasoning-core: bridge_unavailable" };
    }
    if (reply?.allowed) return reply;
    if (isInfraReason(reply)) {
      api.logger?.warn?.(`reasoning-core edit unreviewed (${reply.reason})`);
      return failOpen() ? { allowed: true, finalInputSha256: finalInput, batchSha256: batchDigest }
        : { allowed: false, finalInputSha256: finalInput, batchSha256: batchDigest, reason: `reasoning-core: ${reply.reason}` };
    }
    return reply;
  });
  api.on("native_edit_result", async (event: unknown, ctx: unknown) => {
    if (!enforcing(ctx)) return;
    try { await gate.result(event, ctx); }
    catch (error: any) { api.logger?.warn?.(`reasoning-core receipt unavailable: ${String(error?.message ?? error)}`); }
  });
  api.on("session_start", async (_event: unknown, ctx: unknown) => {
    if (!enforcing(ctx)) return;
    try { await gate.lifecycle("session_start", ctx); }
    catch (error: any) {
      degraded = true;
      announce("bridge_unavailable");
      api.logger?.warn?.(`reasoning-core session start unavailable: ${String(error?.message ?? error)}`);
    }
  });
  api.on("session_stop", async (_event: unknown, ctx: unknown) => {
    if (!enforcing(ctx)) return;
    const unavailable = () => failOpen() ? undefined
      : { decision: "block", reason: "reasoning-core session audit unavailable" };
    try {
      const refusal = await gate.lifecycle("session_stop", ctx);
      if (refusal && refusal.decision === "block" && !isInfra(refusal)) return refusal;
      if (isInfra(refusal)) {
        api.logger?.warn?.(`reasoning-core session audit unavailable: ${refusal.reason}`);
        return unavailable();
      }
    } catch (error: any) {
      api.logger?.warn?.(`reasoning-core session audit unavailable: ${String(error?.message ?? error)}`);
      return unavailable();
    }
  });
  api.on("tool_call", async (event: unknown, ctx: unknown) => {
    if (!active()) return;
    const toolName = strField(event, "toolName");
    if (toolName === "edit") {
      if (api.nativeEditProtocol === 1) return;
      return advisory("native_edit_unavailable", "this host build exposes no native edit boundary, so edits are not governed.");
    }
    if (toolName !== "bash" && toolName !== "write") return;
    const path = strField(objField(event, "input"), "path");
    if (toolName === "write" && /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(path) && !/^file:/i.test(path)) return;
    const runtime = runtimeConfig(strField(ctx, "cwd"));
    if (!runtime.active) return unreviewed(toolName, ctx, "runtime_unresolved", runtime.reason);
    let reply: any;
    try {
      reply = toolName === "bash" ? await gate.bash(event, ctx) : await gate.writePrepare(event, ctx);
    } catch (error: any) {
      return unreviewed(toolName, ctx, "bridge_unavailable", String(error?.message ?? error));
    }
    if (!reply) return;
    if (!reply.block) return reply.note ? advisory("not_governed", reply.note) : undefined;
    if (isInfra(reply)) return unreviewed(toolName, ctx, String(reply.reason), null);
    return enforcing(ctx) ? { block: true, reason: reply.reason } : advisory("policy_downgraded", `would have blocked: ${reply.reason}`);
  });
  api.on("tool_result", async (event: unknown, ctx: unknown) => {
    if (!enforcing(ctx)) return;
    if (strField(event, "toolName") !== "write") return;
    try { await gate.writeResult(event, ctx); }
    catch (error: any) { api.logger?.warn?.(`reasoning-core receipt unavailable: ${String(error?.message ?? error)}`); }
  });
  api.on("before_subagent_spawn", async (event: unknown, ctx: unknown) => {
    if (!enforcing(ctx)) return;
    let reply: SubagentDecision;
    try { reply = await gate.subagent(event, ctx); }
    catch (error: any) { return unreviewed("subagent", ctx, "bridge_unavailable", String(error?.message ?? error)); }
    if (!reply?.block) return;
    if (isInfra(reply)) return unreviewed("subagent", ctx, String(reply.reason), null);
    return { block: true, reason: reply.reason };
  });
  api.on("advisor_cost_changed", async (event: unknown, ctx: unknown) => {
    if (!enforcing(ctx)) return;
    await gate.advisor(event, ctx);
  });
  api.on("advisor_yielded", async (event: unknown, ctx: unknown) => {
    if (!enforcing(ctx)) return;
    await gate.advisor(event, ctx);
  });
}
