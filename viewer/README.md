# Change viewer

MapLibre GL + TypeScript viewer for the Lumwana Sentinel-2 change result: Esri imagery basemap, the exported
raster overlays, a before/after swipe, and filterable change polygons with popups.

```bash
npm install
npm run dev        # http://localhost:5173, serves ../docs/web at /web/
npm run build      # typecheck + bundle into ../docs/viewer/
npm run preview    # serves the built viewer (and /web/) locally
```

Data is fetched at runtime from `../web/` relative to `index.html` (`manifest.json`, `change_features.geojson`,
`aoi.geojson`, the WebP overlays). It is never copied or bundled, so GitHub Pages serving `/docs` works as is:
`docs/viewer/index.html` loads `docs/web/*`. Regenerate the web assets with the Python pipeline, no rebuild needed.

Override the data location with `?data=<url-or-path>` (e.g. `?data=https://host/path/`) or `VITE_DATA_BASE` at build time.
Append `?debug` to expose the map pair as `window.__maps` (used for scripted screenshots).

Notes
- Swipe uses two synced maps; the "before" map is clipped with CSS `clip-path` above the "after" map. Intensity and
  polygon layers are mirrored on both so they stay visible across the split.
- maplibre-gl 6 locates its web worker next to its entry module, so `src/maps.ts` points `setWorkerUrl` at the
  bundled worker asset. Remove that if upgrading to a version that bundles it itself.
- The Esri World Imagery basemap needs internet access; see Esri's terms for production use.
