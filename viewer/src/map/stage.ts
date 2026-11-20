import type { Feature, FeatureCollection, Polygon, Position } from "geojson";
import * as maplibregl from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { ExpressionSpecification, FilterSpecification, MapGeoJSONFeature, StyleSpecification } from "maplibre-gl";
import { BINARY_COLOR, CHANGE_TYPES, esc, prefersReducedMotion } from "../domain";
import type { AoiCollection, Bounds, Dataset, FacilityCollection, FacilityProps, MapRaster, MineralsManifest } from "../types";
import { isValidAoi, labelPoint } from "./geo";
import type { LayerDef } from "./layers";

// maplibre-gl 6 looks for its worker next to the main module, which a bundler moves; point it at the emitted asset.
maplibregl.setWorkerUrl(workerUrl);

const ESRI_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const ESRI_ATTRIBUTION = 'Tiles &copy; <a href="https://www.esri.com/" target="_blank" rel="noopener">Esri</a>, Maxar, Earthstar Geographics';

const BASE_STYLE: StyleSpecification = {
  version: 8,
  sources: { esri: { type: "raster", tiles: [ESRI_TILES], tileSize: 256, maxzoom: 18, attribution: ESRI_ATTRIBUTION } },
  layers: [
    { id: "background", type: "background", paint: { "background-color": "#0a0d12" } },
    { id: "esri", type: "raster", source: "esri", paint: { "raster-brightness-max": 0.92, "raster-saturation": -0.1 } },
  ],
};

const RASTER_IDS: readonly MapRaster[] = ["before", "after", "after_normalized", "change_intensity", "redness_before", "redness_after"];
const CHANGE_LAYERS = ["changes-fill", "changes-line", "changes-extrude"] as const;
const easeOut = (t: number): number => 1 - (1 - t) ** 4;

const colorByType = ["match", ["get", "change_type"], ...CHANGE_TYPES.flatMap((t) => [t.key, t.color]), "#cbd5e1"] as unknown as ExpressionSpecification;
const hovered = ["boolean", ["feature-state", "hover"], false] as unknown as ExpressionSpecification;
const selected = ["boolean", ["feature-state", "selected"], false] as unknown as ExpressionSpecification;
const EXTRUDE_MAX_M = 700;
const HEIGHT_BY_CONFIDENCE = ["interpolate", ["linear"], ["get", "confidence"], 0, 25, 1, EXTRUDE_MAX_M] as unknown as ExpressionSpecification;

/** Corner order MapLibre expects for an image source: NW, NE, SE, SW as [lon, lat]. */
function corners([[s, w], [n, e]]: Bounds): [[number, number], [number, number], [number, number], [number, number]] {
  return [[w, n], [e, n], [e, s], [w, s]];
}

export function aoiBounds(aoi: AoiCollection): maplibregl.LngLatBounds {
  const b = new maplibregl.LngLatBounds();
  for (const f of aoi.features) for (const ring of f.geometry.coordinates) for (const [x, y] of ring) b.extend([x as number, y as number]);
  return b;
}

export interface StageOptions {
  main: HTMLElement;
  compare: HTMLElement;
  aoi: AoiCollection;
  facilities: FacilityCollection;
  dataset: Dataset;
  minerals: MineralsManifest | null;
  mineralsBase: URL;
  initialLayer: LayerDef;
  initialSwipe: boolean;
  onFeatureClick: (feature: MapGeoJSONFeature | null) => void;
  onFacilityClick: (props: FacilityProps) => void;
  onDrawChange: (state: { points: number; active: boolean }) => void;
  onDrawDone: (ring: Position[]) => void;
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
  private oblique = false;
  private extrude = false;
  private bearing = 24;
  private markers: maplibregl.Marker[] = [];
  private draw: Position[] | null = null;
  private cursor: Position | null = null;

