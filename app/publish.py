"""Publish the SQLite results into PostGIS for the app: ``python -m app.publish`` (idempotent, rerunnable).

Reads data/change_detection.sqlite (change_runs, change_features, change_context) and, if present,
data/timeline/timeline.sqlite (change_runs, change_features incl. likely_cause, timeline_months) and
data/facilities/facilities.sqlite (facilities, facility_changes).
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
from sqlalchemy.dialects.postgresql import JSONB

from s2cd.db import FEATURE_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
MAIN_DB = ROOT / "data" / "change_detection.sqlite"
FACILITIES_DB = ROOT / "data" / "facilities" / "facilities.sqlite"
TIMELINE_DB = ROOT / "data" / "timeline" / "timeline.sqlite"
SCHEMAS = [ROOT / "sql" / "schema_postgis.sql", ROOT / "sql" / "schema_app.sql"]
TIMELINE_COLUMNS = [
    *("month", "status", "clear_fraction", "ndvi_mean", "nbr_mean", "mndwi_mean", "bsi_mean"),
    *("bare_ha", "water_ha", "footprint_ha", "changed_ha", "changed_ha_in_footprint"),
]
FEATURE_COLS = [*FEATURE_COLUMNS, "likely_cause"]


def _table(path: Path, name: str) -> pd.DataFrame | None:
    with closing(sqlite3.connect(path)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone():
            return None
        return pd.read_sql(f"SELECT * FROM {name}", con)


def _insert(conn, table: str, df: pd.DataFrame, columns: list[str] | None = None) -> None:
    """Insert the DataFrame columns that exist in the target table (``columns`` restricts them further)."""
    known = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table})
    known = {r[0] for r in known}
    cols = [c for c in (columns or df.columns) if c in df.columns and c in known]
    df = df[cols].astype(object).where(df[cols].notna(), None)
    conn.execute(
        text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})"), df.to_dict("records")
    )


def load_source(conn, path: Path, keep_ids: bool) -> dict:
    """Replace every run in ``path`` (cascades to features/context). ``keep_ids`` keeps the SQLite feature ids."""
    runs = _table(path, "change_runs")
    feats = gpd.read_file(path, layer="change_features", fid_as_index=False)
    feats = feats.set_crs(32735, allow_override=True) if feats.crs is None else feats.to_crs(32735)
    ctx = _table(path, "change_context")
    if "id" not in feats.columns:
        feats["id"] = range(1, len(feats) + 1)
    if ctx is not None and "likely_cause" in ctx.columns:
        feats["likely_cause"] = feats["id"].map(ctx.set_index("id")["likely_cause"])
    for c in ("date_before", "date_after"):
        feats[c] = pd.to_datetime(feats[c]).dt.date
    feats["in_disturbed_area"] = feats["in_disturbed_area"].map(lambda v: None if pd.isna(v) else bool(v))
    feats["zone"] = feats["zone"].astype("Int64").astype(object).where(feats["zone"].notna(), None)
    old_ids = feats["id"].copy()
    for run_id in runs["run_id"]:
        conn.execute(text("DELETE FROM change_runs WHERE run_id = :r"), {"r": run_id})
    _insert(conn, "change_runs", runs, ["run_id", "method", "date_before", "date_after", "params", "summary"])
    cols = [c for c in FEATURE_COLS if c in feats.columns]
    out = feats[[*cols, "geometry"]].copy()
    if keep_ids:
        out.insert(0, "id", old_ids)
    out.to_postgis("change_features", conn, if_exists="append", index=False)
    if ctx is not None and keep_ids:
        _insert(conn, "change_context", ctx)
    return {"runs": len(runs), "features": len(feats), "context": 0 if ctx is None else len(ctx)}


def load_timeline(conn, path: Path) -> int:
    months = _table(path, "timeline_months")
    conn.execute(text("DELETE FROM timeline_months"))
    if months is None:
        return 0
    months["month"] = months["month"].astype(str).str[:7]
    _insert(conn, "timeline_months", months, TIMELINE_COLUMNS)
    return len(months)


def load_facilities(conn, path: Path) -> dict:
    """Replace the facility inventory (``facilities`` + ``facility_changes``) with the contents of ``path``."""
    facilities = gpd.read_file(path, layer="facilities", fid_as_index=False)
    facilities = facilities.set_crs(32735, allow_override=True) if facilities.crs is None else facilities.to_crs(32735)
    changes = _table(path, "facility_changes")
    facilities["evidence"] = facilities["evidence"].map(json.loads)
    conn.execute(text("DELETE FROM facilities"))  # cascades to facility_changes
    facilities[["facility_id", "class", "confidence", "area_ha", "label", "evidence", "geometry"]].to_postgis(
        "facilities", conn, if_exists="append", index=False, dtype={"evidence": JSONB}
    )
    if changes is not None:
        _insert(conn, "facility_changes", changes)
    return {"facilities": len(facilities), "changes": 0 if changes is None else len(changes)}


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
        print("main:", load_source(conn, MAIN_DB, keep_ids=True))
        conn.execute(
            text("SELECT setval(pg_get_serial_sequence('change_features', 'id'), (SELECT max(id) FROM change_features))")
        )
        if TIMELINE_DB.exists():
            print("timeline runs:", load_source(conn, TIMELINE_DB, keep_ids=False))
            print("timeline months:", load_timeline(conn, TIMELINE_DB))
        else:
            print(f"{TIMELINE_DB.relative_to(ROOT)} not found: skipped")
        if FACILITIES_DB.exists():
            print("facilities:", load_facilities(conn, FACILITIES_DB))
        else:
            print(f"{FACILITIES_DB.relative_to(ROOT)} not found: skipped")
        set_reader_password(conn)


if __name__ == "__main__":
    main()
