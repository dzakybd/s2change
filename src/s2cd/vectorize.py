"""Turn change rasters into a clean binary mask and attributed polygons."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import rasterio.features
import shapely
from scipy import ndimage as ndi
from scipy import stats
from skimage import morphology
from skimage.filters import apply_hysteresis_threshold, threshold_otsu

from .io import Grid


@dataclass(frozen=True)
class BinaryRule:
    far_low: float = 1e-3  # per-pixel false-alarm rate for growing regions
    far_high: float = 1e-5  # stricter rate a region must reach somewhere to be kept (hysteresis seed)
    seam_buffer_px: float = 2.0  # drop pixels this close to a stripe boundary (overlap artefacts)
    opening_radius_px: int = 1  # removes 1-2 px lines and speckle (misregistration, field edges)
    mmu_px: int = 20  # minimum mapping unit (20 px = 0.2 ha at 10 m)
    fill_holes_px: int = 20

    def thresholds(self, dof: int) -> tuple[float, float]:
        return float(stats.chi2.isf(self.far_low, dof)), float(stats.chi2.isf(self.far_high, dof))


def clean_mask(mask: np.ndarray, rule: BinaryRule) -> np.ndarray:
    if rule.opening_radius_px > 0:
        mask = ndi.binary_opening(mask, morphology.disk(rule.opening_radius_px))
    mask = morphology.remove_small_objects(mask, max_size=rule.mmu_px - 1)
    return morphology.remove_small_holes(mask, max_size=rule.fill_holes_px)


def binary_from_d2(d2: np.ndarray, valid: np.ndarray, seam_dist: np.ndarray, dof: int, rule: BinaryRule) -> np.ndarray:
    """Hysteresis threshold on the chi2 statistic, then seam masking and morphological cleanup."""
    low, high = rule.thresholds(dof)
    score = np.where(valid & (seam_dist >= rule.seam_buffer_px), d2, 0)
    return clean_mask(apply_hysteresis_threshold(score, low, high), rule)


def binary_from_otsu(prob: np.ndarray, valid: np.ndarray, rule: BinaryRule) -> tuple[np.ndarray, float]:
    """Baseline thresholding: Otsu on the valid probability values, then the same cleanup."""
    t = float(threshold_otsu(prob[valid]))
    return clean_mask(valid & (prob > t), rule), t


def disturbed_footprint(refl_before: np.ndarray, valid: np.ndarray, buffer_px: int = 10) -> np.ndarray:
    """Heuristic mine/industrial footprint: bright bare surfaces at T1 (Otsu on mean RGB reflectance),
    closed, cleaned of features under 1 ha and buffered by ``buffer_px``.
    """
    bright = refl_before.mean(axis=0)  # NaN outside valid; comparisons there are False
    mask = valid & (bright > threshold_otsu(bright[valid]))
    mask = ndi.binary_closing(mask, morphology.disk(3))
    mask = morphology.remove_small_objects(mask, max_size=99)
    return ndi.binary_dilation(mask, morphology.disk(buffer_px)) & valid


def _label_means(labels: np.ndarray, n: int, values: np.ndarray) -> np.ndarray:
    m = labels > 0
    v = np.nan_to_num(values[m])
    return np.bincount(labels[m], v, minlength=n + 1)[1:] / np.bincount(labels[m], minlength=n + 1)[1:]


def polygonize(
    mask: np.ndarray,
    grid: Grid,
    layers: dict[str, np.ndarray],
    noise_sigma: float,
    strong_d2: float,
    low_d2: float,
) -> gpd.GeoDataFrame:
    """Vectorize connected change regions (8-connectivity) and attach per-region statistics.

    ``layers`` must hold ``d2``, ``p_change``, ``delta`` (bands, H, W), ``bright_before``,
    ``bright_after``, ``zone`` and ``disturbed``.
    """
    labels, n = ndi.label(mask, structure=np.ones((3, 3)))
    if n == 0:
        return gpd.GeoDataFrame(geometry=[], crs=grid.crs)

    geoms: dict[int, list] = {}
    for geom, val in rasterio.features.shapes(labels.astype(np.int32), mask=labels > 0, transform=grid.transform, connectivity=8):
        geoms.setdefault(int(val), []).append(shapely.geometry.shape(geom))

    d2 = layers["d2"]
    log_d2 = np.log10(np.clip(d2, 1e-6, None))
    # Magnitude score: 0 at the growing threshold, 1 at 100x that d2 (log scale), clipped.
    magnitude = np.clip((log_d2 - np.log10(low_d2)) / 2.0, 0, 1)
    delta = layers["delta"]
    d_bright = delta.mean(axis=0)
    cols = {
        "confidence": _label_means(labels, n, magnitude),
        "mean_p_change": _label_means(labels, n, layers["p_change"]),
        "strong_fraction": _label_means(labels, n, (d2 >= strong_d2).astype(np.float32)),
        "d_blue": _label_means(labels, n, delta[0]),
        "d_green": _label_means(labels, n, delta[1]),
        "d_red": _label_means(labels, n, delta[2]),
        "d_brightness": _label_means(labels, n, d_bright),
        "brightness_before": _label_means(labels, n, layers["bright_before"]),
        "brightness_after": _label_means(labels, n, layers["bright_after"]),
        "disturbed_fraction": _label_means(labels, n, layers["disturbed"].astype(np.float32)),
    }
    zone_mode = ndi.labeled_comprehension(layers["zone"], labels, np.arange(1, n + 1), lambda v: np.bincount(v).argmax(), int, -1)
    ids = np.arange(1, n + 1)
    gdf = gpd.GeoDataFrame(
        {"region": ids, **cols, "zone": zone_mode},
        geometry=[shapely.union_all(geoms[i]) if len(geoms[i]) > 1 else geoms[i][0] for i in ids],
        crs=grid.crs,
    )
    gdf["area_m2"] = gdf.geometry.area
    gdf["perimeter_m"] = gdf.geometry.length
    gdf["compactness"] = 4 * np.pi * gdf.area_m2 / gdf.perimeter_m**2
    gdf["change_direction"] = np.select(
        [gdf.d_brightness > 2 * noise_sigma, gdf.d_brightness < -2 * noise_sigma],
        ["brightening", "darkening"],
        "spectral shift",
    )
    gdf["in_disturbed_area"] = gdf.disturbed_fraction >= 0.5
    gdf = gdf.sort_values("area_m2", ascending=False).reset_index(drop=True)
    gdf["confidence"] = gdf.confidence.round(3)
    return gdf