  constructor(private readonly opts: StageOptions) {
    this.layer = opts.initialLayer;
    this.swipe = opts.initialSwipe;
    this.dataset = opts.dataset;
    const common = { style: BASE_STYLE, bounds: aoiBounds(opts.aoi), fitBoundsOptions: { padding: 24 }, maxZoom: 19, maxPitch: 70 } as const;
    this.main = new maplibregl.Map({ ...common, container: opts.main, attributionControl: { compact: true } });
    this.compare = new maplibregl.Map({ ...common, container: opts.compare, interactive: false, attributionControl: false });
    this.maps = [this.main, this.compare];
    this.main.addControl(new maplibregl.NavigationControl({ visualizePitch: false, showCompass: false }), "bottom-right");
    this.main.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    this.main.dragRotate.disable();
    this.main.touchZoomRotate.disableRotation();
    this.main.on("move", () => {
      this.compare.jumpTo({ center: this.main.getCenter(), zoom: this.main.getZoom(), bearing: this.main.getBearing(), pitch: this.main.getPitch() });
    });
  }

  /** Resolves once both maps have their style and every layer is in place. */
  async init(): Promise<void> {
    await Promise.all(this.maps.map((m) => (m.loaded() ? Promise.resolve() : new Promise<void>((r) => m.once("load", () => r())))));
    for (const m of this.maps) this.addLayers(m);
    this.addOverlayLayers(this.main);
    this.addFacilityLabels();
    this.apply();
    this.bindInteractions();
  }

