"""Change detection methods.

* ``euclidean_baseline`` runs the provided example algorithm unchanged (imported from ``inputs/``).
* ``robust_cva`` (main method) is change vector analysis (CVA; Malila 1980) on radiometrically normalized reflectance,
  scored by a Mahalanobis distance whose noise model is learned from no-change pixels, separately for every radiometric zone
  and every brightness stratum of the before image. The model is checked on held-out no-change pixels (``NoiseRules``).

All "false-alarm rates" here are *nominal per-pixel rates under the chi-square model*: they hold exactly only if the noise
is Gaussian with the fitted covariance. ``robust_cva`` measures the realised rate on held-out pixels and reports it.
"""

from __future__ import annotations

import importlib.util
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import stats

EXAMPLE_PATH = Path(__file__).resolve().parents[2] / "inputs" / "example_change_detection.py"


def euclidean_baseline(before: np.ndarray, after: np.ndarray, valid: np.ndarray, example_path: Path = EXAMPLE_PATH) -> np.ndarray:
    """Apply the provided ``compute_change_distance`` to (bands, H, W) arrays; return its distance score in [0, 1].

    The score is min-max scaled; the example calls it a probability, but it is not one.

    The example expects (H, W, bands) and min-max normalizes over the whole array, so invalid
    pixels are filled with the valid-pixel median first (otherwise nodata zeros would dominate
    the min-max range) and set to NaN afterwards.
    """
    spec = importlib.util.spec_from_file_location("example_change_detection", example_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    b = np.moveaxis(before, 0, -1).copy()
    a = np.moveaxis(after, 0, -1).copy()
    for img in (a, b):
        img[~valid] = np.nanmedian(img[valid], axis=0)
    _, prob = module.compute_change_distance(b, a)
    prob = prob.astype(np.float32)
    prob[~valid] = np.nan
    return prob


SIGNIFICANCE_CAP = 50.0  # -log10 p is capped here: p < 1e-50 is "certainly changed" and the tail is numerically meaningless


def neglog10_p(stat: np.ndarray, dof: int) -> np.ndarray:
    """-log10 of the no-change p-value P(chi2(dof) >= stat), via the log survival function (exact far into the tail)."""
    with np.errstate(invalid="ignore"):
        out = -stats.chi2.logsf(stat, dof) / math.log(10)
    return np.clip(out, 0, SIGNIFICANCE_CAP).astype(np.float32)


@dataclass(frozen=True)
class NoiseRules:
    """How the CVA noise model is stratified and validated."""

    n_bins: int = 6  # brightness strata of the before image per zone (quantile bins)
    min_cell_px: int = 500  # fewer fitting pixels than this per (zone, bin): use fewer bins
    holdout_fraction: float = 0.3  # share of no-change pixels kept out of the fit, to measure the realised exceedance
    holdout_block_px: int = 32  # held out in blocks: neighbouring pixels are correlated (lag-1 about 0.65)
    tolerance: float = 1.5  # realised/nominal beyond this factor (and beyond binomial noise) -> empirical threshold
    seed: int = 0


@dataclass
class CvaResult:
    d2: np.ndarray  # (H, W) decision statistic: squared Mahalanobis distance, nominal-threshold comparable; NaN where invalid
    neglog10_p: np.ndarray  # (H, W) -log10 of the no-change p-value chi2.sf(d2, bands), capped at SIGNIFICANCE_CAP
    delta: np.ndarray  # (bands, H, W) normalized after minus before reflectance
    bin: np.ndarray  # (H, W) int8 brightness stratum of the pixel within its zone, -1 where invalid
    noise_cov: dict[int, list[list[list[float]]]]  # per zone, per brightness bin: covariance of the no-change change vectors
    calibration: dict | None  # noise_calibration block for run_summary.json (None if the data were too few to validate)
    d2_model: np.ndarray  # d2 before the empirical rescaling (the brightness-aware model at nominal chi-square thresholds)
    d2_previous: np.ndarray  # d2 of the previous design: one covariance per zone from 2.5-sigma-trimmed PIFs (for comparison)


def _fit_zone(dz: np.ndarray, bright: np.ndarray, fit: np.ndarray, n_bins: int, min_cell: int):
    """Per brightness stratum of one zone: bin edges, median and covariance of the fitting pixels' change vectors."""
    n_eff = int(np.clip(fit.sum() // min_cell, 1, n_bins))
    edges = np.quantile(bright[fit], np.linspace(0, 1, n_eff + 1)[1:-1])
    cell = np.digitize(bright, edges)
    mu = np.zeros((n_eff, dz.shape[0]))
    cov = np.zeros((n_eff, dz.shape[0], dz.shape[0]))
    for k in range(n_eff):
        ref = dz[:, fit & (cell == k)]
        mu[k], cov[k] = np.median(ref, axis=1), np.cov(ref)
    return cell, mu, cov


def _mahalanobis(dz: np.ndarray, cell: np.ndarray, mu: np.ndarray, cov: np.ndarray) -> np.ndarray:
    d2 = np.empty(dz.shape[1], np.float32)
    for k in range(len(mu)):
        sel = cell == k
        r = dz[:, sel] - mu[k][:, None]
        d2[sel] = np.einsum("in,ij,jn->n", r, np.linalg.inv(cov[k]), r)
    return d2


def _holdout_blocks(shape: tuple[int, int], rules: NoiseRules) -> tuple[np.ndarray, np.ndarray]:
    """Random blocks of the image, split into two folds that together hold out ``holdout_fraction`` of it."""
    b = rules.holdout_block_px
    rows, cols = -(-shape[0] // b), -(-shape[1] // b)
    u = np.random.default_rng(rules.seed).random((rows, cols))
    expand = lambda blocks: np.kron(blocks, np.ones((b, b), bool))[: shape[0], : shape[1]]  # noqa: E731
    half = rules.holdout_fraction / 2
    return expand(u < half), expand((u >= half) & (u < 2 * half))


def robust_cva(
    before: np.ndarray,
    after_norm: np.ndarray,
    zones: np.ndarray,
    pif: np.ndarray,
    noise_pif: np.ndarray | None = None,
    far: float = 1e-3,
    rules: NoiseRules = NoiseRules(),
) -> CvaResult:
    """Change vector analysis with a brightness-aware, per-zone noise model that is validated on held-out pixels.

    ``delta = after_norm - before``. In each zone and each brightness stratum of the before image, the change vectors of
    no-change pixels give the location and covariance of "no change" (sensor noise, residual misregistration, small
    phenology). The squared Mahalanobis distance d2 of a pixel's change vector from its stratum's distribution is
    chi2(bands) distributed *if* the noise is Gaussian, so ``far`` is a nominal per-pixel false-alarm rate.
    It is not exactly that: radiometric noise grows with brightness (hence the strata), pixels are spatially correlated and
    tails are heavier than Gaussian. So the model is fitted on a ``1 - holdout_fraction`` share of the no-change pixels and the
    realised exceedance of the nominal threshold is measured per stratum on the held-out rest, for this model and for the
    previous design (one model per zone, fitted on the tightly trimmed ``pif``). If any stratum is still off by more than
    ``rules.tolerance`` (and by more than binomial noise), the threshold of that stratum is set to the empirical
    ``1 - far`` quantile of its held-out d2; ``d2`` is rescaled by nominal/empirical so that the nominal chi-square
    thresholds apply to it unchanged. Both thresholds are reported in ``calibration``.

    ``noise_pif`` (default: ``pif``) is the no-change set for the fit; pass a set whose tails are not trimmed
    (``radiometry.Normalization.noise_pif``) so the covariance is not shrunk by the selection itself.
    """
    nb = before.shape[0]
    noise_pif = pif if noise_pif is None else noise_pif
    delta = (after_norm - before).astype(np.float32)
    bright = before.mean(axis=0)
    fold_a, fold_b = _holdout_blocks(zones.shape, rules)
    held = fold_a | fold_b

    d2 = np.full(zones.shape, np.nan, np.float32)
    d2_old = np.full(zones.shape, np.nan, np.float32)  # previous design, for the before/after comparison
    stratum = np.full(zones.shape, -1, np.int8)
    covs = {}
    for z in np.unique(zones[zones >= 0]):
        m = zones == z
        dz, bz = delta[:, m], bright[m]
        cell, mu, cov = _fit_zone(dz, bz, (noise_pif & ~held)[m], rules.n_bins, rules.min_cell_px)
        d2[m] = _mahalanobis(dz, cell, mu, cov)
        stratum[m] = cell
        covs[int(z)] = cov.tolist()
        zero = np.zeros(m.sum(), int)
        _, mu1, cov1 = _fit_zone(dz, bz, (pif & ~held)[m], 1, rules.min_cell_px)
        d2_old[m] = _mahalanobis(dz, zero, mu1, cov1)

    d2_final, calibration = _calibrate(d2, d2_old, stratum, bright, noise_pif & held, (fold_a, fold_b), nb, far, rules)
    return CvaResult(d2_final, neglog10_p(d2_final, nb), delta, stratum, covs, calibration, d2, d2_old)


def _calibrate(d2_model, d2_old, stratum, bright, test, folds, nb, far, rules):
    """Realised exceedance per brightness stratum on held-out no-change pixels; rescale d2 if the model is still off.

    Returns (final decision statistic, calibration summary or None when no stratum has enough held-out pixels).
    """
    thr = float(stats.chi2.isf(far, nb))
    min_n = 20 / far  # fewer held-out pixels than this cannot resolve the 1 - far quantile
    rows = []
    for k in range(int(stratum.max()) + 1):
        sel = test & (stratum == k)
        row = {"stratum": k, "n_holdout": int(sel.sum()), "nominal_far": far, "d2_threshold_nominal": round(thr, 2)}
        if row["n_holdout"] >= min_n:
            n, r_model = row["n_holdout"], float((d2_model[sel] > thr).mean())
            noise = 3 * math.sqrt(far * (1 - far) / n)  # binomial noise of the realised rate
            row.update(
                brightness_before_mean=round(float(bright[sel].mean()), 4),
                realised_far_previous_design=round(float((d2_old[sel] > thr).mean()), 5),
                realised_far_model=round(r_model, 5),
                d2_threshold_empirical=round(float(np.quantile(d2_model[sel], 1 - far)), 2),
                off_nominal=bool(
                    (r_model > far * rules.tolerance or r_model < far / rules.tolerance) and abs(r_model - far) > noise
                ),
            )
        else:
            row["resolved"] = False
        rows.append(row)
    resolved = [r for r in rows if "realised_far_model" in r]
    if not resolved:
        return d2_model, None
    empirical = any(r["off_nominal"] for r in resolved)

    def rescale(d2: np.ndarray, quantile_of: np.ndarray | None) -> np.ndarray:
        """d2 * nominal/empirical per resolved stratum, the empirical threshold taken from the pixels in ``quantile_of``."""
        out = d2.copy()
        for r in resolved:
            k = r["stratum"]
            out[stratum == k] *= thr / np.quantile(d2_model[quantile_of & (stratum == k)], 1 - far)
        return out

    final = rescale(d2_model, test) if empirical else d2_model
    # Out-of-sample check of the final thresholds: threshold from one held-out fold, exceedance on the other fold.
    realised, per_stratum = [], {r["stratum"]: [] for r in resolved}
    for fit_fold, eval_fold in (folds, folds[::-1]):
        d2_cv = rescale(d2_model, test & fit_fold) if empirical else d2_model
        for r in resolved:
            sel = test & eval_fold & (stratum == r["stratum"])
            per_stratum[r["stratum"]].append(float((d2_cv[sel] > thr).mean()))
        realised.append(float((d2_cv[test & eval_fold & (stratum >= 0)] > thr).mean()))
    for r in resolved:
        r["realised_far_final"] = round(float(np.mean(per_stratum[r["stratum"]])), 5)
        r["d2_threshold_used"] = r["d2_threshold_empirical"] if empirical else r["d2_threshold_nominal"]
    all_test = test & (stratum >= 0)
    return final, {
        "far": far,
        "holdout": {"fraction": rules.holdout_fraction, "block_px": rules.holdout_block_px, "n_pixels": int(all_test.sum())},
        "strata": rows,
        "realised_far_previous_design": round(float((d2_old[all_test] > thr).mean()), 5),
        "realised_far_model": round(float((d2_model[all_test] > thr).mean()), 5),
        "thresholds_source": "empirical held-out quantiles" if empirical else "nominal chi-square",
        "realised_far_final": round(float(np.mean(realised)), 5),
    }
