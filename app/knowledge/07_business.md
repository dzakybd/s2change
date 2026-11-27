# What the results mean, and who acts on them

Change maps are leads for decisions, not proof. Every statement here is an *indicator* to confirm with the operator's records, licence boundaries, very-high-resolution imagery or a site visit.

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
