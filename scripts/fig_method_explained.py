"""Render docs/figures/16_method_explained.png: a six-step schematic of how the change map is made.

    uv run python scripts/fig_method_explained.py

Purely illustrative. Every number drawn here comes from a seeded synthetic example, not from satellite imagery;
the real numbers are in the report. Panels: (1) split into zones, (2) match brightness per zone, (3) measure normal
noise, (4) flag what noise rarely does, (5) skip the seams, (6) clean up.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon, Rectangle
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/figures/16_method_explained.png"

INK, MUTED, FAINT = "#0f172a", "#475569", "#94a3b8"
RED, GREY_CELL = "#dc2626", "#e2e8f0"
BLUE, BLUE_T = "#2563eb", "#bfdbfe"
ORANGE, ORANGE_T = "#ea580c", "#fed7aa"
GREEN, GREEN_T = "#15803d", "#86efac"
SAND, SAND_T = "#a16207", "#f2d38a"

TITLE_PT, SUB_PT, BODY_PT, SMALL_PT = 17, 11.5, 12, 11
RNG = np.random.default_rng(7)

plt.rcParams["font.family"] = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]
plt.rcParams["hatch.linewidth"] = 1.4


def header(ax, n: int, title: str, sub: str) -> None:
    ax.text(0, 1.14, f"{n}  {title}", transform=ax.transAxes, fontsize=TITLE_PT, fontweight="bold", color=INK, va="bottom")
    ax.text(0, 1.115, sub, transform=ax.transAxes, fontsize=SUB_PT, color=MUTED, va="top")


def arrow(ax, p0, p1, color=MUTED, lw=1.8) -> None:
    ax.annotate("", xy=p1, xytext=p0, arrowprops={"arrowstyle": "-|>", "color": color, "lw": lw, "shrinkA": 0, "shrinkB": 0})


def txt(ax, x, y, s, size=BODY_PT, color=INK, ha="left", va="center", weight="normal", **kw) -> None:
    ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va, fontweight=weight, linespacing=1.25, **kw)


def canvas(ax, width: float) -> float:
    """Free drawing area where one x unit and one y unit are the same length on the page; returns the y extent."""
    box = ax.get_position()
    w_in, h_in = box.width * ax.figure.get_figwidth(), box.height * ax.figure.get_figheight()
    height = width * h_in / w_in
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")
    return height


def seam_poly(x0, y0, s, fb, ft, side):
    """Polygon of the left or right part of an s-sided square cut by a slanted seam (bottom fraction fb, top fraction ft)."""
    xb, xt = x0 + fb * s, x0 + ft * s
    if side == "left":
        return [(x0, y0), (xb, y0), (xt, y0 + s), (x0, y0 + s)]
    return [(xb, y0), (x0 + s, y0), (x0 + s, y0 + s), (xt, y0 + s)]


# 1 ------------------------------------------------------------------------------------------------------------------
def panel_zones(ax) -> None:
    canvas(ax, 10)
    s_top, s_mid = 2.1, 2.8
    seam_before, seam_after = (0.42, 0.58), (0.54, 0.70)

    for x0, seam, label in ((1.0, seam_before, "Before"), (6.7, seam_after, "After")):
        y0 = 4.9
        for side, tint, letter in (("left", BLUE_T, "A"), ("right", ORANGE_T, "B")):
            ax.add_patch(Polygon(seam_poly(x0, y0, s_top, *seam, side), fc=tint, ec="none"))
            cx = x0 + (0.2 if side == "left" else 0.82) * s_top
            txt(ax, cx, y0 + s_top * 0.5, letter, size=16, color=BLUE if letter == "A" else ORANGE, ha="center", weight="bold")
        ax.add_patch(Rectangle((x0, y0), s_top, s_top, fc="none", ec=MUTED, lw=1.4))
        ax.plot([x0 + seam[0] * s_top, x0 + seam[1] * s_top], [y0, y0 + s_top], color=INK, lw=1.2, ls=(0, (3, 2)))
        txt(ax, x0 + s_top / 2, y0 + s_top + 0.35, label, size=BODY_PT, weight="bold", ha="center")
    txt(ax, 5.0, 5.95, "+", size=26, color=MUTED, ha="center")

    arrow(ax, (5.0, 4.7), (5.0, 4.3))
    txt(ax, 5.25, 4.5, "laid over each other", size=SMALL_PT, color=MUTED)

    x0, y0 = 3.6, 1.3
    west = [(x0, y0), (x0 + seam_before[0] * s_mid, y0), (x0 + seam_before[1] * s_mid, y0 + s_mid), (x0, y0 + s_mid)]
    mid = [
        (x0 + seam_before[0] * s_mid, y0),
        (x0 + seam_after[0] * s_mid, y0),
        (x0 + seam_after[1] * s_mid, y0 + s_mid),
        (x0 + seam_before[1] * s_mid, y0 + s_mid),
    ]
    east = seam_poly(x0, y0, s_mid, *seam_after, "right")
    ax.add_patch(Polygon(west, fc=BLUE_T, ec="none"))
    ax.add_patch(Polygon(east, fc=ORANGE_T, ec="none"))
    ax.add_patch(Polygon(mid, fc=BLUE_T, ec="#f59e0b", hatch="////", lw=0))
    ax.add_patch(Rectangle((x0, y0), s_mid, s_mid, fc="none", ec=MUTED, lw=1.4))
    txt(ax, x0 + 0.55, y0 + s_mid * 0.5, "West", size=BODY_PT, ha="center", weight="bold", color=BLUE)
    txt(ax, x0 + s_mid - 0.5, y0 + s_mid * 0.5, "East", size=BODY_PT, ha="center", weight="bold", color=ORANGE)
    txt(ax, 5.0, 0.6, "3 zones", size=BODY_PT, weight="bold", ha="center")

    txt(ax, 7.0, 2.3, "Middle strip:\nA before,\nB after", size=SMALL_PT, color=MUTED)
    arrow(ax, (6.95, 1.9), (x0 + (0.5 + 0.16 * 0.2) * s_mid + 0.05, y0 + s_mid * 0.2), color=MUTED, lw=1.3)
    for y, letter, col, tint in ((3.4, "A", BLUE, BLUE_T), (2.6, "B", ORANGE, ORANGE_T)):
        ax.add_patch(Rectangle((0.3, y - 0.25), 0.5, 0.5, fc=tint, ec="none"))
        txt(ax, 0.55, y, letter, size=SMALL_PT, color=col, ha="center", weight="bold")
    txt(ax, 0.95, 3.4, "one pass", size=SMALL_PT, color=MUTED)
    txt(ax, 0.95, 2.6, "next pass", size=SMALL_PT, color=MUTED)


# 2 ------------------------------------------------------------------------------------------------------------------
def panel_match(ax) -> None:
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(FAINT)
    ax.set_xlim(0, 0.5)
    ax.set_ylim(0, 0.9)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("Brightness before", fontsize=BODY_PT, color=MUTED, labelpad=4)
    ax.set_ylabel("Brightness after", fontsize=BODY_PT, color=MUTED, labelpad=4)

    xs = np.linspace(0.05, 0.42, 60)
    zones = ((BLUE, 0.04, 1.3, "West zone"), (ORANGE, 0.02, 1.1, "East zone"))
    ax.plot([0, 0.5], [0, 0.5], color=FAINT, lw=1.5, ls="--", zorder=1)
    txt(ax, 0.085, 0.075, "no shift (1:1)", size=SMALL_PT, color=FAINT, ha="left", va="top", rotation=23, rotation_mode="anchor")
    for col, a, b, _name in zones:
        x = RNG.uniform(0.05, 0.42, 60)
        y = a + b * x + RNG.normal(0, 0.012, x.size)
        ax.scatter(x, y, s=18, color=col, alpha=0.55, lw=0, zorder=2)
        slope, icpt = np.polyfit(x, y, 1)
        ax.plot(xs, icpt + slope * xs, color=col, lw=2.6, zorder=3)
    txt(ax, 0.435, 0.595, "West line", size=SMALL_PT, color=BLUE, ha="left", weight="bold", clip_on=False)
    txt(ax, 0.435, 0.47, "East line", size=SMALL_PT, color=ORANGE, ha="left", weight="bold", clip_on=False)

    change = np.array([[0.13, 0.40], [0.19, 0.50], [0.30, 0.13], [0.38, 0.19], [0.25, 0.08]])
    ax.scatter(*change.T, s=60, color=RED, zorder=4, lw=0)
    txt(ax, 0.01, 0.80, "Fit a line per zone on unchanged\nground, then map 'after' onto 'before'", size=BODY_PT)
    txt(ax, 0.27, 0.025, "real change stays off the line", size=BODY_PT, color=RED, ha="left", weight="bold")
    arrow(ax, (0.33, 0.06), (0.37, 0.165), color=RED, lw=1.5)


# 3 ------------------------------------------------------------------------------------------------------------------
def panel_noise(ax) -> None:
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(FAINT)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("Size of colour difference on unchanged ground", fontsize=BODY_PT, color=MUTED, labelpad=4)
    ax.set_ylabel("Number of pixels", fontsize=BODY_PT, color=MUTED, labelpad=4)

    n = 400_000
    forest = 0.5 * np.linalg.norm(RNG.normal(size=(n, 3)), axis=1)
    bright = 1.6 * np.linalg.norm(RNG.normal(size=(n, 3)), axis=1)
    cut_f, cut_b = np.percentile(forest, 99.9), np.percentile(bright, 99.9)
    flagged = (bright > cut_f).mean()

    edges = np.linspace(0, 10, 181)
    mid = (edges[:-1] + edges[1:]) / 2
    kernel = np.ones(5) / 5
    dens_f = np.convolve(np.histogram(forest, edges, density=True)[0], kernel, "same")
    dens_b = np.convolve(np.histogram(bright, edges, density=True)[0], kernel, "same")
    top = dens_f.max() * 1.55
    ax.set_xlim(0, 10)
    ax.set_ylim(0, top)

    ax.fill_between(mid, dens_f, color=GREEN_T, alpha=0.8, lw=0)
    ax.plot(mid, dens_f, color=GREEN, lw=2.2)
    ax.fill_between(mid, dens_b, color=SAND_T, alpha=0.8, lw=0)
    ax.plot(mid, dens_b, color=SAND, lw=2.2)
    over = mid >= cut_f
    ax.fill_between(mid[over], dens_b[over], facecolor="none", edgecolor=RED, hatch="///", lw=0, zorder=4)

    for cut, col in ((cut_f, GREEN), (cut_b, SAND)):
        ax.axvline(cut, ymax=0.8, color=col, lw=2, ls=(0, (4, 2)))
    txt(ax, cut_f + 0.12, top * 0.9, "forest cut-off", size=SMALL_PT, color=GREEN, weight="bold")
    txt(ax, cut_f + 0.12, top * 0.82, "1 in 1,000", size=SMALL_PT, color=GREEN)
    txt(ax, cut_b + 0.12, top * 0.9, "bright\ncut-off\n1 in 1,000", size=SMALL_PT, color=SAND, weight="bold", va="top")

    txt(ax, 0.15, top * 0.8, "Dark\nforest", size=BODY_PT, color=GREEN, weight="bold")
    txt(ax, 4.2, top * 0.27, "Bright\ntailings", size=BODY_PT, color=SAND, weight="bold")
    txt(
        ax,
        2.3,
        top * 0.66,
        f"One cut-off for all:\n{flagged:.0%} of bright ground\nwrongly flagged",
        size=SMALL_PT,
        color=RED,
        weight="bold",
    )
    arrow(ax, (3.9, top * 0.56), (3.9, dens_b[np.searchsorted(mid, 3.9)] + 0.01 * top), color=RED, lw=1.5)


# 4 ------------------------------------------------------------------------------------------------------------------
def panel_flag(ax) -> None:
    cols, rows = 40, 25
    height = canvas(ax, 45)
    grid = np.zeros((rows, cols), bool)
    speck = (5, 9)
    block = (slice(13, 19), slice(26, 33))
    grid[speck] = True
    grid[block] = True
    img = np.where(grid[..., None], np.array([0.863, 0.149, 0.149]), np.array([0.886, 0.910, 0.941]))
    pad = (height - rows) / 2
    ax.set_xlim(-2.5, 42.5)
    ax.set_ylim(rows - 0.5 + pad, -0.5 - pad)
    ax.imshow(img, extent=(-0.5, cols - 0.5, rows - 0.5, -0.5), interpolation="nearest", aspect="auto", zorder=1)
    ax.vlines(np.arange(-0.5, cols), -0.5, rows - 0.5, color="white", lw=0.9, zorder=2)
    ax.hlines(np.arange(-0.5, rows), -0.5, cols - 0.5, color="white", lw=0.9, zorder=2)

    txt(ax, -0.5, -3.7, "Unchanged ground: ~1 of 1,000 pixels\npasses by chance, as a lone speck", size=BODY_PT)
    arrow(ax, (speck[1], -1.3), (speck[1], speck[0] - 0.9), color=RED, lw=1.5)
    txt(
        ax,
        39.5,
        rows + 3.0,
        "Real change: passes everywhere,\nas a solid block",
        size=BODY_PT,
        color=RED,
        ha="right",
        weight="bold",
    )
    arrow(ax, (29.5, rows + 0.9), (29.5, 18.9), color=RED, lw=1.5)


# 5 ------------------------------------------------------------------------------------------------------------------
def panel_seams(ax) -> None:
    canvas(ax, 10)
    x0, y0, s = 0.4, 1.2, 5.6
    fb, ft, half = 0.36, 0.58, 0.55
    for side, tint in (("left", BLUE_T), ("right", ORANGE_T)):
        ax.add_patch(Polygon(seam_poly(x0, y0, s, fb, ft, side), fc=tint, ec="none", alpha=0.7))
    xb, xt = x0 + fb * s, x0 + ft * s
    band = [(xb - half, y0), (xb + half, y0), (xt + half, y0 + s), (xt - half, y0 + s)]
    ax.add_patch(Polygon(band, fc="#94a3b8", ec="none", alpha=0.75, zorder=2))
    ax.plot([xb, xt], [y0, y0 + s], color=INK, lw=1.2, ls=(0, (3, 2)), zorder=3)
    ax.add_patch(Rectangle((x0, y0), s, s, fc="none", ec=MUTED, lw=1.4, zorder=5))

    for t in (0.14, 0.45, 0.66, 0.88):
        cx = xb + (xt - xb) * t + (0.12 if t > 0.5 else -0.1)
        cy = y0 + s * t
        ax.add_patch(Rectangle((cx - 0.17, cy - 0.17), 0.34, 0.34, fc="#fecaca", ec=RED, lw=1.2, zorder=4))
        ax.plot([cx - 0.3, cx + 0.3], [cy - 0.3, cy + 0.3], color=INK, lw=1.6, zorder=6)
        ax.plot([cx - 0.3, cx + 0.3], [cy + 0.3, cy - 0.3], color=INK, lw=1.6, zorder=6)
    ax.add_patch(Rectangle((4.5, 4.9), 1.0, 0.9, fc=RED, ec="none", zorder=4))

    txt(ax, 6.2, 6.1, "Real change, away\nfrom the seam: kept", size=BODY_PT, color=RED, weight="bold")
    arrow(ax, (6.15, 5.55), (5.55, 5.4), color=RED, lw=1.5)
    txt(ax, 6.2, 4.0, "Chance specks on\nthe seam: ignored", size=BODY_PT, color=INK, weight="bold")
    arrow(ax, (6.15, 3.7), (3.4, 3.15), color=MUTED, lw=1.5)
    txt(ax, 6.2, 1.9, "Grey band: 90 m left\nout, 2.4 % of the area", size=BODY_PT, color=MUTED, weight="bold")
    arrow(ax, (6.15, 1.75), (xb + half * 0.6, y0 + 0.2), color=MUTED, lw=1.5)
    txt(ax, x0 + 0.35, y0 + s - 0.4, "A", size=16, color=BLUE, weight="bold", ha="center", zorder=7)
    txt(ax, x0 + s - 0.35, y0 + s - 0.4, "B", size=16, color=ORANGE, weight="bold", ha="center", zorder=7)


# 6 ------------------------------------------------------------------------------------------------------------------
def blob_map(n: int = 24) -> np.ndarray:
    m = np.zeros((n, n), bool)
    yy, xx = np.mgrid[:n, :n]
    m |= (yy - 10) ** 2 + (xx - 16) ** 2 <= 4.6**2  # big patch
    m[4:7, 2:5] = True  # small patch: 9 + 1 pixels
    m[5, 5] = True
    m[19, 2:16] = True  # one-pixel line
    taken = ndimage.binary_dilation(m, iterations=2)
    placed = 0
    while placed < 14:
        r, c = RNG.integers(0, n, 2)
        if not taken[r, c]:
            m[r, c] = True
            taken[max(r - 2, 0) : r + 3, max(c - 2, 0) : c + 3] = True
            placed += 1
    return m


def clean(m: np.ndarray, min_pixels: int = 20) -> np.ndarray:
    opened = ndimage.binary_opening(m, structure=np.ones((3, 3)))
    lab, k = ndimage.label(opened)
    sizes = ndimage.sum(opened, lab, range(1, k + 1))
    return np.isin(lab, 1 + np.flatnonzero(sizes >= min_pixels))


def panel_cleanup(ax) -> None:
    n, gap = 24, 8
    height = canvas(ax, 2 * n + gap + 4)
    before = blob_map(n)
    after = clean(before)
    top = 9.5
    ax.set_xlim(-2, 2 * n + gap + 2)
    ax.set_ylim(height, 0)
    for x0, m, name in ((0, before, "Before"), (n + gap, after, "After")):
        img = np.where(m[..., None], np.array([0.863, 0.149, 0.149]), np.array([0.886, 0.910, 0.941]))
        ax.imshow(img, extent=(x0 - 0.5, x0 + n - 0.5, top + n, top), interpolation="nearest", aspect="auto", zorder=1)
        ax.vlines(x0 + np.arange(-0.5, n), top, top + n, color="white", lw=0.8, zorder=2)
        ax.hlines(top + np.arange(0, n + 1), x0 - 0.5, x0 + n - 0.5, color="white", lw=0.8, zorder=2)
        txt(ax, x0 + n / 2 - 0.5, top - 6.2, name, size=BODY_PT, weight="bold", ha="center")
    arrow(ax, (n + 0.7, top + n / 2), (n + gap - 1.2, top + n / 2), lw=2.4)

    txt(ax, 3.5, top - 2.2, "small patch", size=SMALL_PT, color=MUTED, ha="center")
    arrow(ax, (3.5, top - 1.2), (3.5, top + 3.0), color=MUTED, lw=1.2)
    txt(ax, 17.5, top - 2.2, "big patch", size=SMALL_PT, color=MUTED, ha="center")
    arrow(ax, (17.5, top - 1.2), (17.5, top + 5.2), color=MUTED, lw=1.2)
    txt(ax, n + gap + 16.2, top - 2.2, "kept", size=SMALL_PT, color=RED, ha="center", weight="bold")
    arrow(ax, (n + gap + 16.2, top - 1.2), (n + gap + 16.2, top + 4.8), color=RED, lw=1.2)
    txt(ax, 8.5, top + n + 1.9, "1-pixel line", size=SMALL_PT, color=MUTED, ha="center")
    arrow(ax, (8.5, top + n + 1.1), (8.5, top + 19.9), color=MUTED, lw=1.2)
    txt(
        ax,
        n + gap / 2 - 0.5,
        top + n + 6.2,
        "Remove 1-pixel lines and specks,\ndrop patches under 0.2 ha (20 pixels)",
        size=BODY_PT,
        ha="center",
        weight="bold",
    )


def main() -> None:
    fig = plt.figure(figsize=(15, 9.5), dpi=150, facecolor="white")
    gs = fig.add_gridspec(2, 3, left=0.045, right=0.985, top=0.9, bottom=0.075, wspace=0.17, hspace=0.42)
    axes = [fig.add_subplot(gs[i, j]) for i in range(2) for j in range(3)]
    panels = [
        (panel_zones, "Split into zones", "Each 'date' is two satellite passes stitched together"),
        (panel_match, "Match brightness, per zone", "Different passes = different haze and sun"),
        (panel_noise, "Measure normal noise", "Bright mine ground flickers more than dark forest"),
        (panel_flag, "Flag what noise rarely does", "Cut-off: noise passes only 1 pixel in 1,000"),
        (panel_seams, "Skip the seams", "Pixels at a stitch line mix two passes"),
        (panel_cleanup, "Clean up", "Lone specks are chance; real change has size"),
    ]
    for i, (ax, (fn, title, sub)) in enumerate(zip(axes, panels, strict=True), start=1):
        fn(ax)
        header(ax, i, title, sub)
    fig.text(
        0.5,
        0.02,
        "Illustration with made-up data; the real numbers are in the report.",
        ha="center",
        fontsize=SMALL_PT,
        color=MUTED,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=150, bbox_inches="tight", facecolor="white")
    print(OUT)


if __name__ == "__main__":
    main()
