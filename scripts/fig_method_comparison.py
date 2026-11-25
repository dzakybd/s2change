# ruff: noqa: E501  (the figure is an HTML/CSS template; its lines are markup, not code)
"""Render docs/figures/03_method_comparison.png: the provided example (Euclidean + Otsu) vs this work (calibrated CVA).

    uv run --with playwright python scripts/fig_method_comparison.py

Everything on the maps is the real raster: change_map_baseline / change_binary_baseline (provided example, red) and
change_map / change_binary (this work, teal), over the after-date image in grey. Numbers come from run_summary.json.
Laid out as HTML/CSS and printed by Chromium, like flow_figure.py.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import numpy as np
import rasterio
from matplotlib import colormaps
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
OUT = ROOT / "docs/figures/03_method_comparison.png"
TEAL, RED = "#0f766e", "#b91c1c"
MAP_PX = 1160  # embedded width of each map (shown at ~580 css px)


def read(name: str) -> np.ndarray:
    with rasterio.open(PROC / f"{name}.tif") as s:
        return s.read(1)


def uri(rgb: np.ndarray) -> str:
    img = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))
    h = round(img.height * MAP_PX / img.width)
    img = img.resize((MAP_PX, h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def underlay() -> np.ndarray:
    """Pale grey after-date image, so the binary maps keep their place on the ground."""
    with rasterio.open(PROC / "sentinel2_20230902_stack.tif") as s:
        r = (s.read().astype("float32") - 1000) / 10000
    lum = r.mean(axis=0)
    lo, hi = np.percentile(lum, 2), np.percentile(lum, 98)
    g = np.clip((lum - lo) / (hi - lo), 0, 1) ** 0.8
    return np.repeat((0.52 + 0.40 * g)[..., None], 3, axis=2)


def intensity(a: np.ndarray, valid: np.ndarray, vmax: float) -> np.ndarray:
    rgb = colormaps["magma"](np.clip(a / vmax, 0, 1))[..., :3]
    rgb[~valid] = 1
    return rgb


def binary(mask: np.ndarray, valid: np.ndarray, color: str, base: np.ndarray) -> np.ndarray:
    rgb = base.copy()
    rgb[mask & valid] = np.array([int(color[i : i + 2], 16) for i in (1, 3, 5)]) / 255
    rgb[~valid] = 1
    return rgb


def card(
    kind: str,
    tag: str,
    title: str,
    sub: str,
    big: str,
    pct: str,
    color: str,
    soft: str,
    imgs: tuple[str, str],
    chips: tuple[str, str],
    note: str,
) -> str:
    return f"""<section class="card {kind}" style="--c:{color};--soft:{soft}">
