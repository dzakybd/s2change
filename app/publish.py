"""Publish the SQLite results into PostGIS for the app: ``python -m app.publish`` (idempotent, rerunnable).

Reads data/change_detection.sqlite (change_runs, change_features, facilities, facility_changes).
Env: DATABASE_URL (admin/owner), READER_PASSWORD (password for the read-only role s2cd_reader).
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import geopandas as gpd
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from s2cd.db import FEATURE_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
MAIN_DB = ROOT / "data" / "change_detection.sqlite"
SCHEMAS = [ROOT / "sql" / "schema_postgis.sql", ROOT / "sql" / "schema_app.sql"]


def _table(path: Path, name: str) -> pd.DataFrame:
    with closing(sqlite3.connect(path)) as con:
        return pd.read_sql(f"SELECT * FROM {name}", con)


def _layer(path: Path, name: str) -> gpd.GeoDataFrame:
    """A spatial layer in EPSG:32735 (the SQLite files carry no CRS); the SQLite feature id becomes the ``id`` column."""
    gdf = gpd.read_file(path, layer=name, fid_as_index=True).rename_axis("id").reset_index()
    return gdf.set_crs(32735, allow_override=True) if gdf.crs is None else gdf.to_crs(32735)


def _insert(conn, table: str, df: pd.DataFrame, columns: list[str]) -> None:
    df = df[columns].astype(object).where(df[columns].notna(), None)
    conn.execute(
        text(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})"), df.to_dict("records")
    )


def load_runs(conn, path: Path) -> dict:
    """Make the database hold exactly the runs of ``path`` (the old ones cascade away), keeping the SQLite feature ids."""
    runs = _table(path, "change_runs")
    feats = _layer(path, "change_features")
    for c in ("date_before", "date_after"):
        feats[c] = pd.to_datetime(feats[c]).dt.date
    for c in ("in_disturbed_area", "touches_aoi_edge"):
        feats[c] = feats[c].map(lambda v: None if pd.isna(v) else bool(v))
    feats["zone"] = feats["zone"].astype("Int64").astype(object).where(feats["zone"].notna(), None)
    conn.execute(text("DELETE FROM change_runs"))
    _insert(conn, "change_runs", runs, ["run_id", "method", "date_before", "date_after", "params", "summary"])
    feats[["id", *FEATURE_COLUMNS, "geometry"]].to_postgis("change_features", conn, if_exists="append", index=False)
    return {"runs": len(runs), "features": len(feats)}


def load_facilities(conn, path: Path) -> dict:
    """Replace the facility register (``facilities`` + ``facility_changes``) with the contents of ``path``."""
    facilities = _layer(path, "facilities")
    changes = _table(path, "facility_changes")
    # Facilities without change are stored as a bare NaN, which is not JSON: publish them as {}.
    changes["change_types"] = [json.dumps(t if isinstance(t := json.loads(raw), dict) else {}) for raw in changes["change_types"]]
    conn.execute(text("DELETE FROM facilities"))  # cascades to facility_changes
    facilities[["facility_id", "name", "class", "cover", "area_ha", "geometry"]].to_postgis(
        "facilities", conn, if_exists="append", index=False
    )
    _insert(conn, "facility_changes", changes, ["facility_id", "changed_ha", "changed_share", "change_types", "summary"])
    return {"facilities": len(facilities), "changes": len(changes)}


def set_reader_password(conn) -> None:
    password = os.environ.get("READER_PASSWORD")
    if not password:
        print("READER_PASSWORD not set: s2cd_reader password left unchanged", file=sys.stderr)
        return
    literal = conn.execute(text("SELECT quote_literal(:p)"), {"p": password}).scalar_one()
    conn.exec_driver_sql(f"ALTER ROLE s2cd_reader PASSWORD {literal}".replace("%", "%%"))


def main() -> None:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    engine = create_engine(url)
    with engine.begin() as conn:
        for schema in SCHEMAS:
            conn.exec_driver_sql(schema.read_text().replace("%", "%%"))
        print("runs:", load_runs(conn, MAIN_DB))
        conn.execute(
            text("SELECT setval(pg_get_serial_sequence('change_features', 'id'), (SELECT max(id) FROM change_features))")
        )
        print("facilities:", load_facilities(conn, MAIN_DB))
        set_reader_password(conn)


if __name__ == "__main__":
    main()
