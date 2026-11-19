"""Radiometric handling: DN -> reflectance, detector-stripe zoning, relative normalization.

Why stripe zones exist
----------------------
Sentinel-2 MSI images the swath with 12 staggered detectors whose footprints are slanted
parallelograms. Odd and even detectors look through the atmosphere at slightly different
viewing angles, so under haze or smoke the path radiance differs between neighbouring
footprints. The image then shows straight, parallel brightness steps. In this AOI the
2023-09-02 scene has them clearly; the 2023-08-12 scene does not. A single global
radiometric relation between dates is therefore wrong, and every change method that assumes one
(image difference, Euclidean distance, global IR-MAD) paints whole stripes as "change".

We model the date-to-date radiometric relation as piecewise constant across stripes:
1. compute the per-pixel blue-band reflectance difference between dates (haze adds path
   radiance, which is additive and strongest in blue),
2. find the stripe orientation that best explains it (straight lines, angle search),
3. segment the 1-D profile across the stripes into zones (binary segmentation),
4. treat each zone independently in IR-MAD and in the relative normalization.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np

# Sentinel-2 L2A products from processing baseline 04.00 (Jan 2022) onward carry BOA_ADD_OFFSET = -1000.
L2A_OFFSET = 1000.0
QUANTIFICATION = 10000.0


def to_reflectance(dn: np.ndarray, valid: np.ndarray, offset: float = L2A_OFFSET) -> np.ndarray:
    """Convert L2A DN to surface reflectance (float32); invalid pixels become NaN."""
    refl = (dn.astype(np.float32) - offset) / QUANTIFICATION
    refl[:, ~valid] = np.nan
    return refl


def infer_offset(dn: np.ndarray, valid: np.ndarray) -> float:
    """Guess the BOA offset: with the +1000 offset, almost no valid DN falls below 1000."""
    frac_below = float(np.mean(dn[:, valid] < L2A_OFFSET))
    return L2A_OFFSET if frac_below < 0.001 else 0.0


@dataclass
class StripeZones:
    zones: np.ndarray  # int16 (H, W): zone id per pixel, -1 where invalid
    angle_deg: float  # orientation of the normal to the stripes (x*cos + y*sin = p)
    breakpoints: list[float]  # zone boundaries along p (pixels)
    zone_offsets: list[float]  # median after-minus-before reflectance per zone (diagnostic)
    explained_variance: float  # eta^2 of the 1-D profile model at the best angle
    profile: tuple[np.ndarray, np.ndarray] = field(repr=False)  # (bin centres, bin medians)


def _block_median(img: np.ndarray, block: int) -> np.ndarray:
    h, w = img.shape[0] // block, img.shape[1] // block
    blocks = img[: h * block, : w * block].reshape(h, block, w, block).transpose(0, 2, 1, 3)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN blocks are expected at the AOI edge
        return np.nanmedian(blocks.reshape(h, w, -1), axis=2)


def _profile_stats(p: np.ndarray, v: np.ndarray, bin_w: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    idx = np.floor((p - p.min()) / bin_w).astype(int)
    n = np.bincount(idx)
    s = np.bincount(idx, v)
    keep = n > 0
    means = np.zeros_like(s)
    means[keep] = s[keep] / n[keep]
    centres = p.min() + (np.arange(len(n)) + 0.5) * bin_w
    return centres[keep], means[keep], n[keep]


def _eta2(p: np.ndarray, v: np.ndarray, bin_w: float) -> float:
    _, m, n = _profile_stats(p, v, bin_w)
    total = np.var(v) * len(v)
    return float(np.sum(n * (m - v.mean()) ** 2) / total) if total > 0 else 0.0


def _binary_segmentation(y: np.ndarray, w: np.ndarray, min_size: int, min_step: float, max_breaks: int) -> list[int]:
    """Weighted L2 binary segmentation of a 1-D profile; returns sorted break indices."""

    def best_split(lo: int, hi: int) -> tuple[float, int] | None:
        ys, ws = y[lo:hi], w[lo:hi]
        if hi - lo < 2 * min_size:
            return None
        cw, cwy, cwy2 = np.cumsum(ws), np.cumsum(ws * ys), np.cumsum(ws * ys**2)
        tw, twy, twy2 = cw[-1], cwy[-1], cwy2[-1]
        ks = np.arange(min_size, hi - lo - min_size + 1)
        lw, lwy, lwy2 = cw[ks - 1], cwy[ks - 1], cwy2[ks - 1]
        rw, rwy, rwy2 = tw - lw, twy - lwy, twy2 - lwy2
        sse = (lwy2 - lwy**2 / lw) + (rwy2 - rwy**2 / rw)
        k = int(np.argmin(sse))
        step = abs(lwy[k] / lw[k] - rwy[k] / rw[k])
        if step < min_step:
            return None
        gain = (twy2 - twy**2 / tw) - sse[k]
        return gain, lo + int(ks[k])

    segments, breaks = [(0, len(y))], []
    while len(breaks) < max_breaks:
        cands = [(s, best_split(*s)) for s in segments]
        cands = [(s, c) for s, c in cands if c is not None]
        if not cands:
            break
        (lo, hi), (_, k) = max(cands, key=lambda sc: sc[1][0])
        segments.remove((lo, hi))
        segments += [(lo, k), (k, hi)]
        breaks.append(k)
    return sorted(breaks)


def detect_stripe_zones(
    refl_before: np.ndarray,
    refl_after: np.ndarray,
    valid: np.ndarray,
    band: int = 0,
    block: int = 8,
    min_zone_px: int = 40,
    min_step: float = 0.006,
    max_zones: int = 6,
    min_explained: float = 0.05,
) -> StripeZones:
    """Find straight, parallel radiometric steps between two dates (detector footprint stripes).

    ``band`` indexes the band used for zoning (0 = B02 blue, most sensitive to haze).
    ``min_step`` is the smallest reflectance step accepted as a zone boundary.
    If the best orientation explains less than ``min_explained`` of the difference variance, a
    single zone is returned (no striping), so clean scene pairs pass through unchanged.
    """
    diff = refl_after[band] - refl_before[band]
    diff[~valid] = np.nan
    blk = _block_median(diff, block)
    yy, xx = np.mgrid[0 : blk.shape[0], 0 : blk.shape[1]]
    ok = ~np.isnan(blk)
    x, y, v = (xx[ok] + 0.5) * block, (yy[ok] + 0.5) * block, blk[ok]
    # Changed surfaces are outliers in the difference; clip them so they do not drive the angle or the steps.
    lo, hi = np.percentile(v, [5, 95])
    v = np.clip(v, lo, hi)

    def search(angles: np.ndarray) -> tuple[float, float]:
        scores = [_eta2(x * np.cos(t) + y * np.sin(t), v, block) for t in angles]
        i = int(np.argmax(scores))
        return float(angles[i]), float(scores[i])

    theta, _ = search(np.deg2rad(np.arange(-90, 90, 1.0)))
    theta, eta2 = search(theta + np.deg2rad(np.arange(-1, 1.01, 0.05)))

    H, W = valid.shape
    if eta2 < min_explained:
        zones = np.where(valid, 0, -1).astype(np.int16)
        return StripeZones(zones, float(np.rad2deg(theta)), [], [float(np.nanmedian(diff))], eta2, (np.array([]), np.array([])))

    p = x * np.cos(theta) + y * np.sin(theta)
    centres, means, counts = _profile_stats(p, v, block)
    k_breaks = _binary_segmentation(
        means, counts.astype(float), min_size=max(1, min_zone_px // block), min_step=min_step, max_breaks=max_zones - 1
    )
    breakpoints = [float((centres[k - 1] + centres[k]) / 2) for k in k_breaks]

    Y, X = np.mgrid[0:H, 0:W]
    p_full = (X + 0.5) * np.cos(theta) + (Y + 0.5) * np.sin(theta)
    zones = np.digitize(p_full, breakpoints).astype(np.int16)
    zones[~valid] = -1
    offsets = [float(np.nanmedian(diff[zones == z])) for z in range(len(breakpoints) + 1)]
    return StripeZones(zones, float(np.rad2deg(theta)), breakpoints, offsets, eta2, (centres, means))


@dataclass
class ZoneCalibration:
    zone: int
    band: str
    slope: float
    intercept: float
    n_pixels: int
    invariant_fraction: float


def _tls(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Orthogonal (total least squares) fit y = intercept + slope * x."""
    evals, evecs = np.linalg.eigh(np.cov(np.vstack([x, y])))
    vx, vy = evecs[:, np.argmax(evals)]
    slope = float(vy / vx)
    return float(y.mean() - slope * x.mean()), slope


