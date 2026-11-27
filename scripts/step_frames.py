"""Render the report's step-by-step frames: the same place at every stage, from the two input images to typed polygons.

    uv run python scripts/step_frames.py        # after `make run`; reads data/processed, writes docs/figures/steps/

Two views per step, each a set of same-sized JPEGs so the app's stepper can swipe between consecutive steps:
``close_NN.jpg`` (2.8 km around the largest typed change, the tailings dam wall) and ``wide_NN.jpg`` (the whole AOI,
with the close-up marked). Prints the areas the report quotes for the threshold and clean steps.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import rasterio.features
from fig_common import app_types
from matplotlib import colormaps
from PIL import Image
from scipy import ndimage
from skimage.segmentation import find_boundaries

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
OUT = ROOT / "docs/figures/steps"
HALF_PX = 140  # 2.8 km close-up at 10 m
CLOSE_PX, WIDE_PX = 640, 720
ZONE_COLORS = np.array([[56, 189, 248], [245, 158, 11], [168, 85, 247]]) / 255
MASK = np.array([232, 131, 58]) / 255
BOX = np.array([250, 204, 21]) / 255
NODATA = np.array([241, 245, 249]) / 255


def refl(name: str) -> tuple[np.ndarray, rasterio.Affine]:
    with rasterio.open(PROC / f"{name}.tif") as s:
        return (s.read().astype("float32") - 1000) / 10000, s.transform


def band(name: str) -> np.ndarray:
    with rasterio.open(PROC / f"{name}.tif") as s:
        return s.read(1)


def main() -> None:
    before, t = refl("sentinel2_20230812_stack")
    after, _ = refl("sentinel2_20230902_stack")
    norm, _ = refl("sentinel2_20230902_stack_normalized")
    valid = (before[0] > -0.1) & (after[0] > -0.1)
    lo = np.array([np.percentile(before[b][valid], 2) for b in range(3)])[:, None, None]
    hi = np.array([np.percentile(before[b][valid], 98) for b in range(3)])[:, None, None]

    def rgb(r: np.ndarray) -> np.ndarray:
        return np.clip((r[::-1] - lo[::-1]) / (hi[::-1] - lo[::-1]), 0, 1).transpose(1, 2, 0) ** 0.8

    mag, binary, zones = band("change_map"), band("change_binary") == 1, band("radiometric_zones")
    low = json.loads((PROC / "run_summary.json").read_text())["thresholds"]["cva_d2"]
    raw = valid & (np.nan_to_num(mag, nan=-9) > np.log10(low))

    g = gpd.read_file(PROC / "change_features.geojson")
    g = g[(g.method == "robust_cva") & (g.change_type != "noise_or_artifact")].to_crs(32735)
    focus = g[g.change_type != "unexplained"].sort_values("area_m2", ascending=False).iloc[0]
    col, row = ~t * (focus.geometry.centroid.x, focus.geometry.centroid.y)
    r0, c0 = int(row) - HALF_PX, int(col) - HALF_PX
    close = (slice(r0, r0 + 2 * HALF_PX), slice(c0, c0 + 2 * HALF_PX))

    colors = {k: np.array([int(v["color"][i : i + 2], 16) for i in (1, 3, 5)]) / 255 for k, v in app_types().items()}
    keys = list(colors)
    type_id = rasterio.features.rasterize(
        ((geom, keys.index(ct) + 1) for geom, ct in zip(g.geometry, g.change_type, strict=True) if ct in colors),
        out_shape=valid.shape, transform=t, fill=0, dtype="uint8",
    )  # fmt: skip
    lut = np.vstack([np.zeros(3), *colors.values()])

    def gray(r: np.ndarray) -> np.ndarray:
        return rgb(r).mean(-1, keepdims=True).repeat(3, -1) * 0.8 + 0.12

    def paint(img: np.ndarray, mask: np.ndarray, color: np.ndarray, a: float = 1.0) -> np.ndarray:
        img[mask] = img[mask] * (1 - a) + color * a
        return img

    zone_tint = rgb(after)
    zm = valid & (zones >= 0)
    zone_tint[zm] = zone_tint[zm] * 0.6 + ZONE_COLORS[np.clip(zones[zm], 0, 2)] * 0.4
    seams = find_boundaries(np.where(zm, zones, -1), mode="inner") & zm
    heat = colormaps["magma"](np.clip(np.nan_to_num(mag, nan=0) / 3, 0, 1))[..., :3]
    typed = rgb(norm) * 0.7
    has = type_id > 0
    typed[has] = typed[has] * 0.25 + lut[type_id[has]] * 0.75
    typed[find_boundaries(type_id, mode="inner") & has] = 1.0

    frames = [
        rgb(before),
        rgb(after),
        paint(zone_tint, seams, np.ones(3)),
        rgb(norm),
        heat,
        paint(gray(norm), raw, MASK),
        paint(gray(norm), binary, MASK),
        typed,
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    box = np.zeros_like(valid)
    box[close] = True
    edge = box & ~ndimage.binary_erosion(box, iterations=5)
    rows = np.flatnonzero(valid.any(1))
    cols = np.flatnonzero(valid.any(0))
    wide = (slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1))
    for i, f in enumerate(frames, 1):
        f = f.copy()
        f[~valid] = NODATA
        save(f[close], OUT / f"close_{i:02d}.jpg", CLOSE_PX)
        save(paint(f, edge, BOX)[wide], OUT / f"wide_{i:02d}.jpg", WIDE_PX)

    px_km2 = abs(t.a * t.e) / 1e6
    print(f"raw threshold: {raw.sum() * px_km2:.2f} km2 in {_count(raw)} blobs")
    print(f"cleaned: {binary.sum() * px_km2:.2f} km2 in {_count(binary)} blobs; polygons kept: {len(g)}")
    print(f"focus: {focus.headline} ({focus.area_m2 / 1e6:.2f} km2)")


def _count(mask: np.ndarray) -> int:
    return int(ndimage.label(mask, structure=np.ones((3, 3)))[1])


def save(img: np.ndarray, path: Path, width: int) -> None:
    im = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
    im = im.resize((width, round(im.height * width / im.width)), Image.Resampling.LANCZOS)
    im.save(path, quality=84, optimize=True, progressive=True)


if __name__ == "__main__":
    main()
