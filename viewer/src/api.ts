import type { PolygonCollection } from "./overlays";
import type { AgentAction, AgentChart, AgentResponse, AgentTable, AgentToolCall, DbConnection, DbPage, DbTable } from "./types";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export type ReadyApi = { kind: "ready"; base: string; mock: false | string };
export type ApiState = ReadyApi | { kind: "disabled"; reason: "static-host" | "unreachable" };

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly retryAfterSec: number | null = null,
  ) {
    super(message);
  }
}

/**
 * Decide whether the live backend is usable. `?mock` serves canned responses from fixtures/; `?api=<url>` overrides
 * the origin; on `*.github.io` (static hosting) or when `/api/health` fails the live features are disabled and the
 * rest of the app keeps working from the static exports.
 */
export async function resolveApi(): Promise<ApiState> {
  const p = new URLSearchParams(location.search);
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

async function call<T>(state: ReadyApi, method: "GET" | "POST", path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  if (state.mock) {
    const { mockCall } = await import("./mock");
    return mockCall<T>(state.mock, method, path, body);
  }
  let res: Response;
  try {
    res = await fetch(`${state.base}${path}`, {
      method,
      credentials: "same-origin", // the access-code session cookie is scoped to /api on this origin
      headers: { Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      ...(signal ? { signal } : {}),
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("Network error", null);
  }
  if (!res.ok) {
    const retry = Number(res.headers.get("Retry-After"));
    let detail = `HTTP ${res.status}`;
    try {
      const d = ((await res.json()) as { detail?: unknown }).detail;
      if (typeof d === "string" && d) detail = d;
    } catch {
      // body was not JSON
    }
    throw new ApiError(detail, res.status, Number.isFinite(retry) && retry > 0 ? retry : null);
  }
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError("The server returned an unreadable response", res.status);
  }
}

/* ---------- access code ---------- */

export interface AuthState {
  enabled: boolean;
  authenticated: boolean;
}

/** Whether the server has an access code at all and whether this browser holds a valid session. Mock/static hosts: open. */
export const authStatus = (s: ReadyApi): Promise<AuthState> => (s.mock ? Promise.resolve({ enabled: false, authenticated: true }) : call<AuthState>(s, "GET", "/api/auth/status"));
export const login = (s: ReadyApi, code: string) => call<AuthState>(s, "POST", "/api/auth/login", { code });
export const logout = (s: ReadyApi) => call<AuthState>(s, "POST", "/api/auth/logout", {});

/* ---------- agent ---------- */

const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

const ACTIONS = new Set(["fly_to", "highlight", "show_run", "set_layer", "set_view", "show_facility"]);

/** Defensive parse: unknown shapes degrade to an answer-only message instead of throwing in the UI. */
function normalizeResponse(raw: unknown): AgentResponse {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    answer: typeof r["answer"] === "string" ? r["answer"] : "",
    // Actions the viewer cannot perform are dropped here.
    actions: arr<AgentAction>(r["actions"]).filter((a) => a && typeof a === "object" && ACTIONS.has(a.type)),
    charts: arr<AgentChart>(r["charts"]).filter((c) => c && Array.isArray(c.x) && Array.isArray(c.series)),
    tables: arr<AgentTable>(r["tables"]).filter((t) => t && Array.isArray(t.columns) && Array.isArray(t.rows)),
    tool_calls: arr<AgentToolCall>(r["tool_calls"]).filter((t) => t && typeof t.name === "string"),
  };
}

export async function chat(state: ReadyApi, messages: ChatMessage[], signal: AbortSignal): Promise<AgentResponse> {
  return normalizeResponse(await call<unknown>(state, "POST", "/api/agent/chat", { messages }, signal));
}

export async function brief(state: ReadyApi): Promise<string> {
  const r = await call<{ markdown?: unknown }>(state, "GET", "/api/brief");
  if (typeof r.markdown !== "string") throw new ApiError("The brief came back empty", null);
  return r.markdown;
}

/** GET /api/changes?run_id=... as GeoJSON. */
export async function fetchRun(state: ReadyApi, runId: string): Promise<PolygonCollection> {
  if (state.mock) throw new ApiError("Run data is not available in mock mode", null);
  const fc = await call<PolygonCollection>(state, "GET", `/api/changes?run_id=${encodeURIComponent(runId)}`);
  if (fc?.type !== "FeatureCollection" || !Array.isArray(fc.features)) throw new ApiError("Not a GeoJSON FeatureCollection", null);
  return fc;
}

/** Run ids that refer to the result already bundled with the viewer. */
export const STATIC_RUN_IDS: ReadonlySet<string> = new Set(["assignment", "default", "robust_cva"]);

/* ---------- database explorer ---------- */

export const dbSchema = (s: ReadyApi) => call<DbTable[]>(s, "GET", "/api/db/schema");
interface PageQuery {
  page: number;
  size: number;
  sort?: string | undefined;
  desc?: boolean;
  column?: string | undefined;
  value?: string | undefined;
  geojson?: boolean;
}
export function dbPage(s: ReadyApi, table: string, q: PageQuery, signal?: AbortSignal): Promise<DbPage> {
  const p = new URLSearchParams({ page: String(q.page), size: String(q.size) });
  if (q.sort) p.set("sort", q.sort);
  if (q.desc) p.set("desc", "true");
  if (q.column !== undefined && q.value !== undefined) (p.set("column", q.column), p.set("value", q.value));
  if (q.geojson) p.set("geojson", "true");
  return call<DbPage>(s, "GET", `/api/db/tables/${encodeURIComponent(table)}?${p}`, undefined, signal);
}
export const dbConnection = (s: ReadyApi) => call<DbConnection>(s, "GET", "/api/db/connection");
export const dbUri = (s: ReadyApi) => call<{ uri: string }>(s, "GET", "/api/db/connection/uri");
/** The SQL dump is a plain file download from the same origin (one table, or every table when omitted). */
export const dbExportUrl = (s: ReadyApi, table?: string) => `${s.base}/api/db/export.sql${table ? `?table=${encodeURIComponent(table)}` : ""}`;

/** Human message for a failed API call. */
export function friendlyError(err: unknown, what = "the service"): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return `${what} is getting a lot of requests.${err.retryAfterSec ? ` Please wait about ${err.retryAfterSec} seconds` : " Please wait a moment"} and try again.`;
    if (err.status === null) return `Could not reach ${what}. Check your connection and try again.`;
    if (err.status === 503) return err.message; // the server says why it is unavailable (e.g. the agent's AI credit is used up)
    if (err.status >= 500) return `${what} hit an error (${err.message}). Try again in a moment.`;
    return err.message;
  }
  return `Something went wrong talking to ${what}.`;
}
