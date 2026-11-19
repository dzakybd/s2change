import type { AgentAction, AgentChart, AgentResponse, AgentTable, AgentToolCall } from "./types";
import type { PolygonCollection } from "./overlays";

export const LIVE_URL = "https://s2change.dzaky.ai.id";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export type ApiState =
  | { kind: "ready"; base: string; mock: false | string }
  | { kind: "disabled"; reason: "static-host" | "unreachable" };

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly retryAfterSec: number | null = null,
  ) {
    super(message);
  }
}

const MOCK_FILES = import.meta.glob<AgentResponse>("../fixtures/agent/*.json", { import: "default" });

function params(): URLSearchParams {
  return new URLSearchParams(location.search);
}

/**
 * Decide whether the live agent is usable. `?mock` uses canned responses; `?api=<url>` overrides the origin;
 * on `*.github.io` (static hosting) or when `/api/health` fails the chat is disabled and the other tabs keep working.
 */
export async function resolveApi(): Promise<ApiState> {
  const p = params();
  if (p.has("mock")) return { kind: "ready", base: "", mock: p.get("mock") || "ok" };
  const override = p.get("api");
  const base = (override ?? "").replace(/\/+$/, "");
  if (!override && location.hostname.endsWith(".github.io")) return { kind: "disabled", reason: "static-host" };
  try {
    const res = await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(4000), headers: { Accept: "application/json" } });
    if (res.ok) return { kind: "ready", base, mock: false };
  } catch {
    // network error or timeout: fall through to disabled
  }
  return { kind: "disabled", reason: "unreachable" };
}

const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

/** Defensive parse: unknown shapes degrade to an answer-only message instead of throwing in the UI. */
export function normalizeResponse(raw: unknown): AgentResponse {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    answer: typeof r["answer"] === "string" ? r["answer"] : "",
    actions: arr<AgentAction>(r["actions"]).filter((a) => a && typeof a === "object" && typeof a.type === "string"),
    charts: arr<AgentChart>(r["charts"]).filter((c) => c && Array.isArray(c.x) && Array.isArray(c.series)),
    tables: arr<AgentTable>(r["tables"]).filter((t) => t && Array.isArray(t.columns) && Array.isArray(t.rows)),
    tool_calls: arr<AgentToolCall>(r["tool_calls"]).filter((t) => t && typeof t.name === "string"),
  };
}

function mockKey(text: string): string {
  const t = text.toLowerCase();
  if (/how much|changed and why|how many/.test(t)) return "how_much_changed";
  if (/largest|biggest|top \d|5 largest/.test(t)) return "largest_changes";
  if (/footprint|grow|growth|since 20/.test(t)) return "footprint_growth";
  if (/burn|fire|scar/.test(t)) return "burn_scars";
  return "fallback";
}

async function mockChat(mode: string, messages: ChatMessage[]): Promise<AgentResponse> {
  await new Promise((r) => setTimeout(r, 900));
  if (mode === "429") throw new ApiError("Too Many Requests", 429, 20);
  if (mode === "error") throw new ApiError("Internal Server Error", 500);
  const last = [...messages].reverse().find((m) => m.role === "user")?.content ?? "";
  const load = MOCK_FILES[`../fixtures/agent/${mockKey(last)}.json`] ?? MOCK_FILES["../fixtures/agent/fallback.json"];
  if (!load) throw new ApiError("No mock fixtures found", null);
  return normalizeResponse(await load());
}

export async function chat(state: Extract<ApiState, { kind: "ready" }>, messages: ChatMessage[], signal: AbortSignal): Promise<AgentResponse> {
  if (state.mock) return mockChat(state.mock, messages);
  let res: Response;
  try {
    res = await fetch(`${state.base}/api/agent/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ messages }),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("Network error", null);
  }
  if (!res.ok) {
    const retry = Number(res.headers.get("Retry-After"));
    throw new ApiError(`HTTP ${res.status}`, res.status, Number.isFinite(retry) && retry > 0 ? retry : null);
  }
  try {
    return normalizeResponse(await res.json());
  } catch {
    throw new ApiError("The agent returned an unreadable response", res.status);
  }
}

/** GET /api/changes?run_id=... as GeoJSON. */
export async function fetchRun(state: Extract<ApiState, { kind: "ready" }>, runId: string): Promise<PolygonCollection> {
  if (state.mock) throw new ApiError("Run data is not available in mock mode", null);
  const res = await fetch(`${state.base}/api/changes?run_id=${encodeURIComponent(runId)}`, { headers: { Accept: "application/geo+json, application/json" } });
  if (!res.ok) throw new ApiError(`HTTP ${res.status}`, res.status);
  const fc = (await res.json()) as PolygonCollection;
  if (fc?.type !== "FeatureCollection" || !Array.isArray(fc.features)) throw new ApiError("Not a GeoJSON FeatureCollection", null);
  return fc;
}

/** Run ids that refer to the assignment result already bundled with the viewer. */
export const STATIC_RUN_IDS: ReadonlySet<string> = new Set(["assignment", "default", "robust_cva"]);
