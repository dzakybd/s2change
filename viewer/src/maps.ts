import type { FeatureCollection, Polygon } from "geojson";
import * as maplibregl from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { FilterSpecification, MapGeoJSONFeature, StyleSpecification } from "maplibre-gl";
import { toExpression } from "./filters";
import type { Bounds, ChangeCollection, Filters, LayerState, Manifest, RasterKey } from "./types";
import { DIRECTION_COLORS, DIRECTIONS, RASTER_KEYS } from "./types";

// maplibre-gl 6 looks for its worker next to the main module, which a bundler moves; point it at the emitted asset.
maplibregl.setWorkerUrl(workerUrl);

const ESRI_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const ESRI_ATTRIBUTION =
  'Tiles &copy; <a href="https://www.esri.com/" target="_blank" rel="noopener">Esri</a> &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community';

const BASE_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    esri: { type: "raster", tiles: [ESRI_TILES], tileSize: 256, maxzoom: 18, attribution: ESRI_ATTRIBUTION },
  },
  layers: [{ id: "esri", type: "raster", source: "esri" }],
};

const RGB_KEYS: readonly RasterKey[] = ["before", "after", "after_normalized"];

const colorByDirection = [
  "match",
  ["get", "change_direction"],
  ...DIRECTIONS.flatMap((d) => [d, DIRECTION_COLORS[d]]),
  "#ffffff",
] as unknown as maplibregl.ExpressionSpecification;

/** Corner order MapLibre expects for an image source: NW, NE, SE, SW as [lon, lat]. */
function corners([[s, w], [n, e]]: Bounds): [[number, number], [number, number], [number, number], [number, number]] {
  return [
    [w, n],
    [e, n],
    [e, s],
    [w, s],
  ];
}

export function aoiBounds(aoi: FeatureCollection<Polygon>): maplibregl.LngLatBounds {
  const b = new maplibregl.LngLatBounds();
  for (const f of aoi.features) for (const ring of f.geometry.coordinates) for (const [x, y] of ring) b.extend([x as number, y as number]);
  return b;
}

export interface MapPairOptions {
  main: HTMLElement;
  compare: HTMLElement;
  manifest: Manifest;
  base: URL;
  changes: ChangeCollection;
  aoi: FeatureCollection<Polygon>;
  onFeatureClick: (feature: MapGeoJSONFeature, lngLat: maplibregl.LngLat) => void;
}

/**
 * Two synced maps. `main` is interactive and shows the "after" side; `compare` sits on top,
 * is clipped by CSS to the left of the swipe handle and shows the "before" side. Every other
 * layer (intensity, polygons, AOI) is mirrored on both so it stays visible across the split.
 */
export class MapPair {
  readonly main: maplibregl.Map;
  readonly compare: maplibregl.Map;
  private readonly maps: readonly maplibregl.Map[];
  private rasters = new Map<RasterKey, LayerState>();
  private swipe = false;
  private changesOn = true;
  private assignmentHidden = false;
  private hovered: number | null = null;

  constructor(private readonly opts: MapPairOptions) {
    const bounds = aoiBounds(opts.aoi);
    const common = { style: BASE_STYLE, bounds, fitBoundsOptions: { padding: 32 }, maxZoom: 19 } as const;
    this.main = new maplibregl.Map({ ...common, container: opts.main, attributionControl: { compact: true } });
    this.compare = new maplibregl.Map({ ...common, container: opts.compare, interactive: false, attributionControl: false });
    this.maps = [this.main, this.compare];
    this.main.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
    this.main.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    this.main.dragRotate.disable();
    this.main.touchZoomRotate.disableRotation();
    this.main.on("move", () => {
      this.compare.jumpTo({ center: this.main.getCenter(), zoom: this.main.getZoom(), bearing: this.main.getBearing(), pitch: this.main.getPitch() });
    });
  }

  /** Resolves once both maps have their style and every layer is in place. */
  async init(initial: Record<RasterKey, LayerState>, filters: Filters): Promise<void> {
    await Promise.all(this.maps.map((m) => (m.loaded() ? Promise.resolve() : new Promise<void>((r) => m.once("load", () => r())))));
    for (const m of this.maps) this.addLayers(m);
    for (const k of RASTER_KEYS) this.rasters.set(k, initial[k]);
    this.applyRasters();
    this.setFilters(filters);
    this.bindInteractions();
  }

