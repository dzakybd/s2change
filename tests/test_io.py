from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from affine import Affine
from conftest import make_grid, write_band
from rasterio.crs import CRS

from s2cd.io import Grid, Scene, check_consistency, load_scene, parse_date, write_raster


def _scene_dir(tmp_path: Path, grid: Grid, name: str = "sentinel2_20230812", bands=("B02", "B03", "B04")) -> Path:
    folder = tmp_path / name
    for i, b in enumerate(bands, start=1):
        write_band(folder / f"{b}.tif", np.full((grid.height, grid.width), 1000 * i, dtype=np.uint16), grid)
    return folder


def test_parse_date():
    assert parse_date(Path("inputs/data/sentinel2_20230812")) == date(2023, 8, 12)
    assert parse_date(Path("S2_20240229_tile")) == date(2024, 2, 29)


@pytest.mark.parametrize("name", ["sentinel2", "scene_2023", "scene_20231345"])
def test_parse_date_rejects_bad_names(name):
    with pytest.raises(ValueError):
        parse_date(Path(name))


def test_load_scene_stacks_bands_in_requested_order(tmp_path, grid):
    folder = _scene_dir(tmp_path, grid)
    scene = load_scene(folder)
    assert scene.bands == ("B02", "B03", "B04")
    assert scene.dn.shape == (3, grid.height, grid.width)
    assert [int(scene.dn[i, 0, 0]) for i in range(3)] == [1000, 2000, 3000]
    assert scene.acquired == date(2023, 8, 12)
    assert scene.grid == grid
    assert scene.nodata == 0

    swapped = load_scene(folder, ("B04", "B02"))
    assert [int(swapped.dn[i, 0, 0]) for i in range(2)] == [3000, 1000]


def test_load_scene_missing_band(tmp_path, grid):
    folder = _scene_dir(tmp_path, grid, bands=("B02", "B03"))
    with pytest.raises(FileNotFoundError):
        load_scene(folder)


def test_scene_valid_requires_every_band(tmp_path, grid):
    folder = _scene_dir(tmp_path, grid)
    data = np.full((grid.height, grid.width), 2000, dtype=np.uint16)
    data[3, 4] = 0  # nodata in one band only
    write_band(folder / "B03.tif", data, grid)
    valid = load_scene(folder).valid
    assert not valid[3, 4]
    assert valid.sum() == grid.height * grid.width - 1


def test_load_scene_rejects_shifted_band(tmp_path, grid):
    folder = _scene_dir(tmp_path, grid)
    shifted = replace(grid, transform=Affine(10, 0, grid.transform.c + 10, 0, -10, grid.transform.f))
    write_band(folder / "B04.tif", np.full((grid.height, grid.width), 3000, dtype=np.uint16), shifted)
    with pytest.raises(ValueError, match="common grid"):
        load_scene(folder)


def test_load_scene_rejects_different_shapes(tmp_path, grid):
    folder = _scene_dir(tmp_path, grid)
    taller = replace(grid, height=grid.height + 1)
    write_band(folder / "B04.tif", np.full((taller.height, taller.width), 3000, dtype=np.uint16), taller)
    with pytest.raises(ValueError):
        load_scene(folder)


def test_load_scene_rejects_mixed_nodata(tmp_path, grid):
    folder = _scene_dir(tmp_path, grid)
    write_band(folder / "B04.tif", np.full((grid.height, grid.width), 3000, dtype=np.uint16), grid, nodata=65535)
    with pytest.raises(ValueError, match="nodata"):
        load_scene(folder)


def test_check_consistency_accepts_identical_grids(grid):
    assert check_consistency({"a": grid, "b": replace(grid)}) == grid


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"crs": CRS.from_epsg(32635)}, "CRS"),
        ({"width": 41}, "shape"),
        ({"height": 31}, "shape"),
        ({"transform": Affine(10, 0, 368010.0, 0, -10, 8654000.0)}, "transform"),
        ({"transform": Affine(20, 0, 368000.0, 0, -20, 8654000.0)}, "transform"),
    ],
)
def test_check_consistency_raises_on_mismatch(grid, change, message):
    with pytest.raises(ValueError, match=message):
        check_consistency({"before": grid, "after": replace(grid, **change)})


def test_check_consistency_reports_every_problem(grid):
    other = replace(grid, crs=CRS.from_epsg(32635), width=99)
    with pytest.raises(ValueError) as err:
        check_consistency({"before": grid, "after": other})
    assert "CRS" in str(err.value)
    assert "shape" in str(err.value)


def test_grid_pixel_area():
    assert make_grid(res=10.0).pixel_area_m2 == 100.0
    assert make_grid(res=20.0).pixel_area_m2 == 400.0


def test_write_raster_round_trip(tmp_path, grid):
    import rasterio

    data = np.arange(grid.height * grid.width, dtype=np.float32).reshape(grid.height, grid.width)
    path = write_raster(tmp_path / "out" / "x.tif", data, grid, nodata=-9999, descriptions=["ramp"], tags={"K": "v"})
    with rasterio.open(path) as ds:
        assert ds.crs == grid.crs
        assert ds.transform.almost_equals(grid.transform)
        assert ds.nodata == -9999
        assert ds.descriptions == ("ramp",)
        assert ds.tags()["K"] == "v"
        np.testing.assert_array_equal(ds.read(1), data)


def test_scene_dataclass_holds_metadata(grid):
    dn = np.ones((3, grid.height, grid.width), dtype=np.uint16)
    scene = Scene("s", date(2023, 1, 1), ("B02", "B03", "B04"), dn, grid, 0.0)
    assert scene.valid.all()
