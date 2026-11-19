-- App layer on top of schema_postgis.sql: enrichment context, monthly timeline, read-only role for the analyst API.
-- Idempotent. Run after schema_postgis.sql (`python -m app.publish` does both). The role password is set by publish.py.

ALTER TABLE change_features ADD COLUMN IF NOT EXISTS likely_cause text;

CREATE TABLE IF NOT EXISTS change_context (
    id                   bigint PRIMARY KEY REFERENCES change_features (id) ON DELETE CASCADE,
    ndvi_before          real, ndvi_after  real, nbr_before   real, nbr_after   real,
    mndwi_before         real, mndwi_after real,
    d_ndvi               real,   -- after - before
    d_nbr                real,   -- before - after (burn: positive)
    d_mndwi              real,   -- after - before
    aot_before           real, aot_after real, d_aot real,
    scl_artifact_before  real, scl_water_before real, scl_cloud_before real,   -- share of polygon pixels per SCL class
    scl_artifact_after   real, scl_water_after  real, scl_cloud_after  real,
    source_before        text, source_after text,          -- STAC scene(s) the polygon's pixels came from
    seam_mix             real,                             -- share of the polygon on a mosaic seam
    likely_cause         text
);
ALTER TABLE change_context ADD COLUMN IF NOT EXISTS source_before text;
ALTER TABLE change_context ADD COLUMN IF NOT EXISTS source_after text;
ALTER TABLE change_context ADD COLUMN IF NOT EXISTS seam_mix real;

CREATE TABLE IF NOT EXISTS timeline_months (
    month                   text PRIMARY KEY,          -- 'YYYY-MM'
    status                  text,
    clear_fraction          real,
    ndvi_mean               real, nbr_mean real, mndwi_mean real, bsi_mean real,
    bare_ha                 real, water_ha real, footprint_ha real,
    changed_ha              real, changed_ha_in_footprint real
);

CREATE INDEX IF NOT EXISTS change_features_cause_idx ON change_features (run_id, likely_cause);

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 's2cd_reader') THEN
        CREATE ROLE s2cd_reader LOGIN;
    END IF;
END
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM s2cd_reader;
GRANT USAGE ON SCHEMA public TO s2cd_reader;
-- Needed where CONNECT was revoked from PUBLIC (production does this).
DO $$ BEGIN EXECUTE format('GRANT CONNECT ON DATABASE %I TO s2cd_reader', current_database()); END $$;
GRANT SELECT ON change_runs, change_features, change_context, timeline_months, change_features_wgs84 TO s2cd_reader;
ALTER ROLE s2cd_reader SET statement_timeout = '5s';
ALTER ROLE s2cd_reader SET default_transaction_read_only = on;
