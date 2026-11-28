/** Constants and formatting shared by every view. */

export const LIVE_URL = "https://s2change.dzaky.ai.id";
const REPO_URL = "https://github.com/dzakybd/s2change";
export const RAW_URL = `${REPO_URL}/raw/main/`;
export const BLOB_URL = `${REPO_URL}/blob/main/`;

interface ChangeTypeInfo {
  key: string;
  label: string;
  color: string;
  blurb: string;
  /** Pictogram, as SVG path data on a 24-unit grid, drawn as a stroke. */
  glyph: string;
}

/** Order = legend order (largest classes first). Colours are picked to stay apart on satellite imagery. */
export const CHANGE_TYPES: readonly ChangeTypeInfo[] = [
  { key: "tailings_deposition", label: "Tailings deposition", color: "#fbbf24", blurb: "Fresh tailings on the storage facility", glyph: "M12 3v7M9 7.5l3 3 3-3M3 15c3-2 6 2 9 0s6-2 9 0M3 19.5c3-2 6 2 9 0s6-2 9 0" },
  { key: "tailings_dam_works", label: "Earthworks on the tailings dam wall", color: "#dc2626", blurb: "Bare soil on the tailings dam wall where plants were; likely upkeep work", glyph: "M2 20.5h20M5.5 20.5 9 13h6l3.5 7.5M12 3.5v6.5M9 6.5l3-3 3 3" },
  { key: "waste_dumping", label: "Waste dumping", color: "#fb7185", blurb: "New waste rock at pit and dump edges", glyph: "M2.5 7.5 11 5l1.5 6.5H3zM12.5 11.5h3.5l3 3v2.5H3v-5.5M7 19.5a1.5 1.5 0 1 0 0-.1M16 19.5a1.5 1.5 0 1 0 0-.1" },
  { key: "pit_excavation", label: "Pit excavation", color: "#a78bfa", blurb: "Benches and pit floors being mined", glyph: "M2 6h3.5l2 3.5h2l1.5 3.5h2l1.5-3.5h2l2-3.5H22M9.5 16.5h5" },
  { key: "water_level_change", label: "Water level change", color: "#38bdf8", blurb: "Ponds, dams and wetlands filling or drying", glyph: "M12 3c3.2 4 6 7.3 6 10.8a6 6 0 0 1-12 0C6 10.3 8.8 7 12 3zM9 14.5a3 3 0 0 0 3 3" },
  { key: "seasonal_vegetation_or_burn", label: "Seasonal vegetation / burn", color: "#4ade80", blurb: "Vegetation cycle or burn scars outside the mine", glyph: "M5 19.5C5 11 10 5 19.5 4.5 19.5 14 13.5 19.5 5 19.5zM5 19.5l8.5-8.5" },
  { key: "infrastructure_or_settlement", label: "Infrastructure / settlement", color: "#2dd4bf", blurb: "Roads, plant yard and buildings", glyph: "M4 20.5V10l8-6 8 6v10.5zM10 20.5v-5.5h4v5.5" },
  { key: "land_clearing", label: "Land clearing", color: "#fb923c", blurb: "Vegetation removed", glyph: "M8 3.5 12 9H9.5l3.5 5H3l3.5-5H4zM8 14v6.5M14 20.5h7M16 17l5-5" },
  { key: "mine_expansion_clearing", label: "Mine expansion clearing", color: "#f0abfc", blurb: "Clearing at the mine edge", glyph: "M9.5 9.5h5v5h-5zM3.5 8.5v-5h5M15.5 3.5h5v5M20.5 15.5v5h-5M8.5 20.5h-5v-5" },
  { key: "unexplained", label: "Unexplained", color: "#94a3b8", blurb: "Changed, but no rule says what it is", glyph: "M9 9.2a3 3 0 1 1 4.2 2.7c-.8.4-1.2 1-1.2 1.8V15M12 18.6v.4" },
  { key: "noise_or_artifact", label: "Noise / artifact", color: "#64748b", blurb: "Probably not real: a stitching seam, haze or shadow", glyph: "M3.5 7h4M10.5 7h10M3.5 12h9M15.5 12h5M3.5 17h5M11.5 17h9" },
];

const TYPE_BY_KEY = new Map(CHANGE_TYPES.map((t) => [t.key, t]));
const FALLBACK_TYPE: ChangeTypeInfo = { key: "other", label: "Other", color: "#cbd5e1", blurb: "", glyph: "M8 12h8" };

export function typeInfo(key: string): ChangeTypeInfo {
  return TYPE_BY_KEY.get(key) ?? { ...FALLBACK_TYPE, key, label: key.replaceAll("_", " ") };
}

const ICON_INK = "#0f172a";

/** A change type's pictogram in its colour badge, inline in HTML (legend, list, cards). */
export function typeIcon(key: string, size = 18): string {
  const t = typeInfo(key);
  return `<svg class="ticon" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true"><circle cx="12" cy="12" r="12" fill="${t.color}"/><g transform="translate(4.2 4.2) scale(0.65)"><path d="${t.glyph}" fill="none" stroke="${ICON_INK}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></g></svg>`;
}

