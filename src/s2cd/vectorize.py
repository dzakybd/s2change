"""Turn change rasters into a clean binary mask and attributed polygons.

Binary rule: hysteresis thresholding (Canny 1986: grow from strong seeds through weaker connected pixels) of the chi-square
change statistic, then seam masking, a morphological opening and a minimum mapping unit. The baseline uses Otsu's (1979)
histogram threshold instead. Polygons are the 8-connected regions of the mask, made valid and stored as MultiPolygons.
"""

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
    """The binary change rule. Sizes are in ha and metres so the rule does not depend on the pixel size (``*_px`` converts)."""

    far_low: float = 1e-3  # nominal per-pixel false-alarm rate (under the noise model) for growing regions
    far_high: float = 1e-5  # stricter nominal rate a region must reach somewhere to be kept (hysteresis seed)
    seam_buffer_m: float = 90.0  # ignore pixels this close to a source-scene / stripe boundary (see seam_buffer_px)
    opening_radius_m: float = 10.0  # removes 1-2 px lines and speckle (misregistration, field edges)
    mmu_ha: float = 0.2  # minimum mapping unit
    fill_holes_ha: float = 0.2

    def thresholds(self, dof: int) -> tuple[float, float]:
        return float(stats.chi2.isf(self.far_low, dof)), float(stats.chi2.isf(self.far_high, dof))

    def seam_buffer_px(self, pixel_m: float) -> float:
        """The single seam-buffer constant, in pixels; used by ``binary_from_d2`` and by ``interpret``."""
        return self.seam_buffer_m / pixel_m

    def mmu_px(self, pixel_m: float) -> int:
        return round(self.mmu_ha * 1e4 / pixel_m**2)

    def fill_holes_px(self, pixel_m: float) -> int:
        return round(self.fill_holes_ha * 1e4 / pixel_m**2)

    def opening_radius_px(self, pixel_m: float) -> int:
        return round(self.opening_radius_m / pixel_m)


def clean_mask(mask: np.ndarray, rule: BinaryRule, pixel_m: float) -> np.ndarray:
    """Opening, minimum mapping unit (8-connected, like the labelling used for the polygons) and hole filling."""
    if rule.opening_radius_px(pixel_m) > 0:
        mask = ndi.binary_opening(mask, morphology.disk(rule.opening_radius_px(pixel_m)))
    mask = morphology.remove_small_objects(mask, max_size=rule.mmu_px(pixel_m) - 1, connectivity=2)
    return morphology.remove_small_holes(mask, max_size=rule.fill_holes_px(pixel_m))


def binary_from_d2(
    d2: np.ndarray, valid: np.ndarray, seam_dist: np.ndarray, dof: int, rule: BinaryRule, pixel_m: float
) -> np.ndarray:
    """Hysteresis threshold on the chi-square statistic, then seam masking and morphological cleanup.

    ``seam_dist`` is the distance to the nearest zone boundary in pixels; pixels closer than ``rule.seam_buffer_px`` are never
    changes. Invalid pixels (NaN or outside ``valid``) are not changes either.
    """
    low, high = rule.thresholds(dof)
    score = np.where(valid & (seam_dist >= rule.seam_buffer_px(pixel_m)), d2, 0)
    return clean_mask(apply_hysteresis_threshold(score, low, high), rule, pixel_m)


def binary_from_otsu(prob: np.ndarray, valid: np.ndarray, rule: BinaryRule, pixel_m: float) -> tuple[np.ndarray, float]:
    """Baseline thresholding: Otsu (1979) on the valid values of the baseline score, then the same cleanup."""
    t = float(threshold_otsu(prob[valid]))
    return clean_mask(valid & (prob > t), rule, pixel_m), t


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


def as_valid_multipolygon(geom) -> shapely.MultiPolygon:
    """Repair ``geom`` (``shapely.make_valid``) and return it as a MultiPolygon, dropping any line or point remnants.

    Rings that pinch at a corner, which 8-connected polygonization produces, are self-touching and therefore invalid;
    ``make_valid`` splits them without changing the area.
    """
    fixed = shapely.make_valid(geom)
    return shapely.MultiPolygon(
        [p for part in shapely.get_parts(fixed) for p in shapely.get_parts(part) if p.geom_type == "Polygon"]
    )


def polygonize(
    mask: np.ndarray,
    grid: Grid,
    layers: dict[str, np.ndarray],
    noise_sigma: float,
    strong_d2: float,
    low_d2: float,
) -> gpd.GeoDataFrame:
    """Vectorize connected change regions (8-connectivity) and attach per-region statistics.

    ``layers`` must hold ``delta`` (bands, H, W), ``bright_before``, ``bright_after``, ``zone``, ``disturbed`` and ``valid``.
    The detection layers ``d2`` and ``neglog10_p`` are optional: without them (the baseline, whose score is not a chi-square
    statistic) ``confidence``, ``strong_fraction`` and ``neglog10_p`` are NaN rather than a made-up number.
    Geometries are valid MultiPolygons; ``area_m2`` is their area. ``touches_aoi_edge`` marks regions adjacent to an invalid
    pixel or the raster border, which may continue outside the AOI and so be under-measured.
    """
    labels, n = ndi.label(mask, structure=np.ones((3, 3)))
    if n == 0:
        return gpd.GeoDataFrame(geometry=[], crs=grid.crs)

    geoms: dict[int, list] = {}
    for geom, val in rasterio.features.shapes(labels.astype(np.int32), mask=labels > 0, transform=grid.transform, connectivity=8):
        geoms.setdefault(int(val), []).append(shapely.geometry.shape(geom))

    delta = layers["delta"]
    d_bright = delta.mean(axis=0)
    cols = {
        "d_blue": _label_means(labels, n, delta[0]),
        "d_green": _label_means(labels, n, delta[1]),
        "d_red": _label_means(labels, n, delta[2]),
        "d_brightness": _label_means(labels, n, d_bright),
        "brightness_before": _label_means(labels, n, layers["bright_before"]),
        "brightness_after": _label_means(labels, n, layers["bright_after"]),
        "disturbed_fraction": _label_means(labels, n, layers["disturbed"].astype(np.float32)),
    }
    if "d2" in layers:
        d2 = layers["d2"]
        # Magnitude score: 0 at the growing threshold, 1 at 100x that d2 (log scale), clipped.
        magnitude = np.clip((np.log10(np.clip(d2, 1e-6, None)) - np.log10(low_d2)) / 2.0, 0, 1)
        cols["confidence"] = _label_means(labels, n, magnitude)
        cols["neglog10_p"] = _label_means(labels, n, layers["neglog10_p"])
        cols["strong_fraction"] = _label_means(labels, n, (d2 >= strong_d2).astype(np.float32))
    else:
        cols.update(confidence=np.full(n, np.nan), neglog10_p=np.full(n, np.nan), strong_fraction=np.full(n, np.nan))
    ids = np.arange(1, n + 1)
    near_invalid = ndi.binary_dilation(~np.pad(layers["valid"], 1, constant_values=False), np.ones((3, 3), bool))[1:-1, 1:-1]
    cols["touches_aoi_edge"] = np.bincount(labels[near_invalid & (labels > 0)], minlength=n + 1)[1:] > 0
    zone_mode = ndi.labeled_comprehension(layers["zone"], labels, ids, lambda v: np.bincount(v).argmax(), int, -1)
    gdf = gpd.GeoDataFrame(
        {"region": ids, **cols, "zone": zone_mode},
        geometry=[as_valid_multipolygon(shapely.union_all(geoms[i]) if len(geoms[i]) > 1 else geoms[i][0]) for i in ids],
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
