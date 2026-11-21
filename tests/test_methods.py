from __future__ import annotations

import importlib.util

import numpy as np
import pytest
from scipy import stats

from s2cd import methods


def test_robust_cva_false_alarm_rate_matches_nominal(rng):
    shape = (500, 500)
    before = rng.uniform(0.05, 0.4, size=(3, *shape)).astype(np.float32)
    after_norm = before + rng.normal(0, 0.01, size=before.shape).astype(np.float32)
    zones = np.zeros(shape, np.int16)
    pif = np.ones(shape, bool)

    res = methods.robust_cva(before, after_norm, zones, pif)

    assert res.d2.shape == shape and res.delta.shape == (3, *shape)
    # Under the noise model d2 ~ chi2(3): the exceedance rate at a nominal FAR should match it.
    n = res.d2.size
    for far in (1e-2, 1e-3):
        thr = stats.chi2.isf(far, 3)
        rate = float((res.d2 > thr).mean())
        sigma = np.sqrt(far * (1 - far) / n)
        assert abs(rate - far) < 5 * sigma + 0.1 * far
    assert abs(float((res.neglog10_p > 3).mean()) - 1e-3) < 4e-4  # -log10 p > 3  <=>  p < 1e-3
    assert float(np.nanmean(res.d2)) == pytest.approx(3.0, abs=0.05)  # E[chi2(3)] = 3


def test_robust_cva_detects_planted_change(rng):
    shape = (200, 200)
    before = rng.uniform(0.05, 0.4, size=(3, *shape)).astype(np.float32)
    after_norm = before + rng.normal(0, 0.01, size=before.shape).astype(np.float32)
    block = np.zeros(shape, bool)
    block[50:70, 80:110] = True
    after_norm[:, block] += 0.12  # a 12-sigma brightening
    zones = np.zeros(shape, np.int16)

    res = methods.robust_cva(before, after_norm, zones, ~block)

    assert (res.neglog10_p[block] > 5).all()
    assert float((res.neglog10_p[~block] > 3).mean()) < 3e-3
    assert res.delta[:, block].mean() == pytest.approx(0.12, abs=0.01)


def test_robust_cva_noise_model_is_per_zone(rng):
    shape = (200, 200)
    zones = np.zeros(shape, np.int16)
    zones[:, 100:] = 1
    sigma = np.where(zones == 0, 0.005, 0.03)  # zone 1 is six times noisier
    before = rng.uniform(0.1, 0.3, size=(3, *shape)).astype(np.float32)
    after_norm = before + (rng.normal(0, 1, size=before.shape) * sigma).astype(np.float32)
    res = methods.robust_cva(before, after_norm, zones, np.ones(shape, bool))
    # A global noise model would flag the noisy zone massively; per-zone covariance keeps both near nominal.
    for z in (0, 1):
        assert float((res.d2[zones == z] > stats.chi2.isf(1e-3, 3)).mean()) < 3e-3
    assert set(res.noise_cov) == {0, 1}
    assert np.trace(np.array(res.noise_cov[1][0])) > 20 * np.trace(np.array(res.noise_cov[0][0]))  # first brightness bin


def test_robust_cva_invalid_pixels_are_nan(rng):
    shape = (60, 60)
    zones = np.zeros(shape, np.int16)
    zones[:5] = -1
    before = rng.uniform(0.1, 0.3, size=(3, *shape)).astype(np.float32)
    after_norm = before + rng.normal(0, 0.01, size=before.shape).astype(np.float32)
    res = methods.robust_cva(before, after_norm, zones, zones >= 0)
    assert np.isnan(res.neglog10_p[:5]).all()
    assert np.isfinite(res.neglog10_p[5:]).all()


class TestEuclideanBaseline:
    @staticmethod
    def _example():
        spec = importlib.util.spec_from_file_location("example", methods.EXAMPLE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.compute_change_distance

    def test_matches_provided_example_on_fully_valid_input(self, rng):
        before = rng.uniform(0.05, 0.4, size=(3, 40, 50)).astype(np.float32)
        after = before + rng.normal(0, 0.03, size=before.shape).astype(np.float32)
        prob = methods.euclidean_baseline(before, after, np.ones((40, 50), bool))
        _, expected = self._example()(np.moveaxis(before, 0, -1), np.moveaxis(after, 0, -1))
        np.testing.assert_allclose(prob, expected, atol=1e-6)
        assert prob.min() == 0 and prob.max() == pytest.approx(1.0)

    def test_valid_pixels_keep_the_example_distance_ranking(self, rng):
        shape = (40, 50)
        before = rng.uniform(0.05, 0.4, size=(3, *shape)).astype(np.float32)
        after = before + rng.normal(0, 0.03, size=before.shape).astype(np.float32)
        valid = np.ones(shape, bool)
        valid[:, :8] = False
        before[:, ~valid] = np.nan
        after[:, ~valid] = np.nan
        prob = methods.euclidean_baseline(before, after, valid)

        assert np.isnan(prob[~valid]).all()
        assert np.isfinite(prob[valid]).all()
        assert prob[valid].min() >= 0 and prob[valid].max() <= 1
        dist = np.linalg.norm(after - before, axis=0)[valid]
        # Same Euclidean distance, only min-max rescaled: rank-identical and linear in the distance.
        assert np.corrcoef(prob[valid], dist)[0, 1] > 0.9999
        order = np.argsort(dist)
        assert (np.diff(prob[valid][order]) >= -1e-6).all()