/** The same badge as a standalone SVG document with a white ring, for rasterising into a map icon. */
export function typeIconSvg(key: string, px: number): string {
  const t = typeInfo(key);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -2 28 28" width="${px}" height="${px}"><circle cx="12" cy="12" r="13" fill="#ffffff"/><circle cx="12" cy="12" r="11.3" fill="${t.color}"/><g transform="translate(4.6 4.6) scale(0.62)"><path d="${t.glyph}" fill="none" stroke="${ICON_INK}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></g></svg>`;
}

/** Facility classes of the register, each with a pictogram (same 24-unit grid as the change types). */
const FACILITY_CLASSES: Record<string, { label: string; glyph: string }> = {
  tailings: { label: "Tailings", glyph: "M5 10.5 8 5.5h8l3 5M3 15c3-2 6 2 9 0s6-2 9 0M3 19.5c3-2 6 2 9 0s6-2 9 0" },
  pit_dump_complex: { label: "Pit and dump complex", glyph: "M2 6h3.5l2 3.5h2l1.5 3.5h2l1.5-3.5h2l2-3.5H22M9.5 16.5h5" },
  waste_dump: { label: "Waste dump", glyph: "M2.5 19.5 9 9l3.5 4.5L15 10l6.5 9.5z" },
  water_reservoir: { label: "Water reservoir", glyph: "M3 8.5c3-2 6 2 9 0s6-2 9 0M3 13.5c3-2 6 2 9 0s6-2 9 0M3 18.5c3-2 6 2 9 0s6-2 9 0" },
  plant: { label: "Process plant", glyph: "M3 20.5V11l5 3v-3l5 3V5h4v15.5zM2 20.5h20" },
  camp: { label: "Camp", glyph: "M3 20 12 5l9 15zM9 20l3-5 3 5" },
  settlement: { label: "Settlement", glyph: "M3 20.5V12l5-4 5 4v8.5zM13 20.5V14l4-3 4 3v6.5" },
};
/** One colour per register facility (map outline, fill and badge); facilities of other registers fall back to teal. */
export const FACILITY_COLORS: Record<string, string> = {
  TSF: "#f97316",
  "PIT-S": "#8b5cf6",
  "PIT-NW": "#d946ef",
  "WD-E": "#e11d48",
  PLANT: "#14b8a6",
  TOWN: "#84cc16",
  DAM: "#3b82f6",
  CAMP: "#eab308",
};
export const FACILITY_FALLBACK = "#5eead4";
export const facilityColor = (id: string): string => FACILITY_COLORS[id] ?? FACILITY_FALLBACK;

export const FACILITY_CLASS_LABEL: Record<string, string> = Object.fromEntries(Object.entries(FACILITY_CLASSES).map(([k, v]) => [k, v.label]));

/** A facility class's pictogram in a rounded badge of the facility's colour. */
export function facilityIcon(cls: string, size = 18, color = FACILITY_FALLBACK): string {
  const glyph = FACILITY_CLASSES[cls]?.glyph ?? "M6 6h12v12H6z";
  return `<svg class="ticon" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true"><rect x="0.5" y="0.5" width="23" height="23" rx="6" fill="${color}"/><g transform="translate(4.2 4.2) scale(0.65)"><path d="${glyph}" fill="none" stroke="${ICON_INK}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></g></svg>`;
}

const int = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const one = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const two = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

export const fmtInt = (v: number): string => int.format(v);
/** An area given in hectares: under 0.1 km² it reads in m² to the nearest 100 ("71,000 m²"), above in km². */
export const fmtKm2 = (ha: number): string => {
  const v = ha / 100;
  if (v === 0) return "0 km²";
  if (v < 0.1) return `${int.format(Math.max(100, Math.round(ha * 100) * 100))} m²`;
  return `${v < 10 ? two.format(v) : v < 100 ? one.format(v) : int.format(v)} km²`;
};
/** An area given in m², formatted like fmtKm2. */
export const fmtArea = (m2: number): string => fmtKm2(m2 / 10_000);
export const fmtPct = (share: number, digits = 0): string => `${(share * 100).toFixed(digits)}%`;

export function ha(m2: number): number {
  return m2 / 10_000;
}

export function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** Query-selector that throws, so a markup typo fails loudly instead of as an undefined later. */
export function q<T extends HTMLElement = HTMLElement>(root: ParentNode, sel: string): T {
  const el = root.querySelector<T>(sel);
  if (!el) throw new Error(`missing ${sel}`);
  return el;
}

export function qa<T extends HTMLElement = HTMLElement>(root: ParentNode, sel: string): T[] {
  return [...root.querySelectorAll<T>(sel)];
}

export const prefersReducedMotion = (): boolean => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export const CHEVRON = `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="m4 6 4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
export const CLOSE = `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;
export const EXTERNAL = `<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M6 3H3.5A.5.5 0 0 0 3 3.5v9a.5.5 0 0 0 .5.5h9a.5.5 0 0 0 .5-.5V10M9 3h4v4M13 3 7.5 8.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
