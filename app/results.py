"""Read-side queries over the published results: shared by the API endpoints and the agent tools.

Everything here goes through the read-only role (``app.db``) and is deterministic: no LLM is involved.
"""

from __future__ import annotations

import json
from datetime import date

from shapely.geometry import shape

from app import db

# The default comparison: robust CVA on the provided pair. Nominal dates, see NOMINAL_DATES_NOTE.
DEFAULT_RUN = {"method": "robust_cva", "date_before": date(2023, 8, 12), "date_after": date(2023, 9, 2)}
NOMINAL_DATES_NOTE = (
    "The two dates are nominal mosaic dates. The 2023-08-12 raster is built from scenes of 2023-07-01/07-04 and the "
    "2023-09-02 raster from scenes of 2023-08-25/09-02, so the effective gap is 52-60 days, not 21."
)
MAX_AOI_VERTICES = 2000


def resolve_run(run_id: str | None = None) -> str:
    """The given run (exact id, or a unique substring) or, with ``None``, the default run. Raises ValueError otherwise."""
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


def change_type_summary(run_id: str | None = None) -> dict:
    """Hectares and polygon count per change_type and brief_category (only robust_cva polygons are interpreted)."""
    run = resolve_run(run_id)
    rows = db.query(
        "SELECT change_type, brief_category, count(*)::int AS n_polygons, "
        "round((sum(area_m2) / 1e4)::numeric, 2)::float AS area_ha FROM change_features WHERE run_id = :r "
        "GROUP BY 1, 2 ORDER BY area_ha DESC",
        r=run,
    )
    total = round(sum(r["area_ha"] for r in rows), 2)
    out = {"run_id": run, "total_ha": total, "change_types": rows}
    if all(r["change_type"] is None for r in rows):
        out["note"] = "This run carries no change_type: only the robust_cva run is interpreted."
    return out


def facility_rows(facility_id: str | None = None) -> list[dict]:
    """The facility register with the change inside each facility (default comparison), largest change first."""
    return db.query(
        "SELECT f.facility_id, f.name, f.class, f.cover, round(f.area_ha::numeric, 1)::float AS area_ha, "
        "round(c.changed_ha::numeric, 1)::float AS changed_ha, "
        "round(c.changed_share::numeric, 3)::float AS changed_share, c.change_types, c.summary "
        "FROM facilities f LEFT JOIN facility_changes c USING (facility_id) "
        "WHERE (CAST(:f AS text) IS NULL OR f.facility_id = :f) ORDER BY c.changed_ha DESC NULLS LAST, f.facility_id",
        f=facility_id,
    )


def facilities_geojson() -> dict:
    """The register as GeoJSON in EPSG:4326 with change statistics as properties."""
    geoms = {
        r["facility_id"]: json.loads(r["geom"])
        for r in db.query("SELECT facility_id, ST_AsGeoJSON(ST_Transform(geometry, 4326), 6) AS geom FROM facilities")
    }
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": r["facility_id"], "geometry": geoms[r["facility_id"]], "properties": r}
            for r in facility_rows()
        ],
    }


def _aoi_polygon(geometry: dict):
    """Validate a user-drawn GeoJSON polygon (EPSG:4326) and return the shapely geometry."""
    try:
        poly = shape(geometry)
    except Exception:
        raise ValueError("geometry must be a GeoJSON Polygon or MultiPolygon.") from None
    if poly.geom_type not in ("Polygon", "MultiPolygon") or poly.is_empty:
        raise ValueError("geometry must be a non-empty GeoJSON Polygon or MultiPolygon.")
    xmin, ymin, xmax, ymax = poly.bounds
    if xmin < -180 or xmax > 180 or ymin < -90 or ymax > 90:
        raise ValueError("Coordinates must be lon/lat degrees (EPSG:4326).")
    if not poly.is_valid:
        raise ValueError("The polygon is invalid (for example self-intersecting).")
    if sum(len(g.exterior.coords) for g in getattr(poly, "geoms", [poly])) > MAX_AOI_VERTICES:
        raise ValueError(f"The polygon has too many vertices (max {MAX_AOI_VERTICES}).")
    return poly


def aoi_stats(geometry: dict, run_id: str | None = None) -> dict:
    """Change inside a user-drawn polygon: its area, the changed hectares (clipped to it) and the split by type and facility."""
    poly = _aoi_polygon(geometry)
    run = resolve_run(run_id)
    aoi = "ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(:g), 4326), 32735)"
    params = {"g": json.dumps(poly.__geo_interface__), "r": run}
    area_m2 = db.query(f"SELECT ST_Area({aoi}) AS a", **params)[0]["a"]
    clipped = db.query(
        f"SELECT change_type, facility_id, ST_Area(ST_Intersection(geometry, {aoi})) AS a FROM change_features "
        f"WHERE run_id = :r AND ST_Intersects(geometry, {aoi})",
        **params,
    )
    by_type: dict[str, float] = {}
    by_facility: dict[str, float] = {}
    for r in clipped:
        by_type[r["change_type"] or "unclassified"] = by_type.get(r["change_type"] or "unclassified", 0) + r["a"]
        if r["facility_id"]:
            by_facility[r["facility_id"]] = by_facility.get(r["facility_id"], 0) + r["a"]
    ha = lambda m2: round(m2 / 1e4, 2)  # noqa: E731
    changed = sum(r["a"] for r in clipped)
    return {
        "run_id": run,
        "area_ha": ha(area_m2),
        "changed_ha": ha(changed),
        "changed_share": round(changed / area_m2, 4) if area_m2 else 0.0,
        "n_polygons": len(clipped),
        "by_change_type": {k: ha(v) for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])},
        "by_facility": {k: ha(v) for k, v in sorted(by_facility.items(), key=lambda kv: -kv[1])},
    }


