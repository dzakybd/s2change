# What the results mean, and who acts on them

Change maps are leads for decisions, not proof. Every statement here is an *indicator* to confirm with the operator's records, licence boundaries, very-high-resolution imagery or a site visit.

## The multi-year view (same season, from the monthly date catalogue)

Fixed results of precomputed comparisons from the provided before date (nominal 2023-08-12, a dry-season mosaic) to later August scenes, so the season matches. They are not in the database; quote them as "from the yearly comparisons".

| After | Changed | Waste dumping | Tailings deposition | Pit excavation | Land clearing | Inside the 8 facilities | Outside them |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2024-08-27 (1 year) | 15.8 km² | 5.7 km² | 4.9 km² | 1.8 km² | 1.5 km² (+0.7 mine-edge clearing) | 10.1 km² | 5.6 km² |
| 2025-08-07 (2 years) | 33.8 km² | 14.5 km² | 7.7 km² | 5.3 km² | 1.5 km² | 20.9 km² | 12.9 km² |
| 2026-08-14 (3 years) | 33.3 km² | 20.2 km² | 4.4 km² | 3.3 km² | 1.7 km² | 15.0 km² | 18.2 km² |

Reading it:
- **Waste movement dominates and keeps growing** (5.7 → 14.5 → 20.2 km² labelled waste dumping). Stripping waste rock is what a mine does to open up or deepen pits, so this is consistent with sustained development of the pits, not a mine winding down.
- **The tailings facility stays active** (5.8–8.8 km² of its 22.4 km² changed between dates), which indicates the process plant kept running.
- **Change outside the named facilities grows** (5.6 → 12.9 → 18.2 km²): the footprint is spreading beyond the regions in the register (new dumps, haul roads, clearing). That is the first thing a regulator would check against licence boundaries.
- Area of change is not new disturbed area: re-worked ground inside the footprint counts too. The two-month pair (the assignment) shows only operations (4.13 km², 74 % inside facilities, no clearing beyond the footprint).

## Who acts on it

| Who | Question they ask | What this provides | What it cannot say |
|---|---|---|---|
| Government / regulator (e.g. mining cadastre, environment agency; the users ShigenAI targets) | Is the mine operating within its licence? Is anything happening where it should not? | Footprint growth outside known facilities, located and sized every month at no data cost (Sentinel-2 is free, 5-day revisit) | Legality: there are no licence boundaries in this dataset |
| Tailings / dam-safety oversight | Is the tailings facility being managed as expected? | Where deposition and ponding move on the facility, monthly | Stability, freeboard or seepage: these need engineering data |
| Investors, lenders, insurers | Is the operation running and growing? | Independent activity indicators: waste movement, tailings deposition, pit work | Production, grade or tonnes |
| ESG and community teams | Is clearing or water change spreading beyond the mine? | Clearing and water-level change outside the register; settlement growth | Social or health impacts |
| The operator | An independent check of what the plan says | A consistent, auditable change record in PostGIS | Survey-grade volumes (no elevation data) |

## How it would run as a service
One clear scene per month, compared with the previous month and with the same month a year earlier; changes outside registered facilities or above a size threshold raise an alert with the polygon, its type and its "why"; analysts confirm with VHR imagery. The database is already keyed by run, so each comparison is one more run.
