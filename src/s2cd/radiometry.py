"""Radiometric handling: DN -> reflectance, radiometric zoning, relative normalization, registration check.

Why zones exist
Image "dates" are often mosaics. In this AOI each provided date combines two Sentinel-2 scenes
from adjacent orbits, seamed along a slanted swath edge (verified pixel-exact against Planetary
Computer STAC by ``s2cd provenance``). Detector-footprint striping under haze produces the same
geometry. Either way the before/after radiometric relation changes in straight, parallel steps,
so a single global relation is wrong, and every method that assumes one (image difference,
Euclidean distance) paints whole zones as "change". The ``stripe`` names below
refer to this geometry, whatever causes it.

We model the date-to-date radiometric relation as piecewise constant across zones. Zones come from one of two sources:

* **provenance** (preferred): when ``s2cd provenance`` has identified the source scene of every pixel, a zone is one
  unique (before-source, after-source) pair (``zones_from_sources``) and the seam is the exact boundary between zones;
* **data-driven** (fallback, also run for validation): ``detect_stripe_zones``
  1. computes the per-pixel blue-band reflectance difference between dates (haze adds path radiance,
     which is additive and strongest in blue),
  2. finds the stripe orientation that best explains it (straight lines, angle search),
  3. segments the 1-D profile across the stripes into zones (binary segmentation).

Each zone is then treated independently in the relative normalization and in the noise model.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi
from skimage.filters import difference_of_gaussians
from skimage.registration import phase_cross_correlation

log = logging.getLogger("s2cd")

# Sentinel-2 L2A products from processing baseline 04.00 (Jan 2022) onward carry BOA_ADD_OFFSET = -1000.
L2A_OFFSET = 1000.0
QUANTIFICATION = 10000.0


def to_reflectance(dn: np.ndarray, valid: np.ndarray, offset: float = L2A_OFFSET) -> np.ndarray:
    """Convert L2A DN to surface reflectance (float32); invalid pixels become NaN."""
    refl = (dn.astype(np.float32) - offset) / QUANTIFICATION
    refl[:, ~valid] = np.nan
    return refl


def infer_offset(dn: np.ndarray, valid: np.ndarray, dark_fraction: float = 0.01) -> float:
    """Decide the BOA offset (+1000 for processing baseline >= 04.00, else 0) from the darkest pixels.

    A scene without the offset has dark targets (water, shadow, dense forest) near DN 0-300; with the +1000 offset the same
    targets sit near 1000 (and any DN below 1000 is just noise around zero reflectance). The median DN of the darkest
    ``dark_fraction`` of all valid band values is therefore far above 900 with the offset and below 500 without it.
    In between the data cannot tell, so the function warns and falls back to ``L2A_OFFSET`` (the value for all scenes
    processed since January 2022).
    """
    v = dn[:, valid].ravel()
    k = max(1, int(dark_fraction * v.size))
    dark = float(np.median(np.partition(v, k - 1)[:k]))
    if dark > 900:
        return L2A_OFFSET
    if dark < 500:
        return 0.0
    log.warning(
        "Ambiguous BOA offset (median of the darkest %.0f%% is DN %.0f); assuming %d", 100 * dark_fraction, dark, L2A_OFFSET
    )
    return L2A_OFFSET


@dataclass
class StripeZones:
    zones: np.ndarray  # int16 (H, W): zone id per pixel, -1 where invalid
    angle_deg: float  # orientation of the normal to the stripes (x*cos + y*sin = p)
    breakpoints: list[float]  # zone boundaries along p (pixels)
    zone_offsets: list[float]  # median after-minus-before reflectance per zone (diagnostic)
    explained_variance: float  # eta^2 of the 1-D profile model at the best angle


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
        return StripeZones(zones, float(np.rad2deg(theta)), [], [float(np.nanmedian(diff))], eta2)

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
    return StripeZones(zones, float(np.rad2deg(theta)), breakpoints, offsets, eta2)


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


@dataclass
class Normalization:
    """Result of ``relative_normalization``."""

    normalized: np.ndarray  # after-image mapped onto the before-image radiometry (bands, H, W)
    pif: np.ndarray  # pseudo-invariant pixels used to fit the calibration lines (tightly trimmed)
    noise_pif: np.ndarray  # looser no-change set for the noise model: its tails are not trimmed (superset of ``pif``)
    calibration: list[ZoneCalibration]


def relative_normalization(
    refl_before: np.ndarray,
    refl_after: np.ndarray,
    zones: np.ndarray,
    band_names: tuple[str, ...],
    n_iter: int = 5,
    reject_z: float = 2.5,
    noise_reject_z: float = 4.0,
) -> Normalization:
    """Map the after-image onto the before-image radiometry, zone by zone.

    Adapted from the iterative orthogonal-regression normalization of Canty & Nielsen (2008, Remote Sensing of Environment
    112:1025). The no-change pixels are the ones that survive the residual trimming below, so no canonical-correlation
    step is needed.

    Per zone, every band gets a linear model before = a + b * after, fitted by orthogonal
    regression (both dates are noisy, so ordinary least squares would bias the slope). The fit is
    iterated: pixels whose residual exceeds ``reject_z`` robust sigmas in any band are dropped and
    the lines refitted. The survivors are the pseudo-invariant features (PIFs); changed pixels
    end up as outliers and do not bias the calibration.

    Trimming at ``reject_z`` is right for a regression but wrong for estimating the noise covariance of the change vector:
    cutting every band at 2.5 sigma shrinks the fitted covariance, so the chi-square test over-detects. ``noise_pif``
    applies the final fit with the much looser ``noise_reject_z`` (4 sigma), which only drops gross outliers, so the
    truncation of the residual tails is negligible.
    """
    out = np.full_like(refl_after, np.nan)
    pif = np.zeros(zones.shape, bool)
    noise_pif = np.zeros(zones.shape, bool)
    cals: list[ZoneCalibration] = []
    for z in np.unique(zones[zones >= 0]):
        m = zones == z
        x, y = refl_after[:, m], refl_before[:, m]
        inl = np.ones(x.shape[1], bool)
        for _ in range(n_iter):
            coefs = [_tls(x[b, inl], y[b, inl]) for b in range(len(band_names))]
            resid = np.stack([y[b] - (c0 + c1 * x[b]) for b, (c0, c1) in enumerate(coefs)])
            zmax = np.max([_robust_z(r, inl) for r in resid], axis=0)
            inl = zmax < reject_z
        out[:, m] = np.stack([c0 + c1 * x[b] for b, (c0, c1) in enumerate(coefs)])
        pif[m] = inl
        noise_pif[m] = zmax < noise_reject_z
        cals += [
            ZoneCalibration(int(z), name, c1, c0, int(m.sum()), float(inl.mean()))
            for name, (c0, c1) in zip(band_names, coefs, strict=True)
        ]
    return Normalization(out, pif, noise_pif, cals)


def seam_distance(shape: tuple[int, int], angle_deg: float, breakpoints: list[float]) -> np.ndarray:
    """Distance in pixels from every pixel centre to the nearest stripe boundary (inf if none)."""
    if not breakpoints:
        return np.full(shape, np.inf, np.float32)
    t = np.deg2rad(angle_deg)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    p = (xx + 0.5) * np.cos(t) + (yy + 0.5) * np.sin(t)
    return np.min([np.abs(p - b) for b in breakpoints], axis=0).astype(np.float32)


def zone_boundary_distance(zones: np.ndarray) -> np.ndarray:
    """Distance in pixels from every pixel to the nearest boundary between two zones (inf if there is none).

    Boundary pixels are valid pixels with a 4-neighbour in a different valid zone. Used for zones that come from
    source-scene pairs, whose seams the analytic ``seam_distance`` (straight lines) would only approximate.
    """
    edge = np.zeros(zones.shape, bool)
    horiz = (zones[:, :-1] != zones[:, 1:]) & (zones[:, :-1] >= 0) & (zones[:, 1:] >= 0)
    vert = (zones[:-1] != zones[1:]) & (zones[:-1] >= 0) & (zones[1:] >= 0)
    edge[:, :-1] |= horiz
    edge[:, 1:] |= horiz
    edge[:-1] |= vert
    edge[1:] |= vert
    if not edge.any():
        return np.full(zones.shape, np.inf, np.float32)
    return ndi.distance_transform_edt(~edge).astype(np.float32)


def zones_from_sources(source_scene: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Zones from the source scenes of both dates: one zone per unique (before source, after source) pair.

    ``source_scene`` is (2, H, W), the index of the source scene of each pixel on each date (-1 = unexplained, see
    ``provenance``). Within one pair the date-to-date radiometric relation is constant by construction (same two
    acquisitions everywhere), which is exactly the assumption of the per-zone models. Pixels no source explains take the zone
    of the nearest explained pixel. Zone ids are ordered west to east. Returns (int16 zone map, -1 outside ``valid``; the
    (before, after) source-index pair of each zone).
    """
    pair = np.where((source_scene >= 0).all(axis=0), source_scene[0].astype(np.int32) * 100 + source_scene[1], -1)
    keys = [k for k in np.unique(pair[valid & (pair >= 0)])]
    cols = np.arange(valid.shape[1])[None, :]
    keys.sort(key=lambda k: float(np.mean(np.broadcast_to(cols, valid.shape)[valid & (pair == k)])))
    zone_of = np.full(pair.shape, -1, np.int16)
    for i, k in enumerate(keys):
        zone_of[pair == k] = i
    known = valid & (zone_of >= 0)
    nearest = ndi.distance_transform_edt(~known, return_distances=False, return_indices=True)
    zones = zone_of[nearest[0], nearest[1]]
    zones[~valid] = -1
    return zones, [(int(k) // 100, int(k) % 100) for k in keys]


def zone_agreement(zones_a: np.ndarray, zones_b: np.ndarray) -> float:
    """Share of valid pixels whose ``zones_a`` zone lies in the same ``zones_b`` zone as most of that zone (1 = A refines B).

    For every zone of A the best-overlapping zone of B is taken; the score is the overlapped share. 1.0 means every A zone is
    contained in a single B zone, i.e. B's seams contain all of A's seams.
    """
    ok = (zones_a >= 0) & (zones_b >= 0)
    a, b = zones_a[ok].astype(int), zones_b[ok].astype(int)
    joint = np.zeros((a.max() + 1, b.max() + 1), np.int64) if a.size else np.zeros((1, 1), np.int64)
    np.add.at(joint, (a, b), 1)
    return round(float(joint.max(axis=1).sum() / max(a.size, 1)), 4)


def registration_shifts(
    before: np.ndarray,
    after_norm: np.ndarray,
    zones: np.ndarray,
    pif: np.ndarray,
    window: int = 48,
    n_windows: int = 8,
    min_pif: float = 0.9,
    upsample: int = 10,
) -> dict[int, dict]:
    """Residual misregistration between the dates, per zone, by phase correlation on invariant, textured windows.

    Per zone, square windows lying entirely inside the zone with at least ``min_pif`` pseudo-invariant pixels are ranked by
    texture (standard deviation of the band-passed brightness) and the best ``n_windows`` are registered with
    ``skimage.registration.phase_cross_correlation`` (subpixel, ``upsample`` = 10 gives 0.1 px). Reported per zone:
    the median shift ``(dy, dx)`` in pixels, its magnitude and the largest deviation of a single window from the median.
    ``shift_px`` is the translation to apply to the after-image to align it with the before-image (rows down, columns
    right). Values well below one pixel mean misregistration is not a driver of the detections.
    """

    def prepare(refl: np.ndarray) -> np.ndarray:  # brightness, band-passed so haze gradients do not dominate
        return difference_of_gaussians(np.nan_to_num(refl.mean(axis=0)), 1.0, 8.0)

    b_img, a_img = prepare(before), prepare(after_norm)
    out: dict[int, dict] = {}
    for z in np.unique(zones[zones >= 0]):
        inside, inv = zones == z, pif & (zones == z)
        cands = []
        for r in range(0, zones.shape[0] - window + 1, window // 2):
            for c in range(0, zones.shape[1] - window + 1, window // 2):
                sl = (slice(r, r + window), slice(c, c + window))
                if inside[sl].all() and inv[sl].mean() >= min_pif:
                    cands.append((float(b_img[sl].std()), sl))
        if not cands:
            out[int(z)] = {"shift_px": None, "n_windows": 0}
            continue
        best = sorted(cands, key=lambda t: t[0], reverse=True)[:n_windows]
        shifts = np.array([phase_cross_correlation(b_img[sl], a_img[sl], upsample_factor=upsample)[0] for _, sl in best])
        med = np.median(shifts, axis=0)
        out[int(z)] = {
            "shift_px": [round(float(v), 2) for v in med],
            "magnitude_px": round(float(np.hypot(*med)), 2),
            "max_window_deviation_px": round(float(np.abs(shifts - med).max()), 2),
            "n_windows": len(best),
        }
    return out
