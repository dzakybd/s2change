```sql
-- Example queries for the PostGIS database (schema: pipeline/sql/schema_postgis.sql + pipeline/sql/schema_app.sql, filled by `s2cd run` with DATABASE_URL set).
-- Tables: change_runs (one row per method run), change_features (one row per change polygon, EPSG:32735, metres),
-- facilities and facility_changes (the facility register and the change inside each facility).
-- Every query is restricted to one run via the psql variable
-- below; the app's database explorer (/api/db/examples) fills in the default run and shows each block as an example.
--
--   psql "$DATABASE_URL" -f pipeline/sql/queries_postgis.sql      (or: make query)
--
-- Block format (read by webapp/api/explorer.py): "-- N. Title", comment lines = description, SQL ending with ";".

\pset footer off
\set run_id 20230812_20230902_robust_cva

-- 1. What changed, in plain words
-- The 20 largest changes as a reader would describe them: what, where, how big, and what to check.
-- Polygons labelled noise or artifact (mosaic seams, haze) are left out.
SELECT id, headline, location, round((area_m2 / 1e6)::numeric, 3) AS area_km2, what_to_check
FROM change_features
WHERE run_id = :'run_id' AND change_type IS DISTINCT FROM 'noise_or_artifact'
ORDER BY area_m2 DESC
LIMIT 20;

-- 2. Runs overview
-- Parameters and summary are JSONB.
SELECT run_id, method, date_before, date_after,
       summary ->> 'changed_area_ha'    AS changed_area_ha,
       summary ->> 'n_features'         AS n_features,
       params -> 'rule' ->> 'mmu_ha'    AS mmu_ha
FROM change_runs
ORDER BY run_id;

-- 3. What changed: area per change type
-- change_type is a rule-based hypothesis from RGB evidence and the facility register (robust_cva only).
SELECT change_type, brief_category,
       count(*)                                       AS n_polygons,
       round((sum(area_m2) / 10000.0)::numeric, 1)    AS area_ha
FROM change_features
WHERE run_id = :'run_id'
GROUP BY change_type, brief_category
ORDER BY area_ha DESC;

-- 4. Change per facility
-- The register outlines each facility from the imagery; change_types is JSONB (hectares per type).
SELECT f.facility_id, f.name, f.class, f.area_ha, c.changed_ha, c.changed_share, c.change_types
FROM facilities AS f
JOIN facility_changes AS c USING (facility_id)
ORDER BY c.changed_ha DESC;

-- 5. Largest changes, on the map
-- Returns the geometry, so the explorer draws the result as a map layer (centroid shown in lon/lat).
SELECT id, change_type, facility_id,
       round((area_m2 / 10000.0)::numeric, 2) AS area_ha,
       confidence, explanation, geometry
FROM change_features
WHERE run_id = :'run_id'
ORDER BY area_m2 DESC
LIMIT 20;

-- 6. Changes inside vs outside the disturbed footprint
-- in_disturbed_area is a brightness heuristic; outside it, change may be new disturbance.
SELECT CASE WHEN in_disturbed_area THEN 'inside disturbed area' ELSE 'outside (possible new disturbance)' END AS location,
       count(*)                                        AS n_polygons,
       round((sum(area_m2) / 10000.0)::numeric, 2)     AS area_ha,
       round((100.0 * sum(area_m2) / sum(sum(area_m2)) OVER ())::numeric, 1) AS pct_of_area
FROM change_features
WHERE run_id = :'run_id'
GROUP BY in_disturbed_area
ORDER BY in_disturbed_area DESC;

-- 7. Changes within 500 m of a point of interest
-- ST_DWithin uses the GiST index; distances are in metres because the geometry is stored in UTM 35S. Replace the lon/lat.
WITH poi AS (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(25.93900, -12.24840), 4326), 32735) AS g)
SELECT f.id,
       round(ST_Distance(f.geometry, poi.g)::numeric, 1) AS distance_m,
       round((f.area_m2 / 10000.0)::numeric, 2)          AS area_ha,
       f.change_type, f.confidence
FROM change_features AS f, poi
WHERE f.run_id = :'run_id'
  AND ST_DWithin(f.geometry, poi.g, 500)
ORDER BY distance_m;

-- 8. Spatial join: hectares of change inside each facility
-- Cross-checks facility_changes by intersecting the polygons with the outlines (polygons within a run never overlap).
SELECT f.facility_id, f.name,
       round((sum(ST_Area(ST_Intersection(c.geometry, f.geometry))) / 10000.0)::numeric, 1) AS changed_ha
FROM facilities AS f
JOIN change_features AS c ON ST_Intersects(c.geometry, f.geometry)
WHERE c.run_id = :'run_id'
GROUP BY f.facility_id, f.name
ORDER BY changed_ha DESC;

-- 9. Polygons that touch the AOI edge
-- These may continue outside the study area, so their size is a lower bound.
SELECT id, change_type,
       round((area_m2 / 10000.0)::numeric, 2) AS area_ha,
       confidence
FROM change_features
WHERE run_id = :'run_id' AND touches_aoi_edge
ORDER BY area_m2 DESC
LIMIT 20;
```
