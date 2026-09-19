import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { isToolCallEventType } from "@earendil-works/pi-coding-agent";

// Environment vars – fall back to defaults mirroring reasoning-core.
const S2_URL = process.env.S2_URL ?? "http://127.0.0.1:8765";
const S2_TIMEOUT_MS = Number(process.env.S2_TIMEOUT ?? "30000");
const FAIL_CLOSED = process.env.S2_FAIL_CLOSED === "1";

/** Call the reasoning‑core sidecar /score endpoint. Returns a verdict. */
async function callSidecar(
  filePath: string,
  beforeSrc: string,
  afterSrc: string,
  changeKind: "edit" | "write",
): Promise<{ blocked: boolean; reason?: string }> {
  const payload = {
    path: filePath,
    before_src: beforeSrc,
    after_src: afterSrc,
    change_kind: changeKind,
    host: "pi",
    // Pi injects session ID into the environment – use it for audit trace.
    session_id: process.env.PI_SESSION_ID ?? "unknown",
  };
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), S2_TIMEOUT_MS);
    const resp = await fetch(`${S2_URL}/score`, {
      method: "POST",
      body: JSON.stringify(payload),
      headers: { "content-type": "application/json" },
      signal: controller.signal,
    });
    clearTimeout(timeout);
    if (!resp.ok) {
      // 415 means language unsupported – treat as allowed with a note.
      if (resp.status === 415) {
        return { blocked: false };
      }
      // Any other error is unexpected – fail‑closed if configured.
      if (FAIL_CLOSED) return { blocked: true, reason: `sidecar error ${resp.status}` };
      return { blocked: false };
    }
    const report = await resp.json();
    const blocked = report.decision === "block" || report.regression_detected === true;
    const reason = blocked ? report.message : undefined;
    return { blocked, reason };
  } catch (e) {
    // Network error / timeout – obey fail‑closed setting.
    if (FAIL_CLOSED) return { blocked: true, reason: "sidecar unreachable (fail‑closed)" };
    // Warn the user but allow the edit.
    console.warn("[reasoning‑core] sidecar unreachable:", e);
    return { blocked: false };
  }
}

export default function (pi: ExtensionAPI) {
  // L1 – bash guard (mirrors pre_bash_guard).
  pi.on("tool_call", async (event, _ctx) => {
    if (isToolCallEventType("bash", event)) {
      // Simple pattern: block any bash that attempts to write guard files.
      const cmd = event.input.command?.toString() ?? "";
      if (/\b(?:rm|>\s|tee)\b/.test(cmd) && /(?:\.pi|\.vibe|\.claude|\.gemini|\.codex|\.copilot|\.kimi)/.test(cmd)) {
        return { block: true, reason: "dangerous bash operation on guard files" };
      }
    }
    return undefined;
  });

  // L2/L3 – edit/write guard via sidecar.
  pi.on("tool_call", async (event, _ctx) => {
    if (isToolCallEventType("write", event) || isToolCallEventType("edit", event)) {
      const { path, before, after, kind } = event.input as any; // tool schema guarantees these fields
      // Normalize to absolute path (pi cwd); allows sidecar to attribute session.
      const cwd = process.cwd();
      const absPath = require("node:path").resolve(cwd, path);
      const verdict = await callSidecar(absPath, before ?? "", after ?? "", kind);
      if (verdict.block) {
        return { block: true, reason: verdict.reason ?? "blocked by reasoning-core" };
      }
    }
    return undefined;
  });

  // L7 – pre‑compact snapshot (optional – just a placeholder for now).
  pi.on("session_before_compact", async () => {
    // No custom logic needed – sidecar already handles compaction thresholds.
    return undefined;
  });

  // L8 – capture manifest on session start (mirrors session_start_manifest).
  pi.on("session_start", async (event, ctx) => {
    // Store host label for later audit rows – already sent via sidecar.
    // Could also emit a custom message for debugging.
    ctx.ui.notify(`reasoning‑core active (host=${event.reason ?? "pi"})`, "info");
  });

  // L10 – post‑turn diff audit (optional, can be expanded later).
  pi.on("agent_settled", async () => {
    // No-op – can invoke a custom diff validator if desired.
  });
}
