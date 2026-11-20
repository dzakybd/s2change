"""Agent tools. Each returns compact JSON; errors are returned as {"error": ...} so the model can recover."""
# ruff: noqa: E501

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from functools import wraps
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel

from app import collector, db, knowledge, results
from app.guard import MAX_ROWS, SqlRejected, guard_sql
from app.results import resolve_run

GROUPS = ("change_direction", "in_disturbed_area", "facility_id", "touches_aoi_edge")
DIRECTION_MEANING = {
    "brightening": "surface got brighter (e.g. fresh bare ground, dry tailings beach, new fill)",
    "darkening": "surface got darker (e.g. wet slurry, water, shadow, vegetation or burn)",
    "spectral shift": "colour changed while brightness stayed about the same (e.g. different waste-rock material)",
}


def _json(obj) -> str:
    return json.dumps(obj, default=lambda o: float(o) if isinstance(o, Decimal) else str(o), separators=(",", ":"))


def safe(fn: Callable) -> Callable:
    """Turn expected failures into an {"error"} result the model can read, instead of crashing the run."""

    @wraps(fn)
    def run(*args, **kwargs) -> str:
        try:
            return _json(fn(*args, **kwargs))
        except (ValueError, SqlRejected) as e:
            return _json({"error": str(e)})
        except Exception as e:  # DB errors (timeouts, bad column): report the type and first line only
            return _json({"error": f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"})

    return run


@tool
@safe
def list_runs() -> str:
    """List all change-detection runs with method, dates, number of polygons and changed area in hectares."""
    return db.query(
        "SELECT r.run_id, r.method, r.date_before, r.date_after, count(f.id) AS n_features, "
        "round((coalesce(sum(f.area_m2), 0) / 1e4)::numeric, 2) AS changed_ha "
        "FROM change_runs r LEFT JOIN change_features f USING (run_id) GROUP BY r.run_id ORDER BY r.run_id"
    )


@tool
@safe
def area_summary(group_by: Literal[*GROUPS], run_id: str | None = None) -> str:  # type: ignore[valid-type]
    """Changed area (ha), polygon count and mean confidence grouped by one attribute for a run. For change types use
    change_type_summary. run_id defaults to the default comparison (robust_cva, nominal 2023-08-12 -> 2023-09-02)."""
    run = resolve_run(run_id)
    if group_by not in GROUPS:
        raise ValueError(f"group_by must be one of {list(GROUPS)}")
    rows = db.query(
        f"SELECT {group_by} AS grp, count(*) AS n, round((sum(area_m2) / 1e4)::numeric, 2) AS area_ha, "
        f"round(avg(confidence)::numeric, 3) AS mean_confidence FROM change_features WHERE run_id = :r "
        f"GROUP BY 1 ORDER BY area_ha DESC",
        r=run,
    )
    return {"run_id": run, "group_by": group_by, "total_ha": round(sum(float(r["area_ha"]) for r in rows), 2), "groups": rows}


@tool
@safe
def change_type_summary(run_id: str | None = None) -> str:
    """Hectares and polygon count per change_type (pit_excavation, waste_dumping, tailings_deposition, water_level_change,
    land_clearing, noise_or_artifact, ...) and brief_category for a run. change_type is a rule-based hypothesis from RGB
    evidence and the facility register, available for the robust_cva run only."""
    return results.change_type_summary(run_id)


@tool
@safe
def facility_summary(facility_id: str | None = None) -> str:
    """The facility register (pits, waste dumps, tailings, dam, plant, camp, town) with each facility's class, area (ha),
    changed hectares, share of the facility that changed, hectares per change_type and a one-line summary, largest change
    first. Pass facility_id (e.g. 'WD-E') for one facility. Covers the default comparison only."""
    rows = results.facility_rows(facility_id)
    if not rows:
        raise ValueError(f"No facility {facility_id!r}. Call facility_summary without an id to list them.")
    return rows


@tool
@safe
def aoi_stats(geometry: dict, run_id: str | None = None) -> str:
    """Change inside an area of interest: a GeoJSON Polygon or MultiPolygon in lon/lat (EPSG:4326). Returns the area (ha),
    changed hectares (polygons clipped to the area), changed share, polygon count, and hectares per change_type and per
    facility. Use it for 'how much changed inside this box/area' questions."""
    return results.aoi_stats(geometry, run_id)


@tool
@safe
def read_context(topic: Literal[*knowledge.TOPICS]) -> str:  # type: ignore[valid-type]
    """Background notes (markdown) for questions about meaning rather than numbers. Topics: use_case (who it is for),
    site (the mine and its surroundings), facilities (what each facility class is and looks like at 10 m), seasons (wet/dry
    season, fire, dambos), method (how change and facilities were derived), interpretation (signal -> likely cause -> how to
    confirm). Numbers still come from the database tools."""
    return {"topic": topic, "text": knowledge.read(topic)}


@tool
@safe
def top_changes(
    n: int = 5,
    order_by: Literal["area_m2", "confidence"] = "area_m2",
    run_id: str | None = None,
    min_confidence: float = 0.0,
    in_disturbed_area: bool | None = None,
    change_direction: Literal["brightening", "darkening", "spectral shift"] | None = None,
    change_type: str | None = None,
    facility_id: str | None = None,
) -> str:
    """The n (max 25) largest or most confident change polygons of a run, optionally filtered. Returns ids, area (ha),
    confidence, direction, change_type, facility_id, explanation, centroid, and the combined bbox [west, south, east, north]
    in lon/lat that map_action can fly to. in_disturbed_area=true means inside the heuristic mine footprint."""
    run, n = resolve_run(run_id), max(1, min(int(n), 25))
    order = {"area_m2": "area_m2", "confidence": "confidence"}[order_by]
    rows = db.query(
        f"SELECT id, round((area_m2 / 1e4)::numeric, 2) AS area_ha, round(confidence::numeric, 3) AS confidence, change_direction, "
        f"in_disturbed_area, change_type, facility_id, explanation, ST_X(ST_Centroid(g)) AS lon, ST_Y(ST_Centroid(g)) AS lat, "
        f"ST_XMin(g) AS w, ST_YMin(g) AS s, ST_XMax(g) AS e, ST_YMax(g) AS n "
        f"FROM (SELECT *, ST_Transform(geometry, 4326) AS g FROM change_features WHERE run_id = :r AND confidence >= :c "
        f"AND (CAST(:d AS boolean) IS NULL OR in_disturbed_area = :d) AND (CAST(:dir AS text) IS NULL OR change_direction = :dir) "
        f"AND (CAST(:type AS text) IS NULL OR change_type = :type) AND (CAST(:fac AS text) IS NULL OR facility_id = :fac)) t "
        f"ORDER BY {order} DESC, id LIMIT :n",
        r=run, c=min_confidence, d=in_disturbed_area, dir=change_direction, type=change_type, fac=facility_id, n=n,
    )  # fmt: skip
    if not rows:
        return {"run_id": run, "ids": [], "note": "No polygons match these filters."}
    bbox = [min(r["w"] for r in rows), min(r["s"] for r in rows), max(r["e"] for r in rows), max(r["n"] for r in rows)]
    items = [
        {k: (round(v, 5) if k in ("lon", "lat") else v) for k, v in r.items() if k not in ("w", "s", "e", "n")} for r in rows
    ]
    return {"run_id": run, "ids": [r["id"] for r in rows], "bbox": [round(v, 5) for v in bbox], "changes": items}


@tool
@safe
def explain_change(id: int) -> str:
    """Everything known about one change polygon: attributes (area, confidence, direction, RGB deltas, facility, whether it
    touches the AOI edge), its change_type with the rule's explanation, and a plain-language reading with caveats."""
    rows = db.query(
        "SELECT id, run_id, round((area_m2 / 1e4)::numeric, 2) AS area_ha, round(confidence::numeric, 3) AS confidence, "
        "round(neglog10_p::numeric, 1) AS neglog10_p, change_direction, in_disturbed_area, touches_aoi_edge, d_blue, d_green, "
        "d_red, d_brightness, change_type, brief_category, explanation, facility_id FROM change_features WHERE id = :i",
        i=int(id),
    )
    if not rows:
        raise ValueError(f"No change polygon with id {id}.")
    r = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in rows[0].items() if v is not None}
    reading = [
        f"A {r['area_ha']} ha polygon, {r.get('change_direction', 'unclassified')}: {DIRECTION_MEANING.get(r.get('change_direction'), '')}."
    ]
    if r.get("facility_id"):
        reading.append(f"It lies mostly inside facility {r['facility_id']}.")
    if r.get("change_type"):
        reading.append(f"Rule-based hypothesis '{r['change_type']}' ({r['explanation']}); it needs a manual check.")
    else:
        reading.append("This run carries no change_type: only the robust_cva run is interpreted.")
    if r.get("touches_aoi_edge"):
        reading.append("It touches the AOI edge, so it may continue outside and its size is a lower bound.")
    reading.append("Confidence is a magnitude score of the spectral change, not a probability of being correct.")
    return {"attributes": r, "reading": " ".join(reading)}


@tool
@safe
def readonly_sql(query: str) -> str:
    """Run one read-only PostgreSQL/PostGIS SELECT (max 200 rows, 5 s). Tables: change_runs(run_id, method, date_before,
    date_after, params, summary), change_features(id, run_id, area_m2, perimeter_m, confidence, neglog10_p, change_direction,
    in_disturbed_area, touches_aoi_edge, d_blue, d_green, d_red, d_brightness, change_type, brief_category, explanation,
    facility_id, geometry in EPSG:32735 metres), facilities(facility_id, name, class, cover, area_ha, geometry),
    facility_changes(facility_id, changed_ha, changed_share, change_types jsonb, summary), view change_features_wgs84
    (geometry in lon/lat). Use for questions the other tools cannot answer. Do not select raw geometry."""
    sql = guard_sql(query)
    with db.reader().connect() as conn:
        res = conn.exec_driver_sql(sql.replace("%", "%%"))
        cols = list(res.keys())
        rows = [[str(v)[:120] if isinstance(v, str) else v for v in r] for r in res.fetchmany(MAX_ROWS)]
    return {"sql": sql, "columns": cols, "rows": rows}


@tool
@safe
def list_dates() -> str:
    """Dates the map can compare besides the provided pair: one clear Sentinel-2 scene per month after it (Sept 2023 to
    now; rainy-season months are missing). Use them with map_action compare_dates. Their results are not in the database."""
    from s2cd import catalog

    return {"dates": [e["date"] for e in catalog.load()], "provided": ["provided-before", "provided-after"]}


@tool
@safe
def map_action(
    type: Literal["fly_to", "highlight", "show_run", "set_layer", "set_view", "show_facility", "compare_dates"],
    bbox: list[float] | None = None,
    run_id: str | None = None,
    ids: list[int] | None = None,
    layer: Literal["types", "after", "before", "after_raw", "intensity", "binary", "facilities", "redness"] | None = None,
    swipe: bool | None = None,
    oblique: bool | None = None,
    facility_id: str | None = None,
    before: str | None = None,
    after: str | None = None,
) -> str:
    """Control the map the user is looking at (several actions per answer are fine; they run in order).
    fly_to: bbox=[west, south, east, north] lon/lat. highlight: run_id + polygon ids (from top_changes). show_run: switch the
    displayed run. set_layer: layer = types (change polygons by type), after/before (true colour), after_raw, intensity
    (change magnitude heat map), binary, facilities (facility register), redness (B04/B02 iron-oxide proxy).
    set_view: swipe (before/after slider) and/or oblique (tilted 2.5D view) true/false. show_facility: facility_id (from
    facility_summary) -> zoom to it and open its card. compare_dates: before/after = 'provided-before', 'provided-after' or a
    date from list_dates -> the map loads that comparison (results are not in the database). Only act on ids, runs,
    facilities and dates returned by other tools."""
    if run_id is not None:
        run_id = resolve_run(run_id)
    if type == "highlight":
        found = db.query(
            "SELECT id FROM change_features WHERE run_id = :r AND id = ANY(:ids)", r=run_id, ids=[int(i) for i in ids or []]
        )
        ids = [r["id"] for r in found]
    if type == "show_facility" and not results.facility_rows(facility_id):
        raise ValueError(f"No facility {facility_id!r}. Call facility_summary to list them.")
    if type == "compare_dates":
        from s2cd import compare

        compare.validate_pair(before or "", after or "")  # ValueError with a usable message for unknown dates
    kw = {"layer": layer, "swipe": swipe, "oblique": oblique, "facility_id": facility_id, "before": before, "after": after}
    return {"ok": True, "action": collector.current().add_action(type, bbox, run_id, ids, **kw)}


class Series(BaseModel):
    name: str
    y: list[float | None]


@tool
@safe
def make_chart(type: Literal["bar", "line"], title: str, x: list[str], series: list[Series]) -> str:
    """Show a chart next to the answer. x are category labels; each series has a name and one y value per x. Use
    numbers taken from tool results only. Units go in the title or series name."""
    return {"ok": True, "chart": collector.current().add_chart(type, title, x, [s.model_dump() for s in series])["title"]}


@tool
@safe
def make_table(title: str, columns: list[str], rows: list[list[str | float | None]]) -> str:
    """Show a table next to the answer (max 100 rows). Every row needs one value per column; use numbers from tool results."""
    return {"ok": True, "table": collector.current().add_table(title, columns, rows)["title"]}


ALL_TOOLS = [
    list_dates,
    list_runs,
    area_summary,
    change_type_summary,
    facility_summary,
    aoi_stats,
    top_changes,
    explain_change,
    read_context,
    readonly_sql,
    map_action,
    make_chart,
    make_table,
]
