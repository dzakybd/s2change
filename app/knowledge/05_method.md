# Method and caveats (for explaining results)

- **Two-date change (the assignment).** The provided "dates" are two-scene mosaics, so the radiometry steps along seams.
  - The zones are the source-scene pairs found with STAC provenance (a blind, data-driven seam detection agrees at 99 %). The pipeline normalizes each zone on stable pixels, then scores change with a robust change vector analysis: a Mahalanobis distance under a brightness-stratified noise model, with thresholds calibrated on held-out stable pixels (realised false-alarm rate 0.10 % at a 0.1 % target).
  - Thresholds are false-alarm rates (1e-3 to grow a region, 1e-5 to seed it). The minimum mapping unit is 2,000 m².
  - Result: 215 polygons and 4.13 km², against 111.4 km² for the provided Euclidean baseline, which mostly maps seams and haze.
- **Confidence** on change polygons is a 0–1 *magnitude* score, not an accuracy or probability. There is no ground truth.
- **change_type** labels each change polygon from RGB evidence plus facility context: pit_excavation, waste_dumping, tailings_deposition, water_level_change, mine_expansion_clearing, land_clearing, infrastructure_or_settlement, seasonal_vegetation_or_burn, tailings_dam_raise, noise_or_artifact. Each has a technical explanation and plain-language fields for business readers: headline, location, evidence, what_to_check and a one-sentence description. Use the plain fields when talking to users. These are hypotheses for follow-up, not ground truth.
- **Facilities.** A curated register of 8 named facilities (`configs/facility_register.yaml`): analyst-defined boxes, with outlines taken from the imagery (bare in both dates, or water in both dates). Without elevation data, telling pits from dumps is the weakest call.
- **Redness (B04/B02)** is an iron-oxide *indicator*. It responds to exposed oxidized rock and laterite; it is not a mineral map or grade.
- **RGB-only limits.** There are no NIR/SWIR bands, so vegetation health (NDVI), burn severity (NBR), proper water indices and mineral indices are unavailable. Dark wet soil, shadow and water can be confused; using both dates and context reduces this.
- **No accuracy assessment.** The false-alarm rate is measured on held-out stable pixels; missed change and label correctness are not. That would need very-high-resolution imagery or field checks on a stratified sample.
