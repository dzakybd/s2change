"""Render docs/figures/07_trap_proof.png: each supplied "date" is two passes, shown date by date.

    uv run python scripts/fig_trap_proof.py

(a-d) Each supplied file in true colour beside a true single-pass scene of about the same date (scripts/real_dates.py),
before with before (8 Aug) and after with after (2 Sep), with one stretch per row; the yellow line is where the file
switches pass (data/processed/provenance/source_scene.tif). (e) log(supplied / single pass) of the mean of B02/B03/B04,
median across each file's seam on the northern band of the AOI (bush, no mine), so land-cover change does not mask the
steps: about 0 where the file is the same scene as the single pass, a step exactly at the seam.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from scipy import ndimage

from s2cd import viz

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
TRUE = ROOT / "data/experiments/2023-08-08_2023-09-02"
OUT = ROOT / "docs/figures/07_trap_proof.png"
BIN_M, RANGE_M = 90, 3500
NORTH = 0.22  # share of rows from the top used for the profile: natural ground north of the mine


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


def reflectance(path: Path) -> np.ndarray:
    with rasterio.open(path) as s:
        return np.clip((s.read().astype("float32") - 1000) / 10000, 0, None)


def main() -> None:
    with rasterio.open(PROC / "provenance/source_scene.tif") as s:
        src_before, src_after = s.read(1), s.read(2)
    north = np.zeros(src_before.shape, bool)
    north[: int(NORTH * north.shape[0])] = True
    # date, supplied file, single pass, source index per pixel, scenes (source 0 = east, 1 = west), single date, colour
    dates = [
        (
            "Before",
            "sentinel2_20230812_stack.tif",
            "sentinel2_20230808_stack.tif",
            src_before,
            ("1 Jul", "4 Jul"),
            "8 Aug",
            '"12 Aug"',
            "#d97706",
        ),
        (
            "After",
            "sentinel2_20230902_stack.tif",
            "sentinel2_20230902_stack.tif",
            src_after,
            ("25 Aug", "2 Sep"),
            "2 Sep",
            '"2 Sep"',
            "#7c3aed",
        ),
    ]

    fig = plt.figure(figsize=(15, 9), dpi=150)
    gs = fig.add_gridspec(2, 4, width_ratios=[1, 1, 0.2, 1.2], wspace=0.08, hspace=0.18)
    curves = []
    for row, (side, sup_name, one_name, src, names, single, label, colour) in enumerate(dates):
        sup, one = reflectance(PROC / sup_name), reflectance(TRUE / one_name)
        ok = (sup > 0).all(0) & (one > 0).all(0) & (src >= 0)
        hi = float(np.percentile(np.r_[sup[:, ok & north].ravel(), one[:, ok & north].ravel()], 99))  # one stretch per row
        line = seam(src)
        for col, (img, title) in enumerate(
            [
                (sup, f"{side}: supplied file {label}\n= {names[1]} (west) + {names[0]} (east)"),
                (one, f"{side}: true single pass, {single}\none scene, the whole area"),
            ]
        ):
            ax = fig.add_subplot(gs[row, col])
            ax.imshow(viz.rgb(img, hi=hi), interpolation="nearest")
            ys, xs = np.nonzero(line & ok)
            ax.scatter(xs[::3], ys[::3], s=0.4, c="#facc15", alpha=0.9, linewidths=0)
            if col == 0:
                for idx, name in enumerate(names):
                    yy, xx = np.nonzero((src == idx) & ok & north)
                    ax.text(
                        np.median(xx),
                        np.median(yy),
                        name,
                        ha="center",
                        va="center",
                        fontsize=11,
                        fontweight="bold",
                        color="#0f172a",
                        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="none", alpha=0.85),
                    )
            ax.set_title(f"({'abcd'[2 * row + col]}) {title}", fontsize=10.5, loc="left", linespacing=1.35)
            ax.set_axis_off()
        b_sup, b_one = sup.mean(0), one.mean(0)
        ratio = np.log(np.clip(b_sup, 1e-3, None) / np.clip(b_one, 1e-3, None))
        curves.append((side, label, single, colour, *profile(ratio, seam_distance(src), ok & north)))
    fig.text(
        0.13,
        0.02,
        "True colour, the same stretch within each row. Yellow line: where the supplied file switches pass; on the single"
        " pass it is only a reference.",
        fontsize=9,
        color="#475569",
    )

    ax = fig.add_subplot(gs[:, 3])
    steps = []
    for side, label, single, colour, x, y in curves:
        ax.plot(x, y, color=colour, lw=2, label=f"{side}: supplied {label} / single pass {single}")
        near = np.nanmedian(y[(x > 0) & (x < 0.6)]) - np.nanmedian(y[(x < 0) & (x > -0.6)])
        steps.append(f"{side} step {near:+.3f}")
    ax.margins(y=0.25)
    ax.axvline(0, color="#64748b", ls="--", lw=1)
    ax.text(-0.08, ax.get_ylim()[1], "west pass ", fontsize=8, ha="right", va="top", color="#334155")
    ax.text(0.08, ax.get_ylim()[1], " east pass", fontsize=8, ha="left", va="top", color="#334155")
    ax.axhline(0, color="#cbd5e1", lw=0.8)
    ax.set_xlabel("Distance from the file's seam (km), east positive")
    ax.set_ylabel("Median log(supplied / single pass) brightness")
    ax.set_title(
        "(e) North of the mine: each file steps at its seam\n0 = as bright as the single pass",
        fontsize=10.5,
        loc="left",
        linespacing=1.35,
    )
    ax.legend(frameon=False, fontsize=9, loc="lower left")
    ax.spines[["top", "right"]].set_visible(False)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT}")
    print(", ".join(steps))


if __name__ == "__main__":
    main()
