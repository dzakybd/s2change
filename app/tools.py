"""Agent tools. Each returns compact JSON; errors are returned as {"error": ...} so the model can recover."""
# ruff: noqa: E501

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from functools import wraps
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel

from app import collector, db
from app.guard import MAX_ROWS, SqlRejected, guard_sql

DEFAULT_RUN = {"method": "robust_cva", "date_before": date(2023, 8, 12), "date_after": date(2023, 9, 2)}
GROUPS = {"change_direction": "change_direction", "likely_cause": "likely_cause", "in_disturbed_area": "in_disturbed_area"}
METRICS = [
    *("clear_fraction", "ndvi_mean", "nbr_mean", "mndwi_mean", "bsi_mean", "bare_ha", "water_ha"),
    *("footprint_ha", "changed_ha", "changed_ha_in_footprint"),
]
CAUSE_MEANING = {
    "active_mining_or_tailings": "inside the disturbed mine footprint with no stronger signal: pit, dump or tailings activity",
    "water_change": "water-class pixels or a large MNDWI shift: dam or pond level, turbidity or glint, not necessarily land change",
    "vegetation_loss": "NDVI dropped by more than 0.1: clearing, drying or burning of vegetation",
    "burn_scar": "NBR dropped by more than 0.1 outside the mine footprint: a probable dry-season fire",
    "cloud_shadow_haze_artifact": "30 % or more of the polygon is cloud, shadow or cirrus in the scene classification: likely not land change",
    "unexplained": "no rule matched; needs a manual look",
}
DIRECTION_MEANING = {
    "brightening": "surface got brighter (e.g. fresh bare ground, dry tailings beach, new fill)",
    "darkening": "surface got darker (e.g. wet slurry, water, shadow, vegetation or burn)",
    "spectral shift": "colour changed while brightness stayed about the same (e.g. different waste-rock material)",
}


def _json(obj) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))


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


def resolve_run(run_id: str | None) -> str:
    runs = db.query("SELECT run_id, method, date_before, date_after FROM change_runs ORDER BY run_id")
    ids = [r["run_id"] for r in runs]
    if run_id is None:
        hit = [r["run_id"] for r in runs if all(r[k] == v for k, v in DEFAULT_RUN.items())]
        if hit:
            return hit[0]
    elif run_id in ids:
        return run_id
    elif len(m := [i for i in ids if run_id.lower() in i.lower()]) == 1:
        return m[0]
    raise ValueError(f"Unknown run_id {run_id!r}. Available: {ids}")


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
def area_summary(
    group_by: Literal["change_direction", "likely_cause", "in_disturbed_area"],
    run_id: str | None = None,
) -> str:
    """Changed area (ha), polygon count and mean confidence grouped by one attribute for a run.
    run_id defaults to the assignment run (robust_cva, 2023-08-12 -> 2023-09-02). likely_cause is only available for some runs."""
    run = resolve_run(run_id)
    col = GROUPS[group_by]
    rows = db.query(
        f"SELECT {col} AS grp, count(*) AS n, round((sum(area_m2) / 1e4)::numeric, 2) AS area_ha, "
        f"round(avg(confidence)::numeric, 3) AS mean_confidence FROM change_features WHERE run_id = :r "
        f"GROUP BY 1 ORDER BY area_ha DESC",
        r=run,
    )
    return {"run_id": run, "group_by": group_by, "total_ha": round(sum(float(r["area_ha"]) for r in rows), 2), "groups": rows}


@tool
@safe
def top_changes(
    n: int = 5,
    order_by: Literal["area_m2", "confidence"] = "area_m2",
    run_id: str | None = None,
    min_confidence: float = 0.0,
    in_disturbed_area: bool | None = None,
    change_direction: Literal["brightening", "darkening", "spectral shift"] | None = None,
    likely_cause: str | None = None,
) -> str:
    """The n (max 25) largest or most confident change polygons of a run, optionally filtered. Returns ids, area (ha),
    confidence, direction, likely cause, centroid, and the combined bbox [west, south, east, north] in lon/lat that
    map_action can fly to. in_disturbed_area=true means inside the mine footprint."""
    run, n = resolve_run(run_id), max(1, min(int(n), 25))
    order = {"area_m2": "area_m2", "confidence": "confidence"}[order_by]
    rows = db.query(
        f"SELECT id, round((area_m2 / 1e4)::numeric, 2) AS area_ha, round(confidence::numeric, 3) AS confidence, change_direction, "
        f"in_disturbed_area, likely_cause, ST_X(ST_Centroid(g)) AS lon, ST_Y(ST_Centroid(g)) AS lat, "
        f"ST_XMin(g) AS w, ST_YMin(g) AS s, ST_XMax(g) AS e, ST_YMax(g) AS n "
        f"FROM (SELECT *, ST_Transform(geometry, 4326) AS g FROM change_features WHERE run_id = :r AND confidence >= :c "
        f"AND (CAST(:d AS boolean) IS NULL OR in_disturbed_area = :d) AND (CAST(:dir AS text) IS NULL OR change_direction = :dir) "
        f"AND (CAST(:cause AS text) IS NULL OR likely_cause = :cause)) t ORDER BY {order} DESC, id LIMIT :n",
        r=run, c=min_confidence, d=in_disturbed_area, dir=change_direction, cause=likely_cause, n=n,
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
    """Everything known about one change polygon: attributes, spectral-index context (NDVI/NBR/MNDWI before and after,
    cloud/water shares, aerosol) and a plain-language reading with caveats."""
    rows = db.query(
        "SELECT c.*, f.id, f.run_id, f.method, round((f.area_m2 / 1e4)::numeric, 2) AS area_ha, round(f.confidence::numeric, 3) AS confidence, "
        "f.change_direction, f.in_disturbed_area, f.d_brightness, f.d_red, f.likely_cause FROM change_features f "
        "LEFT JOIN change_context c USING (id) WHERE f.id = :i",
        i=int(id),
    )  # fmt: skip
    if not rows:
        raise ValueError(f"No change polygon with id {id}.")
    r = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in rows[0].items() if v is not None}
    cause = r.get("likely_cause")
    reading = [
        f"A {r['area_ha']} ha polygon, {r.get('change_direction', 'unclassified')}: {DIRECTION_MEANING.get(r.get('change_direction'), '')}."
    ]
    reading.append(f"It lies {'inside' if r.get('in_disturbed_area') else 'outside'} the heuristic mine footprint.")
    reading.append(
        f"Likely cause '{cause}': {CAUSE_MEANING.get(cause, '')}."
        if cause
        else "No cause rule output for this run (enrichment only covers some runs)."
    )
    if r.get("seam_mix", 0) > 0.05:
        reading.append("It overlaps a mosaic seam (different source scenes), so part of the signal may be a scene difference.")
    reading.append("Confidence is a magnitude score of the spectral change, not a probability of being correct.")
    return {"attributes": r, "reading": " ".join(reading)}


