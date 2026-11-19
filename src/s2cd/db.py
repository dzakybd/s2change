"""Store change polygons in SpatiaLite (default, single file) or PostGIS (``DATABASE_URL``).

Both backends get the same logical schema (see ``sql/schema_postgis.sql``):

change_runs      one row per (date pair, method) run, with parameters and summary as JSON
change_features  one row per change polygon, geometry stored as MULTIPOLYGON in EPSG:32735

Writes are idempotent per ``run_id``: rerunning the pipeline replaces that run's rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely

FEATURE_COLUMNS = [
    "run_id",
    "date_before",
    "date_after",
    "method",
    "area_m2",
    "perimeter_m",
    "compactness",
    "confidence",
    "mean_p_change",
    "strong_fraction",
    "change_direction",
    "in_disturbed_area",
    "d_blue",
    "d_green",
    "d_red",
    "d_brightness",
    "brightness_before",
    "brightness_after",
    "zone",
]
SCHEMA_SQL = Path(__file__).resolve().parents[2] / "sql" / "schema_postgis.sql"


def as_feature_table(gdf: gpd.GeoDataFrame, run_id: str, method: str, date_before, date_after) -> gpd.GeoDataFrame:
    out = gdf.copy()
    out["run_id"], out["method"] = run_id, method
    out["date_before"], out["date_after"] = str(date_before), str(date_after)
    extra = [c for c in out.columns if c not in FEATURE_COLUMNS and c not in ("geometry", "region")]
    out = out[FEATURE_COLUMNS + extra + ["geometry"]]
    return out.set_geometry(out.geometry.apply(_to_multi))


def _to_multi(g):
    return g if g.geom_type == "MultiPolygon" else shapely.MultiPolygon([g])


def write_sqlite(path: Path, features: gpd.GeoDataFrame, runs: pd.DataFrame) -> Path:
    """Write a SpatiaLite database (GDAL SQLite driver, SPATIALITE=YES) with an R*Tree spatial index."""
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
    return path


def write_postgis(url: str, features: gpd.GeoDataFrame, runs: pd.DataFrame) -> int:
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
        features.drop(columns=[c for c in features.columns if c not in FEATURE_COLUMNS + ["geometry"]]).to_postgis(
            "change_features", conn, if_exists="append", index=False
        )
    return len(features)


def runs_table(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{**r, "params": json.dumps(r["params"]), "summary": json.dumps(r["summary"])} for r in rows])
