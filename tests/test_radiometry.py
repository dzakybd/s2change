from __future__ import annotations

import numpy as np
import pytest

from s2cd import radiometry

BANDS = ("B02", "B03", "B04")


def _projection(shape, angle_deg):
    t = np.deg2rad(angle_deg)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    return (xx + 0.5) * np.cos(t) + (yy + 0.5) * np.sin(t)


def _scene_pair(rng, shape=(320, 320), angle=20.0, breakpoints=(150.0, 290.0), offsets=(0.0, 0.025, -0.012), sigma=0.003):
    """Before/after reflectance where `after` differs from `before` by a per-stripe additive offset."""
    p = _projection(shape, angle)
    zone_truth = np.digitize(p, breakpoints)
    before = rng.uniform(0.05, 0.35, size=(3, *shape)).astype(np.float32)
    after = before + np.asarray(offsets, dtype=np.float32)[zone_truth] + rng.normal(0, sigma, size=(3, *shape)).astype(np.float32)
    return before, after, np.ones(shape, bool), zone_truth


class TestDetectStripeZones:
    def test_recovers_angle_and_breakpoints(self, rng):
        before, after, valid, _ = _scene_pair(rng)
        z = radiometry.detect_stripe_zones(before, after, valid)
        assert z.angle_deg == pytest.approx(20.0, abs=1.0)
        assert len(z.breakpoints) == 2
        assert sorted(z.breakpoints) == pytest.approx([150.0, 290.0], abs=8.0)
        assert z.zones.max() == 2 and z.zones.min() == 0
        np.testing.assert_allclose(z.zone_offsets, [0.0, 0.025, -0.012], atol=0.003)
        assert z.explained_variance > 0.5

    def test_zone_map_matches_truth(self, rng):
        before, after, valid, truth = _scene_pair(rng)
        z = radiometry.detect_stripe_zones(before, after, valid)
        assert (z.zones == truth).mean() > 0.97

    def test_two_zones_negative_angle(self, rng):
        # for a negative angle the projection p is mostly negative; put the breakpoint inside the covered range
        bp = float(np.median(_projection((320, 320), -35.0)))
        before, after, valid, _ = _scene_pair(rng, angle=-35.0, breakpoints=(bp,), offsets=(0.0, 0.02))
        z = radiometry.detect_stripe_zones(before, after, valid)
        assert z.angle_deg == pytest.approx(-35.0, abs=1.0)
        assert len(z.breakpoints) == 1
        assert z.breakpoints[0] == pytest.approx(bp, abs=8.0)

    def test_planted_change_does_not_move_the_stripes(self, rng):
        before, after, valid, _ = _scene_pair(rng)
        after[:, 100:140, 100:150] += 0.2  # a changed block is an outlier, not a stripe
        z = radiometry.detect_stripe_zones(before, after, valid)
        assert z.angle_deg == pytest.approx(20.0, abs=1.0)
        assert len(z.breakpoints) == 2

    def test_no_stripes_gives_single_zone(self, rng):
        before, after, valid, _ = _scene_pair(rng, offsets=(0.01, 0.01, 0.01))
        z = radiometry.detect_stripe_zones(before, after, valid)
        assert z.breakpoints == []
        assert len(z.zone_offsets) == 1
        assert z.zone_offsets[0] == pytest.approx(0.01, abs=0.002)
        assert set(np.unique(z.zones)) == {0}

    def test_invalid_pixels_get_zone_minus_one(self, rng):
        before, after, valid, _ = _scene_pair(rng)
        valid[:, :20] = False
        before[:, ~valid] = np.nan
        after[:, ~valid] = np.nan
        z = radiometry.detect_stripe_zones(before, after, valid)
        assert (z.zones[~valid] == -1).all()
        assert (z.zones[valid] >= 0).all()