<header><span class="tag">{tag}</span><h2>{title}</h2><p>{sub}</p>
<div class="big"><b>{big}</b><span>{pct}</span></div></header>
<figure><img src="{imgs[0]}"><span class="chip">{chips[0]}</span><span class="scale"><i></i>5 km</span>
<div class="ramp"><span>less change</span><i></i><span>more</span></div></figure>
<figure><img src="{imgs[1]}"><span class="chip on">{chips[1]}</span><span class="scale"><i></i>5 km</span></figure>
<footer>{note}</footer></section>"""


def build() -> str:
    run = json.loads((PROC / "run_summary.json").read_text())
    ha = run["changed_area_ha"]
    n_cva = run["n_features"]["robust_cva"]
    with rasterio.open(PROC / "change_map.tif") as s:
        px_ha = abs(s.transform.a * s.transform.e) / 1e4
        scale_frac = 5000 / (s.width * s.transform.a)
    b_map, b_bin = read("change_map_baseline"), read("change_binary_baseline")
    c_map, c_bin = read("change_map"), read("change_binary")
    vb, vc = b_map != 255, c_map != -9999
    km2_b, km2_c = ha["euclidean_baseline"] / 100, ha["robust_cva"] / 100
    valid_ha = vb.sum() * px_ha
    pct_b, pct_c = ha["euclidean_baseline"] / valid_ha * 100, ha["robust_cva"] / valid_ha * 100
    share = run["footprint"]["register_mine_classes"]["share_of_change"] * 100
    base = underlay()
    b_img = uri(intensity(b_map.astype("float32"), vb, float(np.percentile(b_map[vb], 99.5))))
    c_img = uri(intensity(c_map, vc, float(np.percentile(c_map[vc], 99.5))))
    b_bin_img = uri(binary(b_bin == 1, vb, RED, base))
    c_bin_img = uri(binary(c_bin == 1, vc, TEAL, base))
    ratio = km2_b / km2_c

    red = card(
        "red",
        "Provided example",
        "Euclidean distance + Otsu",
        "One colour distance for every pixel, split in two by a single cut-off.",
        f"{km2_b:.1f} km²",
        f"{pct_b:.0f} % of the area flagged as changed",
        RED,
        "#fef2f2",
        (b_img, b_bin_img),
        ("Change intensity", "Flagged as changed"),
        "The straight line is the seam between two satellite passes. The western pass is brighter and hazier, so almost all of it is flagged: <b>an artefact, not mining</b>.",
    )
    teal = card(
        "teal",
        "This work",
        "Calibrated change vector analysis",
        "Colour shift per pixel, scored against measured noise, one pass at a time.",
        f"{km2_c:.2f} km²",
        f"{pct_c:.1f} % of the area, in {n_cva} regions",
        TEAL,
        "#f0fdfa",
        (c_img, c_bin_img),
        ("Change intensity", "Flagged as changed"),
        f"The pass-to-pass offset is calibrated out, so no large area is flagged at the seam. <b>{share:.0f} %</b> of what is flagged lies inside the 8 named mine facilities.",
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; margin: 0; }}
body {{ width: 1300px; font: 18px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #0f172a; background: #f6f7f9; padding: 30px 34px 34px; }}
h1 {{ font-size: 34px; letter-spacing: -.02em; }}
.sub {{ color: #475569; font-size: 19px; margin: 6px 0 22px; }}
.cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 26px; }}
.card {{ background: #fff; border-radius: 20px; padding: 22px 22px 20px; box-shadow: 0 0 0 1px rgb(15 23 42 / .08), 0 10px 28px rgb(15 23 42 / .10), 0 2px 6px rgb(15 23 42 / .06); border-top: 6px solid var(--c); display: grid; gap: 14px; align-content: start; }}
.tag {{ display: inline-block; font-size: 14px; font-weight: 800; letter-spacing: .13em; text-transform: uppercase; color: var(--c); background: var(--soft); border-radius: 99px; padding: 4px 12px; }}
h2 {{ font-size: 26px; letter-spacing: -.01em; margin-top: 8px; }}
header p {{ color: #475569; font-size: 17.5px; margin-top: 2px; min-height: 50px; }}
.big {{ margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }}
.big b {{ font-size: 56px; line-height: 1; letter-spacing: -.03em; color: var(--c); }}
.big span {{ font-size: 18.5px; font-weight: 700; color: #334155; }}
figure {{ position: relative; border-radius: 12px; overflow: hidden; box-shadow: 0 0 0 1px rgb(15 23 42 / .12); line-height: 0; }}
figure img {{ width: 100%; display: block; }}
.chip {{ position: absolute; left: 12px; top: 12px; line-height: 1.2; font-size: 16px; font-weight: 700; color: #0f172a; background: rgb(255 255 255 / .94); border-radius: 99px; padding: 6px 13px; box-shadow: 0 1px 4px rgb(15 23 42 / .25); }}
.chip.on::before {{ content: ""; display: inline-block; width: 12px; height: 12px; border-radius: 3px; background: var(--c); margin-right: 8px; vertical-align: -1px; }}
.scale {{ position: absolute; right: 12px; bottom: 12px; line-height: 1.2; font-size: 14px; font-weight: 700; color: #0f172a; background: rgb(255 255 255 / .92); border-radius: 8px; padding: 5px 10px; display: flex; align-items: center; gap: 8px; }}
.scale i {{ width: {scale_frac * 580:.0f}px; height: 6px; border: 2px solid #0f172a; border-top: 0; }}
.ramp {{ position: absolute; left: 12px; bottom: 12px; line-height: 1.2; font-size: 14px; font-weight: 700; color: #0f172a; background: rgb(255 255 255 / .92); border-radius: 8px; padding: 5px 10px; display: flex; align-items: center; gap: 8px; }}
.ramp i {{ width: 90px; height: 10px; border-radius: 5px; background: linear-gradient(90deg, #000004, #51127c, #b73779, #fc8961, #fcfdbf); }}
footer {{ font-size: 17px; color: #334155; background: var(--soft); border-radius: 12px; padding: 12px 16px; line-height: 1.4; }}
footer b {{ color: var(--c); }}
.verdict {{ margin-top: 22px; display: flex; align-items: center; justify-content: center; gap: 22px; background: #fff; border-radius: 18px; padding: 16px 24px; box-shadow: 0 0 0 1px rgb(15 23 42 / .08), 0 1px 2px rgb(15 23 42 / .06); }}
.verdict b.x {{ font-size: 44px; letter-spacing: -.02em; color: {TEAL}; line-height: 1; }}
.verdict span {{ font-size: 20px; color: #334155; }}
.verdict span b {{ color: #0f172a; }}
.fine {{ margin-top: 12px; font-size: 15px; color: #64748b; text-align: center; }}
</style></head><body>
<h1>Same two images, two answers</h1>
<p class="sub">Both methods see the same area (16.7 × 16.0 km) on the same two dates. Maps are the real result rasters.</p>
<div class="cols">{red}{teal}</div>
<div class="verdict"><b class="x">{ratio:.0f}× less</b><span>area flagged by this work: <b>{km2_c:.2f} km²</b> instead of <b>{km2_b:.1f} km²</b>, and it is mostly the mine.</span></div>
<p class="fine">Each intensity map uses its own method's scale, so colours are not comparable between the two.</p>
</body></html>"""


def main() -> None:
    page_html = ROOT / "docs/figures/.method_comparison.html"
    page_html.write_text(build())
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1300, "height": 900}, device_scale_factor=1.25)
        page.goto(page_html.as_uri(), wait_until="networkidle")
        page.screenshot(path=str(OUT), full_page=True)
        browser.close()
    page_html.unlink()
    print(f"{OUT}: {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
