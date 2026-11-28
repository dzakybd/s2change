"""Store the results in PostGIS (``DATABASE_URL``): the production database, or the local one of ``make db-up``.

Schema: ``pipeline/sql/schema_postgis.sql`` (results) and ``pipeline/sql/schema_app.sql`` (facility register, read-only role):

change_runs      one row per (date pair, method) run, with parameters and summary as JSON
change_features  one row per change polygon, geometry stored as a valid MULTIPOLYGON in EPSG:32735
facilities       the named facility outlines of the register
facility_changes changed area, change types and a one-line summary per facility

Writes are idempotent: the database ends up holding exactly the runs of the last pipeline run.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .vectorize import as_valid_multipolygon

FEATURE_COLUMNS = [
    "run_id",
    "date_before",
    "date_after",
    "method",
    "area_m2",
    "perimeter_m",
    "compactness",
    "confidence",
    "neglog10_p",
    "strong_fraction",
    "change_direction",
    "in_disturbed_area",
    "touches_aoi_edge",
    "d_blue",
    "d_green",
    "d_red",
    "d_brightness",
    "brightness_before",
    "brightness_after",
    "zone",
    "change_type",
    "brief_category",
    "explanation",
    "facility_id",
    "headline",
    "location",
    "evidence",
    "what_to_check",
    "description",
]
SQL_DIR = Path(__file__).resolve().parents[2] / "pipeline" / "sql"
SCHEMA_SQL = SQL_DIR / "schema_postgis.sql"
SCHEMAS = (SCHEMA_SQL, SQL_DIR / "schema_app.sql")


def as_feature_table(gdf: gpd.GeoDataFrame, run_id: str, method: str, date_before, date_after) -> gpd.GeoDataFrame:
    """Add the run columns, order the columns like the schema (missing ones become NULL), make geometries valid MultiPolygons."""
    out = gdf.copy()
    out["run_id"], out["method"] = run_id, method
    out["date_before"], out["date_after"] = str(date_before), str(date_after)
    for c in FEATURE_COLUMNS:
        if c not in out:
            out[c] = None  # e.g. change_type exists only for the main method
    extra = [c for c in out.columns if c not in FEATURE_COLUMNS and c not in ("geometry", "region")]
    out = out[FEATURE_COLUMNS + extra + ["geometry"]]
    return out.set_geometry(out.geometry.apply(as_valid_multipolygon))


def _nulls(df: pd.DataFrame) -> pd.DataFrame:
    """NaN and NA become None, so they are stored as NULL (NaN would fail the CHECK constraints)."""
    return df.astype(object).where(df.notna(), None)


def write_postgis(
    url: str,
    features: gpd.GeoDataFrame,
    runs: pd.DataFrame,
    facilities: gpd.GeoDataFrame,
    facility_changes: pd.DataFrame,
) -> None:
    """Create the schema if needed, then replace every run, polygon and facility row with these, in one transaction.

    Polygon ids are 1..n in the order of ``features``. ``READER_PASSWORD``, when set, becomes the password of the
    read-only role ``s2cd_reader`` that the app connects with.
    """
    from sqlalchemy import create_engine, text

    rows = features[FEATURE_COLUMNS + ["geometry"]].copy()
    rows.insert(0, "id", range(1, len(rows) + 1))
    for c in ("date_before", "date_after"):
        rows[c] = pd.to_datetime(rows[c]).dt.date
    for c in rows.columns.drop("geometry"):
        rows[c] = _nulls(rows[c])
    fac_changes = facility_changes.assign(
        change_types=[json.dumps(t if isinstance(t, dict) else {}) for t in facility_changes.change_types]
    )
    fac_cols = ["facility_id", "changed_ha", "changed_share", "change_types", "summary"]
    run_cols = ["run_id", "method", "date_before", "date_after", "params", "summary"]

    with create_engine(url).begin() as conn:
        for schema in SCHEMAS:
            conn.exec_driver_sql(schema.read_text().replace("%", "%%"))
        conn.execute(text("DELETE FROM change_runs"))  # cascades to change_features
        conn.execute(
            text(
                "INSERT INTO change_runs (run_id, method, date_before, date_after, params, summary) VALUES "
                "(:run_id, :method, :date_before, :date_after, CAST(:params AS jsonb), CAST(:summary AS jsonb))"
            ),
            runs[run_cols].to_dict("records"),
        )
        rows.to_postgis("change_features", conn, if_exists="append", index=False)
        conn.execute(
            text("SELECT setval(pg_get_serial_sequence('change_features', 'id'), (SELECT max(id) FROM change_features))")
        )
        conn.execute(text("DELETE FROM facilities"))  # cascades to facility_changes
        facilities[["facility_id", "name", "class", "cover", "area_ha", "geometry"]].to_postgis(
            "facilities", conn, if_exists="append", index=False
        )
        conn.execute(
            text(f"INSERT INTO facility_changes ({', '.join(fac_cols)}) VALUES ({', '.join(':' + c for c in fac_cols)})"),
            _nulls(fac_changes[fac_cols]).to_dict("records"),
        )
        if password := os.environ.get("READER_PASSWORD"):
            literal = conn.execute(text("SELECT quote_literal(:p)"), {"p": password}).scalar_one()
            conn.exec_driver_sql(f"ALTER ROLE s2cd_reader PASSWORD {literal}".replace("%", "%%"))
        else:
            print("READER_PASSWORD not set: s2cd_reader password left unchanged", file=sys.stderr)


def runs_table(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{**r, "params": json.dumps(r["params"]), "summary": json.dumps(r["summary"])} for r in rows])
