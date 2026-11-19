"""Change detection methods.

* ``euclidean_baseline`` runs the provided example algorithm unchanged (imported from ``inputs/``).
* ``robust_cva`` (main method) is change vector analysis on radiometrically normalized
  reflectance, scored by a Mahalanobis distance whose noise model is learned from
  pseudo-invariant pixels of each stripe zone.
* ``irmad`` is iteratively reweighted multivariate alteration detection (Nielsen 2007), run
  independently per radiometric zone. It is kept as a comparison: see report.md for why it
  over-detects on this 3-band, haze-affected pair.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import linalg, stats

EXAMPLE_PATH = Path(__file__).resolve().parents[2] / "inputs" / "example_change_detection.py"


def euclidean_baseline(before: np.ndarray, after: np.ndarray, valid: np.ndarray, example_path: Path = EXAMPLE_PATH) -> np.ndarray:
    """Apply the provided ``compute_change_distance`` to (bands, H, W) arrays; return probability in [0, 1].

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


@dataclass
class CvaResult:
    d2: np.ndarray  # (H, W) squared Mahalanobis distance of the change vector; NaN where invalid
    p_change: np.ndarray  # (H, W) chi2(df=bands) CDF of d2
    delta: np.ndarray  # (bands, H, W) normalized after minus before reflectance
    noise_cov: dict[int, list[list[float]]]  # per-zone covariance of PIF change vectors


def robust_cva(before: np.ndarray, after_norm: np.ndarray, zones: np.ndarray, pif: np.ndarray) -> CvaResult:
    """Change vector analysis with a per-zone noise model.

    ``delta = after_norm - before``. In each zone, the PIF change vectors give the location and
    covariance of "no change" (sensor noise, residual misregistration, small phenology). The
    squared Mahalanobis distance d2 of every pixel's change vector from that distribution is
    chi2-distributed with ``bands`` degrees of freedom when nothing changed, so
    ``p_change = chi2.cdf(d2)`` is a per-pixel change probability with a known false-alarm rate.
    """
    nb = before.shape[0]
    delta = (after_norm - before).astype(np.float32)
    d2 = np.full(zones.shape, np.nan, np.float32)
    covs = {}
    for z in np.unique(zones[zones >= 0]):
        m = zones == z
        ref = delta[:, m & pif]
        mu = np.median(ref, axis=1)
        cov = np.cov(ref)
        dz = delta[:, m] - mu[:, None]
        d2[m] = np.einsum("in,ij,jn->n", dz, np.linalg.inv(cov), dz)
        covs[int(z)] = cov.tolist()
    p = stats.chi2.cdf(d2, nb).astype(np.float32)
    p[zones < 0] = np.nan
    return CvaResult(d2, p, delta, covs)


@dataclass
class IrmadResult:
    chi2: np.ndarray  # (H, W) sum of squared standardized MAD variates; NaN where invalid
    p_change: np.ndarray  # (H, W) chi2 CDF = probability the pixel changed
    mad: np.ndarray  # (bands, H, W) MAD variates, ordered from most to least correlated pair
    rho: dict[int, list[float]]  # canonical correlations per zone
    iterations: dict[int, int]


def _irmad_samples(x: np.ndarray, y: np.ndarray, max_iter: int, tol: float) -> tuple[np.ndarray, np.ndarray, list[float], int]:
    """IR-MAD on (n, p) sample matrices. Returns (MAD variates (n, p), chi2 (n,), rho, iterations)."""
    n, p = x.shape
    w = np.ones(n)
    rho_old = np.zeros(p)
    for it in range(1, max_iter + 1):  # noqa: B007 (returned as the iteration count)
        ws = w / w.sum()
        mx, my = ws @ x, ws @ y
        xc, yc = x - mx, y - my
        sxx = (xc * ws[:, None]).T @ xc
        syy = (yc * ws[:, None]).T @ yc
        sxy = (xc * ws[:, None]).T @ yc
        # Generalized eigenproblem: Sxy Syy^-1 Syx a = rho^2 Sxx a
        lam, a = linalg.eigh(sxy @ linalg.solve(syy, sxy.T), sxx)
        order = np.argsort(lam)[::-1]  # most correlated first
        lam, a = lam[order], a[:, order]
        rho = np.sqrt(np.clip(lam, 0, 1))
        b = linalg.solve(syy, sxy.T @ a) / np.where(rho > 0, rho, 1)
        b /= np.sqrt(np.einsum("ij,ik,kj->j", b, syy, b))  # unit variance V
        a /= np.sqrt(np.einsum("ij,ik,kj->j", a, sxx, a))  # unit variance U
        sign = np.sign(np.einsum("ij,ik,kj->j", a, sxy, b))
        b *= sign  # positive correlation between U_i and V_i
        mad = xc @ a - yc @ b
        var_mad = np.clip(2 * (1 - rho), 1e-12, None)
        chi2 = np.sum(mad**2 / var_mad, axis=1)
        w = 1 - stats.chi2.cdf(chi2, p)  # probability of no change -> weight for the next iteration
        if np.max(np.abs(rho - rho_old)) < tol:
            break
        rho_old = rho
    return mad, chi2, rho.tolist(), it


def irmad(
    before: np.ndarray,
    after: np.ndarray,
    valid: np.ndarray,
    zones: np.ndarray | None = None,
    max_iter: int = 50,
    tol: float = 1e-5,
    min_zone_pixels: int = 5000,
) -> IrmadResult:
    """Zone-wise IR-MAD.

    IR-MAD is invariant to any affine radiometric transform of either image (gain/offset from
    illumination, atmosphere, sensor calibration). Running it per stripe zone makes it invariant
    to a different gain/offset in every detector footprint. Zones too small for stable
    covariance estimates are merged into the largest zone.
    """
    nb, H, W = before.shape
    zones = np.where(valid, 0, -1) if zones is None else np.where(valid, zones, -1)
    sizes = {int(z): int((zones == z).sum()) for z in np.unique(zones[zones >= 0])}
    largest = max(sizes, key=sizes.get)
    for z, n in sizes.items():
        if n < min_zone_pixels:
            zones = np.where(zones == z, largest, zones)

    chi2 = np.full((H, W), np.nan, np.float32)
    mad = np.full((nb, H, W), np.nan, np.float32)
    rho_by_zone, iters = {}, {}
    for z in np.unique(zones[zones >= 0]):
        m = zones == z
        x = before[:, m].T.astype(np.float64)
        y = after[:, m].T.astype(np.float64)
        mz, cz, rz, it = _irmad_samples(x, y, max_iter, tol)
        chi2[m] = cz
        mad[:, m] = mz.T
        rho_by_zone[int(z)] = rz
        iters[int(z)] = it
    p_change = stats.chi2.cdf(chi2, nb).astype(np.float32)
    p_change[~valid] = np.nan
    return IrmadResult(chi2, p_change, mad, rho_by_zone, iters)
