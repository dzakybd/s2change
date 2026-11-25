/**
 * Canned backend for `?mock`: serves fixtures/agent/*.json and fixtures/api/*.json so every live feature can be
 * developed and demoed without the API. Nothing here runs unless `?mock` is in the URL.
 */
import { ApiError } from "./api";
import type { AgentResponse } from "./types";
import { dataUrl } from "./data";

const AGENT = import.meta.glob<AgentResponse>("../fixtures/agent/*.json", { import: "default" });
const API = import.meta.glob<unknown>("../fixtures/api/*.json", { import: "default" });

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

async function fixture<T>(name: string): Promise<T> {
  const load = API[`../fixtures/api/${name}.json`];
  if (!load) throw new ApiError(`No mock fixture ${name}`, 404);
  return (await load()) as T;
}

function agentKey(text: string): string {
  const t = text.toLowerCase();
  if (/how much|changed and why|how many/.test(t)) return "how_much_changed";
  if (/largest|biggest|top \d|5 largest/.test(t)) return "largest_changes";
  if (/facilit|tailings|which .* most/.test(t)) return "facility_most";
  if (/burn|fire|scar/.test(t)) return "burn_scars";
  return "fallback";
}

async function agent(mode: string, body: unknown): Promise<unknown> {
  await delay(900);
  if (mode === "429") throw new ApiError("Too Many Requests", 429, 20);
  if (mode === "error") throw new ApiError("Internal Server Error", 500);
  const messages = ((body as { messages?: { role: string; content: string }[] })?.messages ?? []).filter((m) => m.role === "user");
  const load = AGENT[`../fixtures/agent/${agentKey(messages.at(-1)?.content ?? "")}.json`] ?? AGENT["../fixtures/agent/fallback.json"];
  if (!load) throw new ApiError("No mock fixtures found", null);
  return load();
}

/** `@web/x` in a fixture means "the static export's file x", resolved against the data base. */
function resolveWeb<T>(v: T): T {
  return JSON.parse(JSON.stringify(v).replaceAll("@web/", dataUrl("").href)) as T;
}

let job: { started: number } | null = null;

async function compare(method: string): Promise<unknown> {
  if (method === "POST") {
    job = { started: Date.now() };
    return { job_id: "mock-job", status: "queued" };
  }
  const t = (Date.now() - (job?.started ?? 0)) / 1000;
  if (t < 1.2) return { status: "queued", message: "Waiting for a worker" };
  if (t < 2.6) return { status: "running", message: "Reading Sentinel-2 scenes for both dates" };
  if (t < 4) return { status: "running", message: "Normalising radiometry and scoring change" };
  return { status: "done", message: "Done", result: resolveWeb(await fixture("compare_result")) };
}

/** Same signature the real endpoints have; throws ApiError like the real client does. */
export async function mockCall<T>(mode: string, method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const url = new URL(path, "http://mock");
  const p = url.pathname;
  let out: unknown;
  if (p === "/api/agent/chat") out = await agent(mode, body);
  else if (p === "/api/brief") (await delay(500), (out = await fixture("brief")));
  else if (p === "/api/db/schema") out = await fixture("db_schema");
  else if (p === "/api/db/connection") out = await fixture("db_connection");
  else if (p === "/api/db/connection/uri") out = { uri: "postgresql://s2cd_reader:mock@db.example.supabase.co:5432/postgres" };
  else if (p.startsWith("/api/db/tables/")) (await delay(200), (out = await fixture("db_page")));
  else if (p === "/api/compare/dates") out = await fixture("compare_dates");
  else if (p === "/api/compare" || p.startsWith("/api/compare/")) out = await compare(method);
  else throw new ApiError(`The mock backend has no ${method} ${p}`, 404);
  return out as T;
}
