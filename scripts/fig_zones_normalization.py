"""Render docs/figures/17_zones_normalization.svg: how the site splits into three zones and how brightness is matched.

    uv run python scripts/fig_zones_normalization.py

Panels 1-2 are schematics. Panels 3-4 are real data: a seeded sample of stable pixels (blue band B02) from a fresh pipeline run
in a temporary directory, with the fitted lines checked against data/processed/run_summary.json. The dot clouds are embedded
as raster layers (2,400 points would otherwise weigh the SVG down); text, lines and shapes stay vector, and text stays text.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon, Rectangle

from s2cd import radiometry
from s2cd.pipeline import Config, run

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/figures/17_zones_normalization.svg"

INK, MUTED, FAINT = "#0f172a", "#475569", "#94a3b8"
RED = "#dc2626"
BLUE, BLUE_T = "#2563eb", "#bfdbfe"
ORANGE, ORANGE_T = "#ea580c", "#fed7aa"
PURPLE, PURPLE_T = "#7c3aed", "#ddd6fe"
GREY_DOT = "#64748b"
ZONE_COLOR = {0: BLUE, 1: PURPLE, 2: ORANGE}
ZONE_NAME = {0: "West", 1: "Middle", 2: "East"}

TITLE_PT, SUB_PT, BODY_PT, SMALL_PT = 22, 13.5, 14, 12.5
PER_ZONE, N_CHANGE, SEED = 1000, 300, 11
SEAM_ANGLE_DEG = 12.6

plt.rcParams.update(
    {
        "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "svg.fonttype": "none",
        "svg.hashsalt": "s2cd",
        "hatch.linewidth": 1.3,
    }
)


def load_data() -> dict:
    """Run the pipeline into a temporary directory and sample stable and changed pixels (blue band)."""
    os.chdir(ROOT)
    base = Config.from_yaml(Path("configs/default.yaml"))
    with tempfile.TemporaryDirectory() as tmp:
        cfg = replace(base, out_dir=Path(tmp) / "processed", sqlite_path=Path(tmp) / "change_detection.sqlite")
        p = run(cfg)
        with rasterio.open(Path(tmp) / "processed/change_binary.tif") as src:
            changed = src.read(1) == 1
    offset = radiometry.infer_offset(p.after.dn, p.valid)
    r2 = radiometry.to_reflectance(p.after.dn, p.valid, offset)
    norm = radiometry.relative_normalization(p.refl_before, r2, p.zones, cfg.bands)
    b = list(cfg.bands).index("B02")

    summary = json.loads((ROOT / "data/processed/run_summary.json").read_text())
    saved = {c["zone"]: c for c in summary["calibration"] if c["band"] == "B02"}
    fits = {c.zone: (c.slope, c.intercept) for c in norm.calibration if c.band == "B02"}
    for z, (slope, icpt) in fits.items():
        assert abs(slope - saved[z]["slope"]) < 1e-6 and abs(icpt - saved[z]["intercept"]) < 1e-6, f"zone {z} fit differs"
    assert np.allclose(norm.normalized[b][p.valid], p.refl_after_norm[b][p.valid], atol=1e-6, equal_nan=True)

    rng = np.random.default_rng(SEED)
    stable = {}
    for z in fits:
        idx = np.flatnonzero((norm.pif & p.valid & (p.zones == z)).ravel())
        idx = rng.choice(idx, PER_ZONE, replace=False)
        stable[z] = (r2[b].ravel()[idx], p.refl_before[b].ravel()[idx], p.refl_after_norm[b].ravel()[idx])
    idx = rng.choice(np.flatnonzero((changed & p.valid & ~norm.pif).ravel()), N_CHANGE, replace=False)
    shares = {z: float((p.zones[p.valid] == z).mean()) for z in fits}
    return {
        "fits": fits,
        "stable": stable,
        "change": (p.refl_before[b].ravel()[idx], p.refl_after_norm[b].ravel()[idx]),
        "shares": shares,
        "seam_buffer_m": summary["seam_buffer_m"],
    }


def txt(ax, x, y, s, size=BODY_PT, color=INK, ha="left", va="center", weight="normal", **kw):
    return ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va, fontweight=weight, linespacing=1.3, **kw)


def header(ax, n: int, title: str, sub: str) -> None:
    ax.text(0, 1.15, f"{n}  {title}", transform=ax.transAxes, fontsize=TITLE_PT, fontweight="bold", color=INK, va="bottom")
    ax.text(0, 1.12, sub, transform=ax.transAxes, fontsize=SUB_PT, color=MUTED, va="top")


def arrow(ax, p0, p1, color=MUTED, lw=1.6) -> None:
    ax.annotate("", xy=p1, xytext=p0, arrowprops={"arrowstyle": "-|>", "color": color, "lw": lw, "shrinkA": 0, "shrinkB": 0})


def canvas(ax, width: float) -> float:
    """Free drawing area where one x unit and one y unit are the same length on the page; returns the y extent."""
    box = ax.get_position()
    w_in, h_in = box.width * ax.figure.get_figwidth(), box.height * ax.figure.get_figheight()
    height = width * h_in / w_in
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")
    return height


def part_wh(x0, y0, w, h, fb, ft, side):
    """Polygon of the left or right part of a w by h rectangle cut by a slanted seam (bottom fraction fb, top fraction ft)."""
    xb, xt = x0 + fb * w, x0 + ft * w
    if side == "left":
        return [(x0, y0), (xb, y0), (xt, y0 + h), (x0, y0 + h)]
    return [(xb, y0), (x0 + w, y0), (x0 + w, y0 + h), (xt, y0 + h)]


def part(x0, y0, s, fb, ft, side):
    """Same for an s-sided square."""
    return part_wh(x0, y0, s, s, fb, ft, side)


# 1 ------------------------------------------------------------------------------------------------------------------
def panel_passes(ax) -> None:
    height = canvas(ax, 12)
    s = 4.7
    y0 = height - 1.5 - s
    slant = np.tan(np.deg2rad(SEAM_ANGLE_DEG))
    files = (
        (0.6, 0.40, "BEFORE file", "12 Aug", "4 Jul", "1 Jul"),
        (6.7, 0.28, "AFTER file", "2 Sep", "2 Sep", "25 Aug"),
    )
    for x0, fb, name, nominal, west, east in files:
        ft = fb + slant
        ax.add_patch(Polygon(part(x0, y0, s, fb, ft, "left"), fc=BLUE_T, ec="none"))
        ax.add_patch(Polygon(part(x0, y0, s, fb, ft, "right"), fc=ORANGE_T, ec="none"))
        ax.plot([x0 + fb * s, x0 + ft * s], [y0, y0 + s], color=INK, lw=1.6, ls=(0, (4, 2.5)))
        ax.add_patch(Rectangle((x0, y0), s, s, fc="none", ec=MUTED, lw=1.6))
        for cx, label, date, col in ((0.19, "West pass", west, BLUE), (0.79, "East pass", east, ORANGE)):
            txt(ax, x0 + cx * s, y0 + 0.62 * s, label, size=SMALL_PT - 1, color=col, ha="center", weight="bold")
            txt(ax, x0 + cx * s, y0 + 0.48 * s, date, size=17, color=col, ha="center", weight="bold")
        txt(ax, x0 + s / 2, y0 + s + 0.75, name, size=BODY_PT + 2, weight="bold", ha="center")
        txt(ax, x0 + s / 2, y0 + s + 0.25, f"labelled {nominal}", size=SMALL_PT, color=MUTED, ha="center")
        txt(ax, x0 + (fb + ft) / 2 * s, y0 - 0.4, "seam", size=SMALL_PT, ha="center")
    txt(ax, 5.95, y0 + s / 2, "+", size=34, color=FAINT, ha="center", weight="bold")
    txt(
        ax,
        6.0,
        y0 - 1.5,
        f"Each file is two satellite passes stitched along a seam, tilted {SEAM_ANGLE_DEG} deg from north-south.\n"
        "The two files are stitched at slightly different places.",
        size=SMALL_PT,
        color=MUTED,
        ha="center",
    )


# 2 ------------------------------------------------------------------------------------------------------------------
def panel_zones(ax, shares: dict, buffer_m: int) -> None:
    height = canvas(ax, 12)
    w, h = 11.0, 3.5
    x0, y0 = 0.5, height - 1.6 - h
    slant = np.tan(np.deg2rad(SEAM_ANGLE_DEG)) * h / w
    fa, fbf = 0.40, 0.50  # bottom fraction of the after-file seam and the before-file seam (after is west of before)
    xa0, xa1 = x0 + fa * w, x0 + (fa + slant) * w
    xb0, xb1 = x0 + fbf * w, x0 + (fbf + slant) * w

    ax.add_patch(Polygon(part_wh(x0, y0, w, h, fa, fa + slant, "left"), fc=BLUE_T, ec="none"))
    ax.add_patch(Polygon(part_wh(x0, y0, w, h, fbf, fbf + slant, "right"), fc=ORANGE_T, ec="none"))
    ax.add_patch(
        Polygon([(xa0, y0), (xb0, y0), (xb1, y0 + h), (xa1, y0 + h)], fc="#ede9fe", ec=PURPLE, hatch="////", lw=0, zorder=2)
    )

    half = 0.14  # schematic width of the skipped band either side of a seam
    for (b0, b1), name, ha in (((xa0, xa1), "after seam", "right"), ((xb0, xb1), "before seam", "left")):
        band = [(b0 - half, y0), (b0 + half, y0), (b1 + half, y0 + h), (b1 - half, y0 + h)]
        ax.add_patch(Polygon(band, fc="#cbd5e1", ec="#334155", hatch="xxxx", lw=0, zorder=3))
        ax.plot([b0, b1], [y0, y0 + h], color=INK, lw=1.3, ls=(0, (4, 2.5)), zorder=4)
        txt(ax, b1 + (-0.2 if ha == "right" else 0.2), y0 + h + 0.3, name, size=SMALL_PT, color=MUTED, ha=ha, va="bottom")
    ax.add_patch(Rectangle((x0, y0), w, h, fc="none", ec=MUTED, lw=1.6, zorder=5))
    txt(ax, x0 + 0.35, y0 + h - 0.45, "West", size=BODY_PT + 2, color=BLUE, weight="bold", zorder=6)
    txt(ax, x0 + w - 0.35, y0 + h - 0.45, "East", size=BODY_PT + 2, color=ORANGE, ha="right", weight="bold", zorder=6)

    cards = (
        (0, BLUE_T, BLUE, "West zone", "4 Jul → 2 Sep", 60, shares[0], 0.5, x0 + 0.9),
        (1, "#ede9fe", PURPLE, "Middle strip", "4 Jul → 25 Aug", 52, shares[1], 4.5, (xa0 + xb0) / 2 + 0.1),
        (2, ORANGE_T, ORANGE, "East zone", "1 Jul → 25 Aug", 55, shares[2], 8.1, x0 + w - 1.5),
    )
    top = y0 - 0.75
    for z, tint, col, name, dates, days, share, cx, target in cards:
        ax.add_patch(
            Rectangle((cx, top - 0.3), 0.5, 0.4, fc=tint, ec=PURPLE if z == 1 else "none", hatch="////" if z == 1 else None, lw=0)
        )
        txt(ax, cx + 0.75, top - 0.1, name, size=BODY_PT, color=col, weight="bold")
        txt(ax, cx + 0.75, top - 0.7, dates, size=SMALL_PT)
        txt(ax, cx + 0.75, top - 1.2, f"{days} days  ·  {share * 100:.1f} %".replace(".0 %", " %"), size=SMALL_PT)
        ax.plot([cx + 0.25, target if z == 1 else cx + 0.25], [top + 0.14, y0 - 0.05], color=col, lw=1.4, zorder=1)
    by = top - 2.1
    ax.add_patch(Rectangle((0.5, by - 0.2), 0.5, 0.4, fc="#cbd5e1", ec="#334155", hatch="xxxx", lw=0, clip_on=False))
    txt(ax, 1.25, by, f"{buffer_m} m seam buffer: skipped", size=SMALL_PT, color=MUTED, weight="bold")
    txt(ax, 0.5, by - 0.6, "days = gap between the two dates;  % = share of the site", size=SMALL_PT, color=MUTED)


# 3 and 4 ------------------------------------------------------------------------------------------------------------
def style_axes(ax) -> None:
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(FAINT)
    ax.tick_params(colors=MUTED, labelsize=SMALL_PT)


def panel_fit(ax, data: dict, xlim: tuple[float, float], ylim: tuple[float, float]) -> None:
    style_axes(ax)
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)
    ax.set_xlabel("After (blue-band brightness, as delivered)", fontsize=BODY_PT, color=MUTED, labelpad=6)
    ax.set_ylabel("Before (blue-band brightness)", fontsize=BODY_PT, color=MUTED, labelpad=6)
    diag = np.array([max(xlim[0], ylim[0]), min(xlim[1], ylim[1])])
    ax.plot(diag, diag, color=GREY_DOT, lw=1.8, ls=(0, (5, 3)), zorder=1)
    txt(
        ax,
        0.985,
        0.03,
        "dashed: no difference (1:1)",
        size=SMALL_PT,
        color=MUTED,
        ha="right",
        va="bottom",
        transform=ax.transAxes,
    )
    for z in (0, 1, 2):
        after, before, _ = data["stable"][z]
        ax.scatter(after, before, s=7, color=ZONE_COLOR[z], alpha=0.22, lw=0, zorder=2, rasterized=True)
    for z in (0, 1, 2):
        slope, icpt = data["fits"][z]
        xs = np.percentile(data["stable"][z][0], [0.5, 99.5])
        ax.plot(xs, icpt + slope * xs, color=ZONE_COLOR[z], lw=3.5, zorder=3, solid_capstyle="round")
    y = 0.97
    for z in (0, 1, 2):
        slope, icpt = data["fits"][z]
        sign = "−" if icpt < 0 else "+"
        ax.add_line(Line2D([0.02, 0.07], [y, y], color=ZONE_COLOR[z], lw=3, transform=ax.transAxes))
        txt(
            ax,
            0.09,
            y,
            f"{ZONE_NAME[z]}: before = {slope:.2f}·after {sign} {abs(icpt):.3f}",
            size=SMALL_PT + 0.5,
            color=ZONE_COLOR[z],
            weight="bold",
            transform=ax.transAxes,
        )
        y -= 0.065
    txt(
        ax,
        0.02,
        y - 0.01,
        "Three different lines:\none global line would be wrong",
        size=SMALL_PT,
        color=INK,
        transform=ax.transAxes,
        va="top",
    )
    txt(
        ax,
        0.5,
        -0.2,
        "Dots: random sample of stable ground. Lines fitted on stable ground only\n(total least squares, outliers trimmed).",
        size=SMALL_PT - 0.5,
        color=MUTED,
        ha="center",
        va="top",
        transform=ax.transAxes,
    )


def panel_after(ax, data: dict, lim: tuple[float, float]) -> None:
    style_axes(ax)
    ax.set_xlim(lim)
    ax.set_ylim(lim)
    ax.set_xlabel("After, matched to before", fontsize=BODY_PT, color=MUTED, labelpad=6)
    ax.set_ylabel("Before (blue-band brightness)", fontsize=BODY_PT, color=MUTED, labelpad=6)
    for z in (0, 1, 2):
        _, before, matched = data["stable"][z]
        ax.scatter(matched, before, s=7, color=GREY_DOT, alpha=0.3, lw=0, zorder=2, rasterized=True)
    before, matched = data["change"]
    ax.scatter(matched, before, s=22, color=RED, alpha=0.75, lw=0, zorder=4, rasterized=True)
    ax.plot(lim, lim, color=INK, lw=1.8, ls=(0, (5, 3)), zorder=3)
    handles = [
        Line2D([], [], marker="o", ls="", color=GREY_DOT, alpha=0.7, ms=8, label="stable ground: on the line"),
        Line2D([], [], marker="o", ls="", color=RED, ms=8, label="flagged as change: off the line"),
        Line2D([], [], color=INK, lw=1.8, ls=(0, (5, 3)), label="no difference (1:1)"),
    ]
    leg = ax.legend(
        handles=handles,
        loc="upper left",
        frameon=True,
        facecolor="white",
        edgecolor=FAINT,
        framealpha=0.95,
        fontsize=SMALL_PT + 0.5,
        handlelength=1.6,
        labelcolor=INK,
    )
    leg.set_zorder(10)
    txt(
        ax,
        0.5,
        -0.2,
        "Same dots as panel 3, after mapping 'after' onto 'before' with each zone's line.\n"
        "Change is a random sample of flagged pixels.",
        size=SMALL_PT - 0.5,
        color=MUTED,
        ha="center",
        va="top",
        transform=ax.transAxes,
    )


def main() -> None:
    data = load_data()
    raw_after = np.concatenate([v[0] for v in data["stable"].values()])
    before = np.concatenate([v[1] for v in data["stable"].values()])
    pad = 0.02
    xlim = (np.percentile(raw_after, 0.2) - pad, np.percentile(raw_after, 99.8) + pad)
    ylim = (np.percentile(before, 0.2) - pad, np.percentile(before, 99.8) + pad)

    fig = plt.figure(figsize=(16, 12.6), dpi=100, facecolor="white")
    gs = fig.add_gridspec(2, 2, left=0.06, right=0.985, top=0.935, bottom=0.1, wspace=0.14, hspace=0.5)
    ax1, ax2, ax3, ax4 = (fig.add_subplot(gs[i, j]) for i in range(2) for j in range(2))
    panel_passes(ax1)
    panel_zones(ax2, data["shares"], data["seam_buffer_m"])
    panel_fit(ax3, data, xlim, ylim)
    panel_after(ax4, data, ylim)
    header(ax1, 1, "Each file is two passes", "A satellite file is two passes stitched together")
    header(ax2, 2, "Overlay the seams: 3 zones", "The two files are stitched at different places")
    header(ax3, 3, "Match brightness, one line per zone", "Unchanged ground should look the same on both dates")
    header(ax4, 4, "After matching, change stands out", "Stable ground sits on the 1:1 line; change does not")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, format="svg", bbox_inches="tight", facecolor="white", metadata={"Date": None})
    print(OUT, f"{OUT.stat().st_size / 1024:.0f} KB")
    for z, (slope, icpt) in data["fits"].items():
        print(f"zone {z} B02 fit matches run_summary.json: before = {slope:.6f} * after + {icpt:.6f}")


if __name__ == "__main__":
    main()
