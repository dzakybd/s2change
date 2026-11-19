"""Offline unit tests for the pure functions of the optional STAC enrichment (no network, no STAC extra needed)."""

import numpy as np
import pandas as pd
import pytest

from s2cd import stac_context as sc


def test_reflectance_removes_offset_and_masks_nodata():
    dn = np.array([[1000, 3500, 0]], dtype=np.uint16)
    r = sc.reflectance(dn)
    assert r[0, 0] == pytest.approx(0.0)
    assert r[0, 1] == pytest.approx(0.25)
    assert np.isnan(r[0, 2])


def test_norm_diff_values_and_degenerate_denominator():
    a, b = np.array([0.4, 0.0, np.nan], np.float32), np.array([0.2, 0.0, 0.1], np.float32)
    out = sc.norm_diff(a, b)
    assert out[0] == pytest.approx(1 / 3)
    assert np.isnan(out[1]) and np.isnan(out[2])


def test_spectral_indices_and_change_signs():
    def scene(b08, b04, b12, b03, b11):
        return {k: np.full((2, 2), v, np.float32) for k, v in dict(B08=b08, B04=b04, B12=b12, B03=b03, B11=b11).items()}

    pre = sc.spectral_indices(scene(0.4, 0.1, 0.1, 0.1, 0.2))  # green vegetation
    post = sc.spectral_indices(scene(0.2, 0.1, 0.2, 0.3, 0.1))  # burnt and wetter
    assert pre["NDVI"][0, 0] == pytest.approx(0.6)
    assert pre["NBR"][0, 0] == pytest.approx(0.6)
    assert pre["MNDWI"][0, 0] == pytest.approx(-1 / 3)
    ch = sc.index_changes(pre, post)
    assert ch["dNBR"][0, 0] == pytest.approx(0.6 - 0.0)  # before - after: positive = loss
    assert ch["dNDVI"][0, 0] < 0  # after - before: negative = greenness loss
    assert ch["dMNDWI"][0, 0] > 0


def test_compare_bands_detects_identity_and_offset():
    rng = np.random.default_rng(0)
    pc = rng.integers(1000, 3000, (50, 50)).astype(np.uint16)
    same = sc.compare_bands(pc.copy(), pc)
    assert same["exact_match_fraction"] == 1.0 and same["correlation"] == pytest.approx(1.0)
    shifted = sc.compare_bands(pc + 100, pc)
    assert shifted["median_diff_dn"] == 100 and shifted["exact_match_fraction"] == 0.0
    assert shifted["correlation"] == pytest.approx(1.0)


def test_compare_bands_ignores_nodata():
    pc = np.array([[0, 1500], [2000, 2500]], np.uint16)
    prov = np.array([[1200, 0], [2000, 2600]], np.uint16)
    assert sc.compare_bands(prov, pc)["n_pixels"] == 2


def test_date_coverage_counts_reprocessed_duplicates():
    base = {"platform": "S2B", "relative_orbit": 121, "datetime": "2023-09-02T08:16:09Z", "mgrs_tile": "35LLG"}
    cov = sc.date_coverage([{**base, "processing_baseline": "05.09"}, {**base, "processing_baseline": "05.10"}])
    assert cov["n_items"] == 2 and cov["n_datatakes"] == 1 and cov["reprocessed_duplicates"] == 1
    assert not cov["more_than_one_datatake"]
    other = {**base, "relative_orbit": 78, "datetime": "2023-09-02T08:06:09Z", "processing_baseline": "05.09"}
    assert sc.date_coverage([{**base, "processing_baseline": "05.09"}, other])["more_than_one_datatake"]


def test_zonal_mean_skips_nan_and_empty_labels():
    labels = np.array([[1, 1, 2], [2, 0, 3]])
    values = np.array([[1.0, np.nan, 4.0], [6.0, 100.0, np.nan]])
    out = sc.zonal_mean(labels, 3, values)
    assert out[0] == pytest.approx(1.0) and out[1] == pytest.approx(5.0) and np.isnan(out[2])


def test_zonal_fraction():
    labels = np.array([[1, 1], [1, 1]])
    assert sc.zonal_fraction(labels, 1, np.array([[True, False], [False, False]]))[0] == pytest.approx(0.25)


def _ctx(**kw) -> pd.DataFrame:
    base = dict(
        scl_artifact_before=0.0, scl_artifact_after=0.0, scl_water_before=0.0, scl_water_after=0.0,
        mndwi_before=-0.4, mndwi_after=-0.4, d_mndwi=0.0, d_nbr=0.0, d_ndvi=0.0,
    )  # fmt: skip
    return pd.DataFrame([{**base, **row} for row in kw["rows"]])


def test_assign_cause_rules_and_priority():
    ctx = _ctx(
        rows=[
            {"scl_artifact_after": 0.5, "d_nbr": 0.5},  # artifact beats burn
            {"scl_water_before": 0.4},  # SCL water
            {"d_mndwi": 0.25},  # large MNDWI change
            {"mndwi_before": -0.05, "mndwi_after": 0.1, "d_mndwi": 0.15},  # sign change
            {"mndwi_before": -0.01, "mndwi_after": 0.01, "d_mndwi": 0.02},  # sign change but negligible
            {"d_nbr": 0.2},  # burn outside the mine
            {"d_nbr": 0.2},  # same stats but inside the mine
            {"d_ndvi": -0.2},  # vegetation loss
            {},  # inside the mine, nothing else
            {},  # nothing
        ]
    )
    disturbed = pd.Series([False, False, False, False, False, False, True, False, True, False])
    out = sc.assign_cause(ctx, disturbed).tolist()
    assert out == [
        "cloud_shadow_haze_artifact",
        "water_change",
        "water_change",
        "water_change",
        "unexplained",
        "burn_scar",
        "active_mining_or_tailings",
        "vegetation_loss",
        "active_mining_or_tailings",
        "unexplained",
    ]


def test_assign_cause_nan_statistics_do_not_trigger_rules():
    ctx = _ctx(rows=[{k: np.nan for k in ("d_nbr", "d_ndvi", "d_mndwi", "mndwi_before", "mndwi_after")}])
    assert sc.assign_cause(ctx, pd.Series([False])).tolist() == ["unexplained"]


def test_eta2_zone_dependence():
    zones = np.repeat([0, 1], 100)
    assert sc.eta2(np.where(zones == 0, 1.0, 2.0), zones) == pytest.approx(1.0)
    noise = np.random.default_rng(0).normal(size=200)
    assert sc.eta2(noise, zones) < 0.1
