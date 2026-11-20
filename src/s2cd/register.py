"""Facility register: named facilities with data-driven outlines, and the change inside each.

1. ``load`` reads configs/facility_register.yaml: analyst-defined regions (human-in-the-loop),
   each with an id, name, class and the cover type that defines it (bare / water / any).
2. ``outlines`` turns each region into the facility's actual footprint from the imagery:
   pixels of that cover type inside the box, cleaned (closing, holes < 5 ha filled, parts < 0.5 ha removed).
   "bare" means bare on BOTH dates (rgb.bare_mask), so seasonal grass is not mine surface.
   Both functions take the before reflectance and the *normalized* after reflectance. Water must be water on *both* dates
   (persistent water): the RGB water rule also fires on dark, hazy or shaded ground, which on a single date (more than twice
   as much on the normalized T2 as on T1) swells the water outlines into the surrounding woodland. Shore fluctuations
   between the dates are still labelled water_level_change by ``interpret``, which looks at the per-pixel water flip.
3. ``change_by_facility`` intersects the change polygons with the outlines and reports per facility:
   changed ha, share of the facility, area of each cover type before/after, and a one-line summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio.features
import shapely
import yaml
from scipy import ndimage as ndi
from skimage import morphology

from . import rgb
from .io import Grid

REGISTER = Path(__file__).resolve().parents[2] / "configs" / "facility_register.yaml"
MINE_CLASSES = ("pit_dump_complex", "waste_dump", "tailings", "plant")  # the mine footprint; camp, town and dam are context


@dataclass(frozen=True)
class RegisterRules:
    closing_px: int = 2
    min_part_ha: float = 0.5
    max_hole_ha: float = 5.0  # fill gaps inside a facility, but keep real islands (e.g. woodland inside the TSF ring)
    simplify_m: float = 5.0  # half a pixel: removes staircase vertices, keeps the shape


def load(path: Path = REGISTER) -> list[dict]:
    return yaml.safe_load(Path(path).read_text())["facilities"]


def footprint(facilities: gpd.GeoDataFrame):
    """Union of the outlines of the mine classes: the "mine footprint" that ``interpret`` measures distances to."""
    return shapely.union_all(facilities[facilities["class"].isin(MINE_CLASSES)].geometry)


def cover_mask(kind: str, before: np.ndarray, after: np.ndarray, valid: np.ndarray) -> np.ndarray:
    bare = rgb.bare_mask(before) & rgb.bare_mask(after)
    water = rgb.water_mask(before) & rgb.water_mask(after)
    return valid & {"bare": bare, "water": water, "any": bare | water}[kind]


def _box_mask(box: list[float], grid: Grid) -> np.ndarray:
    return rasterio.features.geometry_mask([shapely.box(*box)], (grid.height, grid.width), grid.transform, invert=True)


def outlines(
    entries: list[dict], before: np.ndarray, after: np.ndarray, valid: np.ndarray, grid: Grid, r: RegisterRules = RegisterRules()
) -> gpd.GeoDataFrame:
    """One (Multi)Polygon per facility: its cover type inside its box, cleaned."""
    px_ha = grid.pixel_area_m2 / 1e4
    min_px = int(round(r.min_part_ha / px_ha))
    rows = []
    for e in entries:
        m = cover_mask(e["cover"], before, after, valid) & _box_mask(e["box"], grid)
        m = ndi.binary_closing(m, morphology.disk(r.closing_px))
        m = morphology.remove_small_holes(m, max_size=int(round(r.max_hole_ha / px_ha)))
        m = morphology.remove_small_objects(m, max_size=min_px - 1)
        parts = [
            shapely.geometry.shape(g)
            for g, v in rasterio.features.shapes(m.astype(np.uint8), mask=m, transform=grid.transform)
            if v
        ]
        geom = shapely.union_all(parts).simplify(r.simplify_m) if parts else shapely.MultiPolygon()
        rows.append(
            {
                "facility_id": e["id"],
                "name": e["name"],
                "class": e["class"],
                "cover": e["cover"],
                "area_ha": round(m.sum() * px_ha, 1),
                "geometry": geom,
            }
        )
    gdf = gpd.GeoDataFrame(rows, crs=grid.crs)
    gdf["geometry"] = gdf.geometry.apply(
        lambda g: g if g.geom_type == "MultiPolygon" else shapely.MultiPolygon([g]) if not g.is_empty else g
    )
    return gdf


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
        out["change_types"] = (
            top.groupby("facility_id")
            .apply(lambda g: dict(zip(g.change_type, g.ha, strict=True)), include_groups=False)
            .reindex(out.index)
        )
    out["summary"] = [
        f"{n}: {c:.0f} ha changed ({s:.0%} of {a:.0f} ha)"
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
