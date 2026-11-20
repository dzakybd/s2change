import type { MapRaster } from "../types";

/**
 * What each entry of the layer menu shows. The map is two synced MapLibre maps: `right` is what the interactive map
 * shows, `left` is what the clipped map shows to the left of the swipe handle. Rasters are listed with their opacity.
 */
export type PolygonMode = "none" | "outline" | "fill";
export type PolygonStyle = "types" | "binary";

export interface LayerDef {
  id: string;
  label: string;
  hint: string;
  right: Partial<Record<MapRaster, number>>;
  left: Partial<Record<MapRaster, number>>;
  polygons: PolygonMode;
  polygonStyle: PolygonStyle;
  facilities: boolean;
  /** Can be compared against "before" with the swipe. */
  swipe: boolean;
  /** Needs the redness export. */
  needsRedness?: boolean;
  /** Not meaningful for a comparison the API computed (no static export behind it). */
  providedOnly?: boolean;
}

export const LAYERS: readonly LayerDef[] = [
  { id: "types", label: "Change types", hint: "Polygons coloured by what changed", right: { after_normalized: 1 }, left: { before: 1 }, polygons: "fill", polygonStyle: "types", facilities: false, swipe: true },
  { id: "after", label: "True colour, after", hint: "Nominal 2023-09-02, normalised to the before date", right: { after_normalized: 1 }, left: { before: 1 }, polygons: "outline", polygonStyle: "types", facilities: false, swipe: true },
  { id: "before", label: "True colour, before", hint: "Nominal 2023-08-12", right: { before: 1 }, left: { before: 1 }, polygons: "outline", polygonStyle: "types", facilities: false, swipe: false },
  { id: "after_raw", label: "True colour, after (as delivered)", hint: "Before radiometric normalisation: the hazier raw mosaic", right: { after: 1 }, left: { before: 1 }, polygons: "outline", polygonStyle: "types", facilities: false, swipe: true },
  { id: "intensity", label: "Change intensity", hint: "log10 of the robust distance d², before thresholding", right: { after_normalized: 0.55, change_intensity: 0.95 }, left: { before: 0.55 }, polygons: "outline", polygonStyle: "types", facilities: false, swipe: true },
  { id: "binary", label: "Change binary", hint: "Changed or not, after hysteresis and the minimum mapping unit", right: { after_normalized: 0.8 }, left: { before: 0.8 }, polygons: "fill", polygonStyle: "binary", facilities: false, swipe: true },
  { id: "facilities", label: "Facility register", hint: "Mine facilities with hectares changed", right: { after_normalized: 1 }, left: { before: 1 }, polygons: "outline", polygonStyle: "types", facilities: true, swipe: true, providedOnly: true },
  { id: "redness", label: "Redness (B04 / B02)", hint: "Iron-oxide proxy from visible bands, before and after", right: { redness_after: 0.92 }, left: { redness_before: 0.92 }, polygons: "none", polygonStyle: "types", facilities: false, swipe: true, needsRedness: true, providedOnly: true },
];

export const DEFAULT_LAYER = "types";

export function layerById(id: string | null | undefined): LayerDef {
  return LAYERS.find((l) => l.id === id) ?? (LAYERS[0] as LayerDef);
}

/** Inferno-like ramp used by the change-intensity export, for the legend. */
export const INTENSITY_RAMP = "linear-gradient(90deg, #08040f, #420a68 25%, #932667 45%, #dd513a 65%, #fca50a 85%, #fcffa4)";
/** YlOrRd, the redness colormap. */
export const REDNESS_RAMP = "linear-gradient(90deg, #ffffb2, #fecc5c 30%, #fd8d3c 60%, #e31a1c 85%, #800026)";
