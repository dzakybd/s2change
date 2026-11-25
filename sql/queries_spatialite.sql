-- Example queries for data/change_detection.sqlite (SpatiaLite, written by `s2cd run`).
-- Tables: change_runs (one row per method run), change_features (one row per change polygon, EPSG:32735, metres).
-- Same questions as sql/queries_postgis.sql, SQLite/SpatiaLite dialect. Statements are separated by ';'.
-- Plus facility_changes / facilities (the named facility register). A file holds one date pair; the date filters below are
-- literals (edit them if you store other pairs side by side).
--
-- Run one with GDAL (no SpatiaLite shell needed):
--   ogrinfo -dialect SQLITE -sql "<query>" data/change_detection.sqlite
-- or load everything in the SpatiaLite shell:
--   spatialite data/change_detection.sqlite < sql/queries_spatialite.sql
-- or from Python: pyogrio.read_dataframe(path, sql=query, sql_dialect="SQLITE")  (`make query` does this).

-- 1. Runs overview: parameters and summary are stored as JSON text.
SELECT run_id, method, date_before, date_after,
       json_extract(summary, '$.changed_area_ha') AS changed_area_ha,
       json_extract(summary, '$.n_features')      AS n_features,
       json_extract(params, '$.rule.mmu_ha')      AS mmu_ha
FROM change_runs
WHERE date_before = '2023-08-12' AND date_after = '2023-09-02'
ORDER BY run_id;

-- 2. Changed area by direction and method.
SELECT method, change_direction,
       COUNT(*)                         AS n_polygons,
       ROUND(SUM(area_m2) / 10000.0, 2) AS area_ha,
       ROUND(AVG(confidence), 3)        AS mean_confidence
FROM change_features
WHERE date_before = '2023-08-12' AND date_after = '2023-09-02'
GROUP BY method, change_direction
ORDER BY method, area_ha DESC;

-- 3. Ten largest robust_cva changes with confidence >= 0.5 (centroid in lon/lat for quick lookup).
SELECT id,
       ROUND(area_m2 / 10000.0, 2)                              AS area_ha,
       confidence, change_direction, in_disturbed_area,
       ROUND(ST_X(ST_Transform(ST_Centroid(geometry), 4326)), 5) AS lon,
       ROUND(ST_Y(ST_Transform(ST_Centroid(geometry), 4326)), 5) AS lat
FROM change_features
WHERE date_before = '2023-08-12' AND date_after = '2023-09-02'
  AND method = 'robust_cva' AND confidence >= 0.5
ORDER BY area_m2 DESC
LIMIT 10;

-- 4. Changes inside vs outside the disturbed (mine / industrial) footprint, per method.
SELECT method,
       CASE WHEN in_disturbed_area THEN 'inside disturbed area' ELSE 'outside (possible new disturbance)' END AS location,
       COUNT(*)                         AS n_polygons,
       ROUND(SUM(area_m2) / 10000.0, 2) AS area_ha,
       ROUND(100.0 * SUM(area_m2) / SUM(SUM(area_m2)) OVER (PARTITION BY method), 1) AS pct_of_method_area
FROM change_features
WHERE date_before = '2023-08-12' AND date_after = '2023-09-02'
GROUP BY method, in_disturbed_area
ORDER BY method, in_disturbed_area DESC;

-- 5. Robust_cva changes within 500 m of a point of interest (lon/lat below; replace with your own).
--    Distances are in metres because the geometry is stored in UTM 35S.
WITH poi AS (SELECT ST_Transform(MakePoint(25.93900, -12.24840, 4326), 32735) AS g)
SELECT f.id,
       ROUND(ST_Distance(f.geometry, poi.g), 1) AS distance_m,
       ROUND(f.area_m2 / 10000.0, 2)             AS area_ha,
       f.change_direction, f.confidence
