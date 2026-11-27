<img src="figures/favicon.svg" alt="s2change logo" width="56" height="56">

# What changed at an open-pit copper mine in NW Zambia?

**Two free satellite images of one copper mine: what changed, what it most likely is, and who should act on it.** [Open the map](https://s2change.dzaky.ai.id/#/map) · [Ask the agent](https://s2change.dzaky.ai.id/#/map?agent=1) · [Explore the database](https://s2change.dzaky.ai.id/#/database) · [Downloads](https://s2change.dzaky.ai.id/#/downloads) · [Repository](https://github.com/dzakybd/s2change) · [Video](https://youtu.be/MBTmg62P2ew)

<a href="https://youtu.be/MBTmg62P2ew"><img src="figures/video_thumb.jpg" width="720" alt="Video: 4.5-minute technical walkthrough on YouTube"></a>

*Prefer watching? A 4.5-minute [technical walkthrough](https://youtu.be/MBTmg62P2ew): the data, the data trap, the method, the database, results, accuracy and the system.*

> [!NOTE]
> **RGB only.** The brief provides Sentinel-2 L2A bands B02, B03 and B04, and nothing else is used: no NIR or SWIR, no cloud mask, no elevation. The layout matches the Lumwana copper mine; the brief only says "an open-pit mining site in Zambia", so the name is an inference.

The report follows the brief: **[1. Method](#-1-method)** (which algorithm, and why), **[2. Results](#-2-results)** (where change occurs, and its pattern) and **[3. Interpretation](#-3-interpretation)** (what the changes most likely represent). **[4. The system](#-4-the-system-stack-agent-and-database)** (stack, AI agent, database) and **[5. Limitations](#-5-limitations-and-next-steps)** follow.

## ⚡ The answer on one page

<table class="kpi">
<tr>
<td align="center"><b>4.13 km²</b><br>🛰️ changed<br><sub>in 215 polygons</sub></td>
<td align="center"><b>82 %</b><br>🏭 inside the mine<br><sub>in the 8 named facilities</sub></td>
<td align="center"><b class="good">27× less</b><br>🪤 than the provided method<br><sub>which maps seams as change</sub></td>
<td align="center"><b>0.67</b><br>🎯 precision<br><sub>on 200 random points</sub></td>
</tr>
</table>

![at a glance](figures/00_at_a_glance.png)

- ⛏️ **The mine was working, not spreading.** Tailings spread, waste tipped, pits worked; outside the mine only 0.02 km² is clearing.
- 🧱 **Fresh fill on the tailings dam's south-east wall, likely a wall raise:** <b class="hl">0.39 km²</b> where vegetation was, made between *4 Jul and 8 Aug*. The first thing a dam-safety team would want to see.
- 🪤 **The data had a trap.** Each supplied "date" is two satellite passes stitched together; the provided method maps the stitch as change (<b class="bad">111 km²</b>). Found, proved and corrected ([the data trap](#the-data-trap-each-date-is-two-passes)).
- 🎯 **Precise but conservative.** Right <b class="good">2 times in 3</b> when it says "changed" (the provided method: <b class="bad">1 in 8</b>), but it finds only <b class="hl">about a fifth</b> of all real change ([how accurate](#how-accurate-is-it)).
- 📈 **Over three years the footprint grows** (exploratory): change outside the named facilities rises from 3.3 to <b class="hl">14.3 km²</b>.

## ⚙️ 1. Method

🧮 **Algorithm.** Two methods on the same stacks, stored in the same database:

- **The provided example, unchanged:** Euclidean distance across the three bands, plus an Otsu threshold for its binary map (`change_map_baseline.tif`, `change_binary_baseline.tif`).
- **This work: a calibrated change vector analysis** (Malila 1980). How far did each pixel's colour move, compared with how far colour moves on ground that did not change? (`change_map.tif`, `change_binary.tif`)

🤔 **Why this method.** The example fails on this data in three ways, and each has a fix:

| | Problem with the example | Measured | Fix here |
|---|---|---|---|
| 🧩 | **The images are mosaics**: brightness jumps at the seams read as change | <b class="bad">111 km²</b>, 42 % of the area | Correct each seam-bounded zone on its own ([the data trap](#the-data-trap-each-date-is-two-passes)) |
| 🌗 | **Noise is not the same everywhere**: bright mine ground varies more than dark forest | one noise level: <b class="bad">22×</b> too many false alarms, <b class="bad">66×</b> on the brightest ground | Measure noise per zone and per brightness level |
| 🎚️ | **Its threshold has no meaning**: with no labels, a cut-off must say how often noise passes it | — | "Noise may pass 1 pixel in 1,000"; held-out stable pixels give <b class="good">0.09–0.11 %</b> |

The 1-in-1,000 rate is per pixel on ground screened as unchanged, the design target; how often a flagged polygon is real is measured separately ([how accurate](#how-accurate-is-it)). Co-registration was checked per zone (phase correlation, 8 windows): shifts are at most 0.05 px, so nothing is resampled. A full run takes 13.6 s on a laptop.

![Method overview: each date is two satellite passes; the provided example flags 111.4 km², mostly seams; this work splits the site into three zones, normalises each, scores change against measured noise and keeps 4.13 km² of change](figures/04_method_overview.png)

*Both methods on the same input. The maps are the real data: source pass per pixel, the three zones, and each method's change mask. Editable source: [`docs/figures/04_method_overview.drawio`](https://github.com/dzakybd/s2change/blob/main/docs/figures/04_method_overview.drawio).*

![From input to output: the two dates as band layers, five analysis steps, and the typed, explained result](figures/01_flow.png)

*Every layer is the real data around the same place, the tailings dam's south-east wall. The output is a typed polygon with a plain sentence, stored and shown on the [map](https://s2change.dzaky.ai.id/#/map).*

🪜 **Each step, close-up and whole site:**

| Step | Close-up: the tailings dam wall | Whole site | What we do, and why |
|---|---|---|---|
| 📥 Input<br>**Before** | <img src="figures/close_01.jpg" width="200" alt="Before image, close-up"> | <img src="figures/wide_01.jpg" width="200" alt="Before image, whole site"> | The provided image for the nominal **12 Aug 2023**, as true colour (bands B04, B03, B02). Pixel numbers are turned into reflectance with the L2A scale and offset. |
| 📥 Input<br>**After, as delivered** | <img src="figures/close_02.jpg" width="200" alt="After image as delivered, close-up"> | <img src="figures/wide_02.jpg" width="200" alt="After image as delivered, whole site"> | The provided image for **2 Sep 2023**. It looks brighter and hazier, partly because it is stitched from different satellite passes than the before image. |
| 🔬 Analysis<br>**1. Find the seams** | <img src="figures/close_03.jpg" width="200" alt="Seam zones, close-up"> | <img src="figures/wide_03.jpg" width="200" alt="Seam zones, whole site"> | Each date is stitched from two passes. Matching each pixel to its source scene splits the site into **3 zones** (tinted), with a seam strip between them. Each zone is then corrected on its own ([the data trap](#the-data-trap-each-date-is-two-passes), [on the map](https://s2change.dzaky.ai.id/#/map?layer=baseline&oblique=0)). |
| 🔬 Analysis<br>**2. Normalise** | <img src="figures/close_04.jpg" width="200" alt="After image normalised, close-up"> | <img src="figures/wide_04.jpg" width="200" alt="After image normalised, whole site"> | In each zone and band, the after image is fitted to the before image on ground that did not change, ignoring outliers (after Canty & Nielsen 2008). Haze and brightness differences go; real change stays. |
| 🔬 Analysis<br>**3. Measure change** | <img src="figures/close_05.jpg" width="200" alt="Change intensity, close-up"> | <img src="figures/wide_05.jpg" width="200" alt="Change intensity, whole site"> | For each pixel, measure how much its colour changed, then compare that with how much colour changes on stable ground in the same zone and at the same brightness. Brighter means less likely to be noise. [Change intensity on the map](https://s2change.dzaky.ai.id/#/map?layer=intensity) |
| 🔬 Analysis<br>**4. Threshold** | <img src="figures/close_06.jpg" width="200" alt="Raw threshold, close-up"> | <img src="figures/wide_06.jpg" width="200" alt="Raw threshold, whole site"> | Keep the pixels that noise would produce only **1 time in 1,000**, with the threshold calibrated on held-out stable pixels. Result: 6.74 km² in 4,479 specks, including thin lines along the seams. |
| 🔬 Analysis<br>**5. Clean** | <img src="figures/close_07.jpg" width="200" alt="Cleaned change mask, close-up"> | <img src="figures/wide_07.jpg" width="200" alt="Cleaned change mask, whole site"> | Keep a region only if part of it is very strong (noise would produce it 1 time in 100,000), so faint seam lines drop out (hysteresis, Canny 1986). Then smooth edges by one pixel and drop regions under 2,000 m². Result: **4.13 km² in 215 regions**. |
| 📤 Output<br>**Outline, type and explain** | <img src="figures/close_08.jpg" width="200" alt="Typed change polygons, close-up"> | <img src="figures/wide_08.jpg" width="200" alt="Typed change polygons, whole site"> | Each region becomes a polygon. Its type comes from the colour change and the facility it sits in, and it gets one plain sentence. Here: **Tailings dam wall likely raised**, 0.39 km² in the south-east of the tailings storage facility, where green vegetation became bright, rust-coloured bare ground. *Check the dam-raise plan and inspect the embankment.* |

![methods](figures/03_method_comparison.png)

*The provided example (Euclidean distance + Otsu) maps 111.4 km², mostly seams and haze. The calibrated method maps 4.13 km², concentrated in the mine.*

### The data trap: each "date" is two passes

> **Claim.** Neither supplied "date" is one image. Each file is stitched from two Sentinel-2 passes taken days apart, so the pair holds brightness jumps that are not on the ground, and before and after are really 52–60 days apart, not 21.

<table class="kpi">
<tr>
<td align="center"><b class="bad">0</b><br>scenes on 12 Aug 2023<br><sub>passes on 8, 10 and 13 Aug only</sub></td>
<td align="center"><b>2</b><br>passes in each file<br><sub>joined at a straight 12.6° seam</sub></td>
<td align="center"><b class="good">100 %</b><br>identical to one pass, west of the seam<br><sub>and 0 % east of it</sub></td>
<td align="center"><b class="bad">52–60</b><br>days between the real passes<br><sub>in three zones, not 21</sub></td>
</tr>
</table>

🔎 **Proof 1. One side is a known pass, the other is not.** With no 12 Aug scene to compare against, each file was set beside the single pass that covers the whole site and is found inside it: before with 4 Jul, after with 2 Sep. West of the seam <b class="good">every pixel is identical</b> in all three bands; east of it, <b class="bad">none is</b>. The brightness steps exactly at the seam (e), through bush, roads and the tailings pond alike: an orbit edge, not the ground.

![Each supplied file beside the full-coverage single pass found inside it, with the brightness profile across its seam](figures/07_trap_proof.png)

*Before with before, after with after: identical on one side of the yellow line, different on the other (`scripts/fig_trap_proof.py`).*

🧬 **Proof 2. Every pixel traced to its scene.** All 55–56 catalogue scenes of the site in the 75 days before each date were read onto the same grid and matched on exact stored values (`s2cd provenance`). Two scenes per date reproduce every pixel, bit for bit; their catalogue records give the true dates. The two files switch pass at slightly different lines, so laid over each other they cut the site into **three zones**, each one real before → after pair:

![Source pass of every pixel on both dates, the three zones they form, and the real time gap of each](figures/06_provenance.png)

*The seams follow the swath edge of orbit 78, which covers only the east of the site.*

🛠️ **What the pipeline does with it: three pairs, each handled on its own.** The pairs are *not chosen*, the files fix them: in each zone, **before** is the pass the "12 Aug" file holds there and **after** is the pass the "2 Sep" file holds.

| Zone | Before → after (true dates) | Gap | Share of the site |
|---|---|---:|---:|
| 🟦 West | 4 Jul → 2 Sep | 60 days | 38.9 % |
| ▨ Middle strip | 4 Jul → 25 Aug | 52 days | 5.1 % |
| 🟧 East | 1 Jul → 25 Aug | 55 days | 56 % |

In each zone, separately:

- ⚖️ **Normalise:** the after image is mapped onto the before image with an orthogonal regression on unchanged pixels (Canty & Nielsen, 2008), so a different pass's haze and sun do not read as change.
- 📏 **Noise model and threshold:** learned per zone and brightness level, then calibrated on held-out stable pixels to <b class="good">0.1 %</b> false alarms.
- ✂️ **Seams:** the 90 m on each side is left out (2.4 % of the area); a seam is a jump in pass, not on the ground.
- 📅 **Dates:** every polygon stores its zone, so its true dates are known. Example: 0.40 of the 0.44 km² dam-wall fill lies in the east zone, so *1 Jul → 25 Aug*.

*Swapping in other passes is a different analysis: Proof 3 below.* [See the trap on the map](https://s2change.dzaky.ai.id/#/map?layer=baseline&oblique=0): the provided method flags the western pass almost entirely.

🔁 **Proof 3. True single passes change the answer.** The same pipeline re-run on single passes that cover the whole site (`scripts/real_dates.py`; on the map: Result → "Ours, true passes"; scenes in Downloads):

| Pair | Provided method | This method | Dam-wall fill |
|---|---:|---:|---:|
| The provided mosaics (nominal 12 Aug → 2 Sep) | <b class="bad">111 km²</b> | <b class="good">4.13 km²</b> | 0.44 km² |
| Single pass, 8 Aug → 2 Sep 2023 (the clear dates closest to the nominal ones) | <b class="bad">194 km²</b> | 2.25 km² | 0.10 km² |
| Single pass, 4 Jul → 8 Aug 2023 | 4.6 km² | 12.1 km² | 0.44 km² |

- Without seams the provided method gets *worse* (<b class="bad">194 km²</b>): its real weakness is the haze and brightness difference between dates, which the normalisation removes.
- The dam-wall fill happened <b class="hl">between 4 Jul and 8 Aug</b>, before the nominal before date; the mosaics catch it only because their "12 Aug" is really early July. The provided pair stays the main result, because it is what the brief supplies.

🧪 **Was the data already corrected?** Yes:

- **ESA Level-2A, bit for bit** (the pixel match above): calibrated, orthorectified, atmospherically corrected.
- No level tag in the files; the −1000 offset in the darkest pixels agrees. Reflectance = `(DN − 1000) / 10000`.

| Correction | In the supplied data | Added here |
|---|---|---|
| Calibration, orthorectification, atmosphere | Done by ESA | Nothing redone; L2A scale and offset applied |
| Haze and scene-to-scene differences, seams | **Not corrected** | Per-zone normalisation on stable pixels, seam buffer |
| Sun and view angle (BRDF) | Not corrected | Treated as a confounder on slopes |

### Does it depend on one threshold?

No. Across 16 settings (false-alarm rates 1 in 100 to 1 in 100,000; minimum areas 1,000–10,000 m²) the changed area stays within <b class="good">2.1–6.8 km²</b> and the share inside the 5 mining facilities within <b class="good">68–88 %</b>, highest at the stricter settings: the strongest changes are the mining ones.

![sensitivity](figures/11_sensitivity.png)

## 📍 2. Results

<table class="kpi">
<tr>
<td align="center"><b>53 %</b><br>on the tailings facility<br><sub>2.19 of 4.13 km²</sub></td>
<td align="center"><b>3.4 km²</b><br>mining activity<br><sub>tailings, dumps, pits, dam wall</sub></td>
<td align="center"><b>0.39 km²</b><br>fresh fill on the dam wall<br><sub>likely a wall raise</sub></td>
<td align="center"><b>0.02 km²</b><br>clearing outside the mine<br><sub>no urban expansion</sub></td>
</tr>
</table>

📍 **Where.** Change concentrates in the mine: 82 % lies inside the eight named facilities, more than half on the tailings storage facility. Outside, change is scattered and small.

🔍 **Pattern.** Patches moving on the tailings facility, brighter and redder faces at dumps and pit edges, bench changes in the pits, and a long strip of fresh fill on the dam's south-east wall. No straight seam lines are left.

![change types](figures/10_change_types.png)

*Change polygons by type, and the area of each type.*

| | Change type | Brief category | Polygons | Area | What we see |
|---|---|---|---:|---:|---|
| <img src="figures/icon_tailings_deposition.svg" width="20" alt=""> | Tailings deposition | mining activity | 63 | 1.86 km² | Wet slurry and drying beaches moving on the tailings facility |
| <img src="figures/icon_waste_dumping.svg" width="20" alt=""> | Waste dumping | mining activity | 47 | 0.75 km² | Brighter, redder faces at dumps and pit edges: fresh oxidised rock |
| <img src="figures/icon_tailings_dam_raise.svg" width="20" alt=""> | Tailings dam wall raised | mining activity | 4 | 0.44 km² | Fresh fill on the embankment where vegetation was |
| <img src="figures/icon_seasonal_vegetation_or_burn.svg" width="20" alt=""> | Seasonal vegetation or burn | seasonal / vegetation | 18 | 0.36 km² | Dry-season browning or burn scars outside the mine |
| <img src="figures/icon_pit_excavation.svg" width="20" alt=""> | Pit excavation | mining activity | 33 | 0.40 km² | Bench and pit-floor changes |
| <img src="figures/icon_water_level_change.svg" width="20" alt=""> | Water level change | water change | 8 | 0.18 km² | Pond and pit-lake edges moving |
| <img src="figures/icon_unexplained.svg" width="20" alt=""> | Noise, unexplained, infrastructure, clearing | other | 42 | 0.15 km² | Seams, haze, small builds; 0.02 km² of clearing |

🏭 **Per facility.** The last row combines the three facilities with almost no change.

| Before → after (change in yellow) | Facility | Area | Changed | Mostly |
|---|---|---:|---:|---|
| <img src="figures/TSF_before.webp" alt="TSF before" width="64"> <img src="figures/TSF_after.webp" alt="TSF after" width="64"> | <img src="figures/fac_TSF.svg" width="20" alt=""> Tailings storage facility (TSF) | 21.84 km² | <b class="hl">2.19 km² (10 %)</b> | tailings deposition, dam-wall fill |
| <img src="figures/PIT-S_before.webp" alt="PIT-S before" width="64"> <img src="figures/PIT-S_after.webp" alt="PIT-S after" width="64"> | <img src="figures/fac_PIT-S.svg" width="20" alt=""> South pit complex | 24.84 km² | 0.55 km² (2 %) | pit / bench activity |
| <img src="figures/PIT-NW_before.webp" alt="PIT-NW before" width="64"> <img src="figures/PIT-NW_after.webp" alt="PIT-NW after" width="64"> | <img src="figures/fac_PIT-NW.svg" width="20" alt=""> North-west pit complex | 13.16 km² | 0.41 km² (3 %) | waste dumping |
| <img src="figures/WD-E_before.webp" alt="WD-E before" width="64"> <img src="figures/WD-E_after.webp" alt="WD-E after" width="64"> | <img src="figures/fac_WD-E.svg" width="20" alt=""> Eastern waste dumps | 3.30 km² | 0.21 km² (6 %) | waste dumping |
| <img src="figures/PLANT_before.webp" alt="PLANT before" width="64"> <img src="figures/PLANT_after.webp" alt="PLANT after" width="64"> | <img src="figures/fac_PLANT.svg" width="20" alt=""> Process plant | 0.95 km² | 0.01 km² | infrastructure |
| <img src="figures/TOWN_before.webp" alt="TOWN before" width="64"> <img src="figures/TOWN_after.webp" alt="TOWN after" width="64"> | <img src="figures/fac_TOWN.svg" width="20" alt=""> Settlement, camp, fresh-water dam | 15.24 km² | 0 km² | — |

Facility outlines are analyst-drawn on very-high-resolution imagery, checked against the 2023 image, and do not overlap (`configs/facility_register.yaml`; [on the map](https://s2change.dzaky.ai.id/#/map?layer=facilities)).

🌿 **Land cover before → after**, a check without the register: simple RGB rules sort changed pixels into water, bright surface, bare ground and vegetation. Vegetation → bare ground (0.62 km², 15 %) is the new disturbance, much of it along the dam wall; most of the rest is bare ground re-worked (30 %) or turned to bright tailings (18 %).

📈 **Over three years** *(exploratory, not validated)*. The supplied before date against later August scenes, so the season matches. Beyond the brief; no accuracy check.

![multi-year](figures/13_multi_year.png)

*"Outside" means outside all 8 register facilities. Area of change counts re-worked ground too, so it is not all new disturbance.*

Compare in the app: [Aug 2023 → Aug 2024](https://s2change.dzaky.ai.id/#/map?before=provided-before&after=2024-08-27) · [→ Aug 2025](https://s2change.dzaky.ai.id/#/map?before=provided-before&after=2025-08-07) · [→ Aug 2026](https://s2change.dzaky.ai.id/#/map?before=provided-before&after=2026-08-14)

### How accurate is it?

<table class="kpi">
<tr>
<td align="center"><b class="good">0.67</b><br>precision<br><sub>right 2 times in 3 when it says "changed"</sub></td>
<td align="center"><b class="bad">0.17</b><br>recall<br><sub>finds about a fifth of real change</sub></td>
<td align="center"><b>0.94</b><br>overall accuracy<br><sub>provided method: 0.62</sub></td>
<td align="center"><b>16.6 km²</b><br>estimated real change<br><sub>± 10.9; the 4.13 km² is its core</sub></td>
</tr>
</table>

200 random points, 50 in each of four groups (both maps say change, only this map, only the provided method, neither), so one sample scores both maps without bias (Olofsson et al. 2014).

🏷️ **How the points were labelled:**

- **Two independent AI vision labellers**, from before/after image chips, blind to both maps.
- Agreement 80 % (Cohen's κ 0.46, moderate); the 40 disagreements settled by a third look with a wider view.
- Visual labels from the same 10 m imagery, not field truth (`scripts/validation.py`, `data/validation/`).

| 95 % intervals | This method | Provided method |
|---|---:|---:|
| Pixels labelled correctly (overall accuracy) | <b class="good">0.94 ± 0.04</b> | 0.62 ± 0.04 |
| When it says "changed", it is right (precision) | <b class="good">0.67 ± 0.11</b> | <b class="bad">0.12 ± 0.08</b> |
| Share of all real change it finds (recall) | <b class="bad">0.17 ± 0.11</b> | 0.80 ± 0.31 |

💡 **What this means.** *Precise but conservative.* Where both maps agree, 72 % of points really changed; 16 of the 21 real extra detections of this map are fresh burn scars the provided method misses. What it misses is subtle: tailings beaches wetting and drying, burn edges, small waste tips. Recall intervals are wide because real change is rare in the large "no change" groups; more points there would tighten them.

## 🧭 3. Interpretation

🧭 **What the changes represent**, in the brief's categories: the mine is operating normally.

| Category | Area | What it is |
|---|---:|---|
| ⛏️ Mining activity | <b class="hl">3.4 km²</b> | Tailings deposition, waste dumping, pit work, a likely dam-wall raise |
| 🌾 Seasonal vegetation | 0.36 km² | Dry-season browning or burns outside the mine |
| 🪓 Land clearing | 0.02 km² | Outside the mine; no urban expansion |
| 〰️ Noise and artifacts | 0.15 km² | Mostly removed by the seam correction; the rest labelled, with unexplained and small builds |

Over three years, in the same dry season, the mine grows and more of the change sits outside the named facilities.

📖 **How to read each pattern.** Every type is a hypothesis, with what could fake it in RGB and what would confirm it:

| Pattern | Likely meaning | Could also be | Confirm with |
|---|---|---|---|
| Bright/dark patches moving on the TSF | Tailings deposition | Beaches drying with the season | Later dates: outline stays, patches move |
| Brighter and redder faces at dumps | Fresh waste rock | Sun 10° higher on slopes, dust | A same-season date; very-high-resolution imagery |
| Bench changes inside pits | Excavation | Shadows shifting with the sun | Sun-matched pairs; elevation data |
| Browner patches outside the mine | Grass drying or burning | Wet ground (RGB cannot separate them) | Fire records; recovery after rain |

> [!WARNING]
> **Confidence is not accuracy.** "Confidence" is a change-magnitude score. Precision and recall are measured on a random sample ([how accurate](#how-accurate-is-it)); a polygon's confidence is not its probability of being right.

👥 **Who acts on it.**

| Who | Their question | What this gives them | What it cannot say |
|---|---|---|---|
| 🏛️ Regulator | Is the mine within its licence? | Footprint growth outside known facilities, located and sized monthly at no data cost | Legality: there are no licence boundaries here |
| 🛡️ Dam-safety oversight | Is the tailings facility run as planned? | Where deposition, ponding and wall raises happen | Stability, freeboard, seepage |
| 💼 Investors, lenders, insurers | Is the operation running and growing? | Independent activity signals: waste moved, tailings deposited, pits worked | Production, grade or tonnes |
| 🌱 ESG and community teams | Is clearing or water change spreading? | Clearing and water change outside the register | Social or health impacts |

🔔 **As a service**, the pipeline runs monthly on a new clear scene, against last month and the same month last year; changes outside a facility or above a size limit become alerts for an analyst. Each comparison is one more run in the database, already keyed by run.

## 🏗️ 4. The system: stack, agent and database

One Python package (`s2cd`) does the analysis. Everything else lets a reader check the results without installing anything.

```mermaid
flowchart LR
  T["📥 GeoTIFFs, 2 dates<br/>+ 🛰️ STAC for provenance"] --> P["🐍 s2cd pipeline<br/>offline, writes files"]
  P -->|app.publish| DB[("🗄️ PostGIS<br/>Supabase, read-only")]
  DB --> A["⚡ FastAPI<br/>VPS: Caddy + PM2"]
  A --> G["🤖 Agent: deepagents<br/>+ ✨ Gemini 3.5 Flash"]
  B["🗺️ Viewer: map, report,<br/>database, chat"] <-->|HTTPS| A
```

| | Layer | Stack | Why |
|---|---|---|---|
| 🐍 | Pipeline | Python 3.12, uv, numpy, scipy, rasterio, scikit-image, geopandas, shapely | Windowed GeoTIFF I/O, robust statistics, morphology, valid polygons in metres (EPSG:32735) |
| 🛰️ | Provenance | pystac-client, Planetary Computer | Finds the true pass behind every pixel (optional extra) |
| 🗄️ | Database | **PostGIS 16** with GiST indexes on Supabase; **SpatiaLite** file offline | The geospatial DB the brief asks for; the app reads through a `SELECT`-only role |
| ⚡ | API | FastAPI, uvicorn, slowapi, sqlglot | Typed endpoints, rate limits, SQL parsed and checked before it runs |
| 🤖 | Agent | LangChain **deepagents** on **Gemini** | Answers questions about the results with typed tools only |
| 🗺️ | Viewer | Vite, TypeScript, MapLibre GL, marked, Mermaid | Static bundle; this report is rendered from the same markdown |
| 🚀 | Delivery | Caddy (TLS), PM2, GitHub Actions | One small VPS; CI runs ruff, pytest and the builds |

### 🤖 The analyst agent

*Ask in plain words; it answers with numbers from the database and moves the map.*

- 🧰 **Tools only, no free hand:** 12 typed tools. deepagents' own planning, file, shell and sub-agent tools are switched off: a question needs a few calls, and each extra tool is attack surface.
- 🔒 **Read-only by construction:** every query runs as `s2cd_reader` (`SELECT` only, 5 s timeout); free SQL passes a sqlglot guard (one `SELECT`, allow-listed tables, `LIMIT` ≤ 200).
- 🧾 **Shows its work:** each answer returns the tool calls, including the SQL, so a number can be checked.
- 🎯 **Honest framing:** the prompt states RGB-only evidence, the true pass dates, that confidence is signal strength (not accuracy) and that a change type is a hypothesis.
- 💸 **Cheap to run:** the starter questions are answered once and kept on disk; the LLM libraries load only when a new question comes in.

| Kind | Tools |
|---|---|
| 📊 Numbers | `list_runs`, `area_summary`, `change_type_summary`, `facility_summary`, `top_changes`, `explain_change` |
| 📚 Background | `read_context` (notes on the site, seasons, method, business use) |
| 🔎 Ad-hoc | `readonly_sql` (guarded) |
| 🗺️ Output | `map_action` (fly to, highlight), `make_chart`, `make_table` |

```mermaid
sequenceDiagram
  autonumber
  actor U as User
  participant V as Viewer
  participant A as FastAPI
  participant G as Agent (Gemini)
  participant D as PostGIS (read-only)
  U->>V: "Where was waste rock tipped?"
  V->>A: POST /api/agent/chat (rate-limited)
  A->>G: messages
  G->>D: facility_summary, top_changes (SELECT)
  D-->>G: numbers + polygon ids
  G-->>A: answer + map_action + chart
  A-->>V: answer, map actions, charts, tool calls
  V-->>U: text, map flies there, polygons highlighted
```

### 🗃️ The database

*Two runs (this method and the provided one) share one table; facilities are the analyst's register.*

```mermaid
erDiagram
  change_runs ||--o{ change_features : "run_id"
  facilities |o--o{ change_features : "facility_id (mostly inside)"
  facilities ||--o| facility_changes : "facility_id"
  change_runs {
    text run_id PK
    text method
    date date_before
    date date_after
    jsonb params "thresholds, zones"
    jsonb summary
  }
  change_features {
    bigint id PK
    text run_id FK
    geometry geometry "MultiPolygon, EPSG 32735, GiST"
    float area_m2
    real confidence
    smallint zone "true dates"
    text change_type "likely type"
    text facility_id
    text description "one plain sentence"
  }
  facilities {
    text facility_id PK
    text name
    text class "pit, dump, tailings ..."
    geometry geometry "GiST"
  }
  facility_changes {
    text facility_id PK
    real changed_ha
    jsonb change_types
  }
```

| Table | Holds | Typical question |
|---|---|---|
| `change_runs` | One row per method and date pair, with its parameters and totals | *Which runs exist, and with which thresholds?* |
| `change_features` | One row per change polygon: geometry, size, confidence, zone, likely type, facility, a sentence | *What changed inside the tailings facility?* |
| `facilities` | The 8 named mine facilities, drawn on very-high-resolution imagery | *Where are the pits and dumps?* |
| `facility_changes` | Change per facility, by type | *Which facility changed most?* |

`change_features_wgs84` is a view in lon/lat for web maps. Example queries: [PostGIS](https://github.com/dzakybd/s2change/blob/main/sql/queries_postgis.sql) · [SpatiaLite](https://github.com/dzakybd/s2change/blob/main/sql/queries_spatialite.sql) · [explore it live](https://s2change.dzaky.ai.id/#/database).

## 🚧 5. Limitations and next steps

> [!WARNING]
> **The map misses most subtle change.** Recall is <b class="bad">0.17 ± 0.11</b>: it is built to avoid false alarms, so faint tailings and burn changes fall below the threshold.

🔬 **Next validation step.** A human analyst relabels the 200 points, plus about 200 more in the "no change" groups, on very-high-resolution imagery from the same weeks (Planet or Esri Wayback). That tightens recall, replaces AI labels, and gives the first training set for a learned model; a per-type threshold would recover the missed tailings changes.

| | Gap | Why it matters | Next step |
|---|---|---|---|
| 🎨 | RGB only | Water, shadow and dark wet soil look alike; burns look like wet ground | Add NIR (B08) for NDVI at the same 10 m, and the L2A cloud and shadow mask |
| ⛰️ | No elevation | No volumes; pits versus dumps rest on context | Stereo very-high-resolution imagery or LiDAR |
| ✏️ | Hand-drawn facility regions | Facility edges are an analyst's call | Licence boundaries or a maintained facility database |
| 🔍 | Super-resolution | Models like SEN2SR invent detail that differs between dates, creating false change | Display only: the map's 2.5 m layer is SEN2SR on the analysed dates; detection stays on the 10 m bands |
| 📆 | Two mosaicked dates | Sun angle and pass mixes differ | Per-pixel acquisition dates, same-season pairs, per-pair calibration (as here) |

### ✅ The brief, covered

| # | Task | Done | See it |
|---|---|---|---|
| 1 ✅ | Data preparation | B02–B04 loaded, CRS, transform and shape checked, stacked | `src/s2cd/io.py` → `data/processed/sentinel2_*_stack.tif` |
| 2 ✅ | Change detection | The provided example, unchanged, and a calibrated change vector analysis | [Section 1](#-1-method) · [Change intensity map](https://s2change.dzaky.ai.id/#/map?layer=intensity) |
| 3 ✅ | Store in a geospatial DB | SpatiaLite file in the repo; PostGIS with spatial indexes, explorer, SQL dump, API | `data/change_detection.sqlite` · [Database](https://s2change.dzaky.ai.id/#/database) · [Example queries](https://github.com/dzakybd/s2change/blob/main/sql/queries_postgis.sql) |
| 4 ✅ | Visualise | Static figures here; interactive map with swipe, types and facilities | [Map](https://s2change.dzaky.ai.id/#/map) |
| 5 ✅ | Interpret | Typed polygons with a sentence each, facility register, confounders | [Section 2](#-2-results) · [Section 3](#-3-interpretation) |

Security controls of the app are in [docs/system.md](https://github.com/dzakybd/s2change/blob/main/docs/system.md).

### References

Malila (1980) change vector analysis; Otsu (1979); Canny (1986) hysteresis; Canty & Nielsen (2008) *Remote Sens. Environ.* 112:1025 (automatic radiometric normalization); Olofsson et al. (2014) *Remote Sens. Environ.* 148:42.


---

*Contains modified Copernicus Sentinel-2 data (2023–2026). Map imagery in the app: the analysed scenes super-resolved with SEN2SR (ESA OpenSR, display only), over Esri World Imagery and Bing Aerial basemaps. Built by Dzaky Zakiyal Fawwaz for the Solafune geospatial take-home.*
