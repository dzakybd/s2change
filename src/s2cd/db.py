"""Store change polygons in SpatiaLite (default, single file) or PostGIS (``DATABASE_URL``).

Both backends get the same logical schema (see ``sql/schema_postgis.sql``):

change_runs      one row per (date pair, method) run, with parameters and summary as JSON
change_features  one row per change polygon, geometry stored as a valid MULTIPOLYGON in EPSG:32735
facilities       (SpatiaLite only) the named facility outlines of the register
facility_changes (SpatiaLite only) changed area, change types and a one-line summary per facility

Writes are idempotent per ``run_id``: rerunning the pipeline replaces that run's rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

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
SCHEMA_SQL = Path(__file__).resolve().parents[2] / "sql" / "schema_postgis.sql"


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


def write_sqlite(
    path: Path,
    features: gpd.GeoDataFrame,
    runs: pd.DataFrame,
    facilities: gpd.GeoDataFrame | None = None,
    facility_changes: pd.DataFrame | None = None,
) -> Path:
    """Write a SpatiaLite database (GDAL SQLite driver, SPATIALITE=YES) with an R*Tree spatial index.

    ``facilities`` (MultiPolygon outlines) and ``facility_changes`` (plain table; dict columns are stored as JSON text)
    are optional extra layers.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    pyogrio.write_dataframe(
        features,
        path,
        layer="change_features",
        driver="SQLite",
        dataset_options={"SPATIALITE": "YES"},
        layer_options={"GEOMETRY_NAME": "geometry", "FID": "id", "SPATIAL_INDEX": "YES"},
        geometry_type="MultiPolygon",
    )
    pyogrio.write_dataframe(runs, path, layer="change_runs", driver="SQLite", append=True)
    if facilities is not None:
        pyogrio.write_dataframe(
            facilities,
            path,
            layer="facilities",
            driver="SQLite",
            append=True,
            layer_options={"GEOMETRY_NAME": "geometry", "FID": "id", "SPATIAL_INDEX": "YES"},
            geometry_type="MultiPolygon",
        )
    if facility_changes is not None:
        table = facility_changes.assign(change_types=facility_changes.change_types.map(json.dumps))
        pyogrio.write_dataframe(table, path, layer="facility_changes", driver="SQLite", append=True)
    return path


def write_postgis(url: str, features: gpd.GeoDataFrame, runs: pd.DataFrame) -> None:
    """Create the schema if needed, then replace the rows of every run_id in ``runs``."""
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(SCHEMA_SQL.read_text()))
        for _, r in runs.iterrows():
            conn.execute(text("DELETE FROM change_runs WHERE run_id = :rid"), {"rid": r.run_id})  # cascades to features
            conn.execute(
                text(
                    "INSERT INTO change_runs (run_id, method, date_before, date_after, params, summary) "
                    "VALUES (:run_id, :method, :date_before, :date_after, CAST(:params AS jsonb), CAST(:summary AS jsonb))"
                ),
                r.to_dict(),
            )
        rows = features[FEATURE_COLUMNS + ["geometry"]].copy()
        for c in rows.select_dtypes("floating").columns:
            rows[c] = rows[c].astype(object).where(rows[c].notna(), None)  # NaN -> NULL (NaN would fail the CHECKs)
        rows.to_postgis("change_features", conn, if_exists="append", index=False)


def runs_table(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{**r, "params": json.dumps(r["params"]), "summary": json.dumps(r["summary"])} for r in rows])
