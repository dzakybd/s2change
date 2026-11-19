-- Example queries for the PostGIS database (schema: sql/schema_postgis.sql, filled by `s2cd run --postgis`).
-- Tables: change_runs (one row per method run), change_features (one row per change polygon, EPSG:32735, metres).
-- Same questions as sql/queries_spatialite.sql, PostGIS dialect.
--
--   docker compose exec -T db psql -U s2cd -d s2cd -f - < sql/queries_postgis.sql      (or: make query-postgis)

\pset footer off

-- 1. Runs overview: parameters and summary are JSONB.
SELECT run_id, method, date_before, date_after,
       summary ->> 'changed_area_ha'    AS changed_area_ha,
       summary ->> 'n_features'         AS n_features,
       params -> 'rule' ->> 'mmu_px'    AS mmu_px
FROM change_runs
ORDER BY run_id;

-- 2. Changed area by direction and method.
SELECT method, change_direction,
       count(*)                           AS n_polygons,
       round((sum(area_m2) / 10000.0)::numeric, 2) AS area_ha,
       round(avg(confidence)::numeric, 3) AS mean_confidence
FROM change_features
GROUP BY method, change_direction
ORDER BY method, area_ha DESC;

-- 3. Ten largest robust_cva changes with confidence >= 0.5 (centroid in lon/lat for quick lookup).
SELECT id,
       round((area_m2 / 10000.0)::numeric, 2) AS area_ha,
       confidence, change_direction, in_disturbed_area,
       round(ST_X(ST_Transform(ST_Centroid(geometry), 4326))::numeric, 5) AS lon,
       round(ST_Y(ST_Transform(ST_Centroid(geometry), 4326))::numeric, 5) AS lat
FROM change_features
WHERE method = 'robust_cva' AND confidence >= 0.5
ORDER BY area_m2 DESC
LIMIT 10;

-- 4. Changes inside vs outside the disturbed (mine / industrial) footprint, per method.
SELECT method,
       CASE WHEN in_disturbed_area THEN 'inside disturbed area' ELSE 'outside (possible new disturbance)' END AS location,
       count(*)                                        AS n_polygons,
       round((sum(area_m2) / 10000.0)::numeric, 2)     AS area_ha,
       round((100.0 * sum(area_m2) / sum(sum(area_m2)) OVER (PARTITION BY method))::numeric, 1) AS pct_of_method_area
FROM change_features
GROUP BY method, in_disturbed_area
ORDER BY method, in_disturbed_area DESC;

-- 5. Robust_cva changes within 500 m of a point of interest (lon/lat below; replace with your own).
--    ST_DWithin uses the GiST index; distances are in metres because the geometry is stored in UTM 35S.
WITH poi AS (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(25.93900, -12.24840), 4326), 32735) AS g)
SELECT f.id,
       round(ST_Distance(f.geometry, poi.g)::numeric, 1) AS distance_m,
       round((f.area_m2 / 10000.0)::numeric, 2)          AS area_ha,
       f.change_direction, f.confidence
FROM change_features AS f, poi
WHERE f.method = 'robust_cva'
  AND ST_DWithin(f.geometry, poi.g, 500)
ORDER BY distance_m;

-- 6. Method agreement: how much of each method's changed area is also flagged by the other?
--    (Exact polygon overlay of the large baseline regions: ~2 s natively, much slower under amd64 emulation on Apple Silicon.)
--    Polygons within one method never overlap, so summing pairwise intersection areas is exact.
WITH pairs AS (
    SELECT sum(ST_Area(ST_Intersection(c.geometry, b.geometry))) AS both_m2
    FROM change_features AS c
    JOIN change_features AS b ON b.method = 'euclidean_baseline' AND ST_Intersects(c.geometry, b.geometry)
    WHERE c.method = 'robust_cva'
),
totals AS (
    SELECT sum(area_m2) FILTER (WHERE method = 'robust_cva')         AS cva_m2,
           sum(area_m2) FILTER (WHERE method = 'euclidean_baseline') AS base_m2
    FROM change_features
)
SELECT round((cva_m2 / 10000.0)::numeric, 2)                          AS robust_cva_ha,
       round((base_m2 / 10000.0)::numeric, 2)                         AS baseline_ha,
       round((both_m2 / 10000.0)::numeric, 2)                         AS both_ha,
       round((100.0 * both_m2 / cva_m2)::numeric, 1)                  AS pct_cva_also_in_baseline,
       round((100.0 * both_m2 / base_m2)::numeric, 1)                 AS pct_baseline_also_in_cva,
       round((100.0 * both_m2 / (cva_m2 + base_m2 - both_m2))::numeric, 1) AS iou_pct
FROM totals, pairs;

-- 7. GeoJSON FeatureCollection in WGS 84 (lon/lat): the 20 largest robust_cva changes.
--    Whole-table alternative: SELECT * FROM change_features_wgs84 (view in the schema) with ogr2ogr or pg_featureserv.
SELECT jsonb_build_object(
           'type', 'FeatureCollection',
           'features', coalesce(jsonb_agg(jsonb_build_object(
               'type', 'Feature',
               'id', id,
               'properties', jsonb_build_object('area_m2', round(area_m2::numeric, 1), 'confidence', confidence,
                                                'change_direction', change_direction, 'in_disturbed_area', in_disturbed_area),
               'geometry', ST_AsGeoJSON(ST_Transform(geometry, 4326), 6)::jsonb)), '[]'::jsonb)
       ) AS geojson
FROM (SELECT * FROM change_features WHERE method = 'robust_cva' ORDER BY area_m2 DESC LIMIT 20) AS top;
