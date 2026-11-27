import type { AoiCollection, ChangeCollection, Dataset, FacilityCollection, Manifest } from "./types";

/**
 * Where the exported web assets live. Resolution order: `?data=` query parameter,
 * VITE_DATA_BASE at build time, then `../web/` (docs/viewer/ next to docs/web/).
 */
function dataBase(): URL {
  const fromQuery = new URLSearchParams(location.search).get("data");
  const raw = fromQuery ?? import.meta.env.VITE_DATA_BASE ?? "../web/";
  return new URL(raw.endsWith("/") ? raw : `${raw}/`, location.href);
}

export async function getJson<T>(url: URL | string): Promise<T> {
  // Revalidate every time (a 304 when unchanged): /web data is regenerated in place, and a stale manifest breaks the map.
  const res = await fetch(url, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${new URL(url, location.href).pathname}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** Fetch once, share the promise. A failed fetch is forgotten so the next caller retries. */
function memo<T>(load: () => Promise<T>): () => Promise<T> {
  let p: Promise<T> | null = null;
  return () => {
    p ??= load().catch((err: unknown) => {
      p = null;
      throw err;
    });
    return p;
  };
}

const base = dataBase();
export const dataUrl = (path: string): URL => new URL(path, base);

const loadManifest = memo(() => getJson<Manifest>(dataUrl("manifest.json")));
export const loadAoi = memo(() => getJson<AoiCollection>(dataUrl("aoi.geojson")));

/** Give every feature a sequential `fid`: the key for hover/selection feature-state. */
export function indexed(fc: ChangeCollection): ChangeCollection {
  fc.features.forEach((f, i) => {
    f.properties.fid = i;
    f.properties.facility_id ??= null;
  });
  return fc;
}


/** A static export (manifest.json + overlays + change polygons) in `dir` under the data base, as a map dataset. */
async function loadStatic(dir: string, id: string, nominal: boolean, manifest?: Manifest): Promise<Dataset> {
  const url = (path: string) => dataUrl(dir + path);
  const m = manifest ?? (await getJson<Manifest>(url("manifest.json")));
  const [raw, facilities] = await Promise.all([
    getJson<ChangeCollection>(url(m.vectors.changes)),
    getJson<FacilityCollection>(url(m.vectors.facilities ?? "facilities.geojson")),
  ]);
  const changes = indexed(raw);
  const rasters = Object.fromEntries(Object.entries(m.rasters).map(([k, r]) => [k, { url: url(r.url).href, bounds: r.bounds }])) as Dataset["rasters"];
  return { id, dates: { before: m.date_before, after: m.date_after }, nominal, rasters, changes, facilities, summary: m.summary };
}

/** The provided pair: two-pass mosaics with nominal dates. */
export const loadDefaultDataset = memo(async () => loadStatic("", "provided", true, await loadManifest()));

/** The same AOI from single-pass scenes on the true acquisition dates (scripts/real_dates.py). */
export const TRUE_PASSES_ID = "true-passes";
const TRUE_PASSES_DIR = "true_passes/";
export const loadTruePassesManifest = memo(() => getJson<Manifest>(dataUrl(`${TRUE_PASSES_DIR}manifest.json`)));
export const loadTruePasses = memo(async () => loadStatic(TRUE_PASSES_DIR, TRUE_PASSES_ID, false, await loadTruePassesManifest()));
