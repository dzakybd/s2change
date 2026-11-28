import type { Feature, FeatureCollection, Point } from "geojson";
import * as maplibregl from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { ExpressionSpecification, FilterSpecification, MapGeoJSONFeature, StyleSpecification } from "maplibre-gl";
import { CHANGE_TYPES, esc, FACILITY_COLORS, FACILITY_FALLBACK, facilityColor, facilityIcon, fmtKm2, prefersReducedMotion, typeIcon, typeIconSvg } from "../domain";
import { RASTER_KEYS } from "../types";
import type { AoiCollection, Bounds, ChangeCollection, ChangeProps, Dataset, FacilityProps } from "../types";
import { featureBounds } from "../overlays";
import { LABEL_COUNT, labelTargets, labelText } from "./changes";
import { labelPoint } from "./geo";
import type { LayerDef } from "./layers";

// maplibre-gl 6 looks for its worker next to the main module, which a bundler moves; point it at the emitted asset.
maplibregl.setWorkerUrl(workerUrl);

const ESRI_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const ESRI_ATTRIBUTION = 'Tiles &copy; <a href="https://www.esri.com/" target="_blank" rel="noopener">Esri</a>, Maxar, Earthstar Geographics';

// Esri has no imagery over the AOI past z17 (it serves "Map data not yet available" tiles), so Esri's z17 is overzoomed
// and Bing Aerial (real z18-19 imagery here) takes over from z18. Mapbox Satellite was cloudy and soft there.
const BING_TILES = [0, 1, 2, 3].map((n) => `https://ecn.t${n}.tiles.virtualearth.net/tiles/a{quadkey}.jpeg?g=1`);
const BING_ATTRIBUTION = 'Imagery &copy; <a href="https://www.microsoft.com/maps" target="_blank" rel="noopener">Microsoft</a>';

const BASE_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    esri: { type: "raster", tiles: [ESRI_TILES], tileSize: 256, maxzoom: 17, attribution: ESRI_ATTRIBUTION },
    bing: { type: "raster", tiles: BING_TILES, tileSize: 256, maxzoom: 19, attribution: BING_ATTRIBUTION },
  },
  layers: [
    { id: "background", type: "background", paint: { "background-color": "#e9edf2" } },
    { id: "esri", type: "raster", source: "esri", paint: { "raster-resampling": "linear", "raster-fade-duration": 120 } },
    { id: "bing", type: "raster", source: "bing", minzoom: 18, paint: { "raster-resampling": "linear", "raster-fade-duration": 120 } },
  ],
};

const CHANGE_LAYERS = ["changes-fill", "changes-line", "changes-extrude"] as const;
/** 2.5D view: tilt, and optionally polygons raised by confidence (a data encoding, not terrain). */
const OBLIQUE_PITCH = 60;
const EXTRUDE_MAX_M = 700;
const HEIGHT_BY_CONFIDENCE = ["interpolate", ["linear"], ["get", "confidence"], 0, 25, 1, EXTRUDE_MAX_M] as unknown as ExpressionSpecification;
const easeOut = (t: number): number => 1 - (1 - t) ** 4;

const colorByType = ["match", ["get", "change_type"], ...CHANGE_TYPES.flatMap((t) => [t.key, t.color]), "#cbd5e1"] as unknown as ExpressionSpecification;
/** Each change gets its type's pictogram at its centroid; the largest win where they would overlap. */
const ICON_PX = 24;
const ICON_KEYS = [...CHANGE_TYPES.map((t) => t.key), "other"];
const iconImage = ["coalesce", ["image", ["concat", "ti-", ["get", "change_type"]]], ["image", "ti-other"]] as unknown as ExpressionSpecification;
const NOT_NOISE = ["!=", ["get", "change_type"], "noise_or_artifact"] as unknown as FilterSpecification;
/** Where a label's icon centre sits from the label's left edge (its left padding plus half the icon). */
const LABEL_ICON_X = 3 + ICON_PX / 2;

