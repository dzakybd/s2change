<p align="center"><img src="viewer/public/favicon.svg" alt="s2change logo" width="72" height="72"></p>

<h1 align="center">s2change</h1>

<p align="center"><b>Change detection on two Sentinel-2 images of an open-pit copper mine in Zambia.</b></p>

This repository is the code and outputs. The findings, method and interpretation are in **[report.md](report.md)**. There is also an optional [live app](https://s2change.dzaky.ai.id) and a [4.5-minute video](https://youtu.be/MBTmg62P2ew).

## Quick start (offline, about 30 seconds)

Needs [uv](https://docs.astral.sh/uv/); Python 3.12 is installed from the lockfile.

```bash
uv sync                          # install
uv run s2cd run                  # run the pipeline: stacks, change maps, polygons, figures (+ PostGIS if DATABASE_URL is set)
uv run pytest -q -m "not slow"   # optional: tests
```

The input is the provided data in `inputs/`, unchanged. The outputs are already committed, so they can be inspected without running anything. The database step needs `DATABASE_URL` in `.env` (the production PostGIS); without it the run skips that step, and `make run-local` writes to a local PostGIS instead.

## What you get

| Brief | Output |
|---|---|
| 1 · Data preparation (B02–B04, CRS, transform and shape checked) | `data/processed/sentinel2_20230812_stack.tif`, `sentinel2_20230902_stack.tif` |
| 2 · Change detection: the provided example | `data/processed/change_map_baseline.tif`, `change_binary_baseline.tif` |
| 2 · Change detection: this work | `data/processed/change_map.tif`, `change_binary.tif` (+ `change_significance.tif`) |
| 3 · Change polygons in a geospatial database | PostGIS (Supabase), table `change_features`: [explore it live](https://s2change.dzaky.ai.id/#/database), or locally with `make run-local`; also `data/processed/change_features.geojson` |
| 4 · Visualisation | `docs/figures/`, plus the interactive map in the app |
| 5 · Report | [report.md](report.md), also as a PDF in the app's Report tab |
| Run settings and numbers | `data/processed/run_summary.json`, `sensitivity.csv` |

## Project structure

The brief is covered by `inputs/`, `src/s2cd/`, `configs/`, `sql/`, `data/` and `report.md`. The rest is optional.

```
inputs/                  provided data, unchanged
src/s2cd/                the pipeline (Python package, CLI `s2cd`)
  io.py                    load, check and stack the bands; write GeoTIFFs
  provenance.py            which satellite pass each pixel came from (the "data trap")
  radiometry.py            reflectance, zones, per-zone brightness matching
  methods.py               the provided example method and this work's change score
  vectorize.py             threshold, seam buffer, cleanup, polygons
  rgb.py, interpret.py     colour rules, change type and a plain sentence per polygon
  register.py              the 8 named mine facilities and the change in each
  scenes.py                download single Sentinel-2 passes (true-pass check)
  sensitivity.py           re-runs with stricter and looser settings
  pipeline.py, cli.py, db.py, viz.py, site.py   orchestration, database, figures, web export
configs/                 settings (default.yaml) and the facility register
sql/                     PostGIS schema and example queries
data/                    outputs (processed/), database, validation sample, caches
tests/                   pytest suite
scripts/                 report figures, accuracy sample, extra experiments
app/                     optional: FastAPI service, database explorer, analyst agent
viewer/                  optional: TypeScript + MapLibre web app
deploy/, Dockerfile      optional: hosting
docs/                    figures, web assets, system and API notes
```

## Other commands

```bash
make help                          # list every task
uv run s2cd provenance             # optional, online: trace each pixel to its satellite pass
uv run python -m s2cd.sensitivity  # stricter / looser settings sweep
make figures                       # re-render the report figures
make run-local                     # same pipeline into a local PostGIS (Docker) instead of production
make query                         # example SQL against that local PostGIS
cd viewer && npm ci && npm run dev # the web app, locally
```

Settings (thresholds, minimum patch size, seam buffer) are in `configs/default.yaml`.

## Assumptions

- The inputs are Level-2A surface reflectance with the −1000 offset (detected, with a warning if not).
- The database stores the dates as supplied; the true pass dates per zone are in `run_summary.json` under `acquisition`.
- Most of the area is unchanged, so stable ground can be found in every zone.
- "Confidence" is a change-strength score from 0 to 1, not a probability of being right. Accuracy is measured separately on 200 random points ([report, section 5](report.md#-5-how-accurate-is-it)).

## The app (optional)

Everything above runs offline. The app at [s2change.dzaky.ai.id](https://s2change.dzaky.ai.id) shows the same results without installing anything: the report, a map with before/after swipe, the facilities, a database explorer and downloads. The analyst agent needs an access code, shared with reviewers on request.

Running the app yourself needs a PostGIS database and an LLM key: see [app/README.md](app/README.md). Architecture, agent and database design are in [report section 7](report.md#-7-the-system-stack-agent-and-database) and [docs/system.md](docs/system.md).
