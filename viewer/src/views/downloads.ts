import "../styles/downloads.css";
import type { AppContext, View } from "../app";
import { BLOB_URL, RAW_URL, esc } from "../domain";

interface Item {
  path: string;
  title: string;
  desc: string;
  size: string;
  kind: string;
  /** Link to the GitHub page instead of the raw file (for documents people read, not save). */
  view?: boolean;
}

const GROUPS: { title: string; blurb: string; items: Item[] }[] = [
  {
    title: "Change maps",
    blurb: "GeoTIFFs on the 10 m grid of the scenes (EPSG:32735). Open in QGIS or rasterio.",
    items: [
      { path: "data/processed/change_map.tif", title: "change_map.tif", desc: "Robust change magnitude per pixel (log10 d²).", size: "10.7 MB", kind: "GeoTIFF" },
      { path: "data/processed/change_significance.tif", title: "change_significance.tif", desc: "−log10 p of the change under the noise model.", size: "10.1 MB", kind: "GeoTIFF" },
      { path: "data/processed/change_binary.tif", title: "change_binary.tif", desc: "Changed (1) or not (0) after hysteresis and the 0.2 ha minimum mapping unit; 255 = no data.", size: "40 KB", kind: "GeoTIFF" },
      { path: "data/processed/change_binary_baseline.tif", title: "change_binary_baseline.tif", desc: "The provided Euclidean-difference baseline, binarised the same way, for comparison.", size: "205 KB", kind: "GeoTIFF" },
    ],
  },
  {
    title: "Sentinel-2 stacks",
    blurb: "B02, B03, B04 as delivered, plus the later date normalised to the earlier one.",
    items: [
      { path: "data/processed/sentinel2_20230812_stack.tif", title: "sentinel2_20230812_stack.tif", desc: "Nominal 2023-08-12 (mosaic of 2023-07-01 and 07-04).", size: "13.2 MB", kind: "GeoTIFF" },
      { path: "data/processed/sentinel2_20230902_stack.tif", title: "sentinel2_20230902_stack.tif", desc: "Nominal 2023-09-02 (mosaic of 2023-08-25 and 09-02), as delivered.", size: "12.6 MB", kind: "GeoTIFF" },
      { path: "data/processed/sentinel2_20230902_stack_normalized.tif", title: "sentinel2_20230902_stack_normalized.tif", desc: "The later date after per-zone radiometric normalisation.", size: "13.4 MB", kind: "GeoTIFF" },
    ],
  },
  {
    title: "Database and vectors",
    blurb: "The SpatiaLite file holds the same tables the live PostGIS database serves.",
    items: [
      { path: "data/change_detection.sqlite", title: "change_detection.sqlite", desc: "SpatiaLite: runs, change features, facility register and per-facility changes.", size: "10.9 MB", kind: "SpatiaLite" },
      { path: "docs/web/change_features.geojson", title: "change_features.geojson", desc: "The 215 change polygons with type, explanation, confidence and facility (web export).", size: "0.5 MB", kind: "GeoJSON" },
      { path: "data/processed/change_features.geojson", title: "change_features.geojson (full)", desc: "Full-precision export with every attribute the pipeline computed.", size: "6.3 MB", kind: "GeoJSON" },
      { path: "docs/web/facilities.geojson", title: "facilities.geojson", desc: "The facility register: outlines, hectares changed and the change mix.", size: "0.6 MB", kind: "GeoJSON" },
      { path: "docs/web/aoi.geojson", title: "aoi.geojson", desc: "The area of interest.", size: "1 KB", kind: "GeoJSON" },
    ],
  },
  {
    title: "Documents",
    blurb: "",
    items: [
      { path: "docs/api.md", title: "api.md", desc: "Every HTTP endpoint behind the map, database console and analyst, with request and response shapes.", size: "3 KB", kind: "Markdown", view: true },
      { path: "report.md", title: "report.md", desc: "The full write-up, as shown in the Report tab.", size: "18 KB", kind: "Markdown", view: true },
    ],
  },
];

const DL = `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 2v8m-3-3 3 3 3-3M3 13h10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

export async function createDownloads(_ctx: AppContext): Promise<View> {
  const el = document.createElement("div");
  el.className = "view";
  el.innerHTML = `<div class="page dl">
    <div class="page-head"><div class="eyebrow">Downloads</div><h1>Take the data with you</h1><p>Everything the map and the database are built from. Files are served from the GitHub repository, so they are the same bytes the pipeline wrote.</p></div>
    ${GROUPS.map(
      (g) => `<section><h2>${esc(g.title)}</h2>${g.blurb ? `<p class="blurb">${esc(g.blurb)}</p>` : ""}<div class="dl-grid">${g.items
        .map(
          (i) => `<article class="card dl-card"><div class="dl-top"><h3 class="mono">${esc(i.title)}</h3><span class="pill">${esc(i.kind)}</span></div><p>${esc(i.desc)}</p><div class="dl-foot"><span class="muted num">${esc(i.size)}</span><a class="btn sm ${i.view ? "" : "primary"}" href="${esc((i.view ? BLOB_URL : RAW_URL) + i.path)}" ${i.view ? 'target="_blank" rel="noopener noreferrer"' : "download"}>${i.view ? "View on GitHub" : `${DL} Download`}</a></div></article>`,
        )
        .join("")}</div></section>`,
    ).join("")}
  </div>`;
  return { el, show() {}, hide() {} };
}
