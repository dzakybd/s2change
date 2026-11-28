import "./styles/base.css";
import type { AppContext, View } from "./app";
import { logoSvg } from "./brand";
import { resolveApi } from "./api";
import type { Route, RouteName } from "./router";
import { onRoute } from "./router";
import { initAuth, mountAuthChip } from "./unlock";

const TITLES: Record<RouteName, string> = {
  report: "s2change · Sentinel-2 change detection at a Zambian copper mine",
  map: "Map · s2change",
  database: "Database · s2change",
  downloads: "Downloads · s2change",
};

/** Views load on demand; the map (MapLibre) and the markdown libraries stay out of the first paint. */
const LOADERS: Record<RouteName, { group: string; load: () => Promise<(ctx: AppContext) => Promise<View>> }> = {
  map: { group: "workspace", load: async () => (await import("./views/workspace")).createWorkspace },
  database: { group: "database", load: async () => (await import("./views/database")).createDatabase },
  report: { group: "report", load: async () => (await import("./views/report")).createReport },
  downloads: { group: "downloads", load: async () => (await import("./views/downloads")).createDownloads },
};

const main = document.getElementById("main") as HTMLElement;
document.getElementById("brand-logo")!.innerHTML = logoSvg(28, "hdr");

const ctx: AppContext = { api: resolveApi() };
void initAuth(ctx.api);
mountAuthChip(document.getElementById("auth-chip") as HTMLElement, ctx.api);
const views = new Map<string, Promise<View>>();
let current: View | null = null;
let token = 0;

function viewFor(route: Route): Promise<View> {
  const { group, load } = LOADERS[route.name];
  let p = views.get(group);
  if (!p) {
    p = load().then(async (create) => {
      const view = await create(ctx);
      view.el.hidden = true;
      main.append(view.el);
      return view;
    });
    views.set(group, p);
    p.catch(() => views.delete(group));
  }
  return p;
}

function setNav(name: RouteName): void {
  for (const a of document.querySelectorAll<HTMLAnchorElement>(".tab")) {
    if (a.dataset["route"] === name) {
      a.setAttribute("aria-current", "page");
      a.scrollIntoView({ block: "nearest", inline: "center" });
    } else a.removeAttribute("aria-current");
  }
  document.title = TITLES[name];
}

onRoute((route) => {
  const mine = ++token;
  setNav(route.name);
  viewFor(route)
    .then((view) => {
      if (mine !== token) return; // a newer navigation won
      if (current && current !== view) current.el.hidden = true;
      view.el.hidden = false;
      current = view;
      view.show(route);
    })
    .catch((err: unknown) => {
      console.error(err);
      if (mine !== token) return;
      main.replaceChildren(Object.assign(document.createElement("div"), { className: "page", innerHTML: `<div class="callout error"><b>This view failed to load</b><p>${err instanceof Error ? err.message : String(err)}</p><p><a href="">Reload</a></p></div>` }));
      current = null;
      views.clear();
    });
});
