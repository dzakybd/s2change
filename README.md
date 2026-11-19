# Sentinel-2 change detection: open-pit mine, Zambia (2023-08-12 → 2023-09-02)

A small geospatial analytics pipeline: it loads two Sentinel-2 scenes, detects change, stores the change polygons in a spatial database (SpatiaLite file or PostGIS) and visualizes and interprets the result.

**The finding that shapes the pipeline:** the 2023-09-02 scene is hazy, and the haze makes Sentinel-2's slanted **detector footprints** visible as brightness stripes. The provided Euclidean baseline cannot tell those stripes from real change: it flags **94 %** of the haziest stripe as changed. The pipeline finds the stripes, normalizes radiometry per stripe, and scores change with a per-stripe statistical noise model. This brings the changed area from 11,078 ha down to 1,345 ha, with the same 3–6 % rate in every stripe, concentrated on the pits, tailings facility and waste dumps.

| | |
|---|---|
| ![inputs](docs/figures/01_inputs_and_normalization.png) | ![methods](docs/figures/03_method_comparison.png) |

**Read next:** [`report.md`](report.md) (method, results, interpretation) · [interactive map](docs/change_map.html) · [TypeScript viewer](docs/viewer/index.html)

---

## Quick start

```bash
# Python 3.12 + uv (https://docs.astral.sh/uv/)
uv sync
uv run s2cd run          # ~40 s on a laptop: all rasters, SQLite DB, figures, maps
uv run pytest -q         # unit tests on synthetic data
```

Outputs:

| Path | What |
|---|---|
| `data/processed/sentinel2_2023MMDD_stack.tif` | Part 1: 3-band stacks (B02, B03, B04), original DN |
| `data/processed/sentinel2_20230902_stack_normalized.tif` | After-date normalized to before-date radiometry, per stripe zone |
| `data/processed/radiometric_zones.tif` | Detected detector-stripe zones |
| `data/processed/change_map.tif` | Part 2: change intensity (log10 Mahalanobis d²), float32 |
| `data/processed/change_probability.tif` | Change probability (χ² CDF) scaled 0–254 |
| `data/processed/change_binary.tif` | Binary change, main method |
| `data/processed/change_map_baseline.tif`, `change_binary_baseline.tif` | Provided Euclidean algorithm + Otsu threshold |
| `data/processed/change_binary_irmad.tif` | Zone-wise IR-MAD (comparison) |
| `data/change_detection.sqlite` | Part 3: SpatiaLite DB, tables `change_features`, `change_runs` |
| `data/processed/change_features.geojson` | Same polygons, EPSG:4326 |
| `data/processed/run_summary.json` | All diagnostics (stripe model, calibration, thresholds, areas) |
| `docs/figures/*.png`, `docs/change_map.html`, `docs/viewer/` | Part 4: figures, Folium map, MapLibre viewer |
| `report.md` | Part 5: interpretation |

### PostGIS (Docker)

```bash
make db-up                       # postgis/postgis:16-3.4 on localhost:5432
make run-postgis                 # same pipeline, also writes to PostGIS
docker compose up --build        # or everything in containers (db + pipeline)
```

The schema is in [`sql/schema_postgis.sql`](sql/schema_postgis.sql): `geometry(MultiPolygon, 32735)` with a GiST index, foreign key to `change_runs`, CHECK constraints, and a `change_features_wgs84` view for web clients. Writes are idempotent per `run_id`. Example queries: [`sql/queries_postgis.sql`](sql/queries_postgis.sql), [`sql/queries_spatialite.sql`](sql/queries_spatialite.sql).

### Optional: enrichment from Planetary Computer

```bash
uv sync --extra stac
uv run s2cd enrich      # pulls B08/B11/B12/SCL/AOT for the same dates, adds dNBR/dMNDWI/dNDVI + likely cause
```

This step is optional. Everything else works offline on the provided data only.

## Approach

```mermaid
flowchart LR
  A[B02/B03/B04<br/>2 dates] --> B[Validate grid<br/>stack, nodata mask]
  B --> C[DN to reflectance<br/>L2A offset 1000]
  C --> D[Detect detector-stripe zones<br/>angle search + 1-D segmentation]
  D --> E[Per-zone relative normalization<br/>orthogonal regression on<br/>pseudo-invariant pixels]
  E --> F[Robust CVA<br/>Mahalanobis d², χ² noise model]
  F --> G[Hysteresis threshold<br/>FAR 1e-3 / 1e-5, seam buffer, MMU 0.2 ha]
  G --> H[Polygons + attributes]
  H --> I[(SpatiaLite / PostGIS)]
  H --> J[Figures, Folium, MapLibre]
  C --> K[Baseline: provided<br/>Euclidean + Otsu]
  D --> L[Zone-wise IR-MAD]
```

