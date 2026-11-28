"""Fetch Sentinel-2 B02/B03/B04 for any date onto the assignment grid (true-pass check, scripts/real_dates.py).

The default comparison is the provided pair. Note how the provided rasters differ from what this module builds: each provided
"date" is a *multi-day* mosaic (the 2023-08-12 raster is made of scenes from 2023-07-01 and 2023-07-04; see
``s2cd provenance``), whereas a scene fetched here is a *single-day* mosaic of that day's items only. To fetch a date:
1. list dates with clear imagery over the AOI (STAC metadata: tile cloud cover + AOI coverage),
2. read B02/B03/B04 for every item of that one day onto the assignment's 10 m grid and mosaic them
   (lowest-cloud item first),
3. write a folder ``sentinel2_YYYYMMDD/B0{2,3,4}.tif`` in the provided layout, so
   ``pipeline.run`` treats it exactly like the inputs (without provenance zones: the data-driven stripe detection applies).
Only the three visible bands are read, by design.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

from . import provenance as pv
from . import rgb
from .io import Grid, load_scene, write_raster
from .radiometry import to_reflectance

REFERENCE = Path("inputs/data/sentinel2_20230812")  # defines the grid every scene is read onto
AOI = Path("inputs/aoi.geojson")
OUT_ROOT = Path("data/scenes")
CACHE = Path("data/cache/stac")


@dataclass(frozen=True)
class SceneRules:
    max_tile_cloud: float = 10.0  # eo:cloud_cover of each item (%), tile-level metadata
    min_aoi_coverage: float = 0.95  # share of the AOI covered by the date's items together
    max_aoi_cloud: float = 0.05  # share of AOI pixels flagged by rgb.cloud_mask after fetching


_TO_UTM = Transformer.from_crs(4326, 32735, always_xy=True).transform  # coverage ratios are computed in metres, not degrees


def _aoi_geometry() -> dict:
    import json

    fc = json.loads(AOI.read_text())
    return fc["features"][0]["geometry"]


def _dedupe(items: list) -> list:
    """One item per datatake and tile: keep the newest processing baseline (pv.search sorts it first)."""
    seen, out = set(), []
    for it in items:
        # the datatake id ends in the baseline ("..._N05.09"), so strip it or a reprocessed copy counts as another datatake
        key = (it.properties.get("s2:datatake_id", "").rsplit("_N", 1)[0], it.properties.get("s2:mgrs_tile"))
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def available_dates(start: date, end: date, rules: SceneRules = SceneRules()) -> list[dict]:
    """Dates whose items cover the AOI with little cloud (metadata only; nothing is downloaded)."""
    aoi = transform(_TO_UTM, shape(_aoi_geometry()))
    items = [
        i
        for i in _dedupe(pv.search(pv.open_catalog(), _aoi_geometry(), start, end))
        if i.properties.get("eo:cloud_cover", 100) <= rules.max_tile_cloud
    ]
    by_day: dict[str, list] = {}
    for it in items:
        by_day.setdefault(it.datetime.date().isoformat(), []).append(it)
    out = []
    for day, its in sorted(by_day.items()):
        cover = transform(_TO_UTM, shapely.union_all([shape(i.geometry) for i in its])).intersection(aoi).area / aoi.area
        if cover >= rules.min_aoi_coverage:
            out.append(
                {
                    "date": day,
                    "items": len(its),
                    "tile_cloud_max": max(i.properties["eo:cloud_cover"] for i in its),
                    "aoi_coverage": round(cover, 3),
                }
            )
    return out


def fetch_scene(day: date, out_root: Path = OUT_ROOT, rules: SceneRules = SceneRules()) -> Path:
    """Write ``out_root/sentinel2_YYYYMMDD/B0x.tif`` for ``day`` and return the folder. Cached: re-runs are free."""
    folder = Path(out_root) / f"sentinel2_{day:%Y%m%d}"
    if all((folder / f"{b}.tif").exists() for b in pv.BANDS):
        return folder
    ref = load_scene(REFERENCE)
    grid: Grid = ref.grid
    items = _dedupe(pv.search(pv.open_catalog(), _aoi_geometry(), day, day))
    if not items:
        raise ValueError(f"No Sentinel-2 L2A item over the AOI on {day}")
    items.sort(key=lambda i: i.properties.get("eo:cloud_cover", 100))  # clearest first wins in the mosaic
    fetch = pv.Fetcher(grid, CACHE)
    stack = np.zeros((3, grid.height, grid.width), np.uint16)
    for it in items:
        layers = np.stack([fetch.get(it, b) for b in pv.BANDS])
        empty = stack[0] == 0
        stack[:, empty] = layers[:, empty]
    valid = np.all(stack > 0, axis=0)
    if valid.mean() < rules.min_aoi_coverage * ref.valid.mean():
        raise ValueError(f"{day}: imagery covers only {valid.mean():.0%} of the AOI")
    cloud = rgb.cloud_mask(to_reflectance(stack, valid))[valid].mean()
    if cloud > rules.max_aoi_cloud:
        raise ValueError(f"{day}: {cloud:.0%} of the AOI looks cloudy or shadowed in RGB; pick another date")
    for k, band in enumerate(pv.BANDS):
        write_raster(
            folder / f"{band}.tif",
            stack[k],
            grid,
            0,
            [band],
            {"SOURCE_ITEMS": ",".join(i.id for i in items)},
            overview_resampling=None,
        )
    return folder
