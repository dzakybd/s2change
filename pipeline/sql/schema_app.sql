-- App layer on top of schema_postgis.sql: the facility register and a read-only role for the API and the analyst.
-- Idempotent. Run after schema_postgis.sql (`s2cd run` does both, see s2cd.db.write_postgis, and sets the role password).

-- Facility register (pipeline/config/facility_register.yaml): analyst-defined regions, outlined from the imagery.
CREATE TABLE IF NOT EXISTS facilities (
    facility_id  text PRIMARY KEY,                    -- 'PIT-NW', 'WD-E', 'TSF', ...
    name         text NOT NULL,
    class        text NOT NULL,                       -- pit_dump_complex, waste_dump, tailings, water_reservoir, plant, camp, settlement
    cover        text NOT NULL,                       -- how the outline was made: drawn (analyst polygon on very-high-resolution imagery)
    area_ha      real,
    geometry     geometry(MultiPolygon, 32735) NOT NULL
);
CREATE INDEX IF NOT EXISTS facilities_geom_idx ON facilities USING gist (geometry);

-- Change inside each facility between the two dates of the default comparison (robust_cva polygons).
CREATE TABLE IF NOT EXISTS facility_changes (
    facility_id    text PRIMARY KEY REFERENCES facilities (facility_id) ON DELETE CASCADE,
    changed_ha     real,
    changed_share  real,                              -- changed_ha / facility area
    change_types   jsonb,                             -- {change_type: ha}, largest first
    summary        text
);

CREATE INDEX IF NOT EXISTS change_features_type_idx ON change_features (run_id, change_type);

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
GRANT SELECT ON change_runs, change_features, change_features_wgs84, facilities, facility_changes TO s2cd_reader;
ALTER ROLE s2cd_reader SET statement_timeout = '5s';
ALTER ROLE s2cd_reader SET default_transaction_read_only = on;

-- Supabase only (no-op on plain PostGIS): PostGIS lives in schema "extensions", and every
-- public table is reachable through the auto-generated REST API via the anon/authenticated
-- roles. The analyst API reads through s2cd_reader; nothing else should touch these tables.
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_namespace WHERE nspname = 'extensions') THEN
        GRANT USAGE ON SCHEMA extensions TO s2cd_reader;
        ALTER ROLE s2cd_reader SET search_path = public, extensions;
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON change_runs, change_features, change_features_wgs84, facilities, facility_changes FROM anon, authenticated;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
    END IF;
END
$$;