function changePoints(changes: ChangeCollection): FeatureCollection<Point, ChangeProps> {
  const features = changes.features.flatMap((f) => {
    const pt = labelPoint(f.geometry);
    return pt ? [{ type: "Feature" as const, id: f.properties.fid, geometry: { type: "Point" as const, coordinates: pt }, properties: f.properties }] : [];
  });
  return { type: "FeatureCollection", features };
}

async function addTypeIcons(m: maplibregl.Map): Promise<void> {
  const ratio = 2;
  await Promise.all(
    ICON_KEYS.map(async (key) => {
      const img = new Image(ICON_PX * ratio, ICON_PX * ratio);
      img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(typeIconSvg(key, ICON_PX * ratio))}`;
      await img.decode();
      if (!m.hasImage(`ti-${key}`)) m.addImage(`ti-${key}`, img, { pixelRatio: ratio });
    }),
  );
}

const colorByFacility = ["match", ["get", "facility_id"], ...Object.entries(FACILITY_COLORS).flat(), FACILITY_FALLBACK] as unknown as ExpressionSpecification;
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
  private oblique = false;
  private showChanges = true;
  private extrude = false;
  private changeLabels: { pt: [number, number]; w: number; markers: maplibregl.Marker[] }[] = [];

  constructor(private readonly opts: StageOptions) {
    this.layer = opts.initialLayer;
    this.swipe = opts.initialSwipe;
    this.dataset = opts.dataset;
    // A phone is too narrow for the whole study area: start on the mine itself (the facility outlines).
    const phone = opts.main.clientWidth < 720;
    const start = (phone ? featureBounds(opts.dataset.facilities.features as Feature[]) : null) ?? featureBounds(opts.aoi.features);
    const common = { style: BASE_STYLE, bounds: start ?? undefined, fitBoundsOptions: { padding: phone ? 12 : 24 }, maxZoom: 19, maxPitch: 70 } as const;
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
    await Promise.all(this.maps.map(addTypeIcons));
    for (const m of this.maps) this.addLayers(m);
    this.addFacilityLabels();
    this.buildChangeLabels();
    this.apply();
    this.bindInteractions();
  }

  private addLayers(m: maplibregl.Map): void {
    const { aoi } = this.opts;
    for (const id of RASTER_KEYS) {
      const { url, bounds } = this.dataset.rasters[id];
      m.addSource(id, { type: "image", url, coordinates: corners(bounds) });
      m.addLayer({ id, type: "raster", source: id, layout: { visibility: "none" }, paint: { "raster-fade-duration": 0, "raster-resampling": "linear", "raster-opacity": 1 } });
    }
    const base = this.dataset.rasters.baseline;
    if (base) {
      m.addSource("baseline", { type: "image", url: base.url, coordinates: corners(base.bounds) });
      m.addLayer({ id: "baseline", type: "raster", source: "baseline", layout: { visibility: "none" }, paint: { "raster-fade-duration": 0, "raster-resampling": "nearest" } });
    }
    m.addSource("facilities", { type: "geojson", data: this.dataset.facilities });
    m.addLayer({ id: "fac-fill", type: "fill", source: "facilities", layout: { visibility: "none" }, paint: { "fill-color": colorByFacility, "fill-opacity": ["case", hovered, 0.3, 0.14] } });
    m.addLayer({ id: "fac-line", type: "line", source: "facilities", layout: { visibility: "none" }, paint: { "line-color": colorByFacility, "line-width": 2, "line-opacity": 0.95 } });
    m.addSource("aoi", { type: "geojson", data: aoi });
    m.addLayer({ id: "aoi", type: "line", source: "aoi", paint: { "line-color": "#f8fafc", "line-width": 1, "line-dasharray": [3, 3], "line-opacity": 0.55 } });
    m.addSource("changes", { type: "geojson", data: this.dataset.changes, promoteId: "fid" });
    m.addLayer({ id: "changes-fill", type: "fill", source: "changes", paint: { "fill-color": colorByType, "fill-opacity": 0.4 } });
    m.addLayer({
      id: "changes-extrude",
      type: "fill-extrusion",
      source: "changes",
      layout: { visibility: "none" },
      paint: { "fill-extrusion-color": colorByType, "fill-extrusion-height": HEIGHT_BY_CONFIDENCE, "fill-extrusion-base": 0, "fill-extrusion-opacity": 0.88, "fill-extrusion-vertical-gradient": true },
    });
    m.addLayer({
      id: "changes-line",
      type: "line",
      source: "changes",
      paint: { "line-color": ["case", selected, "#ffffff", colorByType], "line-width": ["case", selected, 3, hovered, 2.6, 1.2], "line-opacity": 0.95 },
    });
    m.addSource("change-points", { type: "geojson", data: changePoints(this.dataset.changes) });
    m.addLayer({
      id: "changes-icon",
      type: "symbol",
      source: "change-points",
      filter: NOT_NOISE,
      layout: {
        "icon-image": iconImage,
        "icon-size": ["interpolate", ["linear"], ["zoom"], 10, 0.7, 14, 1],
        "symbol-sort-key": ["-", 0, ["get", "area_m2"]],
        "icon-padding": 1,
        "icon-pitch-alignment": "viewport",
      },
    });
  }

  private addFacilityLabels(): void {
    for (const mk of this.markers) mk.remove();
    this.markers = [];
    for (const f of this.dataset.facilities.features) {
      const pt = labelPoint(f.geometry);
      if (!pt) continue;
      const p = f.properties;
      for (const m of this.maps) {
        const el = document.createElement("div");
        el.className = "fac-label";
        el.hidden = true;
        el.innerHTML = `${facilityIcon(p.class, 20, facilityColor(p.facility_id))}<b>${esc(p.facility_id)}</b><span>${fmtKm2(p.changed_ha)} changed</span>`;
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
        el.innerHTML = `${typeIcon(p.change_type, ICON_PX)}<span>${esc(text)}</span>`;
        // The label's own icon sits exactly over the map icon at the centroid and the name runs off to the right.
        return new maplibregl.Marker({ element: el, anchor: "left", offset: [-LABEL_ICON_X, 0] }).setLngLat(pt).addTo(m);
      });
      this.changeLabels.push({ pt, w: text.length * 6.4 + 44, markers });
    }
    this.layoutChangeLabels();
  }

  /** Show labels largest-first and drop any that would overlap one already shown, so zooming out thins them instead of piling them up. */
  private layoutChangeLabels(): void {
    const on = !!this.layer.typed && this.showChanges;
    const placed: { x0: number; x1: number; y0: number; y1: number }[] = [];
    const limit = this.main.getContainer().clientWidth < 720 ? 3 : LABEL_COUNT; // a phone screen has room for few
    for (const l of this.changeLabels) {
      let show = on && placed.length < limit;
      if (show) {
        const { x, y } = this.main.project(l.pt);
        const box = { x0: x - LABEL_ICON_X, x1: x - LABEL_ICON_X + l.w, y0: y - 14, y1: y + 14 };
        show = !placed.some((o) => box.x0 < o.x1 + 6 && box.x1 > o.x0 - 6 && box.y0 < o.y1 + 4 && box.y1 > o.y0 - 4);
        if (show) placed.push(box);
      }
      for (const mk of l.markers) mk.getElement().hidden = !show;
    }
  }

  /* Layer state */

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
    this.compare.jumpTo({ center: this.main.getCenter(), zoom: this.main.getZoom(), bearing: this.main.getBearing(), pitch: this.main.getPitch() });
  }

  get swipeActive(): boolean {
    return this.swipe && this.layer.swipe;
  }

  /** Show or hide every change layer, to see the imagery underneath. */
  setChangesVisible(on: boolean): void {
    this.showChanges = on;
    this.apply();
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
      if (m.getLayer("baseline")) m.setLayoutProperty("baseline", "visibility", def.baseline && this.dataset.nominal ? "visible" : "none");
      m.setLayoutProperty("fac-fill", "visibility", def.facilities ? "visible" : "none");
      m.setLayoutProperty("fac-line", "visibility", def.facilities ? "visible" : "none");
    }
    for (const mk of this.markers) mk.getElement().hidden = !def.facilities;
    this.layoutChangeLabels();

    const fill = def.polygons === "fill";
    const filter: FilterSpecification | null = this.hiddenTypes.size ? (["!", ["in", ["get", "change_type"], ["literal", [...this.hiddenTypes]]]] as unknown as FilterSpecification) : null;
    const raised = this.oblique && this.extrude;
    for (const m of this.maps) {
      const vis = (on: boolean) => (on && this.showChanges ? "visible" : "none");
      m.setLayoutProperty("changes-fill", "visibility", vis(!raised));
      m.setLayoutProperty("changes-extrude", "visibility", vis(raised));
      m.setLayoutProperty("changes-line", "visibility", vis(true));
      m.setPaintProperty("changes-fill", "fill-opacity", fill ? (["case", hovered, 0.72, 0.46] as unknown as ExpressionSpecification) : (["case", hovered, 0.32, 0.015] as unknown as ExpressionSpecification));
      m.setPaintProperty("changes-line", "line-opacity", fill ? 0.95 : 0.85);
      for (const id of CHANGE_LAYERS) m.setFilter(id, filter);
      m.setFilter("changes-icon", filter ? (["all", NOT_NOISE, filter] as unknown as FilterSpecification) : NOT_NOISE);
      m.setLayoutProperty("changes-icon", "visibility", vis(def.id !== "intensity" && !def.facilities && !def.baseline));
    }
  }

  /* 2.5D view */

  get isOblique(): boolean {
    return this.oblique;
  }

  /** Tilt the camera (and allow rotating it) or return to the flat, north-up view. */
  setOblique(on: boolean, bearing: number): void {
    this.oblique = on;
    if (on) this.main.dragRotate.enable();
    else this.main.dragRotate.disable();
    this.apply();
    this.main.easeTo({ pitch: on ? OBLIQUE_PITCH : 0, bearing: on ? bearing : 0, duration: prefersReducedMotion() ? 0 : 700, easing: easeOut });
  }

  setBearing(deg: number): void {
    if (this.oblique) this.main.jumpTo({ bearing: deg });
  }

  setExtrude(on: boolean): void {
    this.extrude = on;
    this.apply();
  }

  /* Dataset */

  setDataset(ds: Dataset): void {
    this.dataset = ds;
    for (const m of this.maps) {
      for (const k of RASTER_KEYS) {
        (m.getSource(k) as maplibregl.ImageSource).updateImage({ url: ds.rasters[k].url, coordinates: corners(ds.rasters[k].bounds) });
      }
      (m.getSource("changes") as maplibregl.GeoJSONSource).setData(ds.changes);
      (m.getSource("change-points") as maplibregl.GeoJSONSource).setData(changePoints(ds.changes));
      (m.getSource("facilities") as maplibregl.GeoJSONSource).setData(ds.facilities);
    }
    this.addFacilityLabels();
    for (const mk of this.markers) mk.getElement().hidden = !this.layer.facilities;
    this.hover = null;
    this.select(null);
    this.buildChangeLabels();
  }

  /* Interaction */

  private bindInteractions(): void {
    const m = this.main;
    for (const layer of ["changes-fill", "changes-extrude", "changes-icon"]) {
      m.on("mousemove", layer, (e) => {
        m.getCanvas().style.cursor = "pointer";
        const id = e.features?.[0]?.properties["fid"] as number | undefined;
        if (id !== undefined) this.setHover(id);
      });
      m.on("mouseleave", layer, () => {
        m.getCanvas().style.cursor = "";
        this.setHover(null);
      });
    }
    m.on("click", (e) => {
      const hit = m.queryRenderedFeatures(e.point, { layers: ["changes-icon", this.oblique && this.extrude ? "changes-extrude" : "changes-fill"] })[0];
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

  /* Fitting */

  fit(b: maplibregl.LngLatBounds, opts: { padding?: maplibregl.PaddingOptions | number; maxZoom?: number } = {}): void {
    this.main.fitBounds(b, { padding: opts.padding ?? 80, maxZoom: opts.maxZoom ?? 17, duration: prefersReducedMotion() ? 0 : 800, easing: easeOut });
  }

  resize(): void {
    for (const m of this.maps) m.resize();
  }
}
