/**
 * Canned backend for `?mock`: serves fixtures/agent/*.json and fixtures/api/*.json so every live feature can be
 * developed and demoed without the API. Nothing here runs unless `?mock` is in the URL.
 */
import { ApiError } from "./api";
import type { AgentResponse } from "./types";

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

/** Same signature the real endpoints have; throws ApiError like the real client does. */
export async function mockCall<T>(mode: string, method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const url = new URL(path, "http://mock");
  const p = url.pathname;
  let out: unknown;
  if (p === "/api/agent/chat") out = await agent(mode, body);
  else if (p === "/api/db/schema") out = await fixture("db_schema");
  else if (p === "/api/db/connection") out = await fixture("db_connection");
  else if (p === "/api/db/connection/uri") out = { uri: "postgresql://s2cd_reader:mock@db.example.supabase.co:5432/postgres" };
  else if (p.startsWith("/api/db/tables/")) (await delay(200), (out = await fixture("db_page")));
  else throw new ApiError(`The mock backend has no ${method} ${p}`, 404);
  return out as T;
}
