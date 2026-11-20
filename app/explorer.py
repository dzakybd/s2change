"""Database explorer: schema, example queries and a guarded free-form SELECT, for the viewer's "store and query" panel.

``run_query`` reuses the agent's SQL guard (one SELECT over allow-listed relations). Geometry columns come back as a GeoJSON
FeatureCollection in EPSG:4326 so a result can be drawn on the map.
"""

from __future__ import annotations

import re
import time
from functools import lru_cache
from pathlib import Path

import shapely
from fastapi.encoders import jsonable_encoder
from pyproj import Transformer
from shapely.ops import transform

from app import db
from app.guard import ALLOWED_TABLES, guard_sql

EXAMPLES_SQL = Path(__file__).resolve().parents[1] / "sql" / "queries_postgis.sql"
MAX_ROWS = 500
TABLES = sorted(ALLOWED_TABLES - {"change_features_wgs84"})  # the view only repeats change_features in lon/lat
_GEOMETRY = re.compile(r"geometry\((?P<type>\w+),(?P<srid>\d+)\)")
_TITLE = re.compile(r"^--\s*\d+\.\s+(?P<title>.+)$")


def schema() -> list[dict]:
    """Tables with row counts, columns and types, and the geometry column (if any) with its SRID and type."""
    cols = db.query(
        "SELECT c.relname AS tbl, a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type "
        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = ANY(:tables) AND a.attnum > 0 AND NOT a.attisdropped "
        "ORDER BY c.relname, a.attnum",
        tables=TABLES,
    )
    out = []
    for table in TABLES:  # names come from the allow-list constant, never from the request
        columns = [{"name": c["name"], "type": c["type"]} for c in cols if c["tbl"] == table]
        geometry = next(
            (
                {"column": c["name"], "srid": int(m["srid"]), "type": m["type"]}
                for c in columns
                if (m := _GEOMETRY.fullmatch(c["type"]))
            ),
            None,
        )
        rows = db.query(f"SELECT count(*)::int AS n FROM {table}")[0]["n"]
        out.append({"table": table, "rows": rows, "columns": columns, "geometry": geometry})
    return out


def parse_examples(text: str) -> list[dict]:
    """Titled blocks of the SQL file: ``-- N. Title``, comment lines as description, then SQL up to the closing ``;``."""
    examples: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        if line.startswith("\\"):  # psql meta-commands (\set, \pset)
            continue
        if m := _TITLE.match(line):
            current = {"title": m["title"].strip(), "description": [], "sql": []}
            examples.append(current)
        elif current is None:
            continue
        elif not current["sql"] and line.startswith("--"):
            current["description"].append(line.removeprefix("--").strip())
        elif current["sql"] or line.strip():
            current["sql"].append(line)
            if line.rstrip().endswith(";"):
                current = None
    return [
        {"title": e["title"], "description": " ".join(e["description"]), "sql": "\n".join(e["sql"]).strip()} for e in examples
    ]


def examples(run_id: str) -> list[dict]:
    """The example queries with the psql variable ``:'run_id'`` replaced by the given run (a known id, so safe to inline)."""
    return [{**e, "sql": e["sql"].replace(":'run_id'", f"'{run_id}'")} for e in parse_examples(EXAMPLES_SQL.read_text())]


@lru_cache(maxsize=1)
def _geometry_oid() -> int:
    return db.query("SELECT 'geometry'::regtype::oid AS oid")[0]["oid"]


@lru_cache(maxsize=8)
def _to_wgs84(srid: int):
    return Transformer.from_crs(srid, 4326, always_xy=True).transform


def _geojson(hex_ewkb: str) -> dict:
    geom = shapely.from_wkb(hex_ewkb)
    srid = shapely.get_srid(geom) or 4326
    return shapely.geometry.mapping(geom if srid == 4326 else transform(_to_wgs84(srid), geom))


def run_query(sql: str) -> dict:
    """Run one guarded SELECT. Returns columns, rows (geometry shown as its type), truncated, elapsed_ms and geojson | None."""
    guarded = guard_sql(sql, max_rows=MAX_ROWS + 1)
    started = time.perf_counter()
    with db.reader().connect() as conn:
        res = conn.exec_driver_sql(guarded.replace("%", "%%"))
        names = list(res.keys())
        is_geom = [col.type_code == _geometry_oid() for col in res.cursor.description]
        fetched = res.fetchmany(MAX_ROWS + 1)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    rows, truncated = fetched[:MAX_ROWS], len(fetched) > MAX_ROWS
    first = is_geom.index(True) if True in is_geom else None
    features = []
    if first is not None:
        for r in rows:
            if r[first] is not None:
                props = {n: v for n, v, g in zip(names, r, is_geom, strict=True) if not g}
                features.append({"type": "Feature", "geometry": _geojson(r[first]), "properties": jsonable_encoder(props)})
    shown = [["geometry" if g and v is not None else v for v, g in zip(r, is_geom, strict=True)] for r in rows]
    return {
        "columns": names,
        "rows": jsonable_encoder(shown),
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
        "geojson": {"type": "FeatureCollection", "features": features} if first is not None else None,
    }