FROM change_features AS f, poi
WHERE f.date_before = '2023-08-12' AND f.date_after = '2023-09-02'
  AND f.method = 'robust_cva'
  AND f.ROWID IN (SELECT ROWID FROM SpatialIndex
                  WHERE f_table_name = 'change_features' AND f_geometry_column = 'geometry'
                    AND search_frame = ST_Buffer(poi.g, 500))
  AND ST_Distance(f.geometry, poi.g) <= 500
ORDER BY distance_m;

-- 6. Method agreement: how much of each method's changed area is also flagged by the other?
--    Polygons within one method never overlap, so summing pairwise intersection areas is exact.
WITH pairs AS (
    SELECT c.area_m2 AS cva_area, ST_Area(ST_Intersection(c.geometry, b.geometry)) AS overlap_m2
    FROM change_features AS c
    JOIN change_features AS b
      ON b.method = 'euclidean_baseline'
     AND b.ROWID IN (SELECT ROWID FROM SpatialIndex
                     WHERE f_table_name = 'change_features' AND f_geometry_column = 'geometry'
                       AND search_frame = c.geometry)
    WHERE c.method = 'robust_cva' AND c.date_before = '2023-08-12' AND c.date_after = '2023-09-02'
      AND ST_Intersects(c.geometry, b.geometry)
),
totals AS (
    SELECT SUM(CASE WHEN method = 'robust_cva' THEN area_m2 END)         AS cva_m2,
           SUM(CASE WHEN method = 'euclidean_baseline' THEN area_m2 END) AS base_m2
    FROM change_features
    WHERE date_before = '2023-08-12' AND date_after = '2023-09-02'
)
SELECT ROUND(totals.cva_m2 / 10000.0, 2)                               AS robust_cva_ha,
       ROUND(totals.base_m2 / 10000.0, 2)                              AS baseline_ha,
       ROUND((SELECT SUM(overlap_m2) FROM pairs) / 10000.0, 2)         AS both_ha,
       ROUND(100.0 * (SELECT SUM(overlap_m2) FROM pairs) / totals.cva_m2, 1)  AS pct_cva_also_in_baseline,
       ROUND(100.0 * (SELECT SUM(overlap_m2) FROM pairs) / totals.base_m2, 1) AS pct_baseline_also_in_cva,
       ROUND(100.0 * (SELECT SUM(overlap_m2) FROM pairs)
             / (totals.cva_m2 + totals.base_m2 - (SELECT SUM(overlap_m2) FROM pairs)), 1) AS iou_pct
FROM totals;

-- 7. GeoJSON export in WGS 84 (lon/lat), one Feature per row: the 20 largest robust_cva changes.
--    For a full file use: ogr2ogr -f GeoJSON -t_srs EPSG:4326 -where "method='robust_cva'" out.geojson data/change_detection.sqlite change_features
SELECT json_object(
           'type', 'Feature',
           'id', id,
           'properties', json_object('area_m2', ROUND(area_m2, 1), 'confidence', confidence,
                                     'change_direction', change_direction, 'in_disturbed_area', in_disturbed_area),
           'geometry', json(AsGeoJSON(ST_Transform(geometry, 4326), 6))
       ) AS feature
FROM change_features
WHERE date_before = '2023-08-12' AND date_after = '2023-09-02' AND method = 'robust_cva'
ORDER BY area_m2 DESC
LIMIT 20;

-- 8. What changed: area per change type (robust_cva only; the baseline carries no interpretation).
SELECT change_type, brief_category,
       COUNT(*)                         AS n_polygons,
       ROUND(SUM(area_m2) / 10000.0, 1) AS area_ha
FROM change_features
WHERE date_before = '2023-08-12' AND date_after = '2023-09-02' AND method = 'robust_cva'
GROUP BY change_type, brief_category
ORDER BY area_ha DESC;

-- 9. Changed area per named facility (register outlines; types are the JSON breakdown of the changed hectares).
SELECT facility_id, name, class, area_ha, changed_ha, changed_share, change_types
FROM facility_changes
ORDER BY changed_ha DESC;
