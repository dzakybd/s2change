"""Render docs/figures/07_trap_proof.png: the seams are in the supplied data, not in the ground.

    uv run python scripts/fig_trap_proof.py

Brightness change between the two dates, log(after / before) of the mean of B02/B03/B04 in reflectance, for
(a) the supplied pair (nominal 2023-08-12 -> 2023-09-02, each a two-pass mosaic) and (b) the true single passes
2023-08-08 -> 2023-09-02 (scripts/real_dates.py). (c) Its median along the distance across the seams: the supplied pair
steps exactly where the source pass changes (data/processed/provenance/source_scene.tif), the single passes do not. The
profile uses the northern band of the AOI (bush, no mine), so land-cover change does not mask the steps.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
TRUE = ROOT / "data/experiments/2023-08-08_2023-09-02"
OUT = ROOT / "docs/figures/07_trap_proof.png"
BIN_M, RANGE_M = 90, 3500
NORTH = 0.22  # share of rows from the top used for the profile: natural ground north of the mine


def brightness(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with rasterio.open(path) as s:
        dn = s.read().astype("float32")
    valid = (dn > 0).all(0)
    return np.clip((dn - 1000) / 10000, 1e-3, None).mean(0), valid


def seam_distance(src: np.ndarray) -> np.ndarray:
    """Signed distance in metres to the boundary between source 0 and source 1 (negative on the source-0 side)."""
    inside = src == 1
    return (ndimage.distance_transform_edt(~inside) - ndimage.distance_transform_edt(inside)) * 10.0


def profile(ratio: np.ndarray, dist: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    edges = np.arange(-RANGE_M, RANGE_M + BIN_M, BIN_M)
    idx = np.digitize(dist[valid], edges)
    vals = ratio[valid]
    med = np.array([np.median(vals[idx == i]) if (idx == i).sum() > 200 else np.nan for i in range(1, len(edges))])
    return (edges[:-1] + edges[1:]) / 2 / 1000, med


def main() -> None:
    with rasterio.open(PROC / "provenance/source_scene.tif") as s:
        src_before, src_after = s.read(1), s.read(2)

    b0, v0 = brightness(PROC / "sentinel2_20230812_stack.tif")
    a0, v1 = brightness(PROC / "sentinel2_20230902_stack.tif")
    b1, w0 = brightness(TRUE / "sentinel2_20230808_stack.tif")
    a1, w1 = brightness(TRUE / "sentinel2_20230902_stack.tif")
    supplied, single = np.log(a0 / b0), np.log(a1 / b1)
    ok0, ok1 = v0 & v1 & (src_before >= 0) & (src_after >= 0), w0 & w1 & (src_before >= 0) & (src_after >= 0)

    seam_line = (ndimage.grey_dilation(src_before, size=3) != ndimage.grey_erosion(src_before, size=3)) | (
        ndimage.grey_dilation(src_after, size=3) != ndimage.grey_erosion(src_after, size=3)
    )
    dist = seam_distance(src_after)  # the after-date seam; the before-date seam runs parallel to it
    before_offset = float(np.median(dist[(ndimage.grey_dilation(src_before, size=3) != src_before) & ok0])) / 1000

    fig = plt.figure(figsize=(15, 5.6), dpi=150)
    gs = fig.add_gridspec(1, 3, width_ratios=[1, 1, 1.15], wspace=0.22)
    north = np.zeros_like(ok0)
    north[: int(NORTH * north.shape[0])] = True
    lim = np.nanpercentile(np.abs(np.r_[supplied[ok0] - np.median(supplied[ok0]), single[ok1] - np.median(single[ok1])]), 97)
    for k, (img, ok, title) in enumerate(
        [
            (supplied, ok0, "(a) Supplied pair: nominal 12 Aug → 2 Sep"),
            (single, ok1, "(b) True single passes: 8 Aug → 2 Sep"),
        ]
    ):
        ax = fig.add_subplot(gs[k])
        show = np.where(ok, img - np.median(img[ok]), np.nan)
        ax.imshow(show, cmap="RdBu_r", vmin=-lim, vmax=lim, interpolation="nearest")
        ys, xs = np.nonzero(seam_line & ok0)
        ax.scatter(xs[::4], ys[::4], s=0.15, c="black", alpha=0.5, linewidths=0)
        ax.axhline(NORTH * img.shape[0], color="#0f172a", lw=0.8, ls=":")
        ax.set_title(title, fontsize=11, loc="left")
        ax.set_axis_off()
    fig.text(
        0.13,
        -0.01,
        "Brightness change, log(after / before), centred: red brighter, blue darker. Thin lines: where the supplied files"
        " switch pass. Above the dotted line: the ground profiled in (c).",
        fontsize=9,
        color="#475569",
    )

    ax = fig.add_subplot(gs[2])
    for img, ok, label, colour in [
        (supplied, ok0, "Supplied pair (two-pass mosaics)", "#dc2626"),
        (single, ok1, "True single passes", "#2563eb"),
    ]:
        x, y = profile(img - np.median(img[ok & north]), dist, ok & north)
        ax.plot(x, y, color=colour, lw=2, label=label)
    ax.margins(y=0.25)
    for xv, txt, ha in [
        (0, "2 Sep file switches pass ", "right"),
        (before_offset, " 12 Aug file switches pass", "left"),
    ]:
        ax.axvline(xv, color="#64748b", ls="--", lw=1)
        ax.text(xv, ax.get_ylim()[1], txt, fontsize=8, ha=ha, va="top", color="#334155")
    ax.axhline(0, color="#cbd5e1", lw=0.8)
    ax.set_xlabel("Distance across the seams (km)")
    ax.set_ylabel("Median brightness change (log ratio, centred)")
    ax.set_title("(c) North of the mine: steps only when the pass switches", fontsize=11, loc="left")
    ax.legend(frameon=False, fontsize=9, loc="lower left")
    ax.spines[["top", "right"]].set_visible(False)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    x, y = profile(supplied - np.median(supplied[ok0 & north]), dist, ok0 & north)
    x1, y1 = profile(single - np.median(single[ok1 & north]), dist, ok1 & north)
    near = lambda xs, ys, c: np.nanmedian(ys[(xs > c) & (xs < c + 0.6)]) - np.nanmedian(ys[(xs < c) & (xs > c - 0.6)])  # noqa: E731
    print(f"wrote {OUT}")
    print(f"step at 2 Sep seam: supplied {near(x, y, 0):+.3f}, single {near(x1, y1, 0):+.3f}")
    print(
        f"step at 12 Aug seam ({before_offset:+.2f} km): supplied {near(x, y, before_offset):+.3f}, "
        f"single {near(x1, y1, before_offset):+.3f}"
    )


if __name__ == "__main__":
    main()
