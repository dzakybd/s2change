"""Raster I/O: load Sentinel-2 band folders, validate grid consistency, stack, write GeoTIFFs / COGs."""

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
from rasterio.io import MemoryFile
from rasterio.shutil import copy

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
    grid = check_consistency(grids)  # before stacking, so a mismatch gets the descriptive error
    return Scene(
        name=folder.name,
        acquired=parse_date(folder),
        bands=bands,
        dn=np.stack(arrays),
        grid=grid,
        nodata=float(nodatas.pop()),
    )


def write_raster(
    path: Path,
    data: np.ndarray,
    grid: Grid,
    nodata: float | None = None,
    descriptions: list[str] | None = None,
    tags: dict | None = None,
    overview_resampling: Resampling | None = Resampling.average,
    scale_offset: tuple[float, float] | None = None,
) -> Path:
    """Write ``data`` ((H, W) or (B, H, W)) as a COG (a plain tiled GeoTIFF if ``overview_resampling`` is None).

    ``overview_resampling`` is explicit because the right choice depends on the content: pass ``Resampling.nearest`` for
    categorical rasters (binary masks, zone ids, source-scene ids), where averaging would invent classes, and
    ``Resampling.average`` for continuous ones. ``scale_offset = (scale, offset)`` is stored as the GDAL band scale/offset
    (physical = DN * scale + offset), so readers can recover reflectance from digital numbers.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = data[None] if data.ndim == 2 else data
    profile = grid.profile(arr.shape[0], str(arr.dtype), nodata)

    def fill(dst) -> None:
        dst.write(arr)
        for i, d in enumerate(descriptions or [], start=1):
            dst.set_band_description(i, d)
        if tags:
            dst.update_tags(**{k: str(v) for k, v in tags.items()})
        if scale_offset:
            dst.scales = (scale_offset[0],) * arr.shape[0]
            dst.offsets = (scale_offset[1],) * arr.shape[0]

    if overview_resampling is None:
        with rasterio.open(path, "w", **profile) as dst:
            fill(dst)
        return path
    # The COG driver only supports CreateCopy, so stage the raster in memory and copy it out.
    staging = {k: v for k, v in profile.items() if k not in ("tiled", "blockxsize", "blockysize", "compress", "predictor")}
    with MemoryFile() as mem, mem.open(**staging) as staged:
        fill(staged)
        copy(
            staged,
            path,
            driver="COG",
            COMPRESS="DEFLATE",
            PREDICTOR="YES",  # horizontal differencing for integers, floating-point predictor for floats
            BLOCKSIZE=256,
            OVERVIEW_RESAMPLING=overview_resampling.name.upper(),
        )
    return path
