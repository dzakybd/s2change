<p align="center"><img src="webapp/viewer/public/favicon.svg" alt="s2change logo" width="72" height="72"></p>

<h1 align="center">s2change</h1>

<p align="center"><b>Change detection on two Sentinel-2 images of Lumwana, Barrick's open-pit copper mine in North-Western Province, Zambia.</b></p>

This repository is the code and outputs. The findings, method and interpretation are in **[report.md](report.md)**. There is also an optional [live app](https://s2change.dzaky.ai.id) and a [2:52 explainer video](https://youtu.be/8pGhwnsoD38).

## Quick start (offline; the run takes about 15 s)

Needs [uv](https://docs.astral.sh/uv/); Python 3.12 is installed from the lockfile.

```bash
uv sync                          # install
uv run s2cd run                  # stacks, change maps, polygons, figures (the database step needs DATABASE_URL, below)
uv run pytest -q -m "not slow"   # optional: tests
```

The input is the provided data in `inputs/`, byte for byte. The outputs are already committed, so they can be inspected without running anything; a run rewrites them in place (`outputs/`, `webapp/data/`).

## Database (PostGIS)

The pipeline writes the change polygons to PostGIS and creates the tables itself (`pipeline/sql/schema_postgis.sql`, `pipeline/sql/schema_app.sql`). To get your own copy, with Docker (Compose v2) and port 5432 free:

```bash
make run-local     # start PostGIS in Docker, then run the pipeline into it
make query         # run the example queries in pipeline/sql/queries_postgis.sql
docker compose exec db psql -U s2cd -d s2cd   # or open a psql shell
```

Without make: `docker compose up -d --wait db`, then `DATABASE_URL=postgresql+psycopg://s2cd:s2cd@localhost:5432/s2cd uv run s2cd run`. `make compose-run` runs the pipeline inside Docker too. On Apple Silicon the PostGIS image runs under emulation, so the first start is slower.

Without `DATABASE_URL` the run skips the database step and says so.

## Use the published database directly

The results are also in a hosted PostGIS, which you can query or run the app against without any setup:

1. Open the [Database tab](https://s2change.dzaky.ai.id/#/database), press **Copy URI** to get the read-only connection string (`s2cd_reader`: `SELECT` only, 5 s per query).
2. Query it with any client, for example `psql "<uri>" -c "SELECT change_type, round(sum(area_m2)) FROM change_features GROUP BY 1"`, or add it in QGIS as a PostGIS connection (the `change_features_wgs84` view is in lon/lat).
3. Or run the app locally on it:

```bash
uv sync --all-extras
READER_DATABASE_URL='<uri>' uv run uvicorn api.main:app --app-dir webapp --port 8000
cd webapp/viewer && npm ci && npm run dev                               # http://localhost:5173 (proxies /api to :8000)
```

The agent chat also needs `GEMINI_API_KEY`; everything else works without it. The Database tab can also download each table as a SQL dump (`psql -f table.sql` into any PostGIS).

## What you get

| Brief | Output |
|---|---|
| 1 · Data preparation (B02–B04, CRS, transform and shape checked) | `outputs/sentinel2_20230812_stack.tif`, `sentinel2_20230902_stack.tif` |
| 2 · Change detection: the provided example | `outputs/change_map_baseline.tif`, `change_binary_baseline.tif` |
| 2 · Change detection: this work | `outputs/change_map.tif`, `change_binary.tif` (+ `change_significance.tif`) |
| 3 · Change polygons in a geospatial database | PostGIS table `change_features` ([setup above](#database-postgis), or [explore it live](https://s2change.dzaky.ai.id/#/database)); also `outputs/change_features.geojson` |
| 4 · Visualisation | `figures/`, plus the interactive map in the app |
| 5 · Report | [report.md](report.md): Method (section 3), Results (4), Interpretation (6); also a PDF in the app's Report tab |
| Run settings and numbers | `outputs/run_summary.json`, `funnel.csv`, `sensitivity.csv`; accuracy check in `outputs/validation/` |

## Project structure

Five folders, read top to bottom: what we were given, what came out, the code that made it, the figures in the report, and the optional app.

```
report.md                the write-up (Method, Results, Interpretation)
inputs/                  provided by the brief, byte for byte: aoi.geojson, example_change_detection.py, data/
outputs/                 everything the pipeline writes (brief Parts 1-3)
  sentinel2_*_stack.tif    stacked B02-B04 per date
  change_map*.tif          change intensity: this work, and *_baseline for the provided example
  change_binary*.tif       changed / not changed, both methods
  change_features.geojson  the change polygons (also in PostGIS)
  run_summary.json         every number of the last run; funnel.csv, sensitivity.csv
  provenance/, true_passes/, super_resolution/, validation/   the checks behind the report
pipeline/                the code
  s2cd/                    the Python package and CLI `s2cd`
    io.py                    load, check and stack the bands; write GeoTIFFs
    provenance.py            which satellite pass each pixel came from (the "data trap")
    radiometry.py            reflectance, zones, per-zone brightness matching
    methods.py               the provided example method and this work's change score
    vectorize.py             threshold, seam buffer, cleanup, polygons
    rgb.py, interpret.py     colour rules, change type and a plain sentence per polygon
    register.py              the 8 named mine facilities and the change in each
    scenes.py, sensitivity.py  single-pass downloads; stricter and looser settings
    pipeline.py, cli.py, db.py, viz.py, site.py   orchestration, database, figures, web export
  config/                  settings (default.yaml) and the facility register
  sql/                     PostGIS schema and example queries
  scripts/                 report figures, accuracy check, extra experiments
figures/                 the report's figures
tests/                   pytest suite
webapp/                  optional: the live app
  api/                     FastAPI service, database explorer, analyst agent (Python package `api`)
  viewer/                  TypeScript + MapLibre front end
  data/                    the app's static data, generated by `python -m s2cd.site` (incl. a copy of report.md)
  deploy/                  server deploy script and Caddy config
Makefile, Dockerfile, docker-compose.yml, pyproject.toml   tasks, container, local PostGIS, dependencies
```

## Other commands

```bash
make help                          # list every task
uv run s2cd provenance             # optional, online: trace each pixel to its satellite pass
uv run python -m s2cd.sensitivity  # stricter / looser settings sweep
make figures                       # re-render the report figures
uv run --extra stac --with sen2sr --with mlstac python pipeline/scripts/super_resolution.py  # optional, online: 2.5 m map backdrop
cd webapp/viewer && npm ci && npm run dev   # the web app, locally
```

Settings (thresholds, minimum patch size, seam buffer) are in `pipeline/config/default.yaml`.

## Assumptions

- The inputs are Level-2A surface reflectance with the −1000 offset (detected, with a warning if not).
- The database stores the dates as supplied; the true pass dates per zone are in `run_summary.json` under `acquisition`.
- Most of the area is unchanged, so stable ground can be found in every zone.
- "Confidence" is a change-strength score from 0 to 1, not a probability of being right. Accuracy is measured separately on 200 random points ([report, section 5](report.md#-5-how-accurate-is-it)).

## The app (optional)

Everything above runs offline. The app at [s2change.dzaky.ai.id](https://s2change.dzaky.ai.id) shows the same results without installing anything: the report, a map with before/after swipe, the facilities, a database explorer, downloads and the analyst agent. Nothing needs a login; requests are rate-limited per visitor, and the agent has a daily cap.

Running the app yourself needs a PostGIS database and an LLM key: see [webapp/api/README.md](webapp/api/README.md). Architecture, agent, database, API and security are in [report section 7](report.md#-7-the-system-stack-agent-and-database).
