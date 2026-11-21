import type { RasterKey } from "../types";

/**
 * What each entry of the layer menu shows. The map is two synced MapLibre maps: `right` is what the interactive map
 * shows, `left` is what the clipped map shows to the left of the swipe handle. Rasters are listed with their opacity.
 */
export type PolygonMode = "outline" | "fill";

export interface LayerDef {
  id: string;
  label: string;
  hint: string;
  right: Partial<Record<RasterKey, number>>;
  left: Partial<Record<RasterKey, number>>;
  polygons: PolygonMode;
  facilities: boolean;
  /** Can be compared against "before" with the swipe. */
  swipe: boolean;
  /** Not meaningful for a comparison the API computed (no static export behind it). */
  providedOnly?: boolean;
}

export const LAYERS: readonly LayerDef[] = [
  { id: "types", label: "Change types", hint: "Polygons coloured by what changed", right: { after_normalized: 1 }, left: { before: 1 }, polygons: "fill", facilities: false, swipe: true },
  { id: "after", label: "True colour, after", hint: "Nominal 2023-09-02, normalised to the before date", right: { after_normalized: 1 }, left: { before: 1 }, polygons: "outline", facilities: false, swipe: true },
  { id: "before", label: "True colour, before", hint: "Nominal 2023-08-12", right: { before: 1 }, left: { before: 1 }, polygons: "outline", facilities: false, swipe: false },
  { id: "intensity", label: "Change intensity", hint: "log10 of the robust distance d², before thresholding", right: { after_normalized: 0.55, change_intensity: 0.95 }, left: { before: 0.55 }, polygons: "outline", facilities: false, swipe: true },
  { id: "facilities", label: "Facility register", hint: "Mine facilities with the area changed", right: { after_normalized: 1 }, left: { before: 1 }, polygons: "outline", facilities: true, swipe: true, providedOnly: true },
];

export const DEFAULT_LAYER = "types";

export function layerById(id: string | null | undefined): LayerDef {
  return LAYERS.find((l) => l.id === id) ?? (LAYERS[0] as LayerDef);
}

/** Inferno-like ramp used by the change-intensity export, for the legend. */
export const INTENSITY_RAMP = "linear-gradient(90deg, #08040f, #420a68 25%, #932667 45%, #dd513a 65%, #fca50a 85%, #fcffa4)";
