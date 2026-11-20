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
| tailings_dam_raise             | (mining)              | brighter + redder, overlapping the tailings facility but mostly at  |
|                                |                       | its edge (fresh fill on the embankment)                             |
| tailings_deposition            | (mining)              | inside the tailings storage facility                                |
| infrastructure_or_settlement   | urban expansion       | inside the plant, camp or settlement                                |
| mine_expansion_clearing        | land clearing         | vegetation -> bare, touching the mine footprint                     |
| land_clearing                  | land clearing         | vegetation -> bare, away from the mine                              |
| seasonal_vegetation_or_burn    | seasonal / vegetation | greenness or darkening change without becoming bare, outside mine   |
| unexplained                    | -                     | none of the above                                                   |

Every label carries a technical `explanation` (the rule and its numbers) and a `brief_category` so the report can be read
against the brief's examples. ``describe`` then turns each polygon into plain language for business readers: a
`headline` ("Waste rock dumped"), a `location` ("on the east side of the South pit complex", "1.8 km north-east of the
Process plant"), the `evidence` in words, `what_to_check`, and one `description` sentence combining them.

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
    "tailings_dam_raise": "mining activity",
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
    dam_edge_share: float = 0.1  # least share inside the tailings facility for edge fill to count as its embankment


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
        elif c.facility_class == "tailings" and c.facility_share >= r.dam_edge_share and brighter and d_red >= r.redder:
            t = "tailings_dam_raise"
            why = f"brighter and redder (redness {d_red:+.2f}), {c.facility_share:.0%} inside {c.facility_id}, rest at its edge"
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


# ---- Plain language ------------------------------------------------------------------------------------------------

#: change_type -> (headline, what to check). Written for a reader who does not know remote sensing.
PLAIN = {
    "tailings_dam_raise": ("Tailings dam wall likely raised", "Check the dam-raise plan and inspect the embankment."),
    "waste_dumping": ("Waste rock dumped", "Check that the dump is inside the approved mine plan."),
    "pit_excavation": ("Pit worked or deepened", "Check that the digging matches the mine plan."),
    "tailings_deposition": ("Tailings deposited", "Routine operation: watch the pond and beach extent month to month."),
    "water_level_change": ("Water level changed", "Check dam and pond levels and discharge records."),
    "infrastructure_or_settlement": ("Buildings, roads or yards changed", "Check whether new construction is permitted."),
    "mine_expansion_clearing": ("Vegetation cleared at the mine edge", "Check the cleared land is inside the licence."),
    "land_clearing": ("Vegetation cleared", "Find out who cleared it: farming, charcoal or mining."),
    "seasonal_vegetation_or_burn": ("Vegetation changed (season or fire)", "Usually seasonal; check fire records if large."),
    "noise_or_artifact": ("Probably not a real change", "No action: it follows image stitching, haze or shadow."),
    "unexplained": ("Unclear change", "Compare the before and after images."),
}
_COMPASS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


def _compass(dx: float, dy: float) -> str:
    return _COMPASS[int(((np.degrees(np.arctan2(dx, dy)) + 360 + 22.5) % 360) // 45)]


def _distance(m: float) -> str:
    return f"{m / 1000:.1f} km" if m >= 1000 else f"{round(m, -1):.0f} m"


def plain_area(m2: float) -> str:
    return f"{m2 / 1e6:.2f} km²" if m2 >= 1e4 else f"{round(m2, -2):,.0f} m²"


def _location(geom, fac_id, outlines: gpd.GeoDataFrame | None) -> str:
    """'on the east side of the X' inside (or touching) a facility, else 'N km <direction> of the nearest facility'."""
    if outlines is None or not len(outlines):
        return ""
    names = dict(zip(outlines.facility_id, outlines["name"], strict=True))
    c = geom.centroid
    if fac_id in names:
        f = outlines.loc[outlines.facility_id == fac_id].geometry.iloc[0]
        x0, y0, x1, y1 = f.bounds
        fx, fy = (c.x - x0) / max(x1 - x0, 1), (c.y - y0) / max(y1 - y0, 1)
        ns = "north" if fy > 2 / 3 else "south" if fy < 1 / 3 else ""
        ew = "east" if fx > 2 / 3 else "west" if fx < 1 / 3 else ""
        part = f"{ns}-{ew}" if ns and ew else ns or ew
        return f"in the {part} of the {names[fac_id]}" if part else f"in the middle of the {names[fac_id]}"
    dist = outlines.geometry.distance(geom)
    k = int(dist.values.argmin())
    near, d = outlines.iloc[k], float(dist.iloc[k])
    if d < 100:
        return f"next to the {near['name']}"
    nc = near.geometry.centroid
    return f"{_distance(d)} {_compass(c.x - nc.x, c.y - nc.y)} of the {near['name']}"


def _evidence(e, change_type: str) -> str:
    """The RGB evidence in everyday words. Water is mentioned only for water-level changes: the RGB water test also fires on
    dark wet vegetation, which ``label`` already guards against."""
    bits = []
    if change_type == "water_level_change":
        bits.append(f"{e.water_flip:.0%} of it switched between water and dry land")
    if e.gcc_before >= rgb.RgbRules().bare_gcc_max and e.bare_after >= 0.5:
        bits.append("green vegetation became bare ground")
    elif e.gcc_after - e.gcc_before >= 0.015:
        bits.append("it turned greener")
    elif e.gcc_after - e.gcc_before <= -0.015:
        bits.append("it turned less green")
    db = e.bright_after - e.bright_before
    if abs(db) >= 0.01:
        bits.append("the ground got brighter" if db > 0 else "the ground got darker")
    if e.red_after - e.red_before >= 0.05:
        bits.append("more rust-coloured, typical of freshly exposed rock")
    elif e.red_after - e.red_before <= -0.05:
        bits.append("less rust-coloured")
    return "; ".join(bits) if bits else "a mixed colour change"


def describe(polys: gpd.GeoDataFrame, labels: pd.DataFrame, ev: pd.DataFrame, outlines: gpd.GeoDataFrame | None) -> pd.DataFrame:
    """Plain-language fields per polygon: headline, location, evidence, what_to_check and one description sentence."""
    rows = []
    for i, p in polys.iterrows():
        t = labels.loc[i, "change_type"]
        headline, check = PLAIN.get(t, PLAIN["unexplained"])
        where = _location(p.geometry, labels.loc[i, "facility_id"], outlines)
        evidence = _evidence(ev.loc[i], t)
        sentence = f"{headline}, {plain_area(p.geometry.area)}{' ' + where if where else ''}: {evidence}. {check}"
        rows.append(
            {"headline": headline, "location": where, "evidence": evidence, "what_to_check": check, "description": sentence}
        )
    return pd.DataFrame(rows, index=polys.index)
