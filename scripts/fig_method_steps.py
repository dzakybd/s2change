"""Render the step figures of report section 3 from a fresh pipeline run (in a temporary directory):

    docs/figures/17_step1_zones.svg     step 1, how the site splits into three zones (schematic)
    docs/figures/18_step2_matching.svg  step 2, brightness matching, one line per zone (real pixels, blue band B02)
    docs/figures/19_step3_4_noise.svg   steps 3-4, normal flicker per brightness class and the d2 cut-off (real pixels)
    docs/figures/20_step5_seam.svg      step 5, share of pixels past the cut-off by distance to the nearest seam (real pixels)

    uv run python scripts/fig_method_steps.py

The fitted lines are checked against data/processed/run_summary.json. Dot clouds are embedded as raster layers (thousands
of points would otherwise weigh the SVG down); text, lines and shapes stay vector, and text stays text.
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
from matplotlib.patches import Ellipse, Polygon, Rectangle
from scipy import stats

from s2cd import methods, radiometry
from s2cd.pipeline import Config, run

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "docs/figures"

INK, MUTED, FAINT = "#0f172a", "#475569", "#94a3b8"
RED = "#dc2626"
BLUE, BLUE_T = "#2563eb", "#bfdbfe"
ORANGE, ORANGE_T = "#ea580c", "#fed7aa"
PURPLE, PURPLE_T = "#7c3aed", "#ddd6fe"
GREY_DOT = "#64748b"
ZONE_COLOR = {0: BLUE, 1: PURPLE, 2: ORANGE}
ZONE_NAME = {0: "West", 1: "Middle", 2: "East"}

TITLE_PT, SUB_PT, BODY_PT, SMALL_PT = 22, 13.5, 14, 12.5
PER_ZONE, N_CHANGE, N_HIST, SEED = 1000, 300, 40000, 11
DARK_GREEN = "#15803d"
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
    """Run the pipeline into a temporary directory and sample the pixels each figure needs."""
    os.chdir(ROOT)
    base = Config.from_yaml(Path("configs/default.yaml"))
    with tempfile.TemporaryDirectory() as tmp:
        cfg = replace(base, out_dir=Path(tmp) / "processed", sqlite_path=Path(tmp) / "change_detection.sqlite")
        p = run(cfg)
        with rasterio.open(Path(tmp) / "processed/change_binary.tif") as src:
            changed = (src.read(1) == 1) & p.valid
    offset = radiometry.infer_offset(p.after.dn, p.valid)
    r2 = radiometry.to_reflectance(p.after.dn, p.valid, offset)
    norm = radiometry.relative_normalization(p.refl_before, r2, p.zones, cfg.bands)
    b = list(cfg.bands).index("B02")
    r = list(cfg.bands).index("B04")

    summary = json.loads((ROOT / "data/processed/run_summary.json").read_text())
    saved = {c["zone"]: c for c in summary["calibration"] if c["band"] == "B02"}
    fits = {c.zone: (c.slope, c.intercept) for c in norm.calibration if c.band == "B02"}
    for z, (slope, icpt) in fits.items():
        assert abs(slope - saved[z]["slope"]) < 1e-6 and abs(icpt - saved[z]["intercept"]) < 1e-6, f"zone {z} fit differs"
    assert np.allclose(norm.normalized[b][p.valid], p.refl_after_norm[b][p.valid], atol=1e-6, equal_nan=True)

    rng = np.random.default_rng(SEED)
    flat = lambda a: a.ravel()  # noqa: E731
    pick = lambda mask, n: rng.choice(np.flatnonzero(flat(mask)), n, replace=False)  # noqa: E731
    before, after_raw, after_norm = flat(p.refl_before[b]), flat(r2[b]), flat(p.refl_after_norm[b])

    stable, shift = {}, {}
    for z in fits:
        in_zone = p.valid & (p.zones == z)
        idx = pick(norm.pif & in_zone, PER_ZONE)
        stable[z] = (after_raw[idx], before[idx], after_norm[idx])
        idx = pick(in_zone, min(N_HIST, int(in_zone.sum())))
        shift[z] = (after_raw[idx] - before[idx], after_norm[idx] - before[idx])
    idx = pick(changed & ~norm.pif, N_CHANGE)
    west = p.valid & (p.zones == 0)
    idx_w = pick(west, 2500)

    # steps 3-4: the noise model of the west zone, refitted exactly as methods.robust_cva does
    fold_a, fold_b = methods._holdout_blocks(p.zones.shape, cfg.noise)
    held = fold_a | fold_b
    bright = p.refl_before.mean(axis=0)
    m = p.zones == 0
    cell, mu, cov = methods._fit_zone(
        p.cva.delta[:, m], bright[m], (norm.noise_pif & ~held)[m], cfg.noise.n_bins, cfg.noise.min_cell_px
    )
    cells = np.full(p.zones.shape, -1)
    cells[m] = cell
    classes = {}
    for k in (0, len(mu) - 1):
        sel = m & (cells == k)
        i_s = pick(sel & norm.noise_pif & held, 2500)
        i_c = pick(sel & changed, min(150, int((sel & changed).sum())))
        dxy = lambda i: np.stack([flat(p.cva.delta[b])[i], flat(p.cva.delta[r])[i]])  # noqa: E731
        classes[k] = {
            "stable": dxy(i_s),
            "change": dxy(i_c),
            "mu": mu[k][[b, r]],
            "cov": cov[k][np.ix_([b, r], [b, r])],
            "bright": float(np.median(bright[sel & norm.noise_pif])),
        }
    d2 = p.cva.d2
    dist_m = p.seam_dist * p.before.grid.transform.a
    past = np.nan_to_num(d2) > float(stats.chi2.isf(cfg.rule.far, len(cfg.bands)))
    edges = np.arange(0, 310, 10)
    seam = [float(past[p.valid & (dist_m >= lo) & (dist_m < lo + 10)].mean()) for lo in edges[:-1]]
    return {
        "seam": (edges[:-1] + 5, np.array(seam), float(past[p.valid & (dist_m >= 100) & (dist_m < 300)].mean())),
        "skipped_share": summary["valid_share_within_seam_buffer"],
        "fits": fits,
        "stable": stable,
        "shift": shift,
        "change": (before[idx], after_norm[idx]),
        "west": (after_raw[idx_w], before[idx_w], flat(norm.pif)[idx_w], float(norm.pif[west].mean())),
        "shares": {z: float((p.zones[p.valid] == z).mean()) for z in fits},
        "seam_buffer_m": summary["seam_buffer_m"],
        "classes": classes,
        "n_classes": len(mu),
        "d2_stable": d2[norm.noise_pif & held & p.valid & np.isfinite(d2)],
        "d2_change": d2[changed & np.isfinite(d2)],
        "thr": float(stats.chi2.isf(cfg.rule.far, len(cfg.bands))),
    }


def txt(ax, x, y, s, size=BODY_PT, color=INK, ha="left", va="center", weight="normal", **kw):
    return ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va, fontweight=weight, linespacing=1.3, **kw)


def header(ax, n: str, title: str, sub: str) -> None:
    ax.text(
        0, 1.15, f"{n}  {title}".strip(), transform=ax.transAxes, fontsize=TITLE_PT, fontweight="bold", color=INK, va="bottom"
    )
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


# step 1 ---------------------------------------------------------------------------------------------------------------
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


# step 1 ---------------------------------------------------------------------------------------------------------------
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


# step 2 -------------------------------------------------------------------------------------------------------------
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
        "Same dots as panel C, after mapping 'after' onto 'before' with each zone's line.\n"
        "Change is a random sample of flagged pixels.",
        size=SMALL_PT - 0.5,
        color=MUTED,
        ha="center",
        va="top",
        transform=ax.transAxes,
    )


def panel_shift(ax, data: dict) -> None:
    """Histogram of after minus before per zone: as delivered (filled) and after matching (lines)."""
    style_axes(ax)
    bins = np.linspace(-0.08, 0.12, 121)
    for z in (0, 1, 2):
        raw, matched = data["shift"][z]
        ax.hist(raw, bins, density=True, color=ZONE_COLOR[z], alpha=0.28, lw=0)
        ax.hist(matched, bins, density=True, histtype="step", color=ZONE_COLOR[z], lw=2.4)
    ax.axvline(0, color=INK, lw=1.6, ls=(0, (5, 3)))
    ax.set_ylim(0, ax.get_ylim()[1] * 1.3)
    txt(ax, 0.98, 0.97, "median shift, as delivered", size=SMALL_PT, color=MUTED, ha="right", va="top", transform=ax.transAxes)
    for i, z in enumerate((0, 1, 2)):
        med = float(np.median(data["shift"][z][0]))
        label = f"{ZONE_NAME[z]}  {med:+.3f}"
        txt(
            ax,
            0.98,
            0.89 - 0.065 * i,
            label,
            size=SMALL_PT + 0.5,
            color=ZONE_COLOR[z],
            ha="right",
            va="top",
            weight="bold",
            transform=ax.transAxes,
        )
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.set_xlabel("After minus before (blue-band brightness)", fontsize=BODY_PT, color=MUTED, labelpad=6)
    handles = [
        Rectangle((0, 0), 1, 1, fc=FAINT, alpha=0.5, lw=0, label="as delivered: each zone shifted differently"),
        Line2D([], [], color=GREY_DOT, lw=2.4, label="after matching: all centred on 0"),
    ]
    ax.legend(handles=handles, loc="upper left", frameon=False, fontsize=SMALL_PT, labelcolor=INK)
    txt(
        ax,
        0.5,
        -0.2,
        "All pixels of each zone, changed or not. Numbers: median shift as delivered.",
        size=SMALL_PT - 0.5,
        color=MUTED,
        ha="center",
        va="top",
        transform=ax.transAxes,
    )


def panel_trim(ax, data: dict, xlim, ylim) -> None:
    """West zone: every pixel, kept (stable ground) or dropped by the robust trimming, and the line fitted on the kept ones."""
    style_axes(ax)
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)
    ax.set_xlabel("After (blue-band brightness, as delivered)", fontsize=BODY_PT, color=MUTED, labelpad=6)
    ax.set_ylabel("Before (blue-band brightness)", fontsize=BODY_PT, color=MUTED, labelpad=6)
    after, before, kept, share = data["west"]
    ax.scatter(after[kept], before[kept], s=7, color=BLUE, alpha=0.25, lw=0, zorder=2, rasterized=True)
    ax.scatter(after[~kept], before[~kept], s=16, color=RED, alpha=0.6, lw=0, marker="x", zorder=3, rasterized=True)
    slope, icpt = data["fits"][0]
    xs = np.percentile(after[kept], [0.5, 99.5])
    ax.plot(xs, icpt + slope * xs, color=BLUE, lw=3.5, zorder=4, solid_capstyle="round")
    handles = [
        Line2D([], [], marker="o", ls="", color=BLUE, alpha=0.6, ms=8, label=f"kept: stable ground ({share * 100:.1f} %)"),
        Line2D([], [], marker="x", ls="", color=RED, ms=8, mew=2, label=f"dropped: off the line ({(1 - share) * 100:.1f} %)"),
        Line2D([], [], color=BLUE, lw=3.5, label="line fitted on the kept pixels"),
    ]
    ax.legend(
        handles=handles,
        loc="upper left",
        frameon=True,
        facecolor="white",
        edgecolor=FAINT,
        fontsize=SMALL_PT + 0.5,
        labelcolor=INK,
    )
    txt(
        ax,
        0.5,
        -0.2,
        "West zone, sample of all its pixels. Fit, drop robust score > 2.5, refit (5 rounds).",
        size=SMALL_PT - 0.5,
        color=MUTED,
        ha="center",
        va="top",
        transform=ax.transAxes,
    )


# steps 3-4 ----------------------------------------------------------------------------------------------------------
def panel_flicker(ax, c: dict, lim: float, name: str) -> None:
    """Change vectors of held-out stable pixels (blue vs red band) in one brightness class, with the 1-in-1,000 ellipse."""
    style_axes(ax)
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_aspect("equal")
    ax.axhline(0, color=FAINT, lw=1)
    ax.axvline(0, color=FAINT, lw=1)
    ax.scatter(*c["stable"], s=7, color=GREY_DOT, alpha=0.3, lw=0, zorder=2, rasterized=True)
    ax.scatter(*c["change"], s=22, color=RED, alpha=0.75, lw=0, zorder=4, rasterized=True)
    vals, vecs = np.linalg.eigh(c["cov"])
    radius = np.sqrt(stats.chi2.isf(1e-3, 2))
    angle = np.degrees(np.arctan2(vecs[1, -1], vecs[0, -1]))
    w, h = 2 * radius * np.sqrt(vals[::-1])
    ax.add_patch(Ellipse(c["mu"], w, h, angle=angle, fc="none", ec=DARK_GREEN, lw=3, zorder=5))
    ax.set_xlabel("Δ blue band", fontsize=BODY_PT, color=MUTED, labelpad=4)
    ax.set_ylabel("Δ red band", fontsize=BODY_PT, color=MUTED, labelpad=4)
    txt(
        ax,
        0.03,
        0.96,
        f"{name}\nbefore brightness ≈ {c['bright']:.2f}",
        size=BODY_PT,
        weight="bold",
        va="top",
        transform=ax.transAxes,
        zorder=6,
        bbox={"fc": "white", "ec": "none", "alpha": 0.9, "pad": 3},
    )


def panel_d2(ax, data: dict) -> None:
    style_axes(ax)
    bins = np.logspace(-2, 4, 121)
    stable, change, thr = data["d2_stable"], data["d2_change"], data["thr"]
    ax.hist(stable, bins, density=False, weights=np.full(stable.size, 1 / stable.size), color=GREY_DOT, alpha=0.55, lw=0)
    ax.hist(change, bins, density=False, weights=np.full(change.size, 1 / change.size), histtype="step", color=RED, lw=2.6)
    ax.set_xscale("log")
    ax.axvline(thr, color=DARK_GREEN, lw=3)
    top = ax.get_ylim()[1]
    txt(
        ax,
        thr / 1.25,
        top * 0.95,
        f"cut-off {thr:.2f}\n(χ²₃, 1 in 1,000)",
        size=BODY_PT,
        color=DARK_GREEN,
        weight="bold",
        va="top",
        ha="right",
    )
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.set_xlabel("Score d² (log scale)", fontsize=BODY_PT, color=MUTED, labelpad=6)
    handles = [
        Rectangle(
            (0, 0),
            1,
            1,
            fc=GREY_DOT,
            alpha=0.55,
            lw=0,
            label=f"held-out stable ground: {(stable > thr).mean() * 100:.2f} % past the cut-off",
        ),
        Line2D([], [], color=RED, lw=2.6, label=f"pixels on the final change map: {(change > thr).mean() * 100:.0f} % past it"),
    ]
    ax.legend(handles=handles, loc="upper left", frameon=False, fontsize=SMALL_PT + 0.5, labelcolor=INK)
    txt(
        ax,
        0.5,
        -0.2,
        "All zones and classes. Below 100 %: small holes filled in by the step 6 cleanup.",
        size=SMALL_PT - 0.5,
        color=MUTED,
        ha="center",
        va="top",
        transform=ax.transAxes,
    )


# step 5 -------------------------------------------------------------------------------------------------------------
def panel_seam(ax, data: dict) -> None:
    style_axes(ax)
    mid, rate, plateau = data["seam"]
    buf = data["seam_buffer_m"]
    ax.axvspan(0, buf, color="#cbd5e1", alpha=0.5, lw=0, hatch="xxxx", ec="#94a3b8")
    ax.axhline(plateau * 100, color=MUTED, lw=1.4, ls=(0, (5, 3)))
    ax.plot(mid, rate * 100, color=RED, lw=3, marker="o", ms=6, zorder=3)
    ax.set_xlim(0, 300)
    ax.set_ylim(0, rate.max() * 110)
    ax.set_xlabel("Distance to the nearest seam (m)", fontsize=BODY_PT, color=MUTED, labelpad=6)
    ax.set_ylabel("Pixels past the cut-off (%)", fontsize=BODY_PT, color=MUTED, labelpad=6)
    txt(
        ax,
        buf / 2,
        rate.max() * 100 * 0.62,
        f"skipped\n0–{buf} m\n({data['skipped_share'] * 100:.1f} % of the site)",
        size=BODY_PT,
        ha="center",
        weight="bold",
        color=INK,
    )
    txt(
        ax,
        295,
        plateau * 100 + 1.6,
        f"away from seams: {plateau * 100:.1f} %",
        size=SMALL_PT + 0.5,
        color=MUTED,
        ha="right",
        va="bottom",
    )
    for x, r in zip(mid[:3], rate[:3], strict=True):
        txt(ax, x + 6, r * 100, f"{r / plateau:.0f}×", size=SMALL_PT + 0.5, color=RED, weight="bold", va="bottom")


def save(fig, name: str) -> None:
    out = FIG / name
    fig.savefig(out, format="svg", bbox_inches="tight", facecolor="white", metadata={"Date": None})
    plt.close(fig)
    print(out.relative_to(ROOT), f"{out.stat().st_size / 1024:.0f} KB")


def main() -> None:
    data = load_data()
    raw_after = np.concatenate([v[0] for v in data["stable"].values()])
    before = np.concatenate([v[1] for v in data["stable"].values()])
    pad = 0.02
    xlim = (np.percentile(raw_after, 0.2) - pad, np.percentile(raw_after, 99.8) + pad)
    ylim = (np.percentile(before, 0.2) - pad, np.percentile(before, 99.8) + pad)

    fig = plt.figure(figsize=(16, 4.7), dpi=100, facecolor="white")
    gs = fig.add_gridspec(1, 2, left=0.06, right=0.985, top=0.82, bottom=0.02, wspace=0.14)
    ax1, ax2 = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
    panel_passes(ax1)
    panel_zones(ax2, data["shares"], data["seam_buffer_m"])
    header(ax1, "A", "Each file is two passes", "A satellite file is two passes stitched together")
    header(ax2, "B", "Overlay the seams: 3 zones", "Every pixel gets a pair: (before pass, after pass)")
    save(fig, "17_step1_zones.svg")

    fig = plt.figure(figsize=(16, 12.6), dpi=100, facecolor="white")
    gs = fig.add_gridspec(2, 2, left=0.06, right=0.985, top=0.935, bottom=0.1, wspace=0.14, hspace=0.5)
    ax1, ax2, ax3, ax4 = (fig.add_subplot(gs[i, j]) for i in range(2) for j in range(2))
    panel_shift(ax1, data)
    panel_trim(ax2, data, xlim, ylim)
    panel_fit(ax3, data, xlim, ylim)
    panel_after(ax4, data, ylim)
    header(ax1, "A", "The problem: each zone is shifted", "Same ground, three different haze and sun offsets")
    header(ax2, "B", "Find stable ground, fit a line", "Pixels that do not follow the line are left out")
    header(ax3, "C", "One line per zone", "Unchanged ground should look the same on both dates")
    header(ax4, "D", "After matching, change stands out", "Stable ground sits on the 1:1 line; change does not")
    save(fig, "18_step2_matching.svg")

    classes = data["classes"]
    dark, bright = min(classes), max(classes)
    lim = float(np.percentile(np.abs(classes[bright]["stable"]), 99.7)) * 1.6
    fig = plt.figure(figsize=(16, 13), dpi=100, facecolor="white")
    gs = fig.add_gridspec(2, 2, left=0.06, right=0.985, top=0.935, bottom=0.08, wspace=0.18, hspace=0.42, height_ratios=[1.15, 1])
    ax1, ax2, ax3 = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[1, :])
    n = data["n_classes"]
    panel_flicker(ax1, classes[dark], lim, f"Darkest class (1 of {n})")
    panel_flicker(ax2, classes[bright], lim, f"Brightest class ({n} of {n})")
    ax1.legend(
        handles=[
            Line2D([], [], marker="o", ls="", color=GREY_DOT, alpha=0.7, ms=8, label="stable ground (held out)"),
            Line2D([], [], marker="o", ls="", color=RED, ms=8, label="on the change map"),
            Line2D([], [], color=DARK_GREEN, lw=3, label="1-in-1,000 boundary (2 bands)"),
        ],
        loc="lower right",
        frameon=True,
        facecolor="white",
        edgecolor=FAINT,
        fontsize=SMALL_PT,
        labelcolor=INK,
    )
    panel_d2(ax3, data)
    header(ax1, "A", "Step 3: normal flicker, dark ground", "West zone. Change vector Δ of each pixel, 2 of 3 bands")
    header(ax2, "B", "Same zone, bright ground", "Bright ground flickers more: a bigger ellipse")
    header(ax3, "C", "Step 4: one score, one cut-off", "d² = distance from normal flicker, in units of that flicker")
    save(fig, "19_step3_4_noise.svg")
    fig = plt.figure(figsize=(16, 5.2), dpi=100, facecolor="white")
    ax = fig.add_axes((0.06, 0.13, 0.92, 0.66))
    panel_seam(ax, data)
    header(
        ax,
        "",
        "Step 5: the seam strip is unreliable",
        "All pixels, all zones. Real change is spread evenly; seam errors pile up in the first 30–40 m",
    )
    save(fig, "20_step5_seam.svg")
    mid, rate, plateau = data["seam"]
    print("seam profile:", ", ".join(f"{m:.0f} m {r / plateau:.1f}x" for m, r in zip(mid[:6], rate[:6], strict=True)))
    for z, (slope, icpt) in data["fits"].items():
        print(f"zone {z} B02 fit matches run_summary.json: before = {slope:.6f} * after + {icpt:.6f}")
    for k, c in classes.items():
        print(
            f"class {k}: brightness {c['bright']:.3f}, sd blue/red {np.sqrt(c['cov'][0, 0]):.4f} / {np.sqrt(c['cov'][1, 1]):.4f}"
        )


if __name__ == "__main__":
    main()
