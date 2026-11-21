import type { FeatureCollection } from "geojson";
import * as maplibregl from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { ExpressionSpecification, FilterSpecification, MapGeoJSONFeature, StyleSpecification } from "maplibre-gl";
import { CHANGE_TYPES, esc, fmtKm2, prefersReducedMotion, typeInfo } from "../domain";
import { RASTER_KEYS } from "../types";
import type { AoiCollection, Bounds, Dataset, FacilityCollection, FacilityProps } from "../types";
import { featureBounds } from "../overlays";
import { LABEL_COUNT, labelTargets, labelText } from "./changes";
import { labelPoint } from "./geo";
import type { LayerDef } from "./layers";

// maplibre-gl 6 looks for its worker next to the main module, which a bundler moves; point it at the emitted asset.
maplibregl.setWorkerUrl(workerUrl);

const ESRI_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const ESRI_ATTRIBUTION = 'Tiles &copy; <a href="https://www.esri.com/" target="_blank" rel="noopener">Esri</a>, Maxar, Earthstar Geographics';

const BASE_STYLE: StyleSpecification = {
  version: 8,
  sources: { esri: { type: "raster", tiles: [ESRI_TILES], tileSize: 256, maxzoom: 18, attribution: ESRI_ATTRIBUTION } },
  layers: [
    { id: "background", type: "background", paint: { "background-color": "#e9edf2" } },
    { id: "esri", type: "raster", source: "esri", paint: { "raster-brightness-max": 0.92, "raster-saturation": -0.1 } },
  ],
};

const CHANGE_LAYERS = ["changes-fill", "changes-line"] as const;
const easeOut = (t: number): number => 1 - (1 - t) ** 4;

const colorByType = ["match", ["get", "change_type"], ...CHANGE_TYPES.flatMap((t) => [t.key, t.color]), "#cbd5e1"] as unknown as ExpressionSpecification;
const hovered = ["boolean", ["feature-state", "hover"], false] as unknown as ExpressionSpecification;
const selected = ["boolean", ["feature-state", "selected"], false] as unknown as ExpressionSpecification;

/** Corner order MapLibre expects for an image source: NW, NE, SE, SW as [lon, lat]. */
function corners([[s, w], [n, e]]: Bounds): [[number, number], [number, number], [number, number], [number, number]] {
  return [[w, n], [e, n], [e, s], [w, s]];
}

interface StageOptions {
  main: HTMLElement;
  compare: HTMLElement;
  aoi: AoiCollection;
  facilities: FacilityCollection;
  dataset: Dataset;
  initialLayer: LayerDef;
  initialSwipe: boolean;
  onFeatureClick: (feature: MapGeoJSONFeature | null) => void;
  onFacilityClick: (props: FacilityProps) => void;
}

/**
 * Two synced maps. `main` is interactive and shows the "right" side of the active layer; `compare` sits on top, is
 * clipped by CSS to the left of the swipe handle and shows the "left" side. Vector layers are mirrored on both.
 */
export class MapStage {
  readonly main: maplibregl.Map;
  readonly compare: maplibregl.Map;
  private readonly maps: readonly maplibregl.Map[];
  private layer: LayerDef;
  private swipe: boolean;
  private dataset: Dataset;
  private hover: number | null = null;
  private selection: number | null = null;
  private hiddenTypes = new Set<string>();
  private markers: maplibregl.Marker[] = [];
  private changeLabels: { pt: [number, number]; w: number; markers: maplibregl.Marker[] }[] = [];

  constructor(private readonly opts: StageOptions) {
    this.layer = opts.initialLayer;
    this.swipe = opts.initialSwipe;
    this.dataset = opts.dataset;
    const common = { style: BASE_STYLE, bounds: featureBounds(opts.aoi.features) ?? undefined, fitBoundsOptions: { padding: 24 }, maxZoom: 19, maxPitch: 0 } as const;
    this.main = new maplibregl.Map({ ...common, container: opts.main, attributionControl: { compact: true } });
    this.compare = new maplibregl.Map({ ...common, container: opts.compare, interactive: false, attributionControl: false });
    this.maps = [this.main, this.compare];
    this.main.addControl(new maplibregl.NavigationControl({ visualizePitch: false, showCompass: false }), "bottom-right");
    this.main.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    this.main.dragRotate.disable();
    this.main.touchZoomRotate.disableRotation();
    this.main.on("move", () => {
      this.syncCompare();
      this.layoutChangeLabels();
    });
  }

  /** Resolves once both maps have their style and every layer is in place. */
  async init(): Promise<void> {
    await Promise.all(this.maps.map((m) => (m.loaded() ? Promise.resolve() : new Promise<void>((r) => m.once("load", () => r())))));
    for (const m of this.maps) this.addLayers(m);
    this.addOverlayLayers(this.main);
    this.addFacilityLabels();
    this.buildChangeLabels();
    this.apply();
    this.bindInteractions();
  }

