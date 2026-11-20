import type { FeatureCollection, Geometry, Polygon } from "geojson";

/** Leaflet-style bounds: [[south, west], [north, east]]. */
export type Bounds = [[number, number], [number, number]];

/** [west, south, east, north] */
export type BBox = [number, number, number, number];

export const RASTER_KEYS = ["before", "after", "after_normalized", "change_intensity"] as const;
export type RasterKey = (typeof RASTER_KEYS)[number];
/** Raster layers on the map: the manifest rasters plus the optional redness pair. */
export type MapRaster = RasterKey | "redness_before" | "redness_after";

export interface RasterEntry {
  url: string;
  label: string;
  bounds: Bounds;
}

export interface Manifest {
  date_before: string;
  date_after: string;
  date_labels?: { before: string; after: string };
  rasters: Record<RasterKey, RasterEntry>;
  /** Changed area in hectares per method. */
  summary: Record<string, number>;
  vectors: { changes: string; facilities?: string; aoi: string };
}

export interface MineralsManifest {
  formula: string;
  legend: { min: number; max: number; colormap: string; label: string };
  layers: { before: RasterEntry; after: RasterEntry };
  caveat: string;
}

export interface ChangeProps {
  /** Sequential id added at load time (feature-state key). */
  fid: number;
  id?: number;
  area_m2: number;
  confidence: number;
  neglog10_p: number;
  change_direction: string;
  change_type: string;
  brief_category: string;
  explanation: string;
  facility_id: string | null;
  touches_aoi_edge: boolean;
}

export type ChangeCollection = FeatureCollection<Geometry, ChangeProps>;

export interface FacilityProps {
  facility_id: string;
  name: string;
  class: string;
  area_ha: number;
  changed_ha: number;
  changed_share: number;
  change_types: Record<string, number> | null;
  summary: string;
}
export type FacilityCollection = FeatureCollection<Geometry, FacilityProps>;

/** What the map shows: the provided pair, or a comparison the API computed for two other dates. */
export interface Dataset {
  id: string;
  dates: { before: string; after: string };
  /** True for the provided pair, whose "dates" are nominal labels of two-scene mosaics. */
  nominal: boolean;
  rasters: Record<RasterKey, { url: string; bounds: Bounds }>;
  changes: ChangeCollection;
}

export type AoiCollection = FeatureCollection<Polygon>;

/** Static exports under docs/web/overview.json (written by `python -m s2cd.site`). */
export interface Overview {
  generated: string;
  pair: {
    before: string;
    after: string;
    note: string;
    acquisitions: Record<string, { nominal: string; source_dates: string[]; source_items: string[] }>;
    compared_gap_days?: number[];
  };
  headline: {
    changed_ha: number;
    n_polygons: number;
    aoi_ha: number;
    changed_pct_of_aoi: number;
    baseline_ha: number;
    baseline_n_polygons: number;
    irmad_ha: number;
    share_in_mine_footprint: number;
    share_in_register_mine_classes: number;
    agreement_iou_with_baseline: number;
    n_zones: number;
    runtime_s: number;
  };
  change_type_ha: Record<string, number>;
  facilities: {
    facility_id: string;
    name: string;
    class: string;
    area_ha: number;
    changed_ha: number;
    changed_share: number;
    change_types: Record<string, number>;
    summary: string;
  }[];
  sensitivity: {
    far_low: number;
    mmu_ha: number;
    changed_ha: number;
    changed_pct_of_aoi: number;
    share_in_mine_footprint: number;
    share_in_register_mine_classes: number;
    is_default: boolean;
  }[];
  /** `{status: "pending"}` until data/processed/validation/accuracy.json exists; otherwise that file verbatim. */
  accuracy: Record<string, unknown>;
}

export type AgentAction =
  | { type: "fly_to"; bbox: BBox }
  | { type: "highlight"; run_id: string; ids: number[] }
  | { type: "show_run"; run_id: string };

export interface AgentChart {
  type: "bar" | "line";
  title?: string;
  x: (string | number)[];
  series: { name: string; y: (number | null)[] }[];
}

export interface AgentTable {
  title?: string;
  columns: string[];
  rows: (string | number | boolean | null)[][];
}

export interface AgentToolCall {
  name: string;
  args?: unknown;
}

export interface AgentResponse {
  answer: string;
  actions: AgentAction[];
  charts: AgentChart[];
  tables: AgentTable[];
  tool_calls: AgentToolCall[];
}

/* ---------- API payloads (docs/api.md) ---------- */

export interface DbColumn {
  name: string;
  type: string;
}
export interface DbTable {
  table: string;
  rows: number;
  columns: DbColumn[];
  geometry: { column: string; srid: number; type: string } | null;
}
export interface DbExample {
  title: string;
  description: string;
  sql: string;
}
export interface DbQueryResult {
  columns: string[];
  rows: unknown[][];
  truncated: boolean;
  elapsed_ms: number;
  geojson: FeatureCollection | null;
}

export interface AoiStats {
  area_ha: number;
  changed_ha: number;
  changed_share: number;
  n_polygons: number;
  by_change_type: Record<string, number>;
  by_facility: Record<string, number>;
  /** Where the numbers came from; set by the client. */
  source?: "api" | "browser";
}

export interface CompareDate {
  date: string;
  aoi_coverage: number;
  tile_cloud_max: number;
}
export interface CompareResult {
  pair: string;
  summary: { changed_ha: number; by_change_type?: Record<string, number>; by_facility?: Record<string, number> };
  changes_url: string;
  overlays: Record<"before" | "after" | "intensity", { url: string; bounds: Bounds }>;
}
export interface CompareJob {
  job_id?: string;
  status: "queued" | "running" | "done" | "error";
  message?: string;
  result?: CompareResult;
}