def _robust_z(e: np.ndarray, ref: np.ndarray) -> np.ndarray:
    med = np.median(e[ref])
    mad = 1.4826 * np.median(np.abs(e[ref] - med))
    return np.abs(e - med) / max(mad, 1e-9)


def relative_normalization(
    refl_before: np.ndarray,
    refl_after: np.ndarray,
    zones: np.ndarray,
    band_names: tuple[str, ...],
    n_iter: int = 5,
    reject_z: float = 2.5,
) -> tuple[np.ndarray, np.ndarray, list[ZoneCalibration]]:
    """Map the after-image onto the before-image radiometry, zone by zone.

    Per zone, every band gets a linear model before = a + b * after, fitted by orthogonal
    regression (both dates are noisy, so ordinary least squares would bias the slope). The fit is
    iterated: pixels whose residual exceeds ``reject_z`` robust sigmas in any band are dropped and
    the lines refitted. The survivors are the pseudo-invariant features (PIFs); changed pixels
    end up as outliers and do not bias the calibration.

    Returns (normalized after-image, PIF mask, calibration table).
    """
    out = np.full_like(refl_after, np.nan)
    pif = np.zeros(zones.shape, bool)
    cals: list[ZoneCalibration] = []
    for z in np.unique(zones[zones >= 0]):
        m = zones == z
        x, y = refl_after[:, m], refl_before[:, m]
        inl = np.ones(x.shape[1], bool)
        for _ in range(n_iter):
            coefs = [_tls(x[b, inl], y[b, inl]) for b in range(len(band_names))]
            resid = np.stack([y[b] - (c0 + c1 * x[b]) for b, (c0, c1) in enumerate(coefs)])
            inl = np.max([_robust_z(r, inl) for r in resid], axis=0) < reject_z
        out[:, m] = np.stack([c0 + c1 * x[b] for b, (c0, c1) in enumerate(coefs)])
        pif[m] = inl
        cals += [
            ZoneCalibration(int(z), name, c1, c0, int(m.sum()), float(inl.mean()))
            for name, (c0, c1) in zip(band_names, coefs, strict=True)
        ]
    return out, pif, cals


def seam_distance(shape: tuple[int, int], angle_deg: float, breakpoints: list[float]) -> np.ndarray:
    """Distance in pixels from every pixel centre to the nearest stripe boundary (inf if none)."""
    if not breakpoints:
        return np.full(shape, np.inf, np.float32)
    t = np.deg2rad(angle_deg)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    p = (xx + 0.5) * np.cos(t) + (yy + 0.5) * np.sin(t)
    return np.min([np.abs(p - b) for b in breakpoints], axis=0).astype(np.float32)