  private addLayers(m: maplibregl.Map): void {
    const { manifest, base, changes, aoi } = this.opts;
    for (const key of RASTER_KEYS) {
      const entry = manifest.rasters[key];
      m.addSource(key, { type: "image", url: new URL(entry.url, base).href, coordinates: corners(entry.bounds) });
      m.addLayer({
        id: key,
        type: "raster",
        source: key,
        layout: { visibility: "none" },
        paint: { "raster-fade-duration": 0, "raster-resampling": "linear", "raster-opacity": 1 },
      });
    }
    m.addSource("aoi", { type: "geojson", data: aoi });
    m.addLayer({ id: "aoi", type: "line", source: "aoi", paint: { "line-color": "#f8fafc", "line-width": 1.5, "line-dasharray": [3, 2], "line-opacity": 0.8 } });
    m.addSource("changes", { type: "geojson", data: changes, promoteId: "fid" });
    m.addLayer({
      id: "changes-fill",
      type: "fill",
      source: "changes",
      paint: { "fill-color": colorByDirection, "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.7, 0.35] },
    });
    m.addLayer({
      id: "changes-line",
      type: "line",
      source: "changes",
      paint: { "line-color": colorByDirection, "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 3, 1.25] },
    });
  }

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
    m.on("click", "changes-fill", (e) => {
      const f = e.features?.[0];
      if (f) this.opts.onFeatureClick(f, e.lngLat);
    });
  }

  private setHover(id: number | null): void {
    if (id === this.hovered) return;
    for (const m of this.maps) {
      if (this.hovered !== null) m.setFeatureState({ source: "changes", id: this.hovered }, { hover: false });
      if (id !== null) m.setFeatureState({ source: "changes", id }, { hover: true });
    }
    this.hovered = id;
  }

  setRaster(key: RasterKey, state: LayerState): void {
    this.rasters.set(key, state);
    this.applyRasters();
  }

  setSwipe(on: boolean): void {
    this.swipe = on;
    this.applyRasters();
    this.compare.resize();
    this.compare.jumpTo({ center: this.main.getCenter(), zoom: this.main.getZoom() });
  }

  /** Swipe pins RGB layers: main shows after_normalized, compare shows before, both opaque. */
  private applyRasters(): void {
    for (const key of RASTER_KEYS) {
      const s = this.rasters.get(key);
      if (!s) continue;
      let main = s;
      let compare = s;
      if (this.assignmentHidden) {
        main = { ...s, visible: false };
        compare = main;
      } else if (this.swipe && RGB_KEYS.includes(key)) {
        main = { visible: key === "after_normalized", opacity: 1 };
        compare = { visible: key === "before", opacity: 1 };
      }
      this.paintRaster(this.main, key, main);
      this.paintRaster(this.compare, key, compare);
    }
  }

  private paintRaster(m: maplibregl.Map, key: RasterKey, s: LayerState): void {
    m.setLayoutProperty(key, "visibility", s.visible ? "visible" : "none");
    m.setPaintProperty(key, "raster-opacity", s.opacity);
  }

  setChangesVisible(visible: boolean): void {
    this.changesOn = visible;
    this.applyChanges();
  }

  /** The timeline tab hides the assignment rasters and polygons so its own frames are the only imagery. */
  setAssignmentHidden(hidden: boolean): void {
    this.assignmentHidden = hidden;
    this.applyRasters();
    this.applyChanges();
  }

  private applyChanges(): void {
    const v = this.changesOn && !this.assignmentHidden ? "visible" : "none";
    for (const m of this.maps) for (const id of ["changes-fill", "changes-line"]) m.setLayoutProperty(id, "visibility", v);
  }

  setFilters(f: Filters): void {
    const expr: FilterSpecification = toExpression(f);
    for (const m of this.maps) for (const id of ["changes-fill", "changes-line"]) m.setFilter(id, expr);
  }
}
