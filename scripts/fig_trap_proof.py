"""Render docs/figures/07_trap_proof.png: each supplied "date" is two passes, shown date by date.

    uv run python scripts/fig_trap_proof.py

Each supplied file is compared with a true single-pass scene of about the same date, before with before and after with
after: log(supplied / single pass) of the mean of B02/B03/B04 in reflectance. (a) Supplied "12 Aug" against the 8 Aug
pass; (b) supplied "2 Sep" against the 2 Sep pass (scripts/real_dates.py). Where a file is the same scene as the single
pass the ratio is about 0; where it is another day it shifts, and the shift switches exactly at the seam of
data/processed/provenance/source_scene.tif. (c) The median ratio across each file's seam, on the northern band of the
AOI (bush, no mine), so land-cover change does not mask the steps.
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
    """Signed distance in metres to the boundary between source 0 (east, positive) and source 1 (west, negative)."""
    inside = src == 1
    return (ndimage.distance_transform_edt(~inside) - ndimage.distance_transform_edt(inside)) * 10.0


def profile(ratio: np.ndarray, dist: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    edges = np.arange(-RANGE_M, RANGE_M + BIN_M, BIN_M)
    idx = np.digitize(dist[valid], edges)
    vals = ratio[valid]
    med = np.array([np.median(vals[idx == i]) if (idx == i).sum() > 200 else np.nan for i in range(1, len(edges))])
    return (edges[:-1] + edges[1:]) / 2 / 1000, med


def seam(src: np.ndarray) -> np.ndarray:
    return ndimage.grey_dilation(src, size=3) != ndimage.grey_erosion(src, size=3)


def main() -> None:
    with rasterio.open(PROC / "provenance/source_scene.tif") as s:
        src_before, src_after = s.read(1), s.read(2)
    b0, v0 = brightness(PROC / "sentinel2_20230812_stack.tif")
    a0, v1 = brightness(PROC / "sentinel2_20230902_stack.tif")
    b1, w0 = brightness(TRUE / "sentinel2_20230808_stack.tif")
    a1, w1 = brightness(TRUE / "sentinel2_20230902_stack.tif")
    north = np.zeros_like(v0)
    north[: int(NORTH * north.shape[0])] = True
    # date, ratio, valid, source index per pixel, scene names (source 0 = east, source 1 = west), colour
    dates = [
        ("before", np.log(b0 / b1), v0 & w0 & (src_before >= 0), src_before, ("1 Jul", "4 Jul"), "8 Aug", '"12 Aug"', "#d97706"),
        ("after", np.log(a0 / a1), v1 & w1 & (src_after >= 0), src_after, ("25 Aug", "2 Sep"), "2 Sep", '"2 Sep"', "#7c3aed"),
    ]
    lim = 0.3

    fig = plt.figure(figsize=(15, 5.6), dpi=150)
    gs = fig.add_gridspec(1, 3, width_ratios=[1, 1, 1.15], wspace=0.3)
    for k, (side, ratio, ok, src, names, single, label, _) in enumerate(dates):
        ax = fig.add_subplot(gs[k])
        ax.imshow(np.where(ok, ratio, np.nan), cmap="RdBu_r", vmin=-lim, vmax=lim, interpolation="nearest")
        ys, xs = np.nonzero(seam(src) & ok)
        ax.scatter(xs[::4], ys[::4], s=0.3, c="black", alpha=0.7, linewidths=0)
        ax.axhline(NORTH * ratio.shape[0], color="#0f172a", lw=0.8, ls=":")
        for idx, name in enumerate(names):
            yy, xx = np.nonzero((src == idx) & ok & north)
            med = float(np.median(ratio[(src == idx) & ok & north]))
            ax.text(
                np.median(xx),
                np.median(yy),
                f"{name}\n{med:+.2f}",
                ha="center",
                va="center",
                fontsize=10,
                fontweight="bold",
                color="#0f172a",
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="none", alpha=0.85),
            )
        ax.set_title(
            f"({'ab'[k]}) {side.capitalize()}: supplied {label} vs single pass {single}\n"
            f"supplied file = {names[1]} (west) + {names[0]} (east)",
            fontsize=10.5,
            loc="left",
            linespacing=1.4,
        )
        ax.set_axis_off()
    fig.text(
        0.13,
        -0.01,
        "log(supplied / single pass) brightness: 0 = as bright as the single pass, blue = darker, red = brighter. Black line:"
        " where the supplied file switches pass. Labels: scene of each side and its median ratio above the dotted line.",
        fontsize=9,
        color="#475569",
    )

    ax = fig.add_subplot(gs[2])
    steps = []
    for side, ratio, ok, src, _names, single, label, colour in dates:
        x, y = profile(ratio, seam_distance(src), ok & north)
        ax.plot(x, y, color=colour, lw=2, label=f"{side.capitalize()}: supplied {label} / single pass {single}")
        near = np.nanmedian(y[(x > 0) & (x < 0.6)]) - np.nanmedian(y[(x < 0) & (x > -0.6)])
        steps.append(f"{side} step {near:+.3f}")
    ax.margins(y=0.25)
    ax.axvline(0, color="#64748b", ls="--", lw=1)
    ax.text(-0.08, ax.get_ylim()[1], "west pass ", fontsize=8, ha="right", va="top", color="#334155")
    ax.text(0.08, ax.get_ylim()[1], " east pass", fontsize=8, ha="left", va="top", color="#334155")
    ax.axhline(0, color="#cbd5e1", lw=0.8)
    ax.set_xlabel("Distance from the file's seam (km), east positive")
    ax.set_ylabel("Median log(supplied / single pass)")
    ax.set_title("(c) North of the mine: each file steps at its seam", fontsize=11, loc="left")
    ax.legend(frameon=False, fontsize=9, loc="lower left")
    ax.spines[["top", "right"]].set_visible(False)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT}")
    print(", ".join(steps))


if __name__ == "__main__":
    main()
