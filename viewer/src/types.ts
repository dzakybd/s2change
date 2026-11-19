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
