from __future__ import annotations

import numpy as np
import pytest
import shapely
from conftest import make_grid

from s2cd import vectorize
from s2cd.vectorize import BinaryRule

RULE = BinaryRule()
PX = 10.0  # pixel size in metres
LOW, HIGH = RULE.thresholds(3)


def _score(shape, blocks):
    d2 = np.zeros(shape, np.float32)
    for (r0, r1, c0, c1), v in blocks:
        d2[r0:r1, c0:c1] = v
    return d2


def test_thresholds_follow_chi2():
    assert pytest.approx(16.27, abs=0.01) == LOW  # chi2(3) upper 1e-3 quantile
    assert pytest.approx(25.90, abs=0.02) == HIGH  # chi2(3) upper 1e-5 quantile
    assert LOW < HIGH


class TestBinaryFromD2:
    shape = (80, 120)

    def _run(self, d2, seam=None, valid=None, rule=RULE):
        valid = np.ones(self.shape, bool) if valid is None else valid
        seam = np.full(self.shape, np.inf, np.float32) if seam is None else seam
        return vectorize.binary_from_d2(d2, valid, seam, 3, rule, PX)

    def test_objects_below_mmu_are_removed(self):
        d2 = _score(self.shape, [((5, 15, 5, 15), 100), ((40, 43, 40, 43), 100)])  # 100 px vs 9 px
        out = self._run(d2)
        assert out[6:14, 6:14].all()  # opening with a small disk may shave the corners
        assert out[5:15, 5:15].sum() >= 96
        assert not out[40:43, 40:43].any()
        assert out.sum() == out[5:15, 5:15].sum()

    def test_mmu_boundary(self):
        d2 = _score(self.shape, [((5, 9, 5, 10), 100), ((40, 44, 40, 45), 100), ((60, 64, 60, 64), 100)])  # 20, 20, 16 px
        out = self._run(d2, rule=BinaryRule(opening_radius_m=0))
        assert out[5:9, 5:10].all() and out[40:44, 40:45].all()
        assert not out[60:64, 60:64].any()

    def test_seam_buffer_pixels_are_dropped(self):
        d2 = _score(self.shape, [((10, 40, 40, 80), 100)])
        seam = np.abs(np.arange(self.shape[1]) + 0.5 - 60.0)[None, :].repeat(self.shape[0], 0).astype(np.float32)
        out = self._run(d2, seam=seam)
        assert not out[seam < RULE.seam_buffer_px(PX)].any()
        assert out[11:39, 41:50].all() and out[11:39, 70:79].all()  # both sides of the seam survive, beyond the 9 px buffer

    def test_region_needs_one_strongly_significant_pixel(self):
        weak = _score(self.shape, [((5, 25, 5, 25), (LOW + HIGH) / 2)])  # above growing threshold, never above seed threshold
        assert not self._run(weak).any()
        seeded = weak.copy()
        seeded[15, 15] = HIGH + 5
        out = self._run(seeded)
        assert out[6:24, 6:24].all()  # the weak region is kept once it contains a seed

    def test_invalid_pixels_never_change(self):
        d2 = _score(self.shape, [((10, 30, 10, 30), 100)])
        valid = np.ones(self.shape, bool)
        valid[10:30, 10:20] = False
        out = self._run(d2, valid=valid)
        assert not out[~valid].any()
        assert out[11:29, 20:29].all()

    def test_nan_scores_are_not_changes(self):
        d2 = np.full(self.shape, np.nan, np.float32)
        d2[10:30, 10:30] = 100
        valid = ~np.isnan(d2)
        out = self._run(d2, valid=valid)
        assert out[11:29, 11:29].all()
        assert out.sum() >= 396
        assert not out[~valid].any()

    def test_clean_input_gives_empty_mask(self):
        assert not self._run(np.zeros(self.shape, np.float32)).any()


def test_binary_from_otsu_separates_two_populations(rng):
    prob = rng.normal(0.1, 0.02, size=(80, 80)).astype(np.float32)
    prob[20:40, 20:40] = 0.9
    valid = np.ones(prob.shape, bool)
    mask, thr = vectorize.binary_from_otsu(prob, valid, RULE, PX)
    assert 0.15 < thr < 0.8
    assert mask[21:39, 21:39].all()
    assert mask[20:40, 20:40].sum() == mask.sum() >= 396


def _layers(shape, delta=None):
    zeros = np.zeros(shape, np.float32)
    d2 = np.full(shape, 50.0, np.float32)
    return {
        "d2": d2,
        "neglog10_p": np.full(shape, 4.0, np.float32),
        "valid": np.ones(shape, bool),
        "delta": np.zeros((3, *shape), np.float32) if delta is None else delta,
        "bright_before": zeros + 0.2,
        "bright_after": zeros + 0.25,
        "zone": np.zeros(shape, np.int16),
        "disturbed": np.zeros(shape, bool),
    }


