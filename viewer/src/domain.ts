/** Constants and formatting shared by every view. */

export const LIVE_URL = "https://s2change.dzaky.ai.id";
export const REPO_URL = "https://github.com/dzakybd/s2change";
export const RAW_URL = `${REPO_URL}/raw/main/`;
export const BLOB_URL = `${REPO_URL}/blob/main/`;

export interface ChangeTypeInfo {
  key: string;
  label: string;
  color: string;
  blurb: string;
}

/** Order = legend order (largest classes first). Colours are picked to stay apart on satellite imagery. */
export const CHANGE_TYPES: readonly ChangeTypeInfo[] = [
  { key: "tailings_deposition", label: "Tailings deposition", color: "#fbbf24", blurb: "Fresh tailings on the storage facility" },
  { key: "waste_dumping", label: "Waste dumping", color: "#fb7185", blurb: "New waste rock at pit and dump edges" },
  { key: "pit_excavation", label: "Pit excavation", color: "#a78bfa", blurb: "Benches and pit floors being mined" },
  { key: "water_level_change", label: "Water level change", color: "#38bdf8", blurb: "Ponds, dams and wetlands filling or drying" },
  { key: "seasonal_vegetation_or_burn", label: "Seasonal vegetation / burn", color: "#4ade80", blurb: "Vegetation cycle or burn scars outside the mine" },
  { key: "infrastructure_or_settlement", label: "Infrastructure / settlement", color: "#2dd4bf", blurb: "Roads, plant yard and buildings" },
  { key: "land_clearing", label: "Land clearing", color: "#fb923c", blurb: "Vegetation removed" },
  { key: "mine_expansion_clearing", label: "Mine expansion clearing", color: "#f0abfc", blurb: "Clearing at the mine edge" },
  { key: "unexplained", label: "Unexplained", color: "#94a3b8", blurb: "No rule matched" },
  { key: "noise_or_artifact", label: "Noise / artifact", color: "#64748b", blurb: "Likely mosaic seam, haze or shadow" },
];

const TYPE_BY_KEY = new Map(CHANGE_TYPES.map((t) => [t.key, t]));
const FALLBACK_TYPE: ChangeTypeInfo = { key: "other", label: "Other", color: "#cbd5e1", blurb: "" };

export function typeInfo(key: string): ChangeTypeInfo {
  return TYPE_BY_KEY.get(key) ?? { ...FALLBACK_TYPE, key, label: key.replaceAll("_", " ") };
}

export const BINARY_COLOR = "#e8833a";

export const FACILITY_CLASS_LABEL: Record<string, string> = {
  tailings: "Tailings",
  pit_dump_complex: "Pit and dump complex",
  waste_dump: "Waste dump",
  water_reservoir: "Water reservoir",
  plant: "Process plant",
  camp: "Camp",
  settlement: "Settlement",
};

const int = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const one = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const two = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

export const fmtInt = (v: number): string => int.format(v);
const three = new Intl.NumberFormat("en-US", { maximumFractionDigits: 3, minimumFractionDigits: 3 });
/** An area given in hectares, shown in km² with sensible precision (the data keeps hectares; 1 km² = 100 ha). */
export const fmtKm2 = (ha: number): string => {
  const v = ha / 100;
  return `${v === 0 ? "0" : v < 0.1 ? three.format(v) : v < 10 ? two.format(v) : v < 100 ? one.format(v) : int.format(v)} km²`;
};
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

/** Download a Blob under a file name. */
export function download(name: string, data: BlobPart, type: string): void {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const CHEVRON = `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="m4 6 4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
export const CLOSE = `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;
export const EXTERNAL = `<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M6 3H3.5A.5.5 0 0 0 3 3.5v9a.5.5 0 0 0 .5.5h9a.5.5 0 0 0 .5-.5V10M9 3h4v4M13 3 7.5 8.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
