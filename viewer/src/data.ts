import type { FeatureCollection, Polygon } from "geojson";
import type { ChangeCollection, Manifest, MonthlyManifest } from "./types";

/**
 * Where the exported web assets live. Resolution order: `?data=` query parameter,
 * VITE_DATA_BASE at build time, then `../web/` (docs/viewer/ next to docs/web/).
 */
export function dataBase(): URL {
  const fromQuery = new URLSearchParams(location.search).get("data");
  const raw = fromQuery ?? import.meta.env.VITE_DATA_BASE ?? "../web/";
  return new URL(raw.endsWith("/") ? raw : `${raw}/`, location.href);
}

async function getJson<T>(url: URL): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url.pathname}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

export interface Data {
  base: URL;
  manifest: Manifest;
  changes: ChangeCollection;
  aoi: FeatureCollection<Polygon>;
}

export async function loadData(): Promise<Data> {
  const base = dataBase();
  const manifest = await getJson<Manifest>(new URL("manifest.json", base));
  const [changes, aoi] = await Promise.all([
    getJson<ChangeCollection>(new URL(manifest.vectors.changes, base)),
    getJson<FeatureCollection<Polygon>>(new URL(manifest.vectors.aoi, base)),
  ]);
  changes.features.forEach((f, i) => {
    f.properties.fid = i;
  });
  return { base, manifest, changes, aoi };
}

/**
 * Monthly timeline manifest (`monthly/manifest.json` next to the assignment data). Returns null when it is
 * absent or malformed so the Timeline tab can show an empty state instead of breaking the viewer. A dev
 * server's SPA fallback answers missing files with index.html, which fails JSON parsing and lands here too.
 */
export async function loadMonthly(base: URL): Promise<MonthlyManifest | null> {
  try {
    const res = await fetch(new URL("monthly/manifest.json", base));
    if (!res.ok) return null;
    const m = (await res.json()) as Partial<MonthlyManifest>;
    if (!Array.isArray(m.months) || m.months.length === 0 || !Array.isArray(m.bounds)) return null;
    return m as MonthlyManifest;
  } catch {
    return null;
  }
}
