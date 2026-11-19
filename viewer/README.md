# Change viewer

MapLibre GL + TypeScript viewer for the Lumwana Sentinel-2 change result: Esri imagery basemap, the exported
raster overlays, a before/after swipe, and filterable change polygons with popups. Three tabs share one map:

1. **Assignment**: the original view (swipe, layers, filters, popups).
2. **Timeline 2023–2026**: month slider with play/pause (insufficient months are greyed and skipped), RGB frame and
   change-intensity overlays with opacity, and KPI charts (footprint, water, changed area) with a synced cursor;
   click a chart point to jump to that month. Reads `monthly/manifest.json` next to the assignment data; if it is
   missing the tab shows an empty state.
3. **Analyst agent**: chat panel (markdown, charts, tables, "how I got this" tool-call trace) that drives the map
   via `fly_to` / `highlight` / `show_run` / `set_month` actions. Charts are hand-rolled SVG and markdown is
   escape-then-whitelist, so there are no extra runtime dependencies.

```bash
npm install
npm run dev        # http://localhost:5173, serves ../docs/web at /web/
npm run build      # typecheck + bundle into ../docs/viewer/ (GitHub Pages, data at ../web/)
npm run build:site # bundle into dist-site/ for a site root, data at ./web/ (VITE_DATA_BASE=./web/)
npm run preview    # serves the built viewer (and /web/) locally; `vite preview --outDir dist-site` for the site build
```

Data is fetched at runtime from `../web/` relative to `index.html` (`manifest.json`, `change_features.geojson`,
`aoi.geojson`, the WebP overlays). It is never copied or bundled, so GitHub Pages serving `/docs` works as is:
`docs/viewer/index.html` loads `docs/web/*`. Regenerate the web assets with the Python pipeline, no rebuild needed.

Dev server proxies `/api` to `http://127.0.0.1:8000` (the agent backend). Open with `?mock` for canned agent
answers from `fixtures/agent/*.json` (`?mock=429` / `?mock=error` simulate failures), `?api=<url>` to point the chat at
another backend. Chat is disabled (with a link to https://s2change.dzaky.ai.id) on `*.github.io` or when
`/api/health` fails; the other tabs keep working statically. `fixtures/monthly/` is a small fake monthly dataset
that the dev/preview server serves at `/web/monthly/` whenever `docs/web/monthly/` does not exist
(`S2CD_FIXTURES=1` prefers it even then). It is never copied into a build.

Agent run ids `assignment`, `default` and `robust_cva` mean the bundled result; any other `run_id` is fetched from
`GET /api/changes?run_id=...` and its feature ids are read from `properties.id`, then the GeoJSON `id`.

Override the data location with `?data=<url-or-path>` (e.g. `?data=https://host/path/`) or `VITE_DATA_BASE` at build time.
Append `?debug` to expose the map pair as `window.__maps` (used for scripted screenshots).

Notes
- Swipe uses two synced maps; the "before" map is clipped with CSS `clip-path` above the "after" map. Intensity and
  polygon layers are mirrored on both so they stay visible across the split.
- maplibre-gl 6 locates its web worker next to its entry module, so `src/maps.ts` points `setWorkerUrl` at the
  bundled worker asset. Remove that if upgrading to a version that bundles it itself.
- The Esri World Imagery basemap needs internet access; see Esri's terms for production use.
