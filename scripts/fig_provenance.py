# ruff: noqa: E501  (the figure is an HTML/CSS template; its lines are markup, not code)
"""Render docs/figures/06_provenance.png: which satellite pass each pixel really comes from, on both nominal dates.

    uv run --with playwright python scripts/fig_provenance.py

Maps: data/processed/provenance/source_scene.tif (band 1 = 12 Aug, band 2 = 2 Sep; value = index into the source list of
provenance_summary.json) tinted over the true-colour mosaic, with the seams drawn where the source changes. The timeline
uses the true acquisition dates from provenance_summary.json and the zone pairs (before scene -> after scene) of run_summary.json.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
from fig_common import render, uri
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
OUT = ROOT / "docs/figures/06_provenance.png"
MAP_PX = 1000
WEST, EAST = "#6366f1", "#f59e0b"  # the two orbits: R121 (west) and R078 (east), the same on both dates
NOMINAL = {"2023-08-12": "12 Aug", "2023-09-02": "2 Sep"}
SEAM_BUFFER_PX = 9  # the 90 m around every seam that the pipeline leaves out


def short(d: str) -> str:
    x = date.fromisoformat(d)
    return f"{x.day} {x:%b}"


def hexrgb(c: str) -> np.ndarray:
    return np.array([int(c[i : i + 2], 16) for i in (1, 3, 5)]) / 255


def true_colour(name: str) -> np.ndarray:
    with rasterio.open(PROC / f"{name}.tif") as s:
        r = (s.read().astype("float32") - 1000) / 10000
    rgb = np.stack([r[2], r[1], r[0]], axis=-1)
    lo, hi = np.percentile(rgb, 2), np.percentile(rgb, 99)
    return np.clip((rgb - lo) / (hi - lo), 0, 1) ** 0.8


def map_img(base: np.ndarray, src: np.ndarray, west_idx: int) -> tuple[str, list[float]]:
    """Tinted mosaic with white seams. Returns the data URI and the x-centre (0-1) of the west and east regions."""
    valid = src >= 0
    tint = np.where((src == west_idx)[..., None], hexrgb(WEST), hexrgb(EAST))
    rgb = base * 0.50 + tint * 0.50
    rgb[~valid] = 1
    seam = (
        valid
        & (ndimage.grey_dilation(src, size=3) != ndimage.grey_erosion(src, size=3))
        & (ndimage.grey_erosion(src, size=3) >= 0)
    )
    rgb[ndimage.binary_dilation(seam, iterations=2)] = 1
    h, w = src.shape
    cols = np.arange(w)
    centres = [
        float(cols[(src == k).any(axis=0) & ((src == k).sum(axis=0) > h * 0.3)].mean() / w) for k in (west_idx, 1 - west_idx)
    ]
    img = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).resize(
        (MAP_PX, round(h * MAP_PX / w)), Image.Resampling.LANCZOS
    )
    return uri(img, quality=90), centres


def zone_img(
    base: np.ndarray, src: np.ndarray, west: tuple[int, int], buffer_px: int
) -> tuple[str, dict[str, tuple[float, float]]]:
    """The three zones (before pass x after pass): west tinted WEST, east EAST, the middle strip striped (west pass before,
    east pass after); the excluded seam buffer white. Returns the data URI and each zone's label position (x, y in 0-1)."""
    valid = (src >= 0).all(0)
    wb, wa = src[0] == west[0], src[1] == west[1]
    zones = {"West": valid & wb & wa, "Middle": valid & wb & ~wa, "East": valid & ~wb & ~wa}
    h, w = valid.shape
    yy, xx = np.mgrid[0:h, 0:w]
    stripes = ((xx + yy) // 14) % 2 == 0
    tint = np.zeros((h, w, 3))
    tint[zones["West"]] = hexrgb(WEST)
    tint[zones["East"]] = hexrgb(EAST)
    tint[zones["Middle"] & stripes] = hexrgb(WEST)
    tint[zones["Middle"] & ~stripes] = hexrgb(EAST)
    rgb = base.mean(-1, keepdims=True) * 0.45 + tint * 0.55
    seams = np.zeros_like(valid)
    for b in (0, 1):
        seams |= valid & (ndimage.grey_dilation(src[b], size=3) != ndimage.grey_erosion(src[b], size=3))
    rgb[ndimage.binary_dilation(seams, iterations=buffer_px) | ~valid] = 1
    top = yy < h * 0.18
    pos = {k: (float(xx[m & top].mean() / w), 0.09) for k, m in zones.items()}
    img = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).resize(
        (MAP_PX // 2, round(h * MAP_PX / 2 / w)), Image.Resampling.LANCZOS
    )
    return uri(img, quality=90), pos


def timeline(summary: dict, pairs: list[tuple[str, str, float, str]]) -> str:
    """SVG: one row per zone, a bar from its real before date to its real after date; the nominal pair on top, in grey."""
    t0, t1 = date(2023, 6, 24), date(2023, 9, 13)
    x0, x1, W = 330, 1195, 1240
    orbit = {s["datetime"][:10]: s["relative_orbit"] for v in summary["stac"].values() for s in v["sources"]}

    def X(d: str | date) -> float:
        d = date.fromisoformat(d) if isinstance(d, str) else d
        return x0 + (d - t0).days / (t1 - t0).days * (x1 - x0)

    top, step = 58, 78
    height = top + step * 4 + 10
    parts = []
    for d in (date(2023, 7, 1), date(2023, 7, 15), date(2023, 8, 1), date(2023, 8, 15), date(2023, 9, 1)):
        parts.append(f'<line x1="{X(d)}" x2="{X(d)}" y1="30" y2="{height - 40}" stroke="#e2e8f0" stroke-width="2"/>')
        parts.append(
            f'<text x="{X(d)}" y="{height - 12}" text-anchor="middle" font-size="17" fill="#64748b">{d.day} {d:%b}</text>'
        )

    swatch = {"West zone": WEST, "East zone": EAST, "Narrow middle strip": "url(#mid)"}

    def bar(i: int, a: str, b: str, left: str, sub: str, grey: bool) -> None:
        y = top + i * step
        lx = 0 if grey else 32
        if not grey:
            parts.append(f'<rect x="0" y="{y - 19}" width="22" height="22" rx="5" fill="{swatch[left]}"/>')
        n = (date.fromisoformat(b) - date.fromisoformat(a)).days
        col = lambda d: "#94a3b8" if grey else (WEST if orbit[d] == 121 else EAST)  # noqa: E731
        parts.append(
            f'<text x="{lx}" y="{y - 2}" font-size="21" font-weight="800" fill="{"#64748b" if grey else "#0f172a"}">{left}</text>'
        )
        parts.append(f'<text x="{lx}" y="{y + 22}" font-size="16.5" fill="#64748b">{sub}</text>')
        dash = ' stroke-dasharray="3 9"' if grey else ""
        parts.append(
            f'<line x1="{X(a)}" x2="{X(b)}" y1="{y}" y2="{y}" stroke="{"#94a3b8" if grey else "#0f172a"}" stroke-width="{5 if grey else 6}" stroke-linecap="round"{dash}/>'
        )
        for d in (a, b):
            parts.append(f'<circle cx="{X(d)}" cy="{y}" r="13" fill="{col(d)}" stroke="#fff" stroke-width="4"/>')
        parts.append(
            f'<text x="{X(a) - 22}" y="{y + 6}" text-anchor="end" font-size="19" font-weight="700" fill="#0f172a">{short(a)}</text>'
        )
        parts.append(f'<text x="{X(b) + 22}" y="{y + 6}" font-size="19" font-weight="700" fill="#0f172a">{short(b)}</text>')
        mid = (X(a) + X(b)) / 2
        parts.append(
            f'<text x="{mid}" y="{y - 16}" text-anchor="middle" font-size="22" font-weight="800" fill="{"#64748b" if grey else "#b91c1c"}">{n} days</text>'
        )

    bar(0, "2023-08-12", "2023-09-02", "What the labels say", "nominal 12 Aug to 2 Sep", True)
    for i, (bef, aft, share, name) in enumerate(pairs, start=1):
        bar(i, bef, aft, name, f"{share:.0f} % of the area", False)
    pattern = (
        f'<defs><pattern id="mid" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">'
        f'<rect width="4" height="8" fill="{WEST}"/><rect x="4" width="4" height="8" fill="{EAST}"/></pattern></defs>'
    )
    return f'<svg viewBox="0 0 {W} {height}" width="100%">{pattern}{"".join(parts)}</svg>'


def build() -> str:
    summary = json.loads((PROC / "provenance/provenance_summary.json").read_text())
    with rasterio.open(PROC / "provenance/source_scene.tif") as s:
        src = s.read()
        tags = s.tags()
    # order of the source list per date (the raster values index it)
    names = {d: tags["SOURCES_" + d.replace("-", "")].split("; ") for d in NOMINAL}
    panels, shares, wests = [], {}, []
    for band, (d, base_name) in enumerate(
        (("2023-08-12", "sentinel2_20230812_stack"), ("2023-09-02", "sentinel2_20230902_stack"))
    ):
        labels = names[d]
        orbit_of = {s["label"]: s["relative_orbit"] for s in summary["stac"][d]["sources"]}
        west_idx = next(i for i, lab in enumerate(labels) if orbit_of[lab] == 121)
        wests.append(west_idx)
        img, (cw, ce) = map_img(true_colour(base_name), src[band], west_idx)
        valid = src[band] >= 0
        fw = float(((src[band] == west_idx) & valid).sum() / valid.sum() * 100)
        shares[d] = (fw, 100 - fw)
        wl, el = labels[west_idx], labels[1 - west_idx]
        wd, ed = wl[:10], el[:10]
        panels.append(
            f"""<section class="panel"><header><span class="k">{NOMINAL[d]}</span><div><b>Nominal date {short(d)}</b><small>what the file name says</small></div></header>
<figure><img src="{img}">
<span class="pill" style="left:{cw * 100:.1f}%;top:46%;--c:{WEST}">West pass<b>{short(wd)}</b></span>
<span class="pill" style="left:{ce * 100:.1f}%;top:46%;--c:{EAST}">East pass<b>{short(ed)}</b></span></figure>
<div class="split"><i style="width:{fw:.1f}%;background:{WEST}"></i><i style="width:{100 - fw:.1f}%;background:{EAST}"></i></div>
<div class="splitl"><span><b>{fw:.0f} %</b> from {short(wd)}</span><span><b>{100 - fw:.0f} %</b> from {short(ed)}</span></div></section>"""
        )
    # zone pairs: (before date, after date, share of the area), from the raster
    pairs, tot = [], (src[0] >= 0) & (src[1] >= 0)
    combos = {}
    lab = {d: [x[:10] for x in names[d]] for d in NOMINAL}
    for bi in (0, 1):
        for ai in (0, 1):
            m = tot & (src[0] == bi) & (src[1] == ai)
            if m.sum() > 0.01 * tot.sum():
                combos[(lab["2023-08-12"][bi], lab["2023-09-02"][ai])] = m.sum() / tot.sum() * 100
    west = {
        d: {x[:10] for x in names[d] if {s["label"]: s["relative_orbit"] for s in summary["stac"][d]["sources"]}[x] == 121}
        for d in NOMINAL
    }
    zone = {(True, True): "West zone", (True, False): "Narrow middle strip", (False, False): "East zone"}
    pairs = [(b, a, s, zone[(b in west["2023-08-12"], a in west["2023-09-02"])]) for (b, a), s in combos.items()]
    pairs.sort(key=lambda t: (date.fromisoformat(t[1]) - date.fromisoformat(t[0])).days, reverse=True)
    gaps = sorted((date.fromisoformat(a) - date.fromisoformat(b)).days for b, a, _, _ in pairs)
    gap_txt = f"{gaps[0]}–{gaps[-1]}"
    match = summary["provided_vs_planetary_computer_mosaic"]
    exact = min(v["exact_match_fraction"] for d in match.values() for v in d.values())
    svg = timeline(summary, pairs)
    zimg, zpos = zone_img(true_colour("sentinel2_20230902_stack"), src, (wests[0], wests[1]), SEAM_BUFFER_PX)
    share = {name.split()[0] if name != "Narrow middle strip" else "Middle": s for _, _, s, name in pairs}
    zone_pills = "".join(
        f'<span class="pill sm" style="left:{zpos[k][0] * 100:.1f}%;top:{zpos[k][1] * 100:.0f}%;--c:{c}">{k}</span>'
        for k, c in (("West", WEST), ("Middle", "#0f172a"), ("East", EAST))
    )
    panels.append(
        f"""<section class="panel"><header><span class="k plus">=</span><div><b>Overlaid: 3 zones</b><small>each one real before → after pair</small></div></header>
<figure><img src="{zimg}">{zone_pills}</figure>
<div class="split"><i style="width:{share["West"]:.1f}%;background:{WEST}"></i><i class="mid" style="width:{share["Middle"]:.1f}%"></i><i style="width:{share["East"]:.1f}%;background:{EAST}"></i></div>
<div class="splitl"><span><b>{share["West"]:.0f} %</b> west</span><span><b>{share["Middle"]:.0f} %</b> middle</span><span><b>{share["East"]:.0f} %</b> east</span></div></section>"""
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; margin: 0; }}
body {{ width: 1300px; font: 18px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #0f172a; background: #f6f7f9; padding: 30px 34px 34px; }}
h1 {{ font-size: 34px; letter-spacing: -.02em; }}
.sub {{ color: #475569; font-size: 19px; margin: 6px 0 22px; }}
.card {{ background: #fff; border-radius: 20px; padding: 22px; box-shadow: 0 0 0 1px rgb(15 23 42 / .08), 0 10px 28px rgb(15 23 42 / .10), 0 2px 6px rgb(15 23 42 / .06); }}
.cols {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 22px; }}
.panel header {{ display: flex; align-items: center; gap: 14px; margin-bottom: 14px; }}
.panel .k {{ background: #0f172a; color: #fff; font-weight: 800; border-radius: 12px; padding: 8px 14px; font-size: 20px; }}
.panel header b {{ font-size: 21px; display: block; line-height: 1.2; }} .panel header small {{ color: #64748b; font-size: 15.5px; }}
figure {{ position: relative; border-radius: 12px; overflow: hidden; box-shadow: 0 0 0 1px rgb(15 23 42 / .12); line-height: 0; }}
figure img {{ width: 100%; display: block; }}
.pill {{ position: absolute; transform: translate(-50%, -50%); line-height: 1.15; text-align: center; font-size: 15px; font-weight: 700; color: #334155; background: rgb(255 255 255 / .96); border-radius: 14px; padding: 8px 14px; box-shadow: 0 0 0 3px var(--c), 0 4px 10px rgb(15 23 42 / .3); white-space: nowrap; }}
.pill b {{ display: block; font-size: 20px; color: #0f172a; }}
.pill.sm {{ font-size: 15px; padding: 5px 10px; color: #0f172a; }}
.panel .k.plus {{ background: #fff; color: #0f172a; box-shadow: 0 0 0 2px #0f172a; }}
.split i.mid {{ background: repeating-linear-gradient(-45deg, {WEST} 0 4px, {EAST} 4px 8px); }}
.split {{ display: flex; height: 14px; border-radius: 7px; overflow: hidden; margin-top: 14px; }}
.splitl {{ display: flex; justify-content: space-between; font-size: 17px; color: #334155; margin-top: 6px; }}
.splitl b {{ color: #0f172a; }}
.legend {{ display: flex; gap: 26px; align-items: center; margin: 16px 2px 0; font-size: 16.5px; color: #475569; }}
.legend i {{ display: inline-block; vertical-align: -3px; }}
.seam {{ width: 26px; height: 4px; background: #fff; box-shadow: 0 0 0 1.5px #94a3b8; border-radius: 2px; margin-right: 8px; }}
.sw {{ width: 14px; height: 14px; border-radius: 4px; margin-right: 8px; }}
.sw.mid {{ background: repeating-linear-gradient(-45deg, {WEST} 0 3px, {EAST} 3px 6px); }}
.time {{ margin-top: 24px; }}
.time h2 {{ font-size: 26px; letter-spacing: -.01em; }}
.time h2 em {{ font-style: normal; color: #b91c1c; }}
.time p {{ color: #475569; font-size: 17.5px; margin: 4px 0 14px; max-width: 1100px; }}
.foot {{ margin-top: 18px; display: flex; gap: 14px; align-items: center; font-size: 17px; color: #334155; background: #ecfdf5; border-radius: 14px; padding: 12px 18px; box-shadow: 0 0 0 1px #bbf7d0; }}
.foot b {{ color: #15803d; }}
</style></head><body>
<h1>Each "date" is really two satellite passes</h1>
<p class="sub">Source pass of every pixel on both nominal dates (true-colour mosaic, tinted). White line = the seam between the two passes.</p>
<div class="card"><div class="cols">{panels[0]}{panels[1]}{panels[2]}</div>
<div class="legend"><span><i class="sw" style="background:{WEST}"></i>West pass (orbit 121)</span><span><i class="sw" style="background:{EAST}"></i>East pass (orbit 78)</span><span><i class="sw mid"></i>middle strip: west pass before, east pass after</span><span><i class="seam"></i>seam (tilted 12.6°); 90 m around it left out</span></div></div>
<div class="card time"><h2>Before and after are <em>{gap_txt} days apart, not 21</em></h2>
<p>Each zone of the map compares one real "before" pass with one real "after" pass. Dot colour = which pass (as on the maps).</p>
{svg}</div>
<div class="foot"><span><b>Proof:</b> all three bands match the Planetary Computer scenes <b>pixel for pixel</b> ({exact * 100:.0f} % identical, 2.6 million pixels per band).</span></div>
</body></html>"""


def main() -> None:
    render(build(), OUT, width=1300)


if __name__ == "__main__":
    main()
