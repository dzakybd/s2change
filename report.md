<img src="viewer/public/favicon.svg" alt="s2change logo" width="56" height="56">

# Report: what changed at an open-pit copper mine in NW Zambia?

**Sentinel-2 change detection from three visible bands and two dates: the change is found, typed, stored in PostGIS and explained.** [Live app](https://s2change.dzaky.ai.id) · [Repository](https://github.com/dzakybd/s2change)

| AOI | Data | Dates (nominal) | Method | Result |
|---|---|---|---|---|
| 25.79–25.94 °E, 12.18–12.32 °S; 16.7 × 16.0 km, EPSG:32735, 10 m | Sentinel-2 L2A **B02, B03, B04** | 2023-08-12 → 2023-09-02 | Per-zone normalization, robust CVA, calibrated thresholds | **4.13 km²** in 215 polygons (1.6 % of the AOI) |

> [!NOTE]
> **RGB only.** The data is Sentinel-2 L2A B02, B03 and B04, the bands the brief provides. Nothing else is used: no NIR/SWIR, no scene classification layer, no elevation model. The AOI layout matches the Lumwana copper mine; the brief only says "an open-pit mining site in Zambia", so the name is an inference. Each date is a two-scene mosaic (section 0), so the true acquisitions are 2023-07-01/07-04 → 2023-08-25/09-02.

**Contents:** [TL;DR](#tldr) · [0 Data finding](#0-the-data-finding-each-date-is-a-mosaic) · [1 Method](#1-method) · [2 Results](#2-results) · [3 Interpretation](#3-interpretation) · [4 Sensitivity](#4-sensitivity) · [5 System and stack](#5-system-and-stack) · [6 Limitations](#6-limitations-and-next-steps)

## TL;DR

> [!IMPORTANT]
> **The data has a trap.** Each provided "date" is a mosaic of two Sentinel-2 scenes from different orbits; I verified it pixel-exact against the STAC catalogue. Radiometry therefore steps along slanted seams, and the gap between the dates is 52–60 days, not 21. The provided Euclidean baseline mostly maps these seams and haze: **111.4 km²**, 42 % of the AOI.

- **Method:** normalize each source-scene zone on stable pixels, then run a robust change vector analysis with a **brightness-aware, empirically calibrated** noise model. The realised false-alarm rate is **0.10 %** at the 0.1 % target, measured on held-out stable pixels; the uncalibrated model gave 2.2 %.
- **Result:** **4.13 km² in 215 polygons** (1.6 % of the AOI). **74 %** of it lies inside the mine's named facilities.
- **Interpretation:** this is operational mining, not expansion.
  - Tailings deposition on the TSF: 1.85 km².
  - Waste dumping: 1.29 km².
  - Pit-floor and bench activity: 0.26 km².
  - Seasonal vegetation or burning outside the mine: 0.36 km².
  - No new clearing beyond the mine footprint at the 2,000 m² scale.
- **How sure:** the conclusions hold over 16 threshold settings (sensitivity, section 4). The false-alarm rate is measured; omission and per-class accuracy are not, because there is no reference data (section 6).

## 0. The data finding: each "date" is a mosaic

What the before/after difference revealed, how it was tested, and what it changes.

![provenance](docs/figures/06_provenance.png)

*Source scene of each pixel for both nominal dates; the seams follow the swath edge of orbit R078.*

The before/after difference contains straight, parallel brightness steps at 12.6°. A model that is constant between the steps explains 56 % of the blue-band difference.

**Hypothesis.** My first reading was detector striping under haze.

**Test.** `s2cd provenance` searches Planetary Computer for the source items and compares every pixel.
- **Result:** B02, B03 and B04 match **100 % exactly** (2.65 M pixels per band). The hypothesis was wrong in an informative way.

| Nominal date | Source scenes (tile 35LLG, baseline N05.09) | Solar zenith / azimuth |
|---|---|---|
| 2023-08-12 | S2B 2023-07-04 R121 (W, 44 %) + S2B 2023-07-01 R078 (E, 56 %) | 42.5° / 34° and 44.1° / 37° |
| 2023-09-02 | S2B 2023-09-02 R121 (W, 39 %) + S2A 2023-08-25 R078 (E, 61 %) | 30.8° / 50° and 34.9° / 50° |

The steps are the seams along the swath edge of orbit R078. The detection uses three zones, each a single before→after scene pair: 07-04→09-02, 07-04→08-25 and 07-01→08-25. My blind, data-driven zone detection had put 99.3 % of its pixels inside one of these zones, so the method held up even though the first explanation did not.

**Consequences, all handled in the pipeline:**
1. Radiometry is normalized per zone.
2. Pixels within 90 m of a seam are excluded (2.4 % of the AOI).
3. Dates are labelled "nominal" everywhere, and the true acquisitions are stored in the outputs.
4. Sun angle differs by about 10° in zenith and 15° in azimuth between the dates. Pit walls and dump faces therefore change illumination, a known confounder (section 3).

## 1. Method

Normalize per source-scene zone, model the noise per brightness stratum, calibrate the thresholds on held-out stable pixels, then type each polygon.

| Step | What | Why |
|---|---|---|
| Load & check | Stack B02–B04 per date; assert identical CRS, transform and shape; nodata 0; DN → reflectance with the L2A −1000 offset (detected robustly, with a warning fallback) | Part 1 |
| Zones | Source-scene zones from STAC provenance (fallback: data-driven seam detection) | Different scenes have different radiometry |
| Normalization | Per zone and band, orthogonal regression `T1 = a + b·T2` on pseudo-invariant pixels, iterated with 2.5σ rejection (adapted from Canty & Nielsen 2008) | Both dates are noisy; haze lowers contrast on T2 (slopes 1.1–1.8) |
| Change statistic | Change vector `Δ = T2_norm − T1` (CVA, Malila 1980); squared Mahalanobis distance d² under a per-zone, **per-brightness-stratum** noise model fitted on stable pixels | Noise grows with brightness: one global model gave bright mine surfaces 10–25× too many false alarms |
| Calibration | Thresholds from the 99.9th / 99.999th percentiles of d² on **held-out** stable pixels, per stratum | The χ² assumption is off (heavy tails, spatial autocorrelation). Calibrated, the realised rate is 0.092–0.107 % in every stratum |
| Binary map | Hysteresis (Canny 1986): grow at 1e-3, seed at 1e-5; seam buffer; 1-px opening; minimum mapping unit 2,000 m² | Keeps coherent change and removes speckle |
| Polygons | 8-connected regions, made valid, stored as MultiPolygon EPSG:32735 with area, magnitude, −log10 p and direction | Part 3 |
| Interpretation | **Facility register** of 8 named facilities, outlined from the imagery; **change type** per polygon from RGB evidence + facility; one-line "why" | Part 5: from "pixels changed" to "what happened" |

The cleanup (1-px opening + 2,000 m² minimum mapping unit) deliberately drops features narrower than about 20 m or smaller than 2,000 m², such as haul-road edges and single noisy pixels.

![methods](docs/figures/03_method_comparison.png)

*The provided baseline against zone-wise IR-MAD and the robust CVA of this work.*

| Method | Changed area | Why it behaves this way |
|---|---:|---|
| Provided Euclidean distance + Otsu | 111.4 km² (79 / 43 / 16 % per zone) | No radiometric model: it maps seams and haze |
| Zone-wise IR-MAD (Nielsen 2007) | 108.2 km² | It converges on woodland (ρ ≈ 0.99). With only three correlated visible bands, every other cover then looks "changed" |
| **Robust CVA, calibrated (this work)** | **4.13 km² (1.1 / 1.0 / 2.0 % per zone)** | Seam-free, mine-concentrated |

> [!TIP]
> **Why no machine learning:** there are no labels, only three bands and two dates. With labelled polygons, a Siamese U-Net or a change transformer on multi-date stacks would be the next step, and this pipeline would become its data and QA layer.

## 2. Results

Where the change is, and what kind it is.

![change types](docs/figures/10_change_types.png)

*Change polygons by type and area per type: hypotheses from RGB evidence and facility context.*

**Per facility** (outlines from the data inside analyst-defined regions; `configs/facility_register.yaml`):

| Facility | Area | Changed | Mostly |
|---|---:|---:|---|
| Tailings storage facility (TSF) | 22.44 km² | **1.94 km² (9 %)** | tailings deposition |
| South pit complex | 20.09 km² | 0.38 km² (2 %) | pit / bench activity |
| Eastern waste dumps | 4.35 km² | 0.36 km² (8 %) | waste dumping |
| North-west pit complex | 11.17 km² | 0.34 km² (3 %) | waste dumping |
| Process plant | 1.95 km² | 0.05 km² | infrastructure |
| Settlement, camp, fresh-water dam | 18.15 km² | 0.004 km² | — |

![facility register](docs/figures/09_facility_register.png)

*The facility register: outlines from the imagery and the change inside each facility.*

**Per change type** (mapped onto the brief's categories):

| Change type | Brief category | Polygons | Area |
|---|---|---:|---:|
| Tailings deposition | mining activity | 61 | 1.85 km² |
| Waste dumping | mining activity | 55 | 1.29 km² |
| Seasonal vegetation or burn | seasonal variation / vegetation | 18 | 0.36 km² |
| Pit excavation | mining activity | 26 | 0.26 km² |
| Water level change | water change | 8 | 0.18 km² |
| Noise or artifact | noise or artifacts | 27 | 0.09 km² |
| Infrastructure / settlement | urban expansion | 9 | 0.04 km² |
| Unexplained | — | 6 | 0.05 km² |
| Land clearing (incl. at the mine edge) | land clearing | 5 | 0.01 km² |

## 3. Interpretation

Each pattern is a hypothesis with its confounders and the evidence that would confirm it.

| Pattern | Most likely meaning | Confidence | Confounders (RGB-only) | How to confirm |
|---|---|---|---|---|
| Moving bright/dark patches on the TSF beaches | Tailings deposition: wet slurry moving between spigot points while dry beaches bleach | Medium–high | Surface drying with the season | Later dates: the TSF outline stays put while patches move |
| Brighter **and redder** faces at dumps and pit-complex edges | Fresh, oxidized waste rock tipped | Medium | Illumination change on slopes (sun 10° higher, 15° further north-east), dust | A later same-season date; very-high-resolution imagery |
| Bench-scale changes inside pits | Excavation and haulage | Low–medium | Shadow changes from sun geometry | Sun-matched pairs; elevation data (not in this brief) |
| Small water-edge changes | Pond and pit-lake level shifts | Medium | Haze lowers T2 brightness; water needs both-date evidence | The water rule on more dates |
| Darker or browner patches outside the mine | Dry-season grass senescence or burning | Medium | Burnt vegetation vs. wetness cannot be separated in RGB | Fire records; recovery after the rains |
| Polygons on or near seams | Artifacts | High | — | The source-scene map |

**What happened, in one paragraph.** Between early July and late August / early September 2023, the measurable change is operational. Tailings were deposited across the TSF (1.94 km² changed, 9 % of the facility). Waste rock was tipped at the eastern dumps and at the edges of both pit complexes (1.29 km² labelled waste dumping). Activity continued inside the pits. Outside the mine, change is small and seasonal. **I see no evidence of mine expansion into new ground at the 2,000 m² / 20 m detection limit**: only 0.01 km² qualifies as clearing. Each polygon carries its "why".

> [!WARNING]
> **Confidence is not accuracy.** "Confidence" here is a change-magnitude score, and every label is a hypothesis built from RGB evidence and facility context. There is no reference data, so accuracy is not assessed (section 6).

## 4. Sensitivity

Does the answer depend on one threshold choice?

![sensitivity](docs/figures/11_sensitivity.png)

*Total change shrinks smoothly as the test gets stricter, while the share inside the mine stays high.*

I re-ran the binary rule over per-pixel false-alarm rates from 1e-2 to 1e-5 and minimum mapping units from 1,000 to 10,000 m² (16 settings):
- **Changed area** ranges over **2.10–6.82 km²**, versus 111.4 km² for the baseline.
- **Share inside the mine's facilities** stays between **61 % and 77 %**, and rises as the test gets stricter: the strongest changes are the mining ones.

The conclusions do not depend on one threshold choice.

## 5. System and stack

The analysis is one Python package (`s2cd`); everything else exists so a reader can check it without installing anything. The pipeline writes files, `app.publish` loads them into PostGIS, a read-only API sits in front of the database, and a static viewer talks to that API.

![Architecture: data, pipeline, storage, app server and browser, with the technology of each part](docs/figures/12_architecture.png)

*Where each piece runs. The pipeline writes files, `app.publish` loads them into PostGIS under an owner role, and the API reads them through the `s2cd_reader` role. Source: `docs/architecture.mmd` (Mermaid, rendered with the Iconify *logos* pack).*

<details>
<summary>Pipeline and services in detail (flowchart)</summary>

```mermaid
flowchart TB
  subgraph IN["1. Inputs"]
    direction LR
    RAS["Provided rasters<br/>B02 / B03 / B04, 2 nominal dates"]
    STAC["STAC: Planetary Computer<br/>B02-B04 only"]
  end
  subgraph PIPE["2. Python pipeline (package s2cd)"]
    direction LR
    IO["io<br/>load, stack"] --> PROV["provenance<br/>source scenes"] --> RAD["radiometry<br/>zones, normalize"] --> METH["methods<br/>robust CVA, baseline, IR-MAD"] --> VEC["vectorize<br/>hysteresis, MMU"] --> INT["rgb, register, interpret<br/>type + why"]
  end
  subgraph OUT["3. Outputs"]
    direction LR
    TIF["GeoTIFF / COG"]
    GJ["GeoJSON"]
    SQL["SpatiaLite"]
  end
  PUB["4. app.publish<br/>owner role, idempotent"]
  PG[("5. Supabase PostGIS<br/>read-only role s2cd_reader")]
  subgraph API["6. FastAPI app"]
    direction LR
    DBX["DB explorer<br/>SQL guard"]
    AOI["AOI stats"]
    CMP["Compare jobs<br/>monthly scene catalogue"]
    AGT["Analyst agent<br/>deepagents + Gemini via OpenRouter<br/>typed tools + knowledge notes"]
  end
  VIEW["7. Viewer<br/>Vite, TypeScript, MapLibre GL"]
  HOST["VPS at s2change.dzaky.ai.id<br/>Caddy (TLS, static files, /api proxy) + PM2 + uv"]
  IN --> PIPE --> OUT --> PUB --> PG --> API --> VIEW
  HOST -.- VIEW
  HOST -.- API
```

</details>

One analyst question, end to end. Every number comes from a typed tool that runs as the read-only role (its `readonly_sql` tool goes through the same SQL guard as the Database tab), and map changes are returned as data for the viewer to apply.

```mermaid
sequenceDiagram
  autonumber
  actor U as Analyst
  participant V as Viewer
  participant A as FastAPI /api/agent/chat
  participant G as Agent (deepagents, Gemini)
  participant T as Typed tools
  participant D as PostGIS (s2cd_reader)
  U->>V: "Where was waste rock tipped?"
  V->>A: POST messages (rate-limited, daily cap)
  A->>G: run_chat(messages)
  G->>T: read_context, facility_summary, top_changes
  T->>D: parameterised SELECT
  D-->>T: rows
  T-->>G: numbers + ids
  G->>T: map_action(fly_to / highlight), make_chart
  Note over T: actions, charts and tables are collected per request
  G-->>A: answer text
  A-->>V: answer + map actions + charts + tool calls
  V-->>U: text in chat, map moves, polygons highlighted
```

| Layer | Technology | Why |
|---|---|---|
| Environment and quality | <img src="https://cdn.simpleicons.org/python" alt="" height="16"> Python 3.12, <img src="https://cdn.simpleicons.org/uv" alt="" height="16"> uv, <img src="https://cdn.simpleicons.org/ruff" alt="" height="16"> ruff, <img src="https://cdn.simpleicons.org/pytest" alt="" height="16"> pytest | One lockfile, one command to run, one linter; tests run in CI |
| Raster and numerics | rasterio, <img src="https://cdn.simpleicons.org/numpy" alt="" height="16"> numpy, <img src="https://cdn.simpleicons.org/scipy" alt="" height="16"> scipy, scikit-image | Windowed GeoTIFF/COG I/O, the robust covariance and statistics, hysteresis and morphology |
| Vector | <img src="https://cdn.simpleicons.org/geopandas" alt="" height="16"> geopandas, shapely, pyogrio, pyproj | Polygons, validity repair, EPSG:32735 areas in m² |
| Source scenes | pystac-client, odc-stac, planetary-computer | Optional extra: recovers the true acquisitions behind each mosaic, B02-B04 only |
| Storage | <img src="https://cdn.simpleicons.org/postgresql" alt="" height="16"> PostGIS 16 with a GiST index, <img src="https://cdn.simpleicons.org/supabase" alt="" height="16"> Supabase, <img src="https://cdn.simpleicons.org/sqlite" alt="" height="16"> SpatiaLite, <img src="https://cdn.simpleicons.org/sqlalchemy" alt="" height="16"> SQLAlchemy, GeoAlchemy2, psycopg | A geospatial DB as the brief asks; the app reads through a `SELECT`-only role |
| API | <img src="https://cdn.simpleicons.org/fastapi" alt="" height="16"> FastAPI, uvicorn, slowapi, sqlglot | Typed endpoints, per-route rate limits, SQL parsed and checked before it runs |
| Analyst | <img src="https://cdn.simpleicons.org/langchain" alt="" height="16"> LangChain deepagents, <img src="https://cdn.simpleicons.org/googlegemini" alt="" height="16"> langchain-google-genai, <img src="https://cdn.simpleicons.org/openrouter" alt="" height="16"> langchain-openai (OpenRouter) | An agent restricted to typed tools plus knowledge notes, on Gemini (directly or via OpenRouter) |
| Viewer | <img src="https://cdn.simpleicons.org/vite" alt="" height="16"> Vite, <img src="https://cdn.simpleicons.org/typescript" alt="" height="16"> TypeScript, <img src="https://cdn.simpleicons.org/maplibre" alt="" height="16"> MapLibre GL, marked, DOMPurify, <img src="https://cdn.simpleicons.org/mermaid" alt="" height="16"> Mermaid | Static bundle, no server rendering; this report is rendered in the app from the same markdown |
| Delivery | <img src="https://cdn.simpleicons.org/caddy" alt="" height="16"> Caddy, <img src="https://cdn.simpleicons.org/pm2" alt="" height="16"> PM2, <img src="https://cdn.simpleicons.org/githubactions" alt="" height="16"> GitHub Actions, <img src="https://cdn.simpleicons.org/docker" alt="" height="16"> Docker Compose, Dockerfile | TLS and static files from one binary; CI runs ruff, pytest, the viewer build and the image; Compose gives a local PostGIS |

## 6. Limitations and next steps

What this analysis cannot tell, and what would close each gap.

> [!WARNING]
> **No accuracy assessment.** There is no reference data. The false-alarm rate is calibrated and measured on held-out stable pixels, but missed change and the correctness of the change types are not. The next step is a stratified random sample labelled on very-high-resolution imagery (Olofsson et al. 2014), with strata from this map and the baseline so both get scored.

- **RGB only.** No vegetation-health, burn-severity or mineral indices. Water, shadow and dark wet soil can be confused, so the water rule requires both dates. The thresholds in `rgb.py` are tuned to this AOI.
- **Two mosaicked dates.** Sun and view geometry differ between orbits (BRDF). Normalization removes a per-zone linear difference but not cover-specific angular effects. Measured co-registration error is small: ≤ 0.05 px mean shift per zone, ≤ 0.25 px in any window.
- **No elevation.** Pits and dumps cannot be separated reliably, and no volumes can be computed. Stereo VHR imagery or LiDAR would be the next data for that.
- **Facility register.** The regions are analyst-defined boxes (human-in-the-loop); outlines come from the imagery. For production, use licence boundaries or a maintained facility database.
- **For production monitoring:** carry per-pixel acquisition provenance instead of nominal mosaic dates; use same-season pairs; calibrate thresholds per scene pair as done here; validate with VHR samples; store per-pair runs in PostGIS (the schema is already run-keyed).

<details>
<summary>References</summary>

Malila (1980) change vector analysis; Otsu (1979); Canny (1986) hysteresis; Nielsen (2007) IR-MAD; Canty & Nielsen (2008) *Remote Sens. Environ.* 112:1025 (automatic radiometric normalization); Richardson et al. (2007) / Sonnentag et al. (2012) green chromatic coordinate (here on BOA reflectance, not camera DN); Olofsson et al. (2014) *Remote Sens. Environ.* 148:42.

</details>