@tool
@safe
def timeline_stats(metric: Literal[*METRICS], start: str | None = None, end: str | None = None) -> str:  # type: ignore[valid-type]
    """Monthly time series of one metric from the 2023-2025 timeline (YYYY-MM bounds, inclusive). Metrics are AOI/footprint
    means or hectares per month; months without clear imagery have status other than ok and null values.
    Returns the series plus first/last/min/max/delta."""
    if metric not in METRICS:
        raise ValueError(f"metric must be one of {METRICS}")
    rows = db.query(
        f"SELECT month, status, {metric} AS value FROM timeline_months WHERE (CAST(:s AS text) IS NULL OR month >= :s) "
        f"AND (CAST(:e AS text) IS NULL OR month <= :e) ORDER BY month",
        s=start, e=end,
    )  # fmt: skip
    if not rows:
        return {"available": False, "note": "No timeline data for this range (the table may be empty)."}
    vals = [r for r in rows if r["value"] is not None]
    summary = {}
    if vals:
        lo, hi = min(vals, key=lambda r: r["value"]), max(vals, key=lambda r: r["value"])
        summary = {
            "first": vals[0],
            "last": vals[-1],
            "min": lo,
            "max": hi,
            "delta_first_to_last": round(vals[-1]["value"] - vals[0]["value"], 4),
        }
    return {
        "available": True,
        "metric": metric,
        "n_months": len(rows),
        "n_with_values": len(vals),
        **summary,
        "series": rows[:60],
    }


@tool
@safe
def readonly_sql(query: str) -> str:
    """Run one read-only PostgreSQL/PostGIS SELECT (max 200 rows, 5 s). Tables: change_runs(run_id, method, date_before,
    date_after, params, summary), change_features(id, run_id, area_m2, perimeter_m, confidence, change_direction,
    in_disturbed_area, d_red, d_brightness, likely_cause, geometry in EPSG:32735 metres), change_context(id -> change_features.id,
    ndvi/nbr/mndwi_before/after, d_ndvi, d_nbr, d_mndwi, scl_*_before/after, seam_mix), timeline_months(month, status, ...),
    view change_features_wgs84 (geometry in lon/lat). Use for questions the other tools cannot answer. Do not select raw geometry."""
    sql = guard_sql(query)
    with db.reader().connect() as conn:
        res = conn.exec_driver_sql(sql.replace("%", "%%"))
        cols = list(res.keys())
        rows = [[str(v)[:120] if isinstance(v, str) else v for v in r] for r in res.fetchmany(MAX_ROWS)]
    return {"sql": sql, "columns": cols, "rows": rows}


@tool
@safe
def map_action(
    type: Literal["fly_to", "highlight", "show_run", "set_month"],
    bbox: list[float] | None = None,
    run_id: str | None = None,
    ids: list[int] | None = None,
    month: str | None = None,
) -> str:
    """Control the map the user is looking at. fly_to: bbox=[west, south, east, north] lon/lat. highlight: run_id + polygon ids
    (from top_changes). show_run: switch the displayed run. set_month: month 'YYYY-MM' on the timeline. Only act on ids and
    runs returned by other tools."""
    if run_id is not None:
        run_id = resolve_run(run_id)
    if type == "highlight":
        found = db.query(
            "SELECT id FROM change_features WHERE run_id = :r AND id = ANY(:ids)", r=run_id, ids=[int(i) for i in ids or []]
        )
        ids = [r["id"] for r in found]
    return {"ok": True, "action": collector.current().add_action(type, bbox, run_id, ids, month)}


class Series(BaseModel):
    name: str
    y: list[float | None]


@tool
@safe
def make_chart(type: Literal["bar", "line"], title: str, x: list[str], series: list[Series]) -> str:
    """Show a chart next to the answer. x are category or month labels; each series has a name and one y value per x. Use
    numbers taken from tool results only. Units go in the title or series name."""
    return {"ok": True, "chart": collector.current().add_chart(type, title, x, [s.model_dump() for s in series])["title"]}


@tool
@safe
def make_table(title: str, columns: list[str], rows: list[list[str | float | None]]) -> str:
    """Show a table next to the answer (max 100 rows). Every row needs one value per column; use numbers from tool results."""
    return {"ok": True, "table": collector.current().add_table(title, columns, rows)["title"]}


ALL_TOOLS = [
    list_runs,
    area_summary,
    top_changes,
    explain_change,
    timeline_stats,
    readonly_sql,
    map_action,
    make_chart,
    make_table,
]
