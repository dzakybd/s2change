"""Label each change polygon with what it most likely represents, from RGB evidence + the facility register.

The brief suggests generic categories (urban expansion, vegetation change, land clearing, seasonal
variation, noise or artifacts). At a mine the useful categories are more specific, so they are
mapped as follows (first matching rule wins, in this order):

| change_type                    | brief category        | rule (RGB only + facility context)                                  |
|--------------------------------|-----------------------|---------------------------------------------------------------------|
| noise_or_artifact              | noise or artifacts    | mostly on a mosaic seam, or tiny and weak                           |
| water_level_change             | (mining: water mgmt)  | water present before xor after on >= 30 % of the polygon, which    |
|                                |                       | lies next to water that is present on both dates (a shore moved)    |
| pit_excavation                 | (mining)              | inside a pit/dump complex, not brighter-and-redder                  |
| waste_dumping                  | (mining)              | inside a waste dump; or brighter + redder in a pit/dump complex or  |
|                                |                       | at the footprint edge (fresh oxidized rock)                         |
| tailings_deposition            | (mining)              | inside the tailings storage facility                                |
| infrastructure_or_settlement   | urban expansion       | inside the plant, camp or settlement                                |
| mine_expansion_clearing        | land clearing         | vegetation -> bare, touching the mine footprint                     |
| land_clearing                  | land clearing         | vegetation -> bare, away from the mine                              |
| seasonal_vegetation_or_burn    | seasonal / vegetation | greenness or darkening change without becoming bare, outside mine   |
| unexplained                    | -                     | none of the above                                                   |

Every label carries a one-line plain-English `explanation` so the viewer and the analyst agent can
show *why*, and a `brief_category` so the report can be read against the brief's examples.

Inputs are the *before* reflectance and the *normalized* after reflectance (``pipeline`` passes ``refl_after_norm``), so
brightness, greenness, redness and the water flip compare the two dates on one radiometric scale instead of reading a
hazy, darker as-delivered T2 as water or vegetation loss. The mine footprint used for "at the mine edge" is the union of the
register outlines of the mine classes (``register.footprint``), not a brightness heuristic. These are RGB proxies and the
thresholds in ``rgb.RgbRules`` and ``InterpretRules`` are AOI-specific; the labels are hypotheses to review, not ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio.features
from scipy import ndimage as ndi
from skimage import morphology

from . import rgb

BRIEF_CATEGORY = {
    "noise_or_artifact": "noise or artifacts",
    "water_level_change": "water change (mining: water management)",
    "pit_excavation": "mining activity",
    "waste_dumping": "mining activity",
    "tailings_deposition": "mining activity",
    "infrastructure_or_settlement": "urban expansion",
    "mine_expansion_clearing": "land clearing",
    "land_clearing": "land clearing",
    "seasonal_vegetation_or_burn": "seasonal variation / vegetation change",
    "unexplained": "unexplained",
}
FACILITY_TO_TYPE = {  # register classes (configs/facility_register.yaml) -> change type
    "pit_dump_complex": "pit_excavation",  # refined below: brighter + redder inside it -> waste_dumping
    "waste_dump": "waste_dumping",
    "tailings": "tailings_deposition",
    "water_reservoir": "water_level_change",
    "plant": "infrastructure_or_settlement",
    "camp": "infrastructure_or_settlement",
    "settlement": "infrastructure_or_settlement",
}


@dataclass(frozen=True)
class InterpretRules:
    seam_share_artifact: float = 0.5  # share of the polygon within the seam zone (seam buffer + seam_margin_px)
    seam_margin_px: float = 3.0  # polygons are pixel-adjacent to the buffer they were cut by: look this far beyond it
    tiny_area_ha: float = 0.5
    weak_confidence: float = 0.1
    water_flip_share: float = 0.3
    water_reach_px: int = 5  # a flip counts as a moving shore only within this distance of water present on both dates
    water_context_share: float = 0.3  # share of the polygon that must lie within water_reach_px of such water
    facility_share: float = 0.5  # polygon share inside a facility to inherit its activity
    footprint_touch_m: float = 50.0
    greenness_drop: float = 0.015  # gcc drop that counts as vegetation loss
    redder: float = 0.05  # redness (B04/B02) increase that marks fresh oxidized material


def _zonal(labels: np.ndarray, n: int, values: np.ndarray) -> np.ndarray:
    m = labels > 0
    v = np.nan_to_num(values[m].astype(np.float64))
    counts = np.bincount(labels[m], minlength=n + 1)[1:]
    return np.bincount(labels[m], v, minlength=n + 1)[1:] / np.maximum(counts, 1)


def pixel_evidence(
    polys: gpd.GeoDataFrame,
    transform,
    shape,
    before: np.ndarray,
    after: np.ndarray,
    seam_dist: np.ndarray,
    seam_within_px: float,
    water_reach_px: int = InterpretRules.water_reach_px,
) -> pd.DataFrame:
    """Per-polygon RGB means before/after, water flip share and seam share (rasterized on the image grid).

    ``after`` is the normalized after-image. ``seam_within_px`` is the seam zone width: callers pass the seam buffer of the
    binary rule (``vectorize.BinaryRule.seam_buffer_px``) plus ``InterpretRules.seam_margin_px``, so there is one constant.
    """
    labels = rasterio.features.rasterize(
        ((g, i + 1) for i, g in enumerate(polys.geometry)), out_shape=shape, transform=transform, fill=0, dtype="int32"
    )
    n = len(polys)
    wb, wa = rgb.water_mask(before), rgb.water_mask(after)
    # The RGB water rule also fires on dark wet vegetation on one date; a real level change moves the shore of a water body
    # that is water on both dates, so flips elsewhere are not called water-level changes.
    near_water = ndi.binary_dilation(wb & wa, morphology.disk(water_reach_px))
    ev = pd.DataFrame(
        {
            "gcc_before": _zonal(labels, n, rgb.gcc(before)),
            "gcc_after": _zonal(labels, n, rgb.gcc(after)),
            "bright_before": _zonal(labels, n, rgb.brightness(before)),
            "bright_after": _zonal(labels, n, rgb.brightness(after)),
            "red_before": _zonal(labels, n, rgb.redness(before)),
            "red_after": _zonal(labels, n, rgb.redness(after)),
            "bare_after": _zonal(labels, n, rgb.bare_mask(after).astype(np.float32)),
            "water_flip": _zonal(labels, n, (wb ^ wa).astype(np.float32)),
            "near_water": _zonal(labels, n, near_water.astype(np.float32)),
            "seam_share": _zonal(labels, n, (seam_dist < seam_within_px).astype(np.float32)),
        },
        index=polys.index,
    )
    return ev


def facility_context(polys: gpd.GeoDataFrame, facilities: gpd.GeoDataFrame | None, footprint, r: InterpretRules) -> pd.DataFrame:
    """Dominant facility (id + class) covering each polygon, and whether it lies within ``footprint_touch_m`` of ``footprint``.

    ``footprint`` is a (Multi)Polygon, normally ``register.footprint(facilities)``.
    """
    out = pd.DataFrame(
        {"facility_id": None, "facility_class": None, "facility_share": 0.0, "near_footprint": False}, index=polys.index
    )
    if facilities is not None and len(facilities):
        inter = gpd.overlay(
            polys[["geometry"]].reset_index(names="pid"),
            facilities[["facility_id", "class", "geometry"]].to_crs(polys.crs),
            how="intersection",
            keep_geom_type=True,
        )
        if len(inter):
            inter["a"] = inter.geometry.area
            best = inter.sort_values("a", ascending=False).drop_duplicates("pid").set_index("pid")
            share = best["a"] / polys.geometry.area.reindex(best.index)
            out.loc[best.index, "facility_id"] = best["facility_id"]
            out.loc[best.index, "facility_class"] = best["class"]
            out.loc[best.index, "facility_share"] = share.values
    if footprint is not None:
        out["near_footprint"] = polys.geometry.distance(footprint) <= r.footprint_touch_m
    return out


def label(polys: gpd.GeoDataFrame, ev: pd.DataFrame, ctx: pd.DataFrame, r: InterpretRules = InterpretRules()) -> pd.DataFrame:
    """Apply the rule table (module docstring) and return change_type, brief_category, explanation per polygon."""
    rows = []
    for i, p in polys.iterrows():
        e, c = ev.loc[i], ctx.loc[i]
        area_ha = p.geometry.area / 1e4
        d_gcc, d_red = e.gcc_after - e.gcc_before, e.red_after - e.red_before
        brighter = e.bright_after > e.bright_before
        veg_to_bare = e.gcc_before >= rgb.RgbRules().bare_gcc_max and e.bare_after >= 0.5 and d_gcc <= -r.greenness_drop
        fac = c.facility_class if c.facility_share >= r.facility_share else None
        if e.seam_share >= r.seam_share_artifact or (area_ha < r.tiny_area_ha and p.get("confidence", 1) < r.weak_confidence):
            t, why = "noise_or_artifact", "lies mostly on a mosaic seam or is tiny and weak"
        elif e.water_flip >= r.water_flip_share and e.near_water >= r.water_context_share:
            t, why = "water_level_change", f"{e.water_flip:.0%} of it switched between water and land"
        elif fac in FACILITY_TO_TYPE:
            t = FACILITY_TO_TYPE[fac]
            if fac == "pit_dump_complex" and brighter and d_red >= r.redder:
                t = "waste_dumping"
            why = f"inside {c.facility_id} ({fac}); {'brighter' if brighter else 'darker'}, redness {d_red:+.2f}"
        elif c.near_footprint and brighter and d_red >= r.redder:
            t, why = "waste_dumping", f"brighter and redder (redness {d_red:+.2f}) at the mine edge: fresh oxidized material"
        elif veg_to_bare:
            t = "mine_expansion_clearing" if c.near_footprint else "land_clearing"
            why = f"vegetation became bare (greenness {d_gcc:+.3f}){' at the mine footprint edge' if c.near_footprint else ''}"
        elif not c.near_footprint and (abs(d_gcc) >= r.greenness_drop or not brighter):
            t, why = (
                "seasonal_vegetation_or_burn",
                f"greenness {d_gcc:+.3f}, {'darker' if not brighter else 'brighter'}; not bare, outside the mine",
            )
        else:
            t, why = "unexplained", "no rule matched"
        rows.append({"change_type": t, "brief_category": BRIEF_CATEGORY[t], "explanation": why, "facility_id": c.facility_id})
    return pd.DataFrame(rows, index=polys.index)
