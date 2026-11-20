var e=[{title:`Changed area by type`,description:`Hectares and polygon count per change type for the robust CVA run.`,sql:`SELECT change_type, count(*) AS n_polygons,
       round((sum(area_m2) / 10000.0)::numeric, 1) AS area_ha
FROM change_features
WHERE method = 'robust_cva'
GROUP BY change_type
ORDER BY area_ha DESC;`},{title:`Ten largest changes`,description:`Largest polygons with their type and facility. Geometry is returned, so the result can be drawn on the map.`,sql:`SELECT id, change_type, facility_id,
       round((area_m2 / 10000.0)::numeric, 1) AS area_ha,
       geometry
FROM change_features
WHERE method = 'robust_cva'
ORDER BY area_m2 DESC
LIMIT 10;`},{title:`Facility register`,description:`Changed area per facility, from the facility register joined to the changes.`,sql:`SELECT f.facility_id, f.name, c.changed_ha, c.changed_share
FROM facilities f JOIN facility_changes c USING (facility_id)
ORDER BY c.changed_ha DESC;`},{title:`Changes inside the tailings facility`,description:`Spatial join: change polygons whose centroid lies in the TSF outline.`,sql:`SELECT c.change_type, count(*) AS n, round((sum(c.area_m2) / 10000.0)::numeric, 1) AS area_ha
FROM change_features c JOIN facilities f ON ST_Intersects(f.geometry, ST_Centroid(c.geometry))
WHERE f.facility_id = 'TSF' AND c.method = 'robust_cva'
GROUP BY c.change_type
ORDER BY area_ha DESC;`}];export{e as default};