1. **Load and validate.** Every band of both dates must share CRS, transform and shape (`check_consistency` raises with a precise message if not). Nodata is 0 in any band. All rasters are EPSG:32735 (UTM 35S), 10 m, 1673 × 1597 px.
2. **Radiometry.** The DN values follow the L2A processing-baseline ≥ 04.00 convention: no valid DN falls below 1000, so the pipeline treats them as `reflectance = (DN − 1000) / 10000`. It then finds the stripe zones. The haze adds path radiance, which is strongest in blue, so it uses the blue difference T2 − T1. A grid search finds the stripe angle that maximizes the variance explained by a 1-D profile, and binary segmentation finds the steps. Result: **12.6°, 4 zones, 56 % of variance explained**.
3. **Normalization.** Per zone and band, an orthogonal-regression line maps T2 onto T1, iterated with 2.5 σ outlier rejection. The pixels that survive are the pseudo-invariant features (PIFs, 86 % of pixels).
4. **Change statistic.** The change vector is Δ = T2_norm − T1. The PIFs of each zone give the "no change" mean and covariance, and d² is each pixel's Mahalanobis distance from it. Under no change, d² ~ χ²(3), so thresholds are **false-alarm rates**, not magic numbers.
5. **Binary map and polygons.** Hysteresis thresholding grows regions at a false-alarm rate (FAR) of 1e-3 and keeps only regions containing a pixel at FAR 1e-5. It then drops pixels within 2 px of a stripe seam, applies an opening, enforces a 0.2 ha minimum mapping unit and fills holes. Regions are vectorized with 8-connectivity. Each polygon gets area, perimeter, confidence, Δ per band, direction (brightening / darkening / spectral shift), and an `in_disturbed_area` flag (a heuristic footprint of bright bare surfaces at T1).
6. **Comparison methods.** The provided `compute_change_distance` is imported from `inputs/` and run unchanged, with an Otsu threshold. Zone-wise IR-MAD (Nielsen 2007) is also run. `report.md` explains why both over-detect here.

### Assumptions

- The inputs are L2A surface reflectance with the +1000 offset. The code infers the offset from the data rather than hard-coding it.
- The two dates are co-registered at the sub-pixel level. Residual edge effects are handled by the opening and the MMU, not modelled.
- Most of the AOI did not change in three weeks. This is what makes PIF selection and the per-zone noise model valid.
- "Confidence" is a 0–1 magnitude score: the polygon mean of `clip(log10(d² / d²_low) / 2, 0, 1)`. It reaches 1 at 100× the detection threshold. It is not a calibrated accuracy, because there is no reference data. `mean_p_change` and `strong_fraction` are stored alongside it.

## Repository layout

```
inputs/                 provided data, unchanged (aoi.geojson, example script, B02-B04 per date)
src/s2cd/
  io.py                 load, validate, stack, write tiled GeoTIFFs with overviews
  radiometry.py         DN->reflectance, stripe-zone detection, per-zone normalization
  methods.py            provided baseline (wrapped), robust CVA, zone-wise IR-MAD
  vectorize.py          hysteresis thresholds, morphology, polygons + attributes
  db.py                 SpatiaLite + PostGIS writers (idempotent per run)
  viz.py                figures, WebP overlays, Folium map
  stac_context.py       optional Planetary Computer enrichment (dNBR, dMNDWI, SCL, AOT)
  pipeline.py, cli.py   orchestration, `s2cd` CLI
configs/default.yaml    every threshold, with its meaning
sql/                    PostGIS schema + example queries (PostGIS and SpatiaLite)
tests/                  pytest suite on synthetic rasters
viewer/                 TypeScript + MapLibre viewer (built into docs/viewer)
docs/                   figures, Folium map, web assets
```

## Tooling

uv · ruff · pytest · GitHub Actions CI · Docker / docker-compose (PostGIS 16) · Vite + TypeScript + MapLibre.
