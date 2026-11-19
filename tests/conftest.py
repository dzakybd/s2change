"""Shared fixtures and the ``slow`` marker (registered here, not in pyproject)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.crs import CRS

from s2cd.io import Grid

ROOT = Path(__file__).resolve().parents[1]
UTM35S = CRS.from_epsg(32735)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--runslow", action="store_true", default=False, help="also run tests marked @pytest.mark.slow")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: runs the full pipeline on the real inputs (skipped unless --runslow)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--runslow"):
        return
    skip = pytest.mark.skip(reason="slow test; pass --runslow to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(12345)


def make_grid(width: int = 40, height: int = 30, x0: float = 368000.0, y0: float = 8654000.0, res: float = 10.0) -> Grid:
    return Grid(crs=UTM35S, transform=Affine(res, 0, x0, 0, -res, y0), width=width, height=height)


@pytest.fixture
def grid() -> Grid:
    return make_grid()


def write_band(path: Path, data: np.ndarray, grid: Grid, nodata: float | None = 0) -> Path:
    """Write a single-band uint16 GeoTIFF on ``grid`` (no overviews, so it stays tiny)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype=str(data.dtype),
        crs=grid.crs,
        transform=grid.transform,
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return path
