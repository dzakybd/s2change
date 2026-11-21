# s2change web app

One place to evaluate the whole take-home: a Vite + strict TypeScript + MapLibre GL app with six hash-routed tabs
(`#/overview`, `#/map`, `#/database`, `#/report`, `#/analyst`, `#/downloads`). Live at
https://s2change.dzaky.ai.id, also buildable for GitHub Pages.

| Tab | What it does | Needs the API? |
|---|---|---|
| Overview | Headline findings, brief-to-tab checklist, method diagram, sensitivity table (`web/overview.json`) | no |
| Map | Before/after swipe, layer menu (change types, true colour, intensity, facility register), polygon inspector with the reason for each label, other-date comparison | only for other dates |
| Database | Schema browser, example queries, SQL editor (Cmd/Ctrl+Enter), results table, CSV/GeoJSON download, "show on map" | yes (live-only; static hosts get an explanation) |
| Report | `web/report/report.md` rendered with a table of contents and numbered figures | no |
| Analyst | Chat with charts, tables, map actions and a tool-call trace, plus "Generate monitoring brief" | yes |
| Downloads | Rasters, stacks, SpatiaLite, GeoJSON, docs (GitHub raw links) | no |

```bash
npm install
npm run dev        # http://localhost:5173, serves ../docs/web at /web/, proxies /api to :8000
npm run build      # typecheck + bundle into ../docs/viewer/ (GitHub Pages, data at ../web/)
npm run build:site # bundle into dist-site/ for a site root, data at ./web/ (VITE_DATA_BASE=./web/)
npm run preview    # serves the built app and /web/ locally (`vite preview --outDir dist-site` for the site build)
```

The static content is written by the Python side, no rebuild needed:

```bash
uv run python -m s2cd.site   # docs/web/overview.json, docs/web/report/report.md + figures
```

## URLs

- Hash routes are deep links, and map state lives in the hash: `#/map?layer=facilities&swipe=0`.
  `#/map?before=provided-before&after=2026-11-02` runs the other-date comparison when the API is reachable.
  Layers: `types` (default), `after`, `before`, `intensity`, `facilities`.
- `?mock` serves every API call from `fixtures/` (`fixtures/agent/*.json` for the chat, `fixtures/api/*.json` for the
  database, dates, comparison job and brief). `?mock=429` / `?mock=error` simulate chat failures.
- `?api=<url>` points at another backend, e.g. `?api=http://127.0.0.1:8000`.
- `?data=<url>` overrides the data location (otherwise `VITE_DATA_BASE`, then `../web/`).
- On `*.github.io`, or when `/api/health` fails, live features show a calm explanation linking to the live site.
- Open the map with any URL, then `window.__stage` exposes the two synced MapLibre maps (used by screenshots).

## Structure

`src/main.ts` is the shell and router; each view loads on demand, so the entry bundle stays small and MapLibre only
loads with the Map and Analyst tabs. `src/map/` holds the map engine (`stage.ts`), layer definitions, cards, the
date picker, the AOI geometry and the swipe. `src/api.ts` is the typed client for `docs/api.md`; `src/mock.ts` is the
fixture backend. `src/rich.ts` renders sanitised Markdown (marked + DOMPurify) for the report and the brief.

Motion is deliberately small: ease-out on enter, 150-220 ms, a 0.97 press scale, no animation on keyboard actions
(menus), and `prefers-reduced-motion` collapses everything to opacity.

Notes
- Swipe uses two synced maps; the "before" map is clipped with CSS `clip-path` above the "after" map and vector layers are mirrored.
- maplibre-gl 6 locates its web worker next to its entry module, so `src/map/stage.ts` points `setWorkerUrl` at the bundled worker asset.
- The Esri World Imagery basemap needs internet access; see Esri's terms for production use.
- `public/` holds the favicon (SVG + 32 px PNG), the Apple touch icon and `og.png` (1200x630).
