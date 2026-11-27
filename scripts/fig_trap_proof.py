"""Render docs/figures/07_trap_proof.png: each supplied "date" is two passes, shown date by date.

    uv run python scripts/fig_trap_proof.py

There is no Sentinel-2 scene on the nominal 12 Aug 2023, so each supplied file is set beside the full-coverage single pass
(relative orbit 121) that `s2cd provenance` found inside it: the "12 Aug" file beside 4 Jul, the "2 Sep" file beside 2 Sep.
Both are the original 2023 products (processing baseline 05.09), read from the provenance cache (data/cache/stac).

(a-d) True colour, one stretch per row; the yellow line is where the file switches pass
(data/processed/provenance/source_scene.tif). Labels give, per side, the share of pixels whose B02/B03/B04 equal the
single pass exactly. (e) log(supplied / single pass) of the mean of B02/B03/B04, median across each file's seam on the
northern band of the AOI (bush, no mine): 0 on the side that is that pass, a step exactly at the seam.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from scipy import ndimage

from s2cd import viz

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
CACHE = ROOT / "data/cache/stac"
OUT = ROOT / "docs/figures/07_trap_proof.png"
BANDS = ("B02", "B03", "B04")
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


def supplied(name: str) -> tuple[np.ndarray, str]:
    """DN stack of a supplied file and the provenance cache tag of its grid."""
    with rasterio.open(PROC / name) as s:
        t = s.transform
        return s.read().astype(np.int32), f"{int(t.c)}_{int(t.f)}_{s.width}x{s.height}"


def single_pass(item_id: str, tag: str) -> np.ndarray:
    paths = [CACHE / f"{item_id}_{band}_s1_{tag}.npy" for band in BANDS]
    if missing := [p.name for p in paths if not p.exists()]:
        raise SystemExit(f"not in the provenance cache, run `uv run --extra stac s2cd provenance` first: {missing}")
    return np.stack([np.load(p) for p in paths]).astype(np.int32)


def reflectance(dn: np.ndarray) -> np.ndarray:
    return np.clip((dn - 1000) / 10000, 0, None).astype(np.float32)


def main() -> None:
    summary = json.loads((PROC / "provenance/provenance_summary.json").read_text())
    with rasterio.open(PROC / "provenance/source_scene.tif") as s:
        sources = s.read()
    north = np.zeros(sources.shape[1:], bool)
    north[: int(NORTH * north.shape[0])] = True
    # side, nominal date, supplied file, colour; source 0 = east pass (R078), source 1 = west pass (R121)
    dates = [
        ("Before", "2023-08-12", "sentinel2_20230812_stack.tif", "#d97706"),
        ("After", "2023-09-02", "sentinel2_20230902_stack.tif", "#7c3aed"),
    ]

    fig = plt.figure(figsize=(15, 9), dpi=150)
    gs = fig.add_gridspec(2, 4, width_ratios=[1, 1, 0.2, 1.2], wspace=0.08, hspace=0.2)
    curves, report = [], []
    for row, (side, nominal, sup_name, colour) in enumerate(dates):
        src = sources[row]
        scenes = summary["stac"][nominal]["sources"]
        names = [f"{int(s['datetime'][8:10])} {('Jul', 'Aug', 'Sep')[int(s['datetime'][5:7]) - 7]}" for s in scenes]
        full = next(i for i, s in enumerate(scenes) if s["relative_orbit"] == 121)  # the pass covering the whole AOI
        sup, tag = supplied(sup_name)
        one = single_pass(scenes[full]["id"], tag)
        ok = (sup > 0).all(0) & (one > 0).all(0) & (src >= 0)
        same = (sup == one).all(0)
        share = {i: float(same[ok & (src == i)].mean()) for i in (0, 1)}
        report.append(f"{side}: identical to {names[full]} on " + ", ".join(f"{names[i]} side {share[i]:.1%}" for i in (0, 1)))
        label, single = f'"{int(nominal[8:])} {("Aug", "Sep")[int(nominal[5:7]) - 8]}"', names[full]

        r_sup, r_one = reflectance(sup), reflectance(one)
        hi = float(np.percentile(np.r_[r_sup[:, ok & north].ravel(), r_one[:, ok & north].ravel()], 99))  # one stretch per row
        line = seam(src)
        no_scene = "; none exists on 12 Aug" if side == "Before" else ""
        for col, (img, title) in enumerate(
            [
                (r_sup, f"{side}: supplied file {label}\n= {names[1]} (west) + {names[0]} (east)"),
                (r_one, f"{side}: true single pass {single}\none scene, whole area{no_scene}"),
            ]
        ):
            ax = fig.add_subplot(gs[row, col])
            ax.imshow(viz.rgb(img, hi=hi), interpolation="nearest")
            ys, xs = np.nonzero(line & ok)
            ax.scatter(xs[::3], ys[::3], s=0.4, c="#facc15", alpha=0.9, linewidths=0)
            if col == 0:
                for i, name in enumerate(names):
                    yy, xx = np.nonzero((src == i) & ok & north)
                    verdict = f"{share[i]:.0%} identical"
                    ax.text(
                        np.median(xx),
                        np.median(yy),
                        f"{name}\n{verdict}",
                        ha="center",
                        va="center",
                        fontsize=9.5,
                        fontweight="bold",
                        color="#0f172a",
                        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="none", alpha=0.85),
                    )
            ax.set_title(f"({'abcd'[2 * row + col]}) {title}", fontsize=10.5, loc="left", linespacing=1.35)
            ax.set_axis_off()
        ratio = np.log(np.clip(r_sup.mean(0), 1e-3, None) / np.clip(r_one.mean(0), 1e-3, None))
        curves.append((side, label, single, colour, *profile(ratio, seam_distance(src), ok & north)))
    fig.text(
        0.13,
        0.02,
        "True colour, the same stretch within each row. Yellow line: where the supplied file switches pass; on the single"
        " pass it is only a reference. Identical: B02, B03 and B04 all equal, pixel for pixel.",
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
        "(e) North of the mine: each file steps at its seam\n0 = the file is that single pass",
        fontsize=10.5,
        loc="left",
        linespacing=1.35,
    )
    ax.legend(frameon=False, fontsize=9, loc="lower left")
    ax.spines[["top", "right"]].set_visible(False)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT}")
    print("\n".join(report))
    print(", ".join(steps))


if __name__ == "__main__":
    main()
