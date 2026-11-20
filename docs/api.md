# s2change API (live at https://s2change.dzaky.ai.id/api)

The app is the single place to inspect the solution, so evaluators never need a database client. All database access goes through the read-only role `s2cd_reader` (5 s statement timeout), and the query, AOI, compare and chat endpoints are rate-limited per client IP.

## Results of the default comparison (the provided pair)

| Method & path | Returns |
|---|---|
| `GET /api/health` | `{ok, db, model}` |
| `GET /api/runs` | `[{run_id, method, date_before, date_after, changed_area_ha, n_features}]` |
| `GET /api/changes?run_id=&limit=5000` | GeoJSON (EPSG:4326). Properties: `id, area_m2, confidence, neglog10_p, change_direction, change_type, brief_category, explanation, facility_id, touches_aoi_edge` |
| `GET /api/facilities` | GeoJSON of the facility register. Properties: `facility_id, name, class, area_ha, changed_ha, changed_share, change_types{type: ha}, summary` |
| `GET /api/brief?run_id=` | `{markdown}`: a deterministic monitoring brief built from the database (no LLM) |

## Database explorer (brief Part 3: "store and query")

| Method & path | Returns |
|---|---|
| `GET /api/db/schema` | `[{table, rows, columns: [{name, type}], geometry: {column, srid, type} \| null}]` |
| `GET /api/db/examples` | `[{title, description, sql}]` (from `sql/queries_postgis.sql`) |
| `POST /api/db/query {sql}` | `{columns, rows, truncated, elapsed_ms, geojson \| null}`. Accepts one SELECT over the allow-listed tables, max 500 rows. Geometry columns come back as a GeoJSON FeatureCollection in EPSG:4326, so results can be drawn on the map |

## Area of interest

| Method & path | Returns |
|---|---|
| `POST /api/aoi/stats {geometry, run_id?}` | `{area_ha, changed_ha, changed_share, n_polygons, by_change_type{}, by_facility{}}` for a GeoJSON polygon (EPSG:4326) drawn by the user |

## Other dates (date picker)

| Method & path | Returns |
|---|---|
| `GET /api/compare/dates` | `[{date, aoi_cloud, ready}]`: the date catalogue (`python -m s2cd.catalog`), one clear scene per month after the provided pair, downloaded and cloud-checked in advance. `ready`: the pair from `provided-before` is precomputed |
| `POST /api/compare {before, after}` | `{job_id, status, message, result?}` (a cached pair comes back `done` with its result). `before`/`after` is `"provided-before"`, `"provided-after"` or a catalogue date `YYYY-MM-DD`; nothing is downloaded at request time. Results are cached per pair; one job runs at a time |
| `GET /api/compare/{job_id}` | `{status: queued\|running\|done\|error, message, result?}`. `result = {pair, summary: {changed_ha, by_change_type, by_facility}, changes_url, overlays: {before, after, intensity: {url, bounds}}}` |
| `GET /api/compare/files/{pair}/{file}` | Cached GeoJSON/WebP for a finished comparison |

## Analyst

| Method & path | Returns |
|---|---|
| `POST /api/agent/chat {messages}` | `{answer, actions, charts, tables, tool_calls}` (see `app/README.md`) |

## Static assets (also on GitHub Pages)

These live under `docs/web/` and are served at `/web/`:
- `manifest.json`: raster overlays and their bounds;
- `change_features.geojson`, `facilities.geojson`, `aoi.geojson`;
- the WebP overlays.
