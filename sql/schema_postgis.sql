-- PostGIS schema for change detection results. Idempotent: safe to run on every pipeline run.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS change_runs (
    run_id       text PRIMARY KEY,                 -- e.g. 20230812_20230902_robust_cva
    method       text NOT NULL,
    date_before  date NOT NULL,
    date_after   date NOT NULL,
    params       jsonb NOT NULL,                   -- thresholds, MMU, zone calibration ...
    summary      jsonb NOT NULL,                   -- changed area, counts, diagnostics
    created_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (date_after > date_before)
);

CREATE TABLE IF NOT EXISTS change_features (
    id                 bigserial PRIMARY KEY,
    run_id             text NOT NULL REFERENCES change_runs (run_id) ON DELETE CASCADE,
    date_before        date NOT NULL,
    date_after         date NOT NULL,
    method             text NOT NULL,
    area_m2            double precision NOT NULL CHECK (area_m2 > 0),
    perimeter_m        double precision,
    compactness        real,
    confidence         real CHECK (confidence BETWEEN 0 AND 1),
    neglog10_p         real,                           -- mean -log10 no-change p-value, chi2.logsf (NULL for the baseline)
    strong_fraction    real,
    change_direction   text CHECK (change_direction IN ('brightening', 'darkening', 'spectral shift')),
    in_disturbed_area  boolean,
    touches_aoi_edge   boolean,                        -- region adjoins the AOI edge, may continue outside it
    d_blue             real,
    d_green            real,
    d_red              real,
    d_brightness       real,
    brightness_before  real,
    brightness_after   real,
    zone               smallint,
    change_type        text,                           -- rule-based interpretation (robust_cva only), see s2cd.interpret
    brief_category     text,
    explanation        text,
    facility_id        text,                           -- facility of the register the polygon mostly lies in
    geometry           geometry(MultiPolygon, 32735) NOT NULL,  -- WGS 84 / UTM 35S, metres
    CONSTRAINT change_features_valid_geometry CHECK (ST_IsValid(geometry))
);

-- Migration for databases created by an earlier version of this file (no-ops on a fresh database).
ALTER TABLE change_features ADD COLUMN IF NOT EXISTS neglog10_p real;
ALTER TABLE change_features ADD COLUMN IF NOT EXISTS touches_aoi_edge boolean;
ALTER TABLE change_features ADD COLUMN IF NOT EXISTS change_type text;
ALTER TABLE change_features ADD COLUMN IF NOT EXISTS brief_category text;
ALTER TABLE change_features ADD COLUMN IF NOT EXISTS explanation text;
ALTER TABLE change_features ADD COLUMN IF NOT EXISTS facility_id text;
ALTER TABLE change_features DROP COLUMN IF EXISTS mean_p_change;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'change_features_valid_geometry') THEN
        ALTER TABLE change_features ADD CONSTRAINT change_features_valid_geometry CHECK (ST_IsValid(geometry)) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS change_features_geom_idx ON change_features USING gist (geometry);
CREATE INDEX IF NOT EXISTS change_features_dates_idx ON change_features (date_before, date_after);
CREATE INDEX IF NOT EXISTS change_features_run_idx ON change_features (run_id);

-- Web clients (MapLibre, Leaflet, pg_featureserv) want lon/lat.
CREATE OR REPLACE VIEW change_features_wgs84 AS
SELECT id, run_id, date_before, date_after, method, area_m2, confidence, change_direction,
       in_disturbed_area, change_type, facility_id, d_brightness, ST_Transform(geometry, 4326) AS geometry
FROM change_features;