class TestPolygonize:
    shape = (60, 100)

    def _polygonize(self, mask, layers=None, grid=None):
        grid = grid or make_grid(width=self.shape[1], height=self.shape[0])
        return vectorize.polygonize(mask, grid, layers or _layers(self.shape), noise_sigma=0.01, strong_d2=HIGH, low_d2=LOW), grid

    def test_areas_equal_pixel_count_times_pixel_area(self):
        mask = np.zeros(self.shape, bool)
        mask[5:11, 5:10] = True  # 30 px
        mask[30:38, 50:58] = True  # 64 px
        mask[50:53, 80:87] = True  # 21 px
        gdf, grid = self._polygonize(mask)
        assert grid.pixel_area_m2 == 100
        assert list(gdf.area_m2) == [6400.0, 3000.0, 2100.0]  # sorted, largest first
        assert gdf.area_m2.sum() == mask.sum() * 100
        assert gdf.geometry.area.sum() == pytest.approx(mask.sum() * 100)
        assert gdf.crs == grid.crs

    def test_geometry_is_georeferenced(self):
        mask = np.zeros(self.shape, bool)
        mask[10:20, 10:20] = True
        gdf, grid = self._polygonize(mask)
        minx, miny, maxx, maxy = gdf.geometry.iloc[0].bounds
        assert (minx, maxx) == (grid.transform.c + 100, grid.transform.c + 200)
        assert (maxy, miny) == (grid.transform.f - 100, grid.transform.f - 200)

    def test_diagonal_pixels_join_with_8_connectivity(self):
        mask = np.zeros(self.shape, bool)
        mask[10:14, 10:14] = True
        mask[14:18, 14:18] = True  # touches the first block at a corner only
        gdf, _ = self._polygonize(mask)
        assert len(gdf) == 1
        assert gdf.area_m2.iloc[0] == 32 * 100

    def test_holes_are_kept_in_the_geometry(self):
        mask = np.zeros(self.shape, bool)
        mask[10:30, 10:30] = True
        mask[18:22, 18:22] = False
        gdf, _ = self._polygonize(mask)
        assert len(gdf) == 1
        assert gdf.area_m2.iloc[0] == (400 - 16) * 100
        assert len(shapely.get_parts(gdf.geometry.iloc[0])[0].interiors) == 1

    def test_change_direction_labels(self):
        mask = np.zeros(self.shape, bool)
        mask[5:15, 5:15] = True  # brightening
        mask[5:15, 30:40] = True  # darkening
        mask[5:15, 60:70] = True  # spectral shift: blue up, red down, brightness unchanged
        delta = np.zeros((3, *self.shape), np.float32)
        delta[:, 5:15, 5:15] = 0.05
        delta[:, 5:15, 30:40] = -0.05
        delta[0, 5:15, 60:70] = 0.05
        delta[2, 5:15, 60:70] = -0.05
        gdf, _ = self._polygonize(mask, _layers(self.shape, delta))
        by_col = {round(g.bounds[0] / 10) % 100: d for g, d in zip(gdf.geometry, gdf.change_direction, strict=True)}
        starts = sorted(by_col)
        assert [by_col[s] for s in starts] == ["brightening", "darkening", "spectral shift"]
        assert gdf.d_brightness.round(3).tolist().count(0.0) == 1

    def test_disturbed_flag_and_zone(self):
        mask = np.zeros(self.shape, bool)
        mask[5:15, 5:15] = True
        mask[30:40, 50:60] = True
        layers = _layers(self.shape)
        layers["disturbed"][:, :30] = True
        layers["zone"][:, 40:] = 2
        gdf, _ = self._polygonize(mask, layers)
        inside = gdf[gdf.geometry.centroid.x < gdf.geometry.centroid.x.mean()]
        outside = gdf[gdf.geometry.centroid.x >= gdf.geometry.centroid.x.mean()]
        assert inside.in_disturbed_area.all() and not outside.in_disturbed_area.any()
        assert (inside.zone == 0).all() and (outside.zone == 2).all()

    def test_confidence_grows_with_d2_and_stays_in_unit_interval(self):
        mask = np.zeros(self.shape, bool)
        mask[5:15, 5:15] = True
        mask[5:15, 40:50] = True
        layers = _layers(self.shape)
        layers["d2"][5:15, 5:15] = LOW * 1.5
        layers["d2"][5:15, 40:50] = LOW * 80
        gdf, _ = self._polygonize(mask, layers)
        assert gdf.confidence.between(0, 1).all()
        weak, strong = (
            gdf.loc[gdf.geometry.bounds.minx.idxmin(), "confidence"],
            gdf.loc[gdf.geometry.bounds.minx.idxmax(), "confidence"],
        )
        assert weak < strong

    def test_empty_mask_gives_empty_frame(self):
        gdf, grid = self._polygonize(np.zeros(self.shape, bool))
        assert len(gdf) == 0
        assert gdf.crs == grid.crs


def test_disturbed_footprint_marks_bright_bare_surface(rng):
    shape = (120, 120)
    refl = rng.normal(0.08, 0.005, size=(3, *shape)).astype(np.float32)
    refl[:, 40:80, 40:80] += 0.25  # a bright mine pad on a dark background
    valid = np.ones(shape, bool)
    out = vectorize.disturbed_footprint(refl, valid, buffer_px=5)
    assert out[41:79, 41:79].all()
    assert not out[:20].any() and not out[:, :20].any()
    assert out[38, 60]  # buffered outwards
