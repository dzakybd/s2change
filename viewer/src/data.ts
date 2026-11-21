import type { AoiCollection, ChangeCollection, Dataset, FacilityCollection, Manifest, Overview } from "./types";

/**
 * Where the exported web assets live. Resolution order: `?data=` query parameter,
 * VITE_DATA_BASE at build time, then `../web/` (docs/viewer/ next to docs/web/).
 */
export function dataBase(): URL {
  const fromQuery = new URLSearchParams(location.search).get("data");
  const raw = fromQuery ?? import.meta.env.VITE_DATA_BASE ?? "../web/";
  return new URL(raw.endsWith("/") ? raw : `${raw}/`, location.href);
}

export async function getJson<T>(url: URL | string): Promise<T> {
  const res = await fetch(url);
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

export const loadManifest = memo(() => getJson<Manifest>(dataUrl("manifest.json")));
export const loadOverview = memo(() => getJson<Overview>(dataUrl("overview.json")));
export const loadAoi = memo(() => getJson<AoiCollection>(dataUrl("aoi.geojson")));
export const loadFacilities = memo(async () => {
  const m = await loadManifest();
  return getJson<FacilityCollection>(dataUrl(m.vectors.facilities ?? "facilities.geojson"));
});
export const loadChanges = memo(async () => {
  const m = await loadManifest();
  return indexed(await getJson<ChangeCollection>(dataUrl(m.vectors.changes)));
});

/** Give every feature a sequential `fid`: the key for hover/selection feature-state. */
export function indexed(fc: ChangeCollection): ChangeCollection {
  fc.features.forEach((f, i) => {
    f.properties.fid = i;
    f.properties.facility_id ??= null;
  });
  return fc;
}

export async function loadDefaultDataset(): Promise<Dataset> {
  const [manifest, changes] = await Promise.all([loadManifest(), loadChanges()]);
  const rasters = Object.fromEntries(
    (Object.keys(manifest.rasters) as (keyof Manifest["rasters"])[]).map((k) => [k, { url: dataUrl(manifest.rasters[k].url).href, bounds: manifest.rasters[k].bounds }]),
  ) as Dataset["rasters"];
  return {
    id: "provided",
    dates: { before: manifest.date_before, after: manifest.date_after },
    nominal: true,
    rasters,
    changes,
  };
}
