import type { FeatureCollection, Polygon } from "geojson";

/** Leaflet-style bounds: [[south, west], [north, east]]. */
export type Bounds = [[number, number], [number, number]];

export const RASTER_KEYS = ["before", "after", "after_normalized", "change_intensity"] as const;
export type RasterKey = (typeof RASTER_KEYS)[number];

export interface RasterEntry {
  url: string;
  label: string;
  bounds: Bounds;
}

export interface Manifest {
  date_before: string;
  date_after: string;
  rasters: Record<RasterKey, RasterEntry>;
  /** Changed area in hectares per method. */
  summary: Record<string, number>;
  vectors: { changes: string; aoi: string };
}

export const DIRECTIONS = ["brightening", "darkening", "spectral shift"] as const;
export type Direction = (typeof DIRECTIONS)[number];

export const DIRECTION_COLORS: Record<Direction, string> = {
  brightening: "#f59e0b",
  darkening: "#3b82f6",
  "spectral shift": "#a855f7",
};

export interface ChangeProps {
  /** Sequential id added at load time (used for hover feature-state). */
  fid: number;
  area_m2: number;
  confidence: number;
  change_direction: Direction;
  d_brightness: number;
  in_disturbed_area: boolean;
  zone: number;
  strong_fraction: number;
  mean_p_change: number;
}

export type ChangeCollection = FeatureCollection<Polygon, ChangeProps>;

export interface Filters {
  minConfidence: number;
  minAreaHa: number;
  directions: ReadonlySet<Direction>;
}

export interface LayerState {
  visible: boolean;
  opacity: number;
}

/** One month of the monthly timeline. Paths in `rgb` / `change` are relative to the data base (docs/web/). */
export interface MonthStats {
  ndvi_mean: number | null;
  nbr_mean: number | null;
  mndwi_mean: number | null;
  bsi_mean: number | null;
  bare_ha: number | null;
  water_ha: number | null;
  footprint_ha: number | null;
  changed_ha: number | null;
  changed_ha_in_footprint: number | null;
}

export interface MonthEntry {
  month: string;
  status: "ok" | "insufficient";
  n_items: number;
  clear_fraction: number;
  rgb: string | null;
  change: string | null;
  prev_month: string | null;
  stats: MonthStats;
}

export interface MonthlyManifest {
  bounds: Bounds;
  months: MonthEntry[];
}

/** [west, south, east, north] */
export type BBox = [number, number, number, number];

export type AgentAction =
  | { type: "fly_to"; bbox: BBox }
  | { type: "highlight"; run_id: string; ids: number[] }
  | { type: "show_run"; run_id: string }
  | { type: "set_month"; month: string };

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
