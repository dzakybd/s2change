# Facility register (what each class is, how it looks at 10 m)

The register (`configs/facility_register.yaml`, tables `facilities` and `facility_changes`) has 8 facilities in 7 classes. Each outline is one analyst-drawn polygon traced on very-high-resolution imagery (Esri World Imagery) and checked against the 2023 Sentinel-2 image; facilities do not overlap. Facility numbers refer to the default comparison only.

| Class | Facilities | What it is | RGB signature (B02/B03/B04 only) | Change type it maps to |
|---|---|---|---|---|
| `pit_dump_complex` | PIT-NW, PIT-S | Open pits with the waste dumps around them: benches, ramps, pit lakes, tipped faces | Bare, terraced (high texture), shadowed walls that vary with sun angle; dumps red/orange (high B04/B02) | `pit_excavation`; `waste_dumping` where the surface gets brighter and redder |
| `waste_dump` | WD-E | Rock removed to reach the ore, placed in mounds | Bare, red/orange (iron oxides), smooth tipped faces | `waste_dumping`: fresh faces brighter and redder as new lifts are tipped |
| `tailings` | TSF | Fine processing residue deposited as slurry, with beaches and free water | Very bright, smooth, flat beaches; wet areas darker; ponded water blue above red | `tailings_deposition`: wet/dry patches move as deposition points change; `tailings_dam_works` where brighter, redder bare earth overlaps the facility edge (earthworks or clearing on the wall) |
| `water_reservoir` | DAM | Fresh-water dam | Water rule (blue clearly above red, not bright) in both dates | `water_level_change` |
| `plant` | PLANT | Processing plant, workshops, stockpile pads | Small, compact, bright, high contrast | `infrastructure_or_settlement` |
| `camp` | CAMP | Accommodation camp | Regular roofs and roads | `infrastructure_or_settlement` |
| `settlement` | TOWN | Town south-west of the mine | Roofs, roads, gardens; mixed with vegetation | `infrastructure_or_settlement` |

Outside the register, change is typed from RGB evidence alone: `mine_expansion_clearing` (new bare ground at the mine edge), `land_clearing`, `seasonal_vegetation_or_burn`, `noise_or_artifact` (seams, AOI edge, low magnitude) or `unexplained`.

**Earthworks on the embankment.** The TSF wall is raised in stages, but the operator's public tailings disclosure (Barrick, GISTM, August 2025) records the last raise in 2013 and the next planned for 2027. So bare ground along the wall in 2023 (new fill, or vegetation stripped from the wall face) is read as maintenance earthworks or clearing, not a raise. The dam's consequence class is High, so it is still worth a check.