  private addLayers(m: maplibregl.Map): void {
    const { aoi, facilities } = this.opts;
    for (const id of RASTER_KEYS) {
      const { url, bounds } = this.dataset.rasters[id];
      m.addSource(id, { type: "image", url, coordinates: corners(bounds) });
      m.addLayer({ id, type: "raster", source: id, layout: { visibility: "none" }, paint: { "raster-fade-duration": 0, "raster-resampling": "linear", "raster-opacity": 1 } });
    }
    m.addSource("facilities", { type: "geojson", data: facilities });
    m.addLayer({ id: "fac-fill", type: "fill", source: "facilities", layout: { visibility: "none" }, paint: { "fill-color": "#5eead4", "fill-opacity": ["case", hovered, 0.16, 0.05] } });
    m.addLayer({ id: "fac-line", type: "line", source: "facilities", layout: { visibility: "none" }, paint: { "line-color": "#5eead4", "line-width": 1.6, "line-opacity": 0.9 } });
    m.addSource("aoi", { type: "geojson", data: aoi });
    m.addLayer({ id: "aoi", type: "line", source: "aoi", paint: { "line-color": "#f8fafc", "line-width": 1, "line-dasharray": [3, 3], "line-opacity": 0.55 } });
    m.addSource("changes", { type: "geojson", data: this.dataset.changes, promoteId: "fid" });
    m.addLayer({ id: "changes-fill", type: "fill", source: "changes", paint: { "fill-color": colorByType, "fill-opacity": 0.4 } });
    m.addLayer({
      id: "changes-line",
      type: "line",
      source: "changes",
      paint: { "line-color": ["case", selected, "#ffffff", colorByType], "line-width": ["case", selected, 3, hovered, 2.6, 1.2], "line-opacity": 0.95 },
    });
  }

