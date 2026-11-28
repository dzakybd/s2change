import type { RasterKey } from "../types";

/**
 * What each entry of the layer menu shows. The map is two synced MapLibre maps: `right` is what the interactive map
 * shows, `left` is what the clipped map shows to the left of the swipe handle. Rasters are listed with their opacity.
 */
type PolygonMode = "outline" | "fill";

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
  /** Only meaningful for the provided pair. */
  providedOnly?: boolean;
  /** Shows the change polygons by type, with the What changed list and labels. */
  typed?: boolean;
  /** Shows the provided baseline's change mask (provided pair only). */
  baseline?: boolean;
}

export const LAYERS: readonly LayerDef[] = [
  { id: "sr", label: "Changes on 2.5 m Sentinel-2", hint: "The two images, sharpened to 2.5 m for viewing. Swipe between before and after", right: { sr_after: 1 }, left: { sr_before: 1 }, polygons: "fill", facilities: false, swipe: true, typed: true },
  { id: "types", label: "Changes on Sentinel-2", hint: "The two images at their real 10 m detail. Swipe between before and after", right: { after_normalized: 1 }, left: { before: 1 }, polygons: "fill", facilities: false, swipe: true, typed: true },
  { id: "intensity", label: "Change intensity", hint: "How much each pixel changed, before we decide what counts as change", right: { after_normalized: 0.55, change_intensity: 0.95 }, left: { before: 0.55 }, polygons: "outline", facilities: false, swipe: true },
  { id: "baseline", label: "Provided method", hint: "What the provided method calls change (red): mostly seams and haze", right: { after_normalized: 0.7 }, left: { before: 0.7 }, polygons: "outline", facilities: false, swipe: false, providedOnly: true, baseline: true },
  { id: "facilities", label: "Facility register", hint: "The parts of the mine, with how much changed in each", right: { sr_after: 1 }, left: { sr_after: 1 }, polygons: "outline", facilities: true, swipe: false },
];

export const DEFAULT_LAYER = "sr";

export function layerById(id: string | null | undefined): LayerDef {
  return LAYERS.find((l) => l.id === id) ?? (LAYERS[0] as LayerDef);
}

/** Inferno-like ramp used by the change-intensity export, for the legend. */
export const INTENSITY_RAMP = "linear-gradient(90deg, #08040f, #420a68 25%, #932667 45%, #dd513a 65%, #fca50a 85%, #fcffa4)";
