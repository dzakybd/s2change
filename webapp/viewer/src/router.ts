/** Hash router: `#/map?layer=intensity&swipe=0`. Every route is deep-linkable and works on any static host. */

const ROUTES = ["report", "map", "database", "downloads"] as const;
/** The agent lives on the map now; its old tab names open the map with the chat panel. */
const AGENT_ALIASES = new Set(["agent", "analyst"]);
export type RouteName = (typeof ROUTES)[number];

export interface Route {
  name: RouteName;
  params: URLSearchParams;
}

function parseHash(hash = location.hash): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [path = "", query = ""] = raw.split("?");
  const first = path.split("/")[0] ?? "";
  const params = new URLSearchParams(query);
  if (AGENT_ALIASES.has(first)) {
    params.set("agent", "1");
    return { name: "map", params };
  }
  const name = ROUTES.find((r) => r === first) ?? "report";
  return { name, params };
}

export function hrefFor(name: RouteName, params?: Record<string, string | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v !== undefined && v !== "") q.set(k, v);
  const s = q.toString();
  return `#/${name}${s ? `?${s}` : ""}`;
}

/** Update the hash without adding a history entry or re-running the router (used for in-view state like map layer). */
export function replaceParams(name: RouteName, params: Record<string, string | undefined>): void {
  history.replaceState(null, "", `${location.pathname}${location.search}${hrefFor(name, params)}`);
}

export function onRoute(cb: (r: Route) => void): void {
  window.addEventListener("hashchange", () => cb(parseHash()));
  cb(parseHash());
}
