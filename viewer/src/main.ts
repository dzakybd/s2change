import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";
import * as maplibregl from "maplibre-gl";
import type { MapGeoJSONFeature } from "maplibre-gl";
import type { Feature } from "geojson";
import { mountAgent } from "./agent";
import type { AgentHost } from "./agent";
import { fetchRun, resolveApi, STATIC_RUN_IDS } from "./api";
import { dataBase, loadData, loadMonthly } from "./data";
import { summarize } from "./filters";
import { MapPair } from "./maps";
import { featureBounds, MonthlyLayers, RunOverlay } from "./overlays";
import { buildPanel } from "./panel";
import { Swipe } from "./swipe";
import { Timeline, mountTimelineUnavailable } from "./timeline";
import type { ChangeProps, LayerState, RasterKey } from "./types";
import { DIRECTION_COLORS, RASTER_KEYS } from "./types";

function $(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} missing`);
  return el;
}

function popupContent(p: ChangeProps): HTMLElement {
  const root = document.createElement("div");
  const title = document.createElement("div");
  title.className = "pop-title";
  const dot = document.createElement("i");
  dot.className = "swatch";
  dot.style.background = DIRECTION_COLORS[p.change_direction];
  title.append(dot, p.change_direction);
  const rows: [string, string][] = [
    ["Area", `${(p.area_m2 / 10_000).toFixed(2)} ha`],
    ["Confidence", p.confidence.toFixed(2)],
    ["Δ brightness", `${p.d_brightness >= 0 ? "+" : ""}${p.d_brightness.toFixed(3)}`],
    ["In disturbed area", p.in_disturbed_area ? "Yes" : "No"],
    ["Stripe zone", String(p.zone)],
  ];
  const dl = document.createElement("dl");
  for (const [k, v] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    dl.append(dt, dd);
  }
  root.append(title, dl);
  return root;
}

type Tab = "assignment" | "timeline" | "agent";
const TABS: readonly Tab[] = ["assignment", "timeline", "agent"];

async function main(): Promise<void> {
  const status = $("status");
  status.textContent = "Loading data…";
  const apiPromise = resolveApi();
  const monthlyPromise = loadMonthly(dataBase());
  const data = await loadData();
  const { manifest, changes } = data;
  document.title = `Lumwana-area open-pit mine, Zambia, Sentinel-2 change ${manifest.date_before} → ${manifest.date_after}`;
  $("swipe-label-left").textContent = `Before · ${manifest.date_before}`;
  $("swipe-label-right").textContent = `After (normalized) · ${manifest.date_after}`;

  let swipeOn = true;
  const rasters = Object.fromEntries(
    RASTER_KEYS.map((k) => [k, { visible: k === "after_normalized", opacity: k === "change_intensity" ? 0.8 : 1 }]),
  ) as Record<RasterKey, LayerState>;

  const popup = new maplibregl.Popup({ className: "pop", maxWidth: "260px", closeButton: true });
  const maps = new MapPair({
    main: $("map"),
    compare: $("map-compare"),
    ...data,
    onFeatureClick: (f: MapGeoJSONFeature, lngLat) => {
      popup.setLngLat(lngLat).setDOMContent(popupContent(f.properties as unknown as ChangeProps)).addTo(maps.main);
    },
  });
  const swipe = new Swipe($("swipe"), $("swipe-handle"), $("map-compare"));

  const refresh = () => {
    const f = panel.filters();
    maps.setFilters(f);
    panel.updateStats(summarize(changes, f));
  };
  const panel = buildPanel($("panel"), data, { rasters, swipe: swipeOn }, {
    onRaster: (k, s) => maps.setRaster(k, s),
    onSwipe: (on) => {
      swipeOn = on;
      swipe.setEnabled(on);
      maps.setSwipe(on);
    },
    onChangesVisible: (v) => maps.setChangesVisible(v),
    onFilters: refresh,
  });

  await maps.init(rasters, panel.filters());
  panel.updateStats(summarize(changes, panel.filters()));

  // Timeline tab: monthly frames on the same map. Missing data degrades to an explanatory empty state.
  const monthly = await monthlyPromise;
  const timeline = monthly
    ? new Timeline({ manifest: monthly, base: data.base, layers: new MonthlyLayers(maps.main, monthly.bounds), stage: $("stage"), panel: $("timeline-panel"), dock: $("dock") })
    : null;
  if (!monthly) mountTimelineUnavailable($("timeline-panel"));

  // Analyst tab: map actions requested by the agent.
  const runs = new RunOverlay(maps.main, changes);
  const fit = (features: Feature[], maxZoom: number) => {
    const b = featureBounds(features);
    if (b) maps.main.fitBounds(b, { padding: 80, duration: 900, maxZoom });
  };
  const host: AgentHost = {
    flyTo(bbox) {
      const [w, s, e, n] = bbox;
      if (![w, s, e, n].every(Number.isFinite) || w >= e || s >= n) return "The agent sent an invalid area.";
      maps.main.fitBounds([[w, s], [e, n]], { padding: 48, duration: 900, maxZoom: 17 });
      return null;
    },
    async highlight(runId, ids, fitView) {
      const wanted = ids.filter((i) => Number.isInteger(i));
      let features: Feature[];
      if (STATIC_RUN_IDS.has(runId)) {
        features = runs.assignmentFeatures(wanted);
      } else {
        const api = await apiPromise;
        if (api.kind !== "ready") return "Loading another run needs the live agent.";
        const fc = await runs.loadRun(runId, (id) => fetchRun(api, id));
        features = runs.selectIds(fc, wanted);
      }
      if (!features.length) return `No polygons with those ids in run ${runId}.`;
      runs.setHighlight(features);
      if (fitView) fit(features, 16);
      return null;
    },
    async showRun(runId) {
      if (STATIC_RUN_IDS.has(runId)) {
        runs.showRun(null);
        const toggle = document.querySelector<HTMLInputElement>("#changes-toggle");
        if (toggle && !toggle.checked) toggle.click();
        return null;
      }
      const api = await apiPromise;
      if (api.kind !== "ready") return "Loading another run needs the live agent.";
      const fc = await runs.loadRun(runId, (id) => fetchRun(api, id));
      runs.showRun(fc);
      fit(fc.features, 15);
      return null;
    },
    setMonth(month) {
      if (!timeline) return "The monthly timeline data is not available.";
      if (!timeline.setMonth(month)) return `The timeline has no data for ${month}.`;
      setTab("timeline");
      return null;
    },
    reset() {
      runs.setHighlight([]);
    },
  };
  mountAgent($("chat-panel"), host, apiPromise);

  // Tabs
  const tabButtons = new Map<Tab, HTMLElement>(TABS.map((t) => [t, $(`tab-${t}`)]));
  const panels: Record<Tab, HTMLElement> = { assignment: $("panel"), timeline: $("timeline-panel"), agent: $("chat-panel") };
  let current: Tab | null = null;
  function setTab(tab: Tab): void {
    if (tab === current) return;
    current = tab;
    for (const t of TABS) {
      const on = t === tab;
      tabButtons.get(t)?.setAttribute("aria-selected", String(on));
      tabButtons.get(t)?.setAttribute("tabindex", on ? "0" : "-1");
      panels[t].hidden = !on;
    }
    $("dock").hidden = !(tab === "timeline" && timeline);
    popup.remove();
    const showTimeline = tab === "timeline" && timeline !== null;
    maps.setAssignmentHidden(showTimeline);
    const swipeActive = tab === "assignment" && swipeOn;
    swipe.setEnabled(swipeActive);
    maps.setSwipe(swipeActive);
    timeline?.setActive(showTimeline);
    runs.setVisible(!showTimeline);
    history.replaceState(null, "", `${location.pathname}?${new URLSearchParams({ ...Object.fromEntries(new URLSearchParams(location.search)), tab }).toString()}${location.hash}`);
    // The dock and the panel widths change the map's box; resize after layout settles.
    requestAnimationFrame(() => {
      maps.main.resize();
      maps.compare.resize();
    });
  }
  for (const [t, btn] of tabButtons) {
    btn.addEventListener("click", () => setTab(t));
    btn.addEventListener("keydown", (e) => {
      const i = TABS.indexOf(t);
      const next = e.key === "ArrowRight" ? TABS[(i + 1) % TABS.length] : e.key === "ArrowLeft" ? TABS[(i + TABS.length - 1) % TABS.length] : undefined;
      if (!next) return;
      e.preventDefault();
      setTab(next);
      tabButtons.get(next)?.focus();
    });
  }
  const wanted = new URLSearchParams(location.search).get("tab");
  setTab(TABS.find((t) => t === wanted) ?? "assignment");

  status.hidden = true;
  document.body.dataset["ready"] = "1";
  // Test hook for scripted screenshots: open with ?debug.
  if (new URLSearchParams(location.search).has("debug")) Object.assign(window, { __maps: maps, __timeline: timeline });
}

main().catch((err: unknown) => {
  const status = $("status");
  status.hidden = false;
  status.classList.add("error");
  status.textContent = `Could not load viewer data: ${err instanceof Error ? err.message : String(err)}`;
  console.error(err);
});
