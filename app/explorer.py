"""Database explorer for the viewer's Database tab: the schema with its keys, paged table rows, a SQL dump and the
connection details that show the data really lives in PostGIS.

Table and column names never come from the request as SQL: a request names a table, and it must be one of ``TABLES``;
sort and filter columns must be columns of that table (read from the catalogue). Values are always bound parameters.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import date, datetime
from decimal import Decimal

from fastapi.encoders import jsonable_encoder
from sqlalchemy.engine import make_url

from app import db

# Shown in this order. Descriptions are what a reader needs to know before opening the table.
TABLES = {
    "change_runs": "One row per change-detection run: the method, the two dates, its parameters and summary numbers.",
    "change_features": "Every change polygon, with its type, a plain-language description, confidence and facility.",
    "facilities": "The facility register: pits, dumps, tailings, plant, camp, dam and town, outlined from the imagery.",
    "facility_changes": "Area changed per facility in the default comparison, and the mix of change types.",
}
# Not a database constraint (a change can lie outside every facility), but the same key.
LOGICAL_LINKS = [{"table": "change_features", "column": "facility_id", "ref_table": "facilities", "ref_column": "facility_id"}]
PAGE_SIZES = (10, 25, 50, 100)
_GEOMETRY = re.compile(r"geometry\((?P<type>\w+),(?P<srid>\d+)\)")
_FK = re.compile(r"FOREIGN KEY \((?P<col>\w+)\) REFERENCES (?P<ref>\w+)\((?P<ref_col>\w+)\)")
_PK = re.compile(r"PRIMARY KEY \((?P<cols>[\w, ]+)\)")
EXPORT_TTL_S = 600


def _columns() -> dict[str, list[dict]]:
    rows = db.query(
        "SELECT c.relname AS tbl, a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull AS not_null "
        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = ANY(:tables) AND a.attnum > 0 AND NOT a.attisdropped "
        "ORDER BY c.relname, a.attnum",
        tables=list(TABLES),
    )
    out: dict[str, list[dict]] = {t: [] for t in TABLES}
    for r in rows:
        out[r["tbl"]].append({"name": r["name"], "type": r["type"], "not_null": r["not_null"]})
    return out


def _constraints() -> list[dict]:
    return db.query(
        "SELECT conrelid::regclass::text AS tbl, conname AS name, contype AS kind, pg_get_constraintdef(oid) AS def "
        "FROM pg_constraint WHERE conrelid::regclass::text = ANY(:tables) "
        "ORDER BY conrelid::regclass::text, contype DESC, conname",
        tables=list(TABLES),
    )


def _primary_key(constraints: list[dict], table: str) -> list[str]:
    for c in constraints:
        if c["tbl"] == table and (m := _PK.search(c["def"])):
            return m["cols"].replace(" ", "").split(",")
    return []


def _geometry(cols: list[dict]) -> dict | None:
    for c in cols:
        if m := _GEOMETRY.fullmatch(c["type"]):
            return {"column": c["name"], "srid": int(m["srid"]), "type": m["type"]}
    return None


def schema() -> list[dict]:
    """Tables in display order: description, row count, columns with types, primary key, links to other tables (real
    foreign keys and the logical facility link) and the geometry column with its type and SRID."""
    columns, constraints = _columns(), _constraints()
    out = []
    for table, description in TABLES.items():
        cols = columns[table]
        pk = _primary_key(constraints, table)
        links = [
            {"column": m["col"], "ref_table": m["ref"], "ref_column": m["ref_col"], "enforced": True}
            for c in constraints
            if c["tbl"] == table and (m := _FK.search(c["def"]))
        ]
        links += [
            {"column": x["column"], "ref_table": x["ref_table"], "ref_column": x["ref_column"], "enforced": False}
            for x in LOGICAL_LINKS
            if x["table"] == table
        ]
        geometry = _geometry(cols)
        rows = db.query(f"SELECT count(*)::int AS n FROM {table}")[0]["n"]  # name from the TABLES constant
        out.append(
            {
                "table": table,
                "description": description,
                "rows": rows,
                "primary_key": pk,
                "links": links,
                "columns": [{"name": c["name"], "type": c["type"]} for c in cols],
                "geometry": geometry,
            }
        )
    return out


def _table_columns(table: str) -> list[dict]:
    if table not in TABLES:
        raise ValueError(f"Unknown table {table!r}. Tables: {', '.join(TABLES)}.")
    return _columns()[table]


def page(table: str, page: int = 1, size: int = 25, sort: str | None = None, desc: bool = False, column: str | None = None,
         value: str | None = None, geojson: bool = False) -> dict:  # fmt: skip
    """One page of a table. The geometry column is summarised (type, vertices) instead of returned; ``geojson`` adds the
    page's geometries in EPSG:4326 so it can be drawn on the map. ``column`` = ``value`` filters on one column."""
    cols = _table_columns(table)
    names = [c["name"] for c in cols]
    geom = (_geometry(cols) or {}).get("column")
    if size not in PAGE_SIZES:
        raise ValueError(f"Page size must be one of {', '.join(map(str, PAGE_SIZES))}.")
    for name in (sort, column):
        if name is not None and (name not in names or name == geom):
            raise ValueError(f"{table} has no sortable or filterable column {name!r}.")
    pk = _primary_key(_constraints(), table)
    order = [sort] if sort else pk or [names[0]]
    order_sql = ", ".join(f'"{o}" {"DESC" if desc and o == sort else "ASC"} NULLS LAST' for o in order)
    where, params = "", {}
    if column is not None:
        where, params = f'WHERE "{column}"::text = :value', {"value": value or ""}
    total = db.query(f"SELECT count(*)::int AS n FROM {table} {where}", **params)[0]["n"]
    pages = max(1, math.ceil(total / size))
    page = min(max(1, page), pages)
    summary = """replace(ST_GeometryType("{n}"), 'ST_', '') || ', ' || ST_NPoints("{n}") || ' vertices' AS "{n}\""""
    shown = [summary.format(n=n) if n == geom else f'"{n}"' for n in names]
    if geojson and geom:
        shown.append(f'ST_AsGeoJSON(ST_Transform("{geom}", 4326), 6) AS __geojson')
    started = time.perf_counter()
    rows = db.query(
        f"SELECT {', '.join(shown)} FROM {table} {where} ORDER BY {order_sql} LIMIT :limit OFFSET :offset",
        **params, limit=size, offset=(page - 1) * size,
    )  # fmt: skip
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    features = None
    if geojson and geom:
        features = []
        for r in rows:
            g = r.pop("__geojson")
            if g:
                props = jsonable_encoder({k: v for k, v in r.items() if k != geom})
                features.append({"type": "Feature", "geometry": json.loads(g), "properties": props})
    return {
        "table": table,
        "columns": [{"name": c["name"], "type": c["type"]} for c in cols],
        "rows": jsonable_encoder([[r[n] for n in names] for r in rows]),
        "total": total,
        "page": page,
        "pages": pages,
        "size": size,
        "sort": sort,
        "desc": desc,
        "elapsed_ms": elapsed_ms,
        "geojson": {"type": "FeatureCollection", "features": features} if features is not None else None,
    }


