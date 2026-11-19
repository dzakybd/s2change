import type { FilterSpecification } from "maplibre-gl";
import type { ChangeCollection, Direction, Filters } from "./types";
import { DIRECTIONS } from "./types";

const M2_PER_HA = 10_000;

export interface Stats {
  count: number;
  areaHa: number;
  byDirection: Record<Direction, number>;
}

export function summarize(changes: ChangeCollection, f: Filters): Stats {
  const stats: Stats = {
    count: 0,
    areaHa: 0,
    byDirection: { brightening: 0, darkening: 0, "spectral shift": 0 },
  };
  for (const { properties: p } of changes.features) {
    if (p.confidence < f.minConfidence || p.area_m2 < f.minAreaHa * M2_PER_HA) continue;
    if (!f.directions.has(p.change_direction)) continue;
    stats.count += 1;
    stats.areaHa += p.area_m2 / M2_PER_HA;
    stats.byDirection[p.change_direction] += 1;
  }
  return stats;
}

/** MapLibre expression equivalent to `summarize`'s predicate. */
export function toExpression(f: Filters): FilterSpecification {
  return [
    "all",
    [">=", ["get", "confidence"], f.minConfidence],
    [">=", ["get", "area_m2"], f.minAreaHa * M2_PER_HA],
    ["in", ["get", "change_direction"], ["literal", DIRECTIONS.filter((d) => f.directions.has(d))]],
  ];
}
