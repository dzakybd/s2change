import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";
import * as maplibregl from "maplibre-gl";
import type { MapGeoJSONFeature } from "maplibre-gl";
import { loadData } from "./data";
import { summarize } from "./filters";
import { MapPair } from "./maps";
import { buildPanel } from "./panel";
import { Swipe } from "./swipe";
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

async function main(): Promise<void> {
  const status = $("status");
  status.textContent = "Loading data…";
  const data = await loadData();
  const { manifest, changes } = data;
  document.title = `Lumwana-area open-pit mine, Zambia, Sentinel-2 change ${manifest.date_before} → ${manifest.date_after}`;
  $("swipe-label-left").textContent = `Before · ${manifest.date_before}`;
  $("swipe-label-right").textContent = `After (normalized) · ${manifest.date_after}`;

  const swipeOn = true;
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
      swipe.setEnabled(on);
      maps.setSwipe(on);
    },
    onChangesVisible: (v) => maps.setChangesVisible(v),
    onFilters: refresh,
  });

  await maps.init(rasters, panel.filters());
  swipe.setEnabled(swipeOn);
  maps.setSwipe(swipeOn);
  panel.updateStats(summarize(changes, panel.filters()));
  status.hidden = true;
  document.body.dataset["ready"] = "1";
  // Test hook for scripted screenshots: open with ?debug.
  if (new URLSearchParams(location.search).has("debug")) Object.assign(window, { __maps: maps });
}

main().catch((err: unknown) => {
  const status = $("status");
  status.hidden = false;
  status.classList.add("error");
  status.textContent = `Could not load viewer data: ${err instanceof Error ? err.message : String(err)}`;
  console.error(err);
});
