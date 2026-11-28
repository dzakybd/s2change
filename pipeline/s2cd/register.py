"""Facility register: named facilities with analyst-drawn outlines, and the change inside each.

1. ``load`` reads pipeline/config/facility_register.yaml: one polygon per facility, traced on very-high-resolution imagery
   (human-in-the-loop), with an id, name and class.
2. ``outlines`` makes them a clean partition: each polygon is clipped to the image grid and loses the area already claimed
   by the facilities listed before it, so neighbours share one edge and no area is counted twice.
3. ``change_by_facility`` intersects the change polygons with the outlines and reports per facility:
   changed ha, share of the facility, area of each change type and a one-line summary.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import rasterio.transform
import shapely
import yaml

from .io import Grid

REGISTER = Path(__file__).resolve().parents[2] / "pipeline" / "config" / "facility_register.yaml"
MINE_CLASSES = ("pit_dump_complex", "waste_dump", "tailings", "plant")  # the mine footprint; camp, town and dam are context


def load(path: Path = REGISTER) -> list[dict]:
    return yaml.safe_load(Path(path).read_text())["facilities"]


def footprint(facilities: gpd.GeoDataFrame):
    """Union of the outlines of the mine classes: the "mine footprint" that ``interpret`` measures distances to."""
    return shapely.union_all(facilities[facilities["class"].isin(MINE_CLASSES)].geometry)


def outlines(entries: list[dict], grid: Grid) -> gpd.GeoDataFrame:
    """One MultiPolygon per facility, in register order; earlier entries win where drawn polygons overlap."""
    west, south, east, north = rasterio.transform.array_bounds(grid.height, grid.width, grid.transform)
    extent = shapely.box(west, south, east, north)
    claimed = shapely.Polygon()
    rows = []
    for e in entries:
        drawn = shapely.make_valid(shapely.Polygon(e["polygon"]))
        geom = drawn.intersection(extent).difference(claimed)
        claimed = claimed.union(geom)
        parts = [g for g in getattr(geom, "geoms", [geom]) if g.geom_type == "Polygon" and not g.is_empty]
        rows.append(
            {
                "facility_id": e["id"],
                "name": e["name"],
                "class": e["class"],
                "cover": "drawn",  # how the outline was made (kept as a column for the database schema)
                "area_ha": round(sum(p.area for p in parts) / 1e4, 1),
                "geometry": shapely.MultiPolygon(parts),
            }
        )
    return gpd.GeoDataFrame(rows, crs=grid.crs)


def change_by_facility(facilities: gpd.GeoDataFrame, changes: gpd.GeoDataFrame) -> pd.DataFrame:
    """Changed area per facility (and per change_type if the polygons carry one)."""
    inter = gpd.overlay(
        changes[[c for c in ("change_type", "geometry") if c in changes]],
        facilities[["facility_id", "geometry"]],
        how="intersection",
        keep_geom_type=True,
    )
    inter["ha"] = inter.geometry.area / 1e4
    total = inter.groupby("facility_id").ha.sum()
    out = facilities[["facility_id", "name", "class", "area_ha"]].set_index("facility_id")
    out["changed_ha"] = total.reindex(out.index).fillna(0).round(1)
    out["changed_share"] = (out.changed_ha / out.area_ha.where(out.area_ha > 0)).round(3)
    if "change_type" in inter:
        top = inter.groupby(["facility_id", "change_type"]).ha.sum().round(1).reset_index().sort_values("ha", ascending=False)
        top = top[top.ha > 0]  # drop types that round to 0.0 ha
        by_fac = {f: dict(zip(g.change_type, g.ha, strict=True)) for f, g in top.groupby("facility_id")}
        out["change_types"] = [by_fac.get(f) for f in out.index]  # None where nothing changed
    out["summary"] = [
        f"{n}: {c / 100:.2f} km² changed ({s:.0%} of {a / 100:.1f} km²)"
        + (f"; mostly {max(t, key=t.get).replace('_', ' ')}" if isinstance(t, dict) and t else "")
        for n, c, s, a, t in zip(
            out.name,
            out.changed_ha,
            out.changed_share.fillna(0),
            out.area_ha,
            out.get("change_types", [None] * len(out)),
            strict=True,
        )
    ]
    return out.reset_index()