  private addLayers(m: maplibregl.Map): void {
    const { aoi, facilities, minerals, mineralsBase } = this.opts;
    const imageDefs: [MapRaster, string, Bounds][] = [];
    for (const k of ["before", "after", "after_normalized", "change_intensity"] as const) imageDefs.push([k, this.dataset.rasters[k].url, this.dataset.rasters[k].bounds]);
    if (minerals) {
      imageDefs.push(["redness_before", new URL(minerals.layers.before.url, mineralsBase).href, minerals.layers.before.bounds]);
      imageDefs.push(["redness_after", new URL(minerals.layers.after.url, mineralsBase).href, minerals.layers.after.bounds]);
    }
    for (const [id, url, bounds] of imageDefs) {
      m.addSource(id, { type: "image", url, coordinates: corners(bounds) });
      m.addLayer({ id, type: "raster", source: id, layout: { visibility: "none" }, paint: { "raster-fade-duration": 0, "raster-resampling": "linear", "raster-opacity": 1 } });
    }
    m.addSource("facilities", { type: "geojson", data: facilities });
    m.addLayer({ id: "fac-fill", type: "fill", source: "facilities", layout: { visibility: "none" }, paint: { "fill-color": "#5eead4", "fill-opacity": ["case", hovered, 0.16, 0.05] } });
    m.addLayer({ id: "fac-line", type: "line", source: "facilities", layout: { visibility: "none" }, paint: { "line-color": "#5eead4", "line-width": 1.6, "line-opacity": 0.9 } });
    m.addSource("aoi", { type: "geojson", data: aoi });
    m.addLayer({ id: "aoi", type: "line", source: "aoi", paint: { "line-color": "#f8fafc", "line-width": 1, "line-dasharray": [3, 3], "line-opacity": 0.55 } });
    m.addSource("changes", { type: "geojson", data: this.dataset.changes, promoteId: "fid" });
    m.addLayer({ id: "changes-extrude", type: "fill-extrusion", source: "changes", layout: { visibility: "none" }, paint: { "fill-extrusion-color": colorByType, "fill-extrusion-height": HEIGHT_BY_CONFIDENCE, "fill-extrusion-base": 0, "fill-extrusion-opacity": 0.88, "fill-extrusion-vertical-gradient": true } });
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
    m.addSource("aoi-shape", { type: "geojson", data: empty });
    m.addLayer({ id: "aoi-shape-fill", type: "fill", source: "aoi-shape", paint: { "fill-color": "#5eead4", "fill-opacity": 0.1 } });
    m.addLayer({ id: "aoi-shape-line", type: "line", source: "aoi-shape", paint: { "line-color": "#5eead4", "line-width": 2.2, "line-dasharray": [2, 1.2] } });
    m.addLayer({ id: "aoi-shape-pts", type: "circle", source: "aoi-shape", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 4.5, "circle-color": "#0a0d12", "circle-stroke-color": "#5eead4", "circle-stroke-width": 2 } });
    m.addSource("query-result", { type: "geojson", data: empty });
    m.addLayer({ id: "query-fill", type: "fill", source: "query-result", filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": "#c4b5fd", "fill-opacity": 0.28 } });
    m.addLayer({ id: "query-line", type: "line", source: "query-result", paint: { "line-color": "#c4b5fd", "line-width": 2 } });
    m.addLayer({ id: "query-pts", type: "circle", source: "query-result", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 5, "circle-color": "#c4b5fd", "circle-stroke-color": "#0a0d12", "circle-stroke-width": 1.5 } });
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
        el.innerHTML = `<b>${esc(p.facility_id)}</b><span>${p.changed_ha < 10 ? (Math.round(p.changed_ha * 10) / 10).toString() : Math.round(p.changed_ha)} ha changed</span>`;
        this.markers.push(new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat(pt).addTo(m));
      }
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
    this.compare.jumpTo({ center: this.main.getCenter(), zoom: this.main.getZoom(), bearing: this.main.getBearing(), pitch: this.main.getPitch() });
  }

  get swipeActive(): boolean {
    return this.swipe && this.layer.swipe;
  }

  setHiddenTypes(hidden: ReadonlySet<string>): void {
    this.hiddenTypes = new Set(hidden);
    this.apply();
  }

  private apply(): void {
    const def = this.layer;
    const swipe = this.swipeActive;
    for (const [m, side] of [[this.main, def.right], [this.compare, def.left]] as const) {
      for (const id of RASTER_IDS) {
        if (!m.getLayer(id)) continue;
        const opacity = side[id];
        m.setLayoutProperty(id, "visibility", opacity === undefined || (m === this.compare && !swipe) ? "none" : "visible");
        if (opacity !== undefined) m.setPaintProperty(id, "raster-opacity", opacity);
      }
      m.setLayoutProperty("fac-fill", "visibility", def.facilities ? "visible" : "none");
      m.setLayoutProperty("fac-line", "visibility", def.facilities ? "visible" : "none");
    }
    for (const mk of this.markers) mk.getElement().hidden = !def.facilities;

    const on = def.polygons !== "none";
    const fill = def.polygons === "fill";
    const binary = def.polygonStyle === "binary";
    const color = binary ? BINARY_COLOR : colorByType;
    const extrudeOn = this.extrude && this.oblique && on;
    const filter: FilterSpecification | null = this.hiddenTypes.size ? (["!", ["in", ["get", "change_type"], ["literal", [...this.hiddenTypes]]]] as unknown as FilterSpecification) : null;
    for (const m of this.maps) {
      m.setLayoutProperty("changes-fill", "visibility", on && !extrudeOn ? "visible" : "none");
      m.setLayoutProperty("changes-line", "visibility", on ? "visible" : "none");
      m.setLayoutProperty("changes-extrude", "visibility", extrudeOn ? "visible" : "none");
      m.setPaintProperty("changes-fill", "fill-color", color);
      m.setPaintProperty("changes-fill", "fill-opacity", fill ? (["case", hovered, 0.72, binary ? 0.6 : 0.46] as unknown as ExpressionSpecification) : (["case", hovered, 0.32, 0.015] as unknown as ExpressionSpecification));
      m.setPaintProperty("changes-line", "line-color", binary ? (["case", selected, "#ffffff", BINARY_COLOR] as unknown as ExpressionSpecification) : (["case", selected, "#ffffff", colorByType] as unknown as ExpressionSpecification));
      m.setPaintProperty("changes-line", "line-opacity", fill ? 0.95 : 0.85);
      m.setPaintProperty("changes-extrude", "fill-extrusion-color", color);
      for (const id of CHANGE_LAYERS) m.setFilter(id, filter);
    }
  }

  /* ---------- dataset ---------- */

  setDataset(ds: Dataset): void {
    this.dataset = ds;
    for (const m of this.maps) {
      for (const k of ["before", "after", "after_normalized", "change_intensity"] as const) {
        (m.getSource(k) as maplibregl.ImageSource).updateImage({ url: ds.rasters[k].url, coordinates: corners(ds.rasters[k].bounds) });
      }
      (m.getSource("changes") as maplibregl.GeoJSONSource).setData(ds.changes);
    }
    this.hover = null;
    this.select(null);
  }

  /* ---------- oblique view ---------- */

  setOblique(on: boolean, bearing = this.bearing): void {
    this.oblique = on;
    this.bearing = bearing;
    if (on) this.main.dragRotate.enable();
    else this.main.dragRotate.disable();
    this.main.touchPitch.enable();
    this.apply();
    this.main.easeTo({ pitch: on ? 60 : 0, bearing: on ? bearing : 0, duration: prefersReducedMotion() ? 0 : 900, easing: easeOut });
  }

  setBearing(deg: number): void {
    this.bearing = deg;
    if (this.oblique) this.main.easeTo({ bearing: deg, duration: 0 });
  }

  setExtrude(on: boolean): void {
    this.extrude = on;
    this.apply();
  }

  /* ---------- interaction ---------- */

  private bindInteractions(): void {
    const m = this.main;
    m.on("mousemove", "changes-fill", (e) => {
      if (this.draw) return;
      m.getCanvas().style.cursor = "pointer";
      const id = e.features?.[0]?.properties["fid"] as number | undefined;
      if (id !== undefined) this.setHover(id);
    });
    m.on("mouseleave", "changes-fill", () => {
      m.getCanvas().style.cursor = this.draw ? "crosshair" : "";
      this.setHover(null);
    });
    m.on("click", (e) => {
      if (this.draw) {
        this.draw.push([e.lngLat.lng, e.lngLat.lat]);
        this.renderDraft();
        return;
      }
      const hit = m.queryRenderedFeatures(e.point, { layers: this.layer.polygons === "none" ? [] : [this.extrude && this.oblique ? "changes-extrude" : "changes-fill"] })[0];
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
    m.on("dblclick", () => {
      if (!this.draw) return;
      this.draw.pop(); // the second click of the double-click added the same vertex again
      this.finishDraw();
    });
    m.on("mousemove", (e) => {
      if (!this.draw) return;
      this.cursor = [e.lngLat.lng, e.lngLat.lat];
      this.renderDraft();
    });
  }

  private setHover(id: number | null): void {
    if (id === this.hover) return;
    for (const m of this.maps) {
      if (this.hover !== null) m.setFeatureState({ source: "changes", id: this.hover }, { hover: false });
      if (id !== null) m.setFeatureState({ source: "changes", id }, { hover: true });
    }
    this.hover = id;
  }

  select(id: number | null): void {
    if (this.selection === id) return;
    for (const m of this.maps) {
      if (this.selection !== null) m.setFeatureState({ source: "changes", id: this.selection }, { selected: false });
      if (id !== null) m.setFeatureState({ source: "changes", id }, { selected: true });
    }
    this.selection = id;
  }

  /* ---------- AOI drawing ---------- */

  get drawing(): boolean {
    return this.draw !== null;
  }

  startDraw(): void {
    this.draw = [];
    this.cursor = null;
    this.setAoiShape(null);
    this.main.doubleClickZoom.disable();
    this.main.getCanvas().style.cursor = "crosshair";
    this.renderDraft();
  }

  cancelDraw(): void {
    if (!this.draw) return;
    this.draw = null;
    this.endDrawMode();
    this.opts.onDrawChange({ points: 0, active: false });
  }

  undoPoint(): void {
    if (!this.draw) return;
    this.draw.pop();
    this.renderDraft();
  }

  finishDraw(): void {
    const pts = this.draw;
    if (!pts) return;
    const ring = [...pts, pts[0] as Position];
    this.draw = null;
    this.endDrawMode();
    if (pts.length >= 3 && isValidAoi(ring)) {
      this.setAoiShape({ type: "Polygon", coordinates: [ring] });
      this.opts.onDrawChange({ points: pts.length, active: false });
      this.opts.onDrawDone(ring);
    } else {
      this.setAoiShape(null);
      this.opts.onDrawChange({ points: 0, active: false });
    }
  }

  private endDrawMode(): void {
    this.main.doubleClickZoom.enable();
    this.main.getCanvas().style.cursor = "";
    this.setAoiShape(null);
  }

  private renderDraft(): void {
    const pts = this.draw ?? [];
    const path = this.cursor ? [...pts, this.cursor] : pts;
    const features: Feature[] = pts.map((p) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: p } }));
    if (path.length >= 3) features.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[...path, path[0] as Position]] } });
    else if (path.length === 2) features.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: path } });
    (this.main.getSource("aoi-shape") as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features });
    this.opts.onDrawChange({ points: pts.length, active: true });
  }

  setAoiShape(poly: Polygon | null): void {
    (this.main.getSource("aoi-shape") as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features: poly ? [{ type: "Feature", properties: {}, geometry: poly }] : [] });
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