# ---- SQL dump ------------------------------------------------------------------------------------------------------


def _literal(v, geometry: bool = False) -> str:
    if v is None:
        return "NULL"
    if geometry:
        return f"'{v}'::geometry"  # hex EWKB (keeps the SRID)
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int | Decimal):
        return str(v)
    if isinstance(v, float):
        return f"'{v}'::float8" if not math.isfinite(v) else repr(v)
    if isinstance(v, dict | list):
        return "'" + json.dumps(v, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    if isinstance(v, datetime | date):
        return f"'{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


_export_cache: dict[str, tuple[float, str]] = {}


def export_sql(table: str | None = None) -> str:
    """A self-contained SQL file: CREATE EXTENSION postgis, CREATE TABLE with the live constraints and indexes, then one
    INSERT per row (geometry as hex EWKB). ``table=None`` dumps every table, parents before children."""
    tables = list(TABLES) if table is None else [table]
    for t in tables:
        _table_columns(t)
    key = table or "*"
    if (hit := _export_cache.get(key)) and time.monotonic() - hit[0] < EXPORT_TTL_S:
        return hit[1]
    order = ["change_runs", "facilities", "change_features", "facility_changes"]  # referenced tables first
    tables.sort(key=order.index)
    columns, constraints = _columns(), _constraints()
    indexes = db.query(
        "SELECT tablename AS tbl, indexdef AS def FROM pg_indexes WHERE schemaname = 'public' AND tablename = ANY(:tables) "
        "AND indexname NOT IN (SELECT conname FROM pg_constraint) ORDER BY tablename, indexname",
        tables=tables,
    )
    out = [
        "-- s2change: change detection at an open-pit copper mine in NW Zambia (https://s2change.dzaky.ai.id)",
        f"-- PostGIS dump of {', '.join(tables)}, exported {datetime.now().astimezone():%Y-%m-%d %H:%M %Z}.",
        '-- Load with: psql "$DATABASE_URL" -f this_file.sql',
        "",
        "CREATE EXTENSION IF NOT EXISTS postgis;",
        "BEGIN;",
        "",
    ]
    for t in tables:
        cols = columns[t]
        geom = {c["name"] for c in cols if _GEOMETRY.fullmatch(c["type"])}
        defs = [f'    "{c["name"]}" {c["type"]}{" NOT NULL" if c["not_null"] else ""}' for c in cols]
        defs += [f'    CONSTRAINT "{c["name"]}" {c["def"]}' for c in constraints if c["tbl"] == t and c["kind"] in "pfc"]
        out += [f"-- {TABLES[t]}", f"CREATE TABLE IF NOT EXISTS {t} (", ",\n".join(defs), ");"]
        out += [f"{i['def'].replace('CREATE INDEX', 'CREATE INDEX IF NOT EXISTS', 1)};" for i in indexes if i["tbl"] == t]
        ewkb = """encode(ST_AsEWKB("{n}"), 'hex') AS "{n}\""""
        select = ", ".join(ewkb.format(n=c["name"]) if c["name"] in geom else f'"{c["name"]}"' for c in cols)
        names = ", ".join(f'"{c["name"]}"' for c in cols)
        rows = db.query(f"SELECT {select} FROM {t}")
        out.append("")
        for r in rows:
            values = ", ".join(_literal(r[c["name"]], c["name"] in geom) for c in cols)
            out.append(f"INSERT INTO {t} ({names}) VALUES ({values});")
        out.append("")
    out.append("COMMIT;")
    text = "\n".join(out) + "\n"
    _export_cache[key] = (time.monotonic(), text)
    return text


# ---- Connection ----------------------------------------------------------------------------------------------------


def _url():
    return make_url(os.environ["READER_DATABASE_URL"])


def connection() -> dict:
    """Server versions and the read-only connection URI with the password masked (the full URI needs the access code)."""
    v = db.query("SELECT version() AS pg, postgis_lib_version() AS postgis, postgis_full_version() AS full")[0]
    url = _url()
    return {
        "postgres": v["pg"].split(" on ")[0],
        "postgis": f"PostGIS {v['postgis']}",
        "postgis_full": v["full"],
        "host": url.host,
        "port": url.port,
        "database": url.database,
        "user": url.username,
        "uri_masked": url.set(drivername="postgresql", password="PASSWORD")
        .render_as_string(hide_password=False)
        .replace("PASSWORD", "••••••••"),
    }


def full_uri() -> str:
    url = _url()
    return url.set(drivername="postgresql").render_as_string(hide_password=False)
