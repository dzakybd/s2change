# s2change web app

One place to evaluate the whole take-home: a Vite + strict TypeScript + MapLibre GL app with four hash-routed tabs
(`#/report`, `#/map`, `#/database`, `#/downloads`). The agent is a panel on the map (`#/map?agent=1`); `#/agent` and `#/analyst` open that panel. Live at
https://s2change.dzaky.ai.id.

| Tab | What it does | Needs the API? |
|---|---|---|
| Map | Opens in 2.5D (tilt, bearing, changes raised by confidence; `?oblique=0` / `?extrude=0` to turn off) on the analysed Sentinel-2 dates super-resolved to 2.5 m (SEN2SR, `pipeline/scripts/super_resolution.py`; view only) with the agent beside it (`?agent=0` collapses it to a rail), a Changes toggle to see the imagery underneath (`?changes=0`), type icons, before/after swipe on the Sentinel-2 layers, Result menu (provided baseline, ours on the mosaic passes, ours on the true passes), layer menu (changes on 2.5 m or 10 m Sentinel-2, change intensity, the provided baseline's mask, facility register with class icons, for either result), polygon inspector with the reason for each label | no |
| Database | Connection card (live PostgreSQL/PostGIS versions, read-only URI with a copy button), interactive entity-relationship diagram (click a table), paged table explorer with sorting and key links between tables, "show this page on the map", SQL dump per table or for the whole database | yes (live-only; static hosts get an explanation) |
| Report (landing tab) | `web/report/report.md` rendered with a table of contents, numbered figures, Mermaid diagrams and facility thumbnails; links into the app open in place | no |
| Agent (panel on the Map) | Live chat with tables, Mermaid charts, map actions and a tool-call trace | yes |
| Downloads | Rasters, stacks, GeoJSON, docs with a preview of each (`web/downloads.json`, `web/previews/`); files from GitHub raw links | no |

```bash
npm install
npm run dev        # http://localhost:5173, serves ../webapp/data at /web/, proxies /api to :8000
npm run build      # typecheck + bundle into dist/
npm run build:site # bundle into dist-site/ for a site root, data at ./web/ (VITE_DATA_BASE=./web/)
npm run preview    # serves the built app and /web/ locally (`vite preview --outDir dist-site` for the site build)
```

The static content is written by the Python side, no rebuild needed:

```bash
uv run python -m s2cd.site   # webapp/data/downloads.json + previews/, facilities/ thumbnails, report/report.md + figures
```

## URLs

- Hash routes are deep links, and map state lives in the hash: `#/map?layer=facilities&swipe=0`.
  Layers: `types` (default), `after`, `before`, `intensity`, `facilities`.
- `?mock` serves every API call from `fixtures/` (`fixtures/agent/*.json` for the chat, `fixtures/api/*.json` for the
  database). `?mock=429` / `?mock=error` simulate chat failures.
- `?api=<url>` points at another backend, e.g. `?api=http://127.0.0.1:8000`.
- `?data=<url>` overrides the data location (otherwise `VITE_DATA_BASE`, then `../web/`).
- When `/api/health` fails, live features show a calm explanation linking to the live site.
- Open the map with any URL, then `window.__stage` exposes the two synced MapLibre maps (used by screenshots).

## Structure

`src/main.ts` is the shell and router; each view loads on demand, so the entry bundle stays small and MapLibre only
loads with the Map and Analyst tabs. `src/map/` holds the map engine (`stage.ts`), layer definitions, cards, the
AOI geometry and the swipe. `src/api.ts` is the typed client for the API (report section 7); `src/mock.ts` is the
fixture backend. `src/rich.ts` renders sanitised Markdown (marked + DOMPurify) for the report and the agent's answers.

Motion is deliberately small: ease-out on enter, 150-220 ms, a 0.97 press scale, no animation on keyboard actions
(menus), and `prefers-reduced-motion` collapses everything to opacity.

Notes
- Swipe uses two synced maps; the "before" map is clipped with CSS `clip-path` above the "after" map and vector layers are mirrored.
- maplibre-gl 6 locates its web worker next to its entry module, so `src/map/stage.ts` points `setWorkerUrl` at the bundled worker asset.
- The Esri World Imagery basemap needs internet access; see Esri's terms for production use.
- `public/` holds the favicon (SVG + 32 px PNG), the Apple touch icon and `og.png` (1200x630).