# ---- Monitoring brief -------------------------------------------------------------------------------------------------


def _table(header: list[str], rows: list[list]) -> list[str]:
    return [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
        *("| " + " | ".join(str(c) for c in r) + " |" for r in rows),
    ]


def _label(change_type: str | None) -> str:
    return (change_type or "not interpreted").replace("_", " ")


def _main_type(change_types: dict | None) -> str:
    return _label(max(change_types, key=change_types.get)) if change_types else "-"


def brief(run_id: str | None = None) -> str:
    """A deterministic markdown monitoring brief: headline, top facilities, change types, caveats, follow-up list."""
    run = resolve_run(run_id)
    info = db.query("SELECT method, date_before, date_after FROM change_runs WHERE run_id = :r", r=run)[0]
    types = change_type_summary(run)
    total, n = types["total_ha"], sum(t["n_polygons"] for t in types["change_types"])
    register = facility_rows()
    facilities = [f for f in register if f["changed_ha"]]
    in_facilities = sum(f["changed_ha"] for f in facilities)
    interpreted = [t for t in types["change_types"] if t["change_type"] not in (None, "noise_or_artifact")]
    top = interpreted[0] if interpreted else None

    lines = [f"# Monitoring brief: {info['date_before']} to {info['date_after']} (nominal dates)", ""]
    lines += ["## Headline", ""]
    headline = f"**{total:,.0f} ha changed across {n} polygons** ({info['method']})."
    if top:
        headline += f" The largest interpreted signal is {_label(top['change_type'])} ({top['area_ha']:,.0f} ha)."
    if facilities and total:
        headline += (
            f" {in_facilities / total:.0%} of the changed area lies inside the facility register "
            f"({len(facilities)} of {len(register)} facilities show change)."
        )
    lines += [headline, ""]

    lines += ["## Top facilities", ""]
    if facilities:
        rows = [
            [f["name"], f["class"], f"{f['changed_ha']:,.1f}", f"{f['changed_share']:.1%}", _main_type(f["change_types"])]
            for f in facilities[:5]
        ]
        lines += _table(["Facility", "Class", "Changed (ha)", "Share of facility", "Main change"], rows)
    else:
        lines += ["No facility register is loaded for this database."]
    lines += ["", "## Change types", ""]
    rows = [
        [_label(t["change_type"]), t["brief_category"] or "-", t["n_polygons"], f"{t['area_ha']:,.1f}"]
        for t in types["change_types"]
    ]
    lines += _table(["Change type", "Brief category", "Polygons", "Hectares"], rows)

    lines += ["", "## Caveats", ""]
    lines += [
        f"- {NOMINAL_DATES_NOTE}",
        "- RGB only (B02, B03, B04), two dates: vegetation health, burn severity and mineral indices are not available.",
        "- Confidence is a 0-1 magnitude score of the spectral change, not a probability or an accuracy.",
        "- change_type is a rule-based hypothesis from RGB evidence and the facility register, not ground truth.",
        "- Mosaic seams, haze and shadows can mimic change; polygons labelled noise or artifact are probably not land change.",
    ]

    lines += ["", "## Follow-up list", ""]
    lines += [f"- {f}" for f in _follow_ups(run)] or ["- Nothing stands out for review."]
    return "\n".join(lines) + "\n"


FOLLOW_UP_GROUPS = [  # (SQL condition, what to do about the polygons that match it)
    ("change_type = 'unexplained'", "no rule matched: review them by eye"),
    ("touches_aoi_edge", "touch the AOI edge and may continue outside it, so their size is a lower bound"),
    ("change_type = 'noise_or_artifact'", "are labelled noise or artifact: confirm they sit on mosaic seams"),
    (
        "change_type = 'water_level_change'",
        "are water-level changes: compare with the season, as ponds and dams follow the rains",
    ),
]


def _follow_ups(run: str) -> list[str]:
    """Concrete review items derived from the polygons: the largest change, then each group that needs a second look."""
    out = []
    largest = db.query(
        "SELECT id, change_type, area_m2 / 1e4 AS ha, ST_X(ST_Transform(ST_Centroid(geometry), 4326)) AS lon, "
        "ST_Y(ST_Transform(ST_Centroid(geometry), 4326)) AS lat FROM change_features WHERE run_id = :r "
        "ORDER BY area_m2 DESC LIMIT 1",
        r=run,
    )
    if largest:
        p = largest[0]
        out.append(
            f"Check the largest polygon #{p['id']} ({p['ha']:.1f} ha, {_label(p['change_type'])}) at "
            f"{p['lat']:.4f}, {p['lon']:.4f} on recent high-resolution imagery."
        )
    for where, advice in FOLLOW_UP_GROUPS:  # `where` is a constant from this module, never user input
        r = db.query(
            f"SELECT count(*)::int AS n, coalesce(sum(area_m2), 0) / 1e4 AS ha "
            f"FROM change_features WHERE run_id = :r AND {where}",
            r=run,
        )[0]
        if r["n"]:
            out.append(f"{r['n']} polygons ({r['ha']:.1f} ha) {advice}.")
    return out