class TestRelativeNormalization:
    @pytest.mark.parametrize("changed_fraction", [0.0, 0.10])
    def test_recovers_per_zone_gain_and_offset(self, rng, changed_fraction):
        shape = (200, 200)
        zones = np.zeros(shape, np.int16)
        zones[:, 100:] = 1
        gain = np.array([[1.10, 0.95, 1.05], [0.90, 1.00, 1.15]])  # [zone, band]: before = intercept + gain * after
        intercept = np.array([[0.010, -0.005, 0.020], [-0.015, 0.008, 0.000]])
        after = rng.uniform(0.05, 0.40, size=(3, *shape)).astype(np.float32)
        before = np.empty_like(after)
        for z in (0, 1):
            m = zones == z
            for b in range(3):
                before[b, m] = intercept[z, b] + gain[z, b] * after[b, m]
        before += rng.normal(0, 0.002, size=before.shape).astype(np.float32)
        changed = np.zeros(shape, bool)
        if changed_fraction:
            n = int(changed_fraction * changed.size)
            changed.flat[rng.choice(changed.size, n, replace=False)] = True
            before[:, changed] += rng.choice([-1, 1]) * 0.15  # same-signed large change, would bias OLS
        res = radiometry.relative_normalization(before, after, zones, BANDS)
        norm, pif, cals = res.normalized, res.pif, res.calibration

        assert len(cals) == 6
        for c in cals:
            b = BANDS.index(c.band)
            assert c.slope == pytest.approx(gain[c.zone, b], abs=0.03)
            assert c.intercept == pytest.approx(intercept[c.zone, b], abs=0.01)
        # normalized after matches before on unchanged pixels, and changed pixels are excluded from the PIFs
        err = np.abs(norm - before)[:, ~changed]
        assert np.median(err) < 0.004
        if changed_fraction:
            assert pif[changed].mean() < 0.05
            assert pif[~changed].mean() > 0.9

    def test_invalid_zone_pixels_stay_nan(self, rng):
        shape = (60, 60)
        zones = np.zeros(shape, np.int16)
        zones[:, :10] = -1
        after = rng.uniform(0.1, 0.3, size=(3, *shape)).astype(np.float32)
        res = radiometry.relative_normalization(after + 0.01, after, zones, BANDS)
        norm, pif, cals = res.normalized, res.pif, res.calibration
        assert np.isnan(norm[:, :, :10]).all()
        assert not pif[:, :10].any()
        assert len(cals) == 3


class TestSeamDistance:
    def test_no_breakpoints_is_infinite(self):
        d = radiometry.seam_distance((5, 7), 20.0, [])
        assert d.shape == (5, 7)
        assert np.isinf(d).all()

    def test_vertical_seam(self):
        d = radiometry.seam_distance((4, 30), 0.0, [10.0])
        # pixel centre of column x is x + 0.5
        np.testing.assert_allclose(d[0], np.abs(np.arange(30) + 0.5 - 10.0), atol=1e-5)
        assert (d == d[0]).all()

    def test_horizontal_seam_and_nearest_of_several(self):
        d = radiometry.seam_distance((40, 4), 90.0, [10.0, 30.0])
        rows = np.arange(40) + 0.5
        expected = np.minimum(np.abs(rows - 10.0), np.abs(rows - 30.0))
        np.testing.assert_allclose(d[:, 0], expected, atol=1e-5)

    def test_slanted_seam_is_perpendicular_distance(self):
        angle = 30.0
        d = radiometry.seam_distance((50, 50), angle, [20.0])
        t = np.deg2rad(angle)
        # (x, y) = 20 * (cos t, sin t) lies on the line; moving along its normal by 5 px changes distance by 5
        x, y = 20 * np.cos(t) + 5 * np.cos(t), 20 * np.sin(t) + 5 * np.sin(t)
        i, j = int(round(y - 0.5)), int(round(x - 0.5))
        assert d[i, j] == pytest.approx(abs((j + 0.5) * np.cos(t) + (i + 0.5) * np.sin(t) - 20.0), abs=1e-4)
        assert d[i, j] == pytest.approx(5.0, abs=0.7)


class TestReflectance:
    def test_to_reflectance_applies_offset_and_masks_invalid(self):
        dn = np.array([[[1000, 2000], [3000, 11000]]], dtype=np.uint16)
        valid = np.array([[True, True], [True, False]])
        refl = radiometry.to_reflectance(dn, valid, 1000.0)
        assert refl.dtype == np.float32
        np.testing.assert_allclose(refl[0, :2, 0], [0.0, 0.2])
        assert np.isnan(refl[0, 1, 1])

    def test_infer_offset(self):
        valid = np.ones((10, 10), bool)
        with_offset = np.full((3, 10, 10), 1500, dtype=np.uint16)
        without = np.full((3, 10, 10), 200, dtype=np.uint16)
        assert radiometry.infer_offset(with_offset, valid) == 1000.0
        assert radiometry.infer_offset(without, valid) == 0.0
