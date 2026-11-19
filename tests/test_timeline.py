"""Offline tests of the pure functions of s2cd.timeline (synthetic arrays, no network)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from s2cd import timeline as T


def item(tile="35LLG", platform="sentinel-2a", dt="2023-08-30T08:06:09Z", baseline="05.09", id_="x"):
    return SimpleNamespace(
        id=id_,
        properties={"s2:mgrs_tile": tile, "platform": platform, "datetime": dt, "s2:processing_baseline": baseline},
    )


def test_month_range_crosses_years():
    r = T.month_range("2023-01", "2026-10")
    assert len(r) == 46 and r[0] == "2023-01" and r[-1] == "2026-10" and r[12] == "2024-01"
    assert T.month_range("2023-12", "2024-01") == ["2023-12", "2024-01"]


def test_month_span_december():
    assert T.month_span("2023-12") == (pd.Timestamp("2023-12-01").date(), pd.Timestamp("2024-01-01").date())


def test_boa_offset_by_baseline():
    assert T.boa_offset("05.09") == 1000.0
    assert T.boa_offset("04.00") == 1000.0
    assert T.boa_offset("03.01") == 0.0


def test_dedupe_keeps_newest_baseline_per_datatake_but_not_other_acquisitions():
    items = [
        item(baseline="05.09", id_="old"),
        item(baseline="05.10", id_="new"),
        item(dt="2023-08-25T08:06:11Z", baseline="05.09", id_="other_day"),
        item(tile="35LKG", baseline="05.09", id_="other_tile"),
    ]
    kept = {i.id for i in T.dedupe_items(items)}
    assert kept == {"new", "other_day", "other_tile"}


def test_clear_mask_excludes_scl_classes_and_nan():
    scl = np.array([[[0, 1, 2, 3], [4, 5, 6, 7]], [[8, 9, 10, 11], [np.nan, 2, 4, 5]]], np.float32)
    clear = T.clear_mask(scl, np.ones(scl.shape, bool))
    assert clear[0].tolist() == [[False, False, True, False], [True, True, True, True]]
    assert clear[1].tolist() == [[False, False, False, False], [False, True, True, True]]
    assert not T.clear_mask(scl, np.zeros(scl.shape, bool)).any()


def test_median_composite_ignores_masked_observations():
    refl = np.array([[[0.10]], [[0.90]], [[0.12]], [[0.14]]], np.float32)  # (T=4, 1, 1)
    clear = np.array([[[True]], [[False]], [[True]], [[True]]])
    assert T.median_composite(refl, clear)[0, 0] == pytest.approx(0.12)
    assert np.isnan(T.median_composite(refl, np.zeros_like(clear))[0, 0])


def test_dn_roundtrip_and_nodata():
    r = np.array([0.0123, -0.02, np.nan, 7.0], np.float32)
    dn = T.refl_to_dn(r)
    assert dn.tolist() == [1123, 800, 0, 65535]
    back = T.dn_to_refl(dn)
    assert back[0] == pytest.approx(0.0123, abs=1e-4) and np.isnan(back[2])


def test_clear_fraction_and_status_rule():
    n_clear = np.array([[0, 1], [2, 0]], np.uint16)
    aoi = np.array([[True, True], [True, False]])
    assert T.clear_fraction(n_clear, aoi) == pytest.approx(2 / 3)  # pixel (1, 1) is outside the AOI
    assert T.month_status(0.9) == "ok" and T.month_status(0.8999) == "insufficient" and T.month_status(1.0) == "ok"


def test_spectral_indices_formulas():
    refl = np.array([0.05, 0.10, 0.20, 0.40, 0.30, 0.10], np.float32).reshape(6, 1, 1)  # B02 B03 B04 B08 B11 B12
    idx = T.spectral_indices(refl)
    assert idx["NDVI"][0, 0] == pytest.approx((0.40 - 0.20) / 0.60)
    assert idx["NBR"][0, 0] == pytest.approx((0.40 - 0.10) / 0.50)
    assert idx["MNDWI"][0, 0] == pytest.approx((0.10 - 0.30) / 0.40)
    assert idx["BSI"][0, 0] == pytest.approx(((0.30 + 0.20) - (0.40 + 0.05)) / ((0.30 + 0.20) + (0.40 + 0.05)))


def _scene(h=60, w=60):
    """Vegetation everywhere, a 20 x 20 bare block, a 2 x 2 bare speck and a 10 x 10 water body."""
    refl = np.empty((6, h, w), np.float32)
    refl[:] = np.array([0.03, 0.05, 0.04, 0.35, 0.18, 0.08], np.float32).reshape(6, 1, 1)  # NDVI ~0.80, BSI < 0
    refl[:, 5:25, 5:25] = np.array([0.12, 0.17, 0.22, 0.25, 0.38, 0.34], np.float32).reshape(6, 1, 1)  # bare soil / rock
    refl[:, 40:42, 40:42] = refl[:, 10:11, 10:11]  # speck of 4 px
    refl[:, 45:55, 5:15] = np.array([0.04, 0.06, 0.04, 0.02, 0.01, 0.01], np.float32).reshape(6, 1, 1)  # water: MNDWI > 0
    return refl


def test_bare_mask_needs_bsi_and_low_ndvi_and_no_water():
    refl = _scene()
    bare = T.bare_mask(T.spectral_indices(refl), np.ones(refl.shape[1:], bool), T.Rules())
    assert bare[10, 10] and bare[40, 40] and not bare[50, 10] and not bare[30, 30]
    assert bare.sum() == 404


def test_month_kpis_areas_and_footprint_min_area():
    refl = _scene()
    valid = np.ones(refl.shape[1:], bool)
    rules = T.Rules(footprint_min_ha=0.1)  # 10 px at 10 m
    stats, foot = T.month_kpis(refl, valid, rules, pixel_area_m2=100.0)
    assert stats["bare_ha"] == pytest.approx((400 + 4) * 0.01)  # raw bare area keeps the speck
    assert stats["footprint_ha"] == pytest.approx(4.0)  # speck below min area and water excluded
    assert foot[10, 10] and not foot[40, 40] and not foot[50, 10]
    assert stats["water_ha"] == pytest.approx(1.0)
    assert stats["ndvi_mean"] < 0.8


def test_month_kpis_respects_valid_mask():
    refl = _scene()
    valid = np.ones(refl.shape[1:], bool)
    valid[:, :30] = False  # drops the bare block (columns 5-24); the speck (columns 40-41) stays
    stats, _ = T.month_kpis(refl, valid, T.Rules(footprint_min_ha=0.1))
    assert stats["footprint_ha"] == 0.0 and stats["bare_ha"] == pytest.approx(4 * 0.01)


def test_link_months_points_to_previous_ok_month():
    s = {"2023-01": "insufficient", "2023-02": "ok", "2023-03": "ok", "2023-04": "insufficient", "2023-05": "ok"}
    assert T.link_months(s) == {"2023-01": None, "2023-02": None, "2023-03": "2023-02", "2023-04": None, "2023-05": "2023-03"}


def test_manifest_entry_shape_for_ok_and_insufficient_rows():
    base = dict.fromkeys(T.MONTH_COLUMNS)
    ok = pd.Series(
        base
        | {"month": "2023-02", "status": "ok", "n_items": 4, "clear_fraction": 0.99871, "ndvi_mean": 0.523456, "bare_ha": 12.345}
        | {"prev_month": "2023-01"}
    )
    e = T.manifest_entry(ok, "monthly/2023-02_rgb.webp", None)
    assert list(e) == ["month", "status", "n_items", "clear_fraction", "rgb", "change", "prev_month", "stats"]
    assert e["clear_fraction"] == 0.9987 and e["stats"]["ndvi_mean"] == 0.5235 and e["stats"]["bare_ha"] == 12.35
    assert e["stats"]["footprint_ha"] is None and e["change"] is None and e["prev_month"] == "2023-01"
    bad = T.manifest_entry(
        pd.Series(base | {"month": "2023-01", "status": "insufficient", "n_items": 0, "clear_fraction": 0.0}), None, None
    )
    assert bad["prev_month"] is None and set(bad["stats"].values()) == {None}
