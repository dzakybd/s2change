# s2change API (live at https://s2change.dzaky.ai.id/api)

The app is the single place to inspect the solution, so evaluators never need a database client. All database access goes through the read-only role `s2cd_reader` (5 s statement timeout), and the query, AOI and chat endpoints are rate-limited per client IP.

Reading is public. Three endpoints need the **access code** (marked "access code" below): the agent chat, the full database URI and starting a new (uncached) comparison. Without a valid session cookie they answer `401 {"detail": "This feature needs the access code."}`. See "Access code" below.

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
| `GET /api/db/schema` | `[{table, description, rows, primary_key, links: [{column, ref_table, ref_column, enforced}], columns: [{name, type}], geometry: {column, srid, type} \| null}]`. Keys come from the live constraints; `enforced: false` marks the logical `change_features.facility_id` link |
| `GET /api/db/tables/{table}?page=1&size=25&sort=&desc=&column=&value=&geojson=` | One page: `{columns, rows, total, page, pages, size, sort, desc, elapsed_ms, geojson \| null}`. `size` is 10, 25, 50 or 100; `sort` and `column` must be columns of the table (the geometry column is summarised, not sortable); `column`=`value` filters; `geojson=true` adds the page's geometries in EPSG:4326. 60/min per IP |
| `GET /api/db/export.sql?table=` | A SQL file: `CREATE EXTENSION postgis`, `CREATE TABLE` with the live constraints and indexes, one `INSERT` per row (geometry as hex EWKB). Without `table`, every table, parents first. Cached 10 min, 6/min per IP |
| `GET /api/db/connection` | `{postgres, postgis, postgis_full, host, port, database, user, uri_masked}`: live server versions and the read-only URI with the password masked |
| `GET /api/db/connection/uri` (access code) | `{uri}`: the full read-only URI (`s2cd_reader`: SELECT only, 5 s statement timeout) |

## Agent

| Method & path | Returns |
|---|---|
| `POST /api/agent/chat {messages}` (access code) | `{answer, actions, charts, tables, tool_calls}` (see `app/README.md`) |

## Access code

| Method & path | Returns |
|---|---|
| `GET /api/auth/status` | `{enabled, authenticated}`. `enabled` is false when the server has no `ACCESS_CODE_HASH` (everything is open) |
| `POST /api/auth/login {code}` | `{enabled, authenticated: true}` and a `Set-Cookie: s2c_session=...; HttpOnly; Secure; SameSite=Strict; Path=/api; Max-Age=604800`. A wrong code is a generic `401`; 5 attempts per minute per IP, then `429` |
| `POST /api/auth/logout` | `{enabled, authenticated}`; clears the cookie |

The cookie value is `<issued-at>.<HMAC-SHA256>` and is valid for 7 days. Send it back with `credentials: "same-origin"` (browsers do this by default for same-origin `fetch`); with curl use `-c`/`-b` cookie files.

## Static assets (also on GitHub Pages)

These live under `docs/web/` and are served at `/web/`:
- `manifest.json`: raster overlays and their bounds;
- `change_features.geojson`, `facilities.geojson`, `aoi.geojson`;
- the WebP overlays.
