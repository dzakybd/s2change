# Method and caveats (for explaining results)

- **Two-date change (the assignment).** The provided "dates" are two-scene mosaics, so the radiometry steps along seams.
  - The pipeline finds those zones from the data, matching the true seams at 99–100 %. It normalizes each zone on stable pixels, then scores change with a robust change vector analysis (a Mahalanobis distance with a χ² noise model).
  - Thresholds are false-alarm rates (1e-3 to grow a region, 1e-5 to seed it). The minimum mapping unit is 0.2 ha.
  - Result: 538 polygons and 1,345 ha, against 11,078 ha for the provided Euclidean baseline, which mostly maps seams and haze.
- **Confidence** on change polygons is a 0–1 *magnitude* score, not an accuracy or probability. There is no ground truth.
- **change_type** labels each change polygon from RGB evidence plus facility context: pit_excavation, waste_dumping, tailings_deposition, water_level_change, mine_expansion_clearing, land_clearing, infrastructure_or_settlement, seasonal_vegetation_or_burn, noise_or_artifact. Each has a one-line explanation. These are hypotheses for follow-up, not ground truth.
- **Facilities.** Objects come from segmenting the mine footprint (bare in **both** dates) and persistent water. Each is classified by transparent RGB rules: brightness, greenness (gcc), redness (B04/B02), blue-vs-red for water, texture, shape and adjacency. Without elevation data, telling pits from dumps is the weakest call; the confidence shows how strongly the rules were met, not validated accuracy.
- **Redness (B04/B02)** is an iron-oxide *indicator*. It responds to exposed oxidized rock and laterite; it is not a mineral map or grade.
- **RGB-only limits.** There are no NIR/SWIR bands, so vegetation health (NDVI), burn severity (NBR), proper water indices and mineral indices are unavailable. Dark wet soil, shadow and water can be confused; using both dates and context reduces this.
- **Validation would need** very-high-resolution imagery or field checks on a stratified sample. That is the honest next step.
