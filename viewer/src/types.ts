import type { FeatureCollection, Geometry, Polygon } from "geojson";

/** Leaflet-style bounds: [[south, west], [north, east]]. */
export type Bounds = [[number, number], [number, number]];

/** [west, south, east, north] */
type BBox = [number, number, number, number];

export const RASTER_KEYS = ["before", "after_normalized", "change_intensity", "sr_before", "sr_after"] as const;
export type RasterKey = (typeof RASTER_KEYS)[number];

interface RasterEntry {
  url: string;
  label: string;
  bounds: Bounds;
}

export interface Manifest {
  date_before: string;
  date_after: string;
  rasters: Record<RasterKey, RasterEntry> & { baseline?: RasterEntry };
  /** Changed area in hectares per method. */
  summary: Record<string, number>;
  vectors: { changes: string; facilities: string; aoi: string };
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
  /** Plain-language fields; absent on features stored before they were added. */
  headline?: string;
  location?: string;
  evidence?: string;
  what_to_check?: string;
  description?: string;
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

/** What the map shows: the provided pair (two-pass mosaics) or the true single passes, both static exports. */
export interface Dataset {
  id: string;
  dates: { before: string; after: string };
  /** True for the provided pair, whose "dates" are nominal labels of two-scene mosaics. */
  nominal: boolean;
  rasters: Record<RasterKey, { url: string; bounds: Bounds }> & { baseline?: { url: string; bounds: Bounds } };
  changes: ChangeCollection;
  /** The facility register with this pair's change inside each facility. */
  facilities: FacilityCollection;
  /** Changed area in hectares per method, for static exports. */
  summary?: Record<string, number>;
}

export type AoiCollection = FeatureCollection<Polygon>;

export type AgentAction =
  | { type: "fly_to"; bbox: BBox }
  | { type: "highlight"; run_id: string; ids: number[] }
  | { type: "show_run"; run_id: string }
  | { type: "set_layer"; layer: string }
  | { type: "set_view"; swipe?: boolean; oblique?: boolean }
  | { type: "show_facility"; facility_id: string };

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

interface DbColumn {
  name: string;
  type: string;
}
interface DbLink {
  column: string;
  ref_table: string;
  ref_column: string;
  /** A real FOREIGN KEY constraint; false for a logical link (same key, not enforced). */
  enforced: boolean;
}
export interface DbTable {
  table: string;
  description: string;
  rows: number;
  primary_key: string[];
  links: DbLink[];
  columns: DbColumn[];
  geometry: { column: string; srid: number; type: string } | null;
}
/** GET /api/db/tables/{table}: one page of rows, in column order. */
export interface DbPage {
  table: string;
  columns: DbColumn[];
  rows: unknown[][];
  total: number;
  page: number;
  pages: number;
  size: number;
  sort: string | null;
  desc: boolean;
  elapsed_ms: number;
  geojson: FeatureCollection | null;
}
export interface DbConnection {
  postgres: string;
  postgis: string;
  postgis_full: string;
  host: string;
  database: string;
  user: string;
  uri_masked: string;
}

