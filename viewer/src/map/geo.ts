import type { Geometry, Position } from "geojson";

/** Small geometry helpers (no turf): polygon parts and label placement. */

type Ring = Position[];

export function polygonsOf(g: Geometry): Ring[][] {
  if (g.type === "Polygon") return [g.coordinates as Ring[]];
  if (g.type === "MultiPolygon") return g.coordinates as Ring[][];
  return [];
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
