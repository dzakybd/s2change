import type { Feature, FeatureCollection, Geometry } from "geojson";
import * as maplibregl from "maplibre-gl";
import type { ChangeCollection } from "./types";

export type PolygonCollection = FeatureCollection<Geometry, Record<string, unknown> | null>;

export function featureBounds(features: Feature[]): maplibregl.LngLatBounds | null {
  const b = new maplibregl.LngLatBounds();
  const visit = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number" && typeof c[1] === "number") b.extend([c[0], c[1]]);
    else if (Array.isArray(c)) c.forEach(visit);
  };
  for (const f of features) visit((f.geometry as { coordinates?: unknown } | null)?.coordinates);
  return b.isEmpty() ? null : b;
}

/** Id of a feature in a run: `properties.id`, then the GeoJSON `id`, then `properties.fid`. */
function featureId(f: Feature, index: number): number {
  const props = (f.properties ?? {}) as Record<string, unknown>;
  for (const v of [props["id"], f.id, props["fid"]]) if (typeof v === "number" || (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v)))) return Number(v);
  return index;
}

/**
 * Agent-driven polygon layers: a "run" layer for runs other than the loaded assignment result, and a
 * highlight layer drawn on top of whatever is shown. Ids refer to feature ids within a run.
 */
export class RunOverlay {
  private readonly cache = new Map<string, PolygonCollection>();

  constructor(
    private readonly map: maplibregl.Map,
    private readonly assignment: ChangeCollection,
  ) {
    const empty: PolygonCollection = { type: "FeatureCollection", features: [] };
    map.addSource("run", { type: "geojson", data: empty });
    map.addSource("highlight", { type: "geojson", data: empty });
    map.addLayer({ id: "run-fill", type: "fill", source: "run", paint: { "fill-color": "#22d3ee", "fill-opacity": 0.28 } });
    map.addLayer({ id: "run-line", type: "line", source: "run", paint: { "line-color": "#22d3ee", "line-width": 1.25 } });
    map.addLayer({ id: "highlight-fill", type: "fill", source: "highlight", paint: { "fill-color": "#fde047", "fill-opacity": 0.3 } });
    map.addLayer({ id: "highlight-casing", type: "line", source: "highlight", paint: { "line-color": "#0b1220", "line-width": 6, "line-opacity": 0.6 } });
    map.addLayer({ id: "highlight-line", type: "line", source: "highlight", paint: { "line-color": "#fde047", "line-width": 3 } });
  }

  /** Ids of the assignment result already in the viewer, shown without a network request. */
  assignmentFeatures(ids: number[]): Feature[] {
    const want = new Set(ids);
    return this.assignment.features.filter((f) => want.has(f.properties.fid));
  }

  async loadRun(runId: string, fetchRun: (id: string) => Promise<PolygonCollection>): Promise<PolygonCollection> {
    const hit = this.cache.get(runId);
    if (hit) return hit;
    const fc = await fetchRun(runId);
    this.cache.set(runId, fc);
    return fc;
  }

  selectIds(fc: PolygonCollection, ids: number[]): Feature[] {
    const want = new Set(ids);
    return fc.features.filter((f, i) => want.has(featureId(f, i)));
  }

  showRun(fc: PolygonCollection | null): void {
    (this.map.getSource("run") as maplibregl.GeoJSONSource).setData(fc ?? { type: "FeatureCollection", features: [] });
  }

  setHighlight(features: Feature[]): void {
    (this.map.getSource("highlight") as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features });
  }
}