  /** Interaction layers that only exist on the interactive map. */
  private addOverlayLayers(m: maplibregl.Map): void {
    const empty: FeatureCollection = { type: "FeatureCollection", features: [] };
    m.addSource("query-result", { type: "geojson", data: empty });
    m.addLayer({ id: "query-fill", type: "fill", source: "query-result", filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": "#c4b5fd", "fill-opacity": 0.28 } });
    m.addLayer({ id: "query-line", type: "line", source: "query-result", paint: { "line-color": "#c4b5fd", "line-width": 2 } });
    m.addLayer({ id: "query-pts", type: "circle", source: "query-result", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 5, "circle-color": "#c4b5fd", "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.5 } });
  }

  private addFacilityLabels(): void {
    for (const f of this.opts.facilities.features) {
      const pt = labelPoint(f.geometry);
      if (!pt) continue;
      const p = f.properties;
      for (const m of this.maps) {
        const el = document.createElement("div");
        el.className = "fac-label";
        el.hidden = true;
        el.innerHTML = `<b>${esc(p.facility_id)}</b><span>${fmtKm2(p.changed_ha)} changed</span>`;
        this.markers.push(new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat(pt).addTo(m));
      }
    }
  }

  /** Name the few largest real changes on the map: "headline · area", as markers like the facility labels. */
  private buildChangeLabels(): void {
    for (const l of this.changeLabels) for (const mk of l.markers) mk.remove();
    this.changeLabels = [];
    for (const p of labelTargets(this.dataset.changes, this.hiddenTypes)) {
      const f = this.dataset.changes.features[p.fid];
      const pt = f ? labelPoint(f.geometry) : null;
      if (!pt) continue;
      const text = labelText(p);
      const markers = this.maps.map((m) => {
        const el = document.createElement("div");
        el.className = "chg-label";
        el.hidden = true;
        el.innerHTML = `<i class="swatch" style="background:${typeInfo(p.change_type).color}"></i><span>${esc(text)}</span>`;
        return new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat(pt).addTo(m);
      });
      this.changeLabels.push({ pt, w: text.length * 6.4 + 34, markers });
    }
    this.layoutChangeLabels();
  }

  /** Show labels largest-first and drop any that would overlap one already shown, so zooming out thins them instead of piling them up. */
  private layoutChangeLabels(): void {
    const on = this.layer.id === "types";
    const placed: { x0: number; x1: number; y0: number; y1: number }[] = [];
    const limit = this.main.getContainer().clientWidth < 720 ? 3 : LABEL_COUNT; // a phone screen has room for few
    for (const l of this.changeLabels) {
      let show = on && placed.length < limit;
      if (show) {
        const { x, y } = this.main.project(l.pt);
        const box = { x0: x - l.w / 2, x1: x + l.w / 2, y0: y - 13, y1: y + 13 };
        show = !placed.some((o) => box.x0 < o.x1 + 6 && box.x1 > o.x0 - 6 && box.y0 < o.y1 + 4 && box.y1 > o.y0 - 4);
        if (show) placed.push(box);
      }
      for (const mk of l.markers) mk.getElement().hidden = !show;
    }
  }

  /* ---------- layer state ---------- */

  setLayer(def: LayerDef): void {
    this.layer = def;
    this.apply();
  }

  setSwipe(on: boolean): void {
    this.swipe = on;
    this.apply();
    this.compare.resize();
    this.syncCompare();
  }

  private syncCompare(): void {
    this.compare.jumpTo({ center: this.main.getCenter(), zoom: this.main.getZoom(), bearing: this.main.getBearing() });
  }

  get swipeActive(): boolean {
    return this.swipe && this.layer.swipe;
  }

  setHiddenTypes(hidden: ReadonlySet<string>): void {
    this.hiddenTypes = new Set(hidden);
    this.apply();
    this.buildChangeLabels();
  }

  private apply(): void {
    const def = this.layer;
    const swipe = this.swipeActive;
    for (const [m, side] of [[this.main, def.right], [this.compare, def.left]] as const) {
      for (const id of RASTER_KEYS) {
        if (!m.getLayer(id)) continue;
        const opacity = side[id];
        m.setLayoutProperty(id, "visibility", opacity === undefined || (m === this.compare && !swipe) ? "none" : "visible");
        if (opacity !== undefined) m.setPaintProperty(id, "raster-opacity", opacity);
      }
      m.setLayoutProperty("fac-fill", "visibility", def.facilities ? "visible" : "none");
      m.setLayoutProperty("fac-line", "visibility", def.facilities ? "visible" : "none");
    }
    for (const mk of this.markers) mk.getElement().hidden = !def.facilities;
    this.layoutChangeLabels();

    const fill = def.polygons === "fill";
    const filter: FilterSpecification | null = this.hiddenTypes.size ? (["!", ["in", ["get", "change_type"], ["literal", [...this.hiddenTypes]]]] as unknown as FilterSpecification) : null;
    for (const m of this.maps) {
      m.setPaintProperty("changes-fill", "fill-opacity", fill ? (["case", hovered, 0.72, 0.46] as unknown as ExpressionSpecification) : (["case", hovered, 0.32, 0.015] as unknown as ExpressionSpecification));
      m.setPaintProperty("changes-line", "line-opacity", fill ? 0.95 : 0.85);
      for (const id of CHANGE_LAYERS) m.setFilter(id, filter);
    }
  }

  /* ---------- dataset ---------- */

  setDataset(ds: Dataset): void {
    this.dataset = ds;
    for (const m of this.maps) {
      for (const k of RASTER_KEYS) {
        (m.getSource(k) as maplibregl.ImageSource).updateImage({ url: ds.rasters[k].url, coordinates: corners(ds.rasters[k].bounds) });
      }
      (m.getSource("changes") as maplibregl.GeoJSONSource).setData(ds.changes);
    }
    this.hover = null;
    this.select(null);
    this.buildChangeLabels();
  }

  /* ---------- interaction ---------- */

  private bindInteractions(): void {
    const m = this.main;
    m.on("mousemove", "changes-fill", (e) => {
      m.getCanvas().style.cursor = "pointer";
      const id = e.features?.[0]?.properties["fid"] as number | undefined;
      if (id !== undefined) this.setHover(id);
    });
    m.on("mouseleave", "changes-fill", () => {
      m.getCanvas().style.cursor = "";
      this.setHover(null);
    });
    m.on("click", (e) => {
      const hit = m.queryRenderedFeatures(e.point, { layers: ["changes-fill"] })[0];
      if (hit) {
        this.opts.onFeatureClick(hit);
        return;
      }
      if (this.layer.facilities) {
        const fac = m.queryRenderedFeatures(e.point, { layers: ["fac-fill"] })[0];
        if (fac) {
          this.opts.onFacilityClick(fac.properties as unknown as FacilityProps);
          return;
        }
      }
      this.opts.onFeatureClick(null);
    });
  }

  /** Highlight a polygon from outside the map (a list row under the pointer). */
  setHover(id: number | null): void {
    if (id === this.hover) return;
    this.moveFeatureState("hover", this.hover, id);
    this.hover = id;
  }

  select(id: number | null): void {
    if (this.selection === id) return;
    this.moveFeatureState("selected", this.selection, id);
    this.selection = id;
  }

  private moveFeatureState(key: "hover" | "selected", from: number | null, to: number | null): void {
    for (const m of this.maps) {
      if (from !== null) m.setFeatureState({ source: "changes", id: from }, { [key]: false });
      if (to !== null) m.setFeatureState({ source: "changes", id: to }, { [key]: true });
    }
  }

  /* ---------- query result and fitting ---------- */

  setQueryResult(fc: FeatureCollection | null): void {
    (this.main.getSource("query-result") as maplibregl.GeoJSONSource).setData(fc ?? { type: "FeatureCollection", features: [] });
  }

  fit(b: maplibregl.LngLatBounds, opts: { padding?: maplibregl.PaddingOptions | number; maxZoom?: number } = {}): void {
    this.main.fitBounds(b, { padding: opts.padding ?? 80, maxZoom: opts.maxZoom ?? 17, duration: prefersReducedMotion() ? 0 : 800, easing: easeOut });
  }

  resize(): void {
    for (const m of this.maps) m.resize();
  }
}
