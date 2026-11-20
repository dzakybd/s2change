import type { Feature, Geometry, Polygon, Position } from "geojson";
import type { AoiStats, ChangeCollection } from "../types";

/** Small geometry helpers (no turf): point-in-polygon, polygon area, and AOI statistics from the loaded polygons. */

type Ring = Position[];
const M_PER_DEG_LAT = 111_320;

export function pointInRing(x: number, y: number, ring: Ring): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i] as [number, number];
    const [xj, yj] = ring[j] as [number, number];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** Polygon with holes: inside the outer ring and outside every hole. */
export function pointInPolygon(x: number, y: number, rings: Ring[]): boolean {
  const [outer, ...holes] = rings;
  return !!outer && pointInRing(x, y, outer) && !holes.some((h) => pointInRing(x, y, h));
}

export function polygonsOf(g: Geometry): Ring[][] {
  if (g.type === "Polygon") return [g.coordinates as Ring[]];
  if (g.type === "MultiPolygon") return g.coordinates as Ring[][];
  return [];
}

function bboxOf(rings: Ring[]): [number, number, number, number] {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const [x, y] of rings[0] ?? []) {
    w = Math.min(w, x as number);
    e = Math.max(e, x as number);
    s = Math.min(s, y as number);
    n = Math.max(n, y as number);
  }
  return [w, s, e, n];
}

/** Area in hectares of a lon/lat polygon, by shoelace in a local equirectangular projection (fine below ~50 km). */
export function areaHa(rings: Ring[]): number {
  const ring0 = rings[0];
  if (!ring0?.length) return 0;
  const lat0 = ring0.reduce((a, p) => a + (p[1] as number), 0) / ring0.length;
  const kx = M_PER_DEG_LAT * Math.cos((lat0 * Math.PI) / 180);
  const shoelace = (r: Ring) => {
    let a = 0;
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const [xi, yi] = r[i] as [number, number];
      const [xj, yj] = r[j] as [number, number];
      a += (xj * kx) * (yi * M_PER_DEG_LAT) - (xi * kx) * (yj * M_PER_DEG_LAT);
    }
    return Math.abs(a) / 2;
  };
  const [outer, ...holes] = rings;
  return (shoelace(outer as Ring) - holes.reduce((s, h) => s + shoelace(h), 0)) / 10_000;
}

export function isValidAoi(ring: Ring): boolean {
  return ring.length >= 4 && areaHa([ring]) > 0.5;
}

/**
 * Changed area inside a drawn polygon, estimated from the change polygons already in the browser.
 * Each change polygon is sampled on a 10 m grid (the Sentinel-2 pixel size) and a cell counts when its centre
 * lies in both the change polygon and the drawn polygon, so partially covered polygons are clipped properly.
 */
export function aoiStatsFromPolygons(aoi: Polygon, changes: ChangeCollection, stepM = 10): AoiStats {
  const aoiRings = aoi.coordinates as Ring[];
  const [aw, as, ae, an] = bboxOf(aoiRings);
  const lat0 = (as + an) / 2;
  const dLat = stepM / M_PER_DEG_LAT;
  const dLon = stepM / (M_PER_DEG_LAT * Math.cos((lat0 * Math.PI) / 180));
  const cellHa = (stepM * stepM) / 10_000;
  const byType: Record<string, number> = {};
  const byFacility: Record<string, number> = {};
  let total = 0;
  const touched = new Set<number>();

  for (const f of changes.features as Feature<Geometry, ChangeCollection["features"][number]["properties"]>[]) {
    for (const rings of polygonsOf(f.geometry)) {
      const [w, s, e, n] = bboxOf(rings);
      if (e < aw || w > ae || n < as || s > an) continue;
      let cells = 0;
      const x0 = Math.max(w, aw);
      const x1 = Math.min(e, ae);
      const y0 = Math.max(s, as);
      const y1 = Math.min(n, an);
      for (let y = y0 + dLat / 2; y < y1; y += dLat) {
        for (let x = x0 + dLon / 2; x < x1; x += dLon) {
          if (pointInPolygon(x, y, rings) && pointInPolygon(x, y, aoiRings)) cells++;
        }
      }
      if (!cells) continue;
      const hectares = cells * cellHa;
      total += hectares;
      touched.add(f.properties.fid);
      byType[f.properties.change_type] = (byType[f.properties.change_type] ?? 0) + hectares;
      const fac = f.properties.facility_id ?? "outside register";
      byFacility[fac] = (byFacility[fac] ?? 0) + hectares;
    }
  }
  const area = areaHa(aoiRings);
  const round = (o: Record<string, number>) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, Math.round(v * 10) / 10]));
  return {
    area_ha: Math.round(area * 10) / 10,
    changed_ha: Math.round(total * 10) / 10,
    changed_share: area > 0 ? total / area : 0,
    n_polygons: touched.size,
    by_change_type: round(byType),
    by_facility: round(byFacility),
    source: "browser",
  };
}

/** Centroid of the largest part of a (Multi)Polygon, for label placement. */
export function labelPoint(g: Geometry): [number, number] | null {
  let best: { a: number; c: [number, number] } | null = null;
  for (const rings of polygonsOf(g)) {
    const outer = rings[0];
    if (!outer || outer.length < 4) continue;
    let a = 0, cx = 0, cy = 0;
    for (let i = 0, j = outer.length - 1; i < outer.length; j = i++) {
      const [xi, yi] = outer[i] as [number, number];
      const [xj, yj] = outer[j] as [number, number];
      const k = xj * yi - xi * yj;
      a += k;
      cx += (xj + xi) * k;
      cy += (yj + yi) * k;
    }
    if (!a) continue;
    const area = Math.abs(a) / 2;
    const c: [number, number] = [cx / (3 * a), cy / (3 * a)];
    if (!best || area > best.a) best = { a: area, c };
  }
  return best?.c ?? null;
}
