from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pyogrio
import pytest
from conftest import ROOT

from s2cd import pipeline


def test_config_from_default_yaml():
    cfg = pipeline.Config.from_yaml(ROOT / "configs" / "default.yaml")
    assert cfg.bands == ("B02", "B03", "B04")
    assert cfg.before.name == "sentinel2_20230812" and cfg.after.name == "sentinel2_20230902"
    assert cfg.rule.mmu_px == 20
    assert cfg.rule.far_high < cfg.rule.far_low
    assert cfg.database_url is None


def test_config_defaults_when_yaml_is_empty(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("")
    assert pipeline.Config.from_yaml(f) == pipeline.Config()


def test_pipeline_rejects_reversed_dates(tmp_path):
    from conftest import make_grid, write_band

    grid = make_grid(width=16, height=16)
    for name in ("sentinel2_20230902", "sentinel2_20230812"):
        for b in ("B02", "B03", "B04"):
            write_band(tmp_path / name / f"{b}.tif", np.full((16, 16), 1500, np.uint16), grid)
    cfg = pipeline.Config(before=tmp_path / "sentinel2_20230902", after=tmp_path / "sentinel2_20230812", out_dir=tmp_path / "out")
    with pytest.raises(ValueError, match="must be earlier"):
        pipeline.run(cfg)


@pytest.mark.slow
def test_full_pipeline_on_real_inputs(tmp_path):
    cfg = pipeline.Config.from_yaml(ROOT / "configs" / "default.yaml")
    for field in ("before", "after", "aoi"):
        setattr(cfg, field, ROOT / getattr(cfg, field))
    if not (cfg.before / "B02.tif").exists():
        pytest.skip("real inputs not available")
    cfg.out_dir = tmp_path / "processed"
    cfg.sqlite_path = tmp_path / "cd.sqlite"
    cfg.figures_dir = tmp_path / "figures"

    products = pipeline.run(cfg)

    s = products.summary
    assert s["stripes"]["breakpoints_px"], "stripe zones should be detected on the 2023-09-02 scene"
    assert 0 < s["changed_area_ha"]["robust_cva"] < s["changed_area_ha"]["euclidean_baseline"]
    assert s["n_features"]["robust_cva"] > 0
    for name in ("change_map.tif", "change_binary.tif", "change_features.geojson", "run_summary.json"):
        assert (cfg.out_dir / name).exists(), name
    assert json.loads((cfg.out_dir / "run_summary.json").read_text())["n_features"] == s["n_features"]

    info = pyogrio.read_info(cfg.sqlite_path, layer="change_features")
    assert info["geometry_type"] == "MultiPolygon" and info["crs"] == "EPSG:32735"
    assert info["features"] == sum(s["n_features"].values())
    runs = pyogrio.read_dataframe(cfg.sqlite_path, sql="SELECT run_id FROM change_runs", sql_dialect="SQLITE")
    assert len(runs) == 2

    # Polygon areas agree with the raster mask (10 m pixels).
    gdf = pyogrio.read_dataframe(cfg.sqlite_path, layer="change_features", where="method = 'robust_cva'")
    assert gdf.area_m2.sum() / 1e4 == pytest.approx(s["changed_area_ha"]["robust_cva"], rel=1e-3)
    shutil.rmtree(Path(cfg.out_dir))
