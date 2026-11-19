import type { FeatureCollection, Polygon } from "geojson";
import type { ChangeCollection, Manifest } from "./types";

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
