"""Raster I/O: load Sentinel-2 band folders, validate grid consistency, stack, write GeoTIFFs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.enums import Resampling

BAND_NAMES = {"B02": "blue", "B03": "green", "B04": "red"}


@dataclass(frozen=True)
class Grid:
    """The pixel grid shared by every raster in the pipeline."""

    crs: CRS
    transform: Affine
    width: int
    height: int

    @property
    def pixel_area_m2(self) -> float:
        return abs(self.transform.a * self.transform.e)

    def profile(self, count: int, dtype: str, nodata: float | None) -> dict:
        return {
            "driver": "GTiff",
            "crs": self.crs,
            "transform": self.transform,
            "width": self.width,
            "height": self.height,
            "count": count,
            "dtype": dtype,
            "nodata": nodata,
            "tiled": True,
            "blockxsize": 256,
            "blockysize": 256,
            "compress": "deflate",
            "predictor": 3 if dtype.startswith("float") else 2,
        }


@dataclass
class Scene:
    """One acquisition: raw DN stack (bands, H, W) plus metadata."""

    name: str
    acquired: date
    bands: tuple[str, ...]
    dn: np.ndarray
    grid: Grid
    nodata: float

    @property
    def valid(self) -> np.ndarray:
        """Pixels with data in every band."""
        return np.all(self.dn != self.nodata, axis=0)


def parse_date(folder: Path) -> date:
    m = re.search(r"(\d{8})", folder.name)
    if not m:
        raise ValueError(f"Cannot parse an acquisition date (YYYYMMDD) from folder name {folder.name!r}")
    s = m.group(1)
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def _grid_of(ds: rasterio.DatasetReader) -> Grid:
    return Grid(crs=ds.crs, transform=ds.transform, width=ds.width, height=ds.height)


def check_consistency(grids: dict[str, Grid]) -> Grid:
    """Raise if any raster disagrees with the first on CRS, transform or shape; return the shared grid."""
    items = list(grids.items())
    ref_name, ref = items[0]
    problems = []
    for name, g in items[1:]:
        if g.crs != ref.crs:
            problems.append(f"{name}: CRS {g.crs} != {ref.crs} ({ref_name})")
        if (g.width, g.height) != (ref.width, ref.height):
            problems.append(f"{name}: shape {g.height}x{g.width} != {ref.height}x{ref.width} ({ref_name})")
        if not g.transform.almost_equals(ref.transform, precision=1e-6):
            problems.append(f"{name}: transform {tuple(g.transform)[:6]} != {tuple(ref.transform)[:6]}")
    if problems:
        raise ValueError("Rasters are not on a common grid:\n  " + "\n  ".join(problems))
    return ref


def load_scene(folder: Path, bands: tuple[str, ...] = ("B02", "B03", "B04")) -> Scene:
    """Read single-band GeoTIFFs ``<folder>/<band>.tif`` into one (bands, H, W) array."""
    folder = Path(folder)
    arrays, grids, nodatas = [], {}, set()
    for b in bands:
        path = folder / f"{b}.tif"
        if not path.exists():
            raise FileNotFoundError(path)
        with rasterio.open(path) as ds:
            if ds.count != 1:
                raise ValueError(f"{path} has {ds.count} bands, expected 1")
            arrays.append(ds.read(1))
            grids[str(path)] = _grid_of(ds)
            nodatas.add(ds.nodata if ds.nodata is not None else 0)
    if len(nodatas) != 1:
        raise ValueError(f"{folder}: bands disagree on nodata {nodatas}")
    return Scene(
        name=folder.name,
        acquired=parse_date(folder),
        bands=bands,
        dn=np.stack(arrays),
        grid=check_consistency(grids),
        nodata=float(nodatas.pop()),
    )


def write_raster(
    path: Path,
    data: np.ndarray,
    grid: Grid,
    nodata: float | None = None,
    descriptions: list[str] | None = None,
    tags: dict | None = None,
    overviews: bool = True,
) -> Path:
    """Write a tiled, deflate-compressed GeoTIFF with internal overviews. ``data`` is (H, W) or (B, H, W)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = data[None] if data.ndim == 2 else data
    with rasterio.open(path, "w", **grid.profile(arr.shape[0], str(arr.dtype), nodata)) as dst:
        dst.write(arr)
        for i, d in enumerate(descriptions or [], start=1):
            dst.set_band_description(i, d)
        if tags:
            dst.update_tags(**{k: str(v) for k, v in tags.items()})
        if overviews:
            categorical = arr.dtype == np.uint8 and arr.max(initial=0) <= 1
            dst.build_overviews([2, 4, 8], Resampling.nearest if categorical else Resampling.average)
    return path
