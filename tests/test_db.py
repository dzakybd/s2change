from __future__ import annotations

from datetime import date

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import pytest
from conftest import make_grid

from s2cd import db, vectorize

SHAPE = (60, 100)
LOW, HIGH = vectorize.BinaryRule().thresholds(3)


def _polygons(rng, grid):
    mask = np.zeros(SHAPE, bool)
    mask[5:15, 5:15] = True  # 100 px, brightening
    mask[30:38, 50:58] = True  # 64 px, darkening
    delta = np.zeros((3, *SHAPE), np.float32)
    delta[:, 5:15, 5:15] = 0.05
    delta[:, 30:38, 50:58] = -0.05
    layers = {
        "d2": np.full(SHAPE, 50.0, np.float32),
        "neglog10_p": np.full(SHAPE, 4.0, np.float32),
        "valid": np.ones(SHAPE, bool),
        "delta": delta,
        "bright_before": np.full(SHAPE, 0.2, np.float32),
        "bright_after": np.full(SHAPE, 0.25, np.float32),
        "zone": np.zeros(SHAPE, np.int16),
        "disturbed": np.zeros(SHAPE, bool),
    }
    return vectorize.polygonize(mask, grid, layers, 0.01, HIGH, LOW)


@pytest.fixture
def tables(rng):
    grid = make_grid(width=SHAPE[1], height=SHAPE[0])
    poly = _polygons(rng, grid)
    before, after = date(2023, 8, 12), date(2023, 9, 2)
    feats = []
    rows = []
    for method in ("robust_cva", "euclidean_baseline"):
        run_id = f"{before:%Y%m%d}_{after:%Y%m%d}_{method}"
        feats.append(db.as_feature_table(poly, run_id, method, before, after))
        rows.append(
            {
                "run_id": run_id,
                "method": method,
                "date_before": str(before),
                "date_after": str(after),
                "params": {"rule": {"mmu_ha": 0.2}, "bands": ["B02", "B03", "B04"]},
                "summary": {"changed_area_ha": 1.64, "n_features": len(poly)},
            }
        )
    return gpd.GeoDataFrame(pd.concat(feats, ignore_index=True), crs=grid.crs), db.runs_table(rows), poly


def test_as_feature_table_schema(tables):
    feats, _, poly = tables
    assert list(feats.columns[: len(db.FEATURE_COLUMNS)]) == db.FEATURE_COLUMNS
    assert "region" not in feats.columns
    assert feats.geometry.geom_type.eq("MultiPolygon").all()
    assert feats.date_before.eq("2023-08-12").all() and feats.date_after.eq("2023-09-02").all()
    assert set(feats.method) == {"robust_cva", "euclidean_baseline"}
    assert feats.geometry.area.sum() == pytest.approx(2 * poly.geometry.area.sum())


def test_as_feature_table_does_not_mutate_input(tables):
    *_, poly = tables
    cols, geoms = list(poly.columns), poly.geometry.copy()
    db.as_feature_table(poly, "r", "m", date(2023, 1, 1), date(2023, 2, 1))
    assert list(poly.columns) == cols
    assert all(a.equals(b) for a, b in zip(poly.geometry, geoms, strict=True))


def test_as_feature_table_is_idempotent_on_multipolygons(tables):
    *_, poly = tables
    once = db.as_feature_table(poly, "r", "m", date(2023, 1, 1), date(2023, 2, 1))
    twice = db.as_feature_table(once, "r", "m", date(2023, 1, 1), date(2023, 2, 1))
    assert all(a.equals(b) for a, b in zip(once.geometry, twice.geometry, strict=True))
    assert twice.geometry.geom_type.eq("MultiPolygon").all()
    assert all(len(g.geoms) == 1 for g in twice.geometry)


def test_write_sqlite_round_trip(tmp_path, tables):
    feats, runs, _ = tables
    path = db.write_sqlite(tmp_path / "nested" / "cd.sqlite", feats, runs)
    assert path.exists()

    info = pyogrio.read_info(path, layer="change_features")
    assert info["geometry_type"] == "MultiPolygon"
    assert info["crs"] == "EPSG:32735"
    assert info["features"] == len(feats)

    back = pyogrio.read_dataframe(path, layer="change_features")
    assert back.crs.to_epsg() == 32735
    assert back.geometry.geom_type.eq("MultiPolygon").all()
    assert sorted(back.area_m2) == pytest.approx(sorted(feats.area_m2))
    assert back.geometry.area.sum() == pytest.approx(feats.geometry.area.sum())
    assert set(back.run_id) == set(feats.run_id)
    assert set(back.change_direction) == {"brightening", "darkening"}
    assert set(db.FEATURE_COLUMNS) <= set(back.columns)
    assert back.in_disturbed_area.dtype == bool

    runs_back = pyogrio.read_dataframe(path, sql="SELECT * FROM change_runs ORDER BY run_id", sql_dialect="SQLITE")
    assert list(runs_back.run_id) == ["20230812_20230902_euclidean_baseline", "20230812_20230902_robust_cva"]
    assert runs_back.params.iloc[0].startswith("{")
    assert set(runs_back.method) == {"robust_cva", "euclidean_baseline"}


def test_write_sqlite_overwrites_instead_of_appending(tmp_path, tables):
    feats, runs, _ = tables
    path = tmp_path / "cd.sqlite"
    db.write_sqlite(path, feats, runs)
    db.write_sqlite(path, feats, runs)
    assert pyogrio.read_info(path, layer="change_features")["features"] == len(feats)
    n_runs = pyogrio.read_dataframe(path, sql="SELECT count(*) AS n FROM change_runs", sql_dialect="SQLITE").n.iloc[0]
    assert n_runs == 2


def test_spatial_sql_runs_against_written_database(tmp_path, tables):
    feats, runs, _ = tables
    path = db.write_sqlite(tmp_path / "cd.sqlite", feats, runs)
    out = pyogrio.read_dataframe(
        path,
        sql="SELECT method, SUM(ST_Area(geometry)) AS a FROM change_features GROUP BY method",
        sql_dialect="SQLITE",
    )
    assert out.a.tolist() == [pytest.approx(16400.0)] * 2


def test_runs_table_serializes_json():
    runs = db.runs_table(
        [
            {
                "run_id": "r",
                "method": "m",
                "date_before": "2023-01-01",
                "date_after": "2023-02-01",
                "params": {"a": 1},
                "summary": {"b": [1, 2]},
            }
        ]
    )
    assert runs.params.iloc[0] == '{"a": 1}'
    assert runs.summary.iloc[0] == '{"b": [1, 2]}'


def test_schema_sql_matches_feature_columns():
    ddl = db.SCHEMA_SQL.read_text()
    for col in db.FEATURE_COLUMNS:
        assert col in ddl, f"{col} missing from schema_postgis.sql"
    assert "geometry(MultiPolygon, 32735)" in ddl
