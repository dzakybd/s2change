# ruff: noqa: E501  (the figure is an HTML/CSS template; its lines are markup, not code)
"""Render docs/figures/01_flow.png: input, analysis and output as isometric (3D) image layers, on the real data.

    uv run --with playwright python scripts/flow_figure.py      # after scripts/step_frames.py

The input band explodes each date into its three Sentinel-2 bands under the true-colour composite; the analysis band is
the five processing steps as tiles; the output band is the typed result with the deliverables. Tiles are the close-up
frames from docs/figures/steps (2.8 km around the tailings dam wall); type colours and pictograms come from
viewer/src/domain.ts.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from fig_common import app_types, icon
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
STEPS = ROOT / "docs/figures/steps"
OUT = ROOT / "docs/figures/01_flow.png"
HALF_PX = 140
BANDS = [("B02", "blue", (70, 140, 255)), ("B03", "green", (60, 200, 110)), ("B04", "red", (240, 80, 70))]


def uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=86)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def frame(n: int) -> str:
    return uri(Image.open(STEPS / f"close_{n:02d}.jpg").resize((360, 360), Image.Resampling.LANCZOS))


def band_layers(name: str, win: tuple[slice, slice]) -> list[str]:
    with rasterio.open(PROC / f"{name}.tif") as s:
        r = (s.read().astype("float32") - 1000) / 10000
    out = []
    for b, (_, _, tint) in enumerate(BANDS):
        a = r[b][win]
        lo, hi = np.percentile(a, 2), np.percentile(a, 98)
        g = np.clip((a - lo) / (hi - lo), 0, 1) ** 0.8
        rgb = (g[..., None] * (np.array(tint) / 255) * 0.92 + 0.04) * 255
        out.append(uri(Image.fromarray(rgb.astype(np.uint8)).resize((360, 360), Image.Resampling.LANCZOS)))
    return out


def stack(title: str, date: str, layers: list[str], composite: str) -> str:
    tiles = "".join(f'<div class="lay" style="--z:{i}"><img src="{src}"></div>' for i, src in enumerate([*layers, composite]))
    labels = "".join(
        f'<span style="--z:{i}"><b class="sw" style="background:rgb{c}"></b>{k} · {n}</span>' for i, (k, n, c) in enumerate(BANDS)
    )
    return f"""<div class="stack"><div class="iso-wrap"><div class="iso">{tiles}</div></div>
<div class="leg">{labels}<span class="top" style="--z:3"><b class="sw rgbsw"></b>true colour</span></div>
<div class="cap"><b>{title}</b><small>{date}</small></div></div>"""


def tile(n: int, k: int, title: str, text: str, out: str) -> str:
    return f"""<div class="step"><div class="iso-wrap sm"><div class="iso"><div class="lay solo"><img src="{frame(n)}"></div></div></div>
<div class="k">{k}</div><b>{title}</b><p>{text}</p><span class="out">{out}</span></div>"""


def build() -> str:
    types = app_types()
    with rasterio.open(PROC / "sentinel2_20230812_stack.tif") as s:
        t, shape = s.transform, (s.height, s.width)
    g = gpd.read_file(PROC / "change_features.geojson")
    g = g[(g.method == "robust_cva") & ~g.change_type.isin(["noise_or_artifact", "unexplained"])].to_crs(32735)
    focus = g.sort_values("area_m2", ascending=False).iloc[0]
    col, row = ~t * (focus.geometry.centroid.x, focus.geometry.centroid.y)
    win = (slice(int(row) - HALF_PX, int(row) + HALF_PX), slice(int(col) - HALF_PX, int(col) + HALF_PX))
    run = json.loads((PROC / "run_summary.json").read_text())
    km2 = run["changed_area_ha"]["robust_cva"] / 100
    n = run["n_features"]["robust_cva"]
    km = (shape[1] * t.a / 1000, shape[0] * -t.e / 1000)
    ft = types[focus.change_type]

    before = stack("Before", "nominal 12 Aug 2023", band_layers("sentinel2_20230812_stack", win), frame(1))
    after = stack("After", "nominal 2 Sep 2023", band_layers("sentinel2_20230902_stack", win), frame(2))
    steps = "".join(
        tile(*a)
        for a in [
            (3, 1, "Find the seams", "Each date is two satellite passes: split into 3 zones", "3 zones"),
            (4, 2, "Normalise", "Fit after to before on ground that did not change, per zone", "comparable pair"),
            (5, 3, "Measure change", "Colour shift per pixel, scored against measured noise", "change intensity"),
            (6, 4, "Threshold", "Keep what noise makes only 1 time in 1,000", "6.74 km², 4,479 specks"),
            (7, 5, "Clean", "Grow from strong cores; drop specks under 2,000 m²", f"{km2:.2f} km², {n} regions"),
        ]
    )
    deliver = [
        (
            "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3v12c0 1.7-3.6 3-8 3s-8-1.3-8-3zM4 6c0 1.7 3.6 3 8 3s8-1.3 8-3M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
            "PostGIS database",
            "polygons, type, sentence, per run",
        ),
        ("M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15M15 6v15", "Map + AI agent", "typed changes on sharp imagery"),
        ("M6 2h9l5 5v15H6zM14 2v6h6M9 13h8M9 17h6", "Report + downloads", "GeoTIFF, GeoJSON, SQL, PDF"),
    ]
    chips = "".join(
        f'<li><svg viewBox="0 0 24 24" width="30" height="30"><path d="{d}" fill="none" stroke="#15803d" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/></svg><div><b>{a}</b><small>{b}</small></div></li>'
        for d, a, b in deliver
    )
    sentence = f"{focus.location[0].upper()}{focus.location[1:]}: {focus.evidence}."
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; margin: 0; }}
body {{ width: 1500px; font: 18px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #0f172a; background: #fff; padding: 30px 34px 30px; }}
h1 {{ font-size: 34px; letter-spacing: -.02em; }}
.sub {{ color: #475569; font-size: 19px; margin: 6px 0 22px; }}
.band {{ position: relative; border-radius: 22px; padding: 18px 26px 20px 26px; display: grid; }}
.input .stacks {{ margin-top: -24px; }}
.band + .band {{ margin-top: 46px; }}
.band + .band::before {{ content: ""; position: absolute; left: 50%; top: -38px; width: 26px; height: 30px; margin-left: -13px; background: #94a3b8; clip-path: polygon(30% 0, 70% 0, 70% 50%, 100% 50%, 50% 100%, 0 50%, 30% 50%); }}
.tag {{ display: inline-flex; align-items: center; gap: 10px; font-size: 15px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }}
.tag i {{ width: 14px; height: 14px; border-radius: 4px; }}
.input {{ background: #eff6ff; box-shadow: inset 0 0 0 1.5px #bfdbfe; grid-template-columns: auto 1fr; }}
.input .tag {{ color: #1d4ed8; }} .input .tag i {{ background: #3b82f6; }}
.analysis {{ background: #fff7ed; box-shadow: inset 0 0 0 1.5px #fed7aa; }}
.analysis .tag {{ color: #c2410c; }} .analysis .tag i {{ background: #f97316; }}
.output {{ background: #f0fdf4; box-shadow: inset 0 0 0 1.5px #bbf7d0; }}
.output .tag {{ color: #15803d; }} .output .tag i {{ background: #22c55e; }}

.iso-wrap {{ position: relative; width: 300px; height: 300px; }}
.iso {{ position: absolute; left: 50%; top: 62%; width: 190px; height: 190px; margin: -95px 0 0 -95px; transform-style: preserve-3d; transform: rotateX(58deg) rotateZ(-42deg); }}
.lay {{ position: absolute; inset: 0; transform: translateZ(calc(var(--z) * 46px)); border-radius: 6px; background: #fff; box-shadow: 0 0 0 3px #fff, 1px 1px 0 3px #e2e8f0, 2px 2px 0 3px #cbd5e1, 3px 3px 0 3px #cbd5e1, 4px 4px 0 3px #94a3b8, 10px 12px 22px 2px rgb(15 23 42 / .18); }}
.lay img {{ display: block; width: 100%; height: 100%; border-radius: 4px; }}
.lay.solo {{ --z: 0; }}
.iso-wrap.sm {{ width: 250px; height: 168px; }}
.iso-wrap.sm .iso {{ top: 50%; width: 160px; height: 160px; margin: -80px 0 0 -80px; }}

.stacks {{ display: flex; gap: 10px; align-items: flex-end; }}
.stack {{ position: relative; width: 390px; height: 330px; }}
.stack .iso-wrap {{ position: absolute; left: 0; top: 0; width: 300px; height: 330px; }}
.stack .iso {{ top: 71%; }}
.leg {{ position: absolute; left: 262px; top: 0; height: 100%; }}
.leg span {{ position: absolute; left: 0; bottom: calc(94px + var(--z) * 39px); display: inline-flex; align-items: center; gap: 7px; font-size: 15px; font-weight: 600; color: #334155; white-space: nowrap; }}
.leg span::before {{ content: ""; position: absolute; right: 100%; width: 26px; margin-right: 4px; border-top: 1.5px dashed #94a3b8; }}
.leg .top {{ font-weight: 800; color: #0f172a; }}
.sw {{ width: 13px; height: 13px; border-radius: 3px; }}
.rgbsw {{ background: linear-gradient(90deg, #ef4444 33%, #22c55e 33% 66%, #3b82f6 66%); }}
.cap {{ position: absolute; left: 40px; bottom: 4px; display: grid; }}
.cap b {{ font-size: 21px; }} .cap small {{ font-size: 15px; color: #475569; }}
.facts {{ align-self: center; padding-left: 26px; display: grid; gap: 14px; }}
.facts ul {{ list-style: none; padding: 0; display: grid; gap: 10px; font-size: 17px; color: #334155; }}
.facts li {{ padding-left: 18px; position: relative; }}
.facts li::before {{ content: ""; position: absolute; left: 0; top: 9px; width: 8px; height: 8px; border-radius: 50%; background: #3b82f6; }}
.facts li b {{ color: #0f172a; }}

.row {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-top: 6px; }}
.step {{ position: relative; display: grid; justify-items: center; text-align: center; gap: 4px; }}
.step:not(:last-child)::after {{ content: ""; position: absolute; right: -16px; top: 74px; width: 18px; height: 22px; background: #f97316; clip-path: polygon(0 0, 100% 50%, 0 100%); }}
.step .k {{ position: absolute; left: 18px; top: 10px; width: 34px; height: 34px; border-radius: 50%; background: #c2410c; color: #fff; font-weight: 800; display: grid; place-items: center; font-size: 17px; box-shadow: 0 0 0 3px #fff7ed; }}
.step b {{ font-size: 20px; margin-top: 2px; }}
.step p {{ font-size: 15.5px; color: #475569; max-width: 245px; min-height: 44px; }}
.out {{ font-size: 14.5px; font-weight: 700; color: #c2410c; background: #fff; border-radius: 99px; padding: 3px 12px; box-shadow: 0 0 0 1px #fed7aa; }}

.output .grid {{ display: grid; grid-template-columns: 330px 1fr 380px; gap: 26px; align-items: center; }}
.result {{ position: relative; height: 290px; }}
.result .iso-wrap {{ width: 330px; height: 290px; }}
.result .iso {{ top: 60%; width: 200px; height: 200px; margin: -100px 0 0 -100px; }}
.pin {{ position: absolute; left: 50%; top: 6px; transform: translateX(-50%); display: grid; justify-items: center; filter: drop-shadow(0 4px 6px rgb(0 0 0 / .3)); }}
.pin::after {{ content: ""; width: 3px; height: 92px; background: linear-gradient({ft["color"]}, rgb(15 23 42 / .15)); }}
.card {{ background: #fff; border-radius: 16px; padding: 18px 22px; box-shadow: 0 1px 2px rgb(15 23 42 / .08), 0 0 0 1px rgb(15 23 42 / .06); }}
.card .t {{ display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; color: #64748b; }}
.card h3 {{ font-size: 26px; margin: 8px 0 6px; letter-spacing: -.01em; }}
.card p {{ font-size: 17px; color: #475569; }}
.card .chk {{ margin-top: 10px; font-weight: 700; color: #b4550c; }}
.card .n {{ margin-top: 12px; font-size: 15px; color: #334155; }}
.chips {{ list-style: none; padding: 0; display: grid; gap: 12px; }}
.chips li {{ display: flex; align-items: center; gap: 14px; background: #fff; border-radius: 14px; padding: 12px 16px; box-shadow: 0 0 0 1px #bbf7d0; }}
.chips b {{ display: block; font-size: 18px; }} .chips small {{ font-size: 15px; color: #475569; }}
</style></head><body>
<h1>From input to output: what happens to the images</h1>
<p class="sub">Every layer is the real data, cut around the same place: the tailings dam's south-east wall (2.8 × 2.8 km).</p>

<section class="band input"><span class="tag"><i></i>Input</span><span></span>
<div class="stacks">{before}{after}</div>
<div class="facts"><ul>
<li><b>Sentinel-2 L2A</b>, already corrected to surface reflectance by ESA</li>
<li><b>3 bands</b> (blue, green, red) at <b>10 m</b>, for <b>2 dates</b></li>
<li>{km[0]:.1f} × {km[1]:.1f} km around an open-pit copper mine</li>
<li>Each "date" is a <b>mosaic of two passes</b>, which is the trap the analysis handles first</li>
</ul></div></section>

<section class="band analysis"><span class="tag"><i></i>Analysis</span>
<div class="row">{steps}</div></section>

<section class="band output"><span class="tag"><i></i>Output</span>
<div class="grid"><div class="result"><div class="iso-wrap"><div class="iso"><div class="lay solo"><img src="{frame(8)}"></div></div></div><div class="pin">{icon(ft, 54)}</div></div>
<div class="card"><div class="t">{icon(ft, 22)} One typed polygon, explained</div><h3>{focus.headline}</h3><p>{sentence} {focus.area_m2 / 1e6:.2f} km².</p><p class="chk">{focus.what_to_check}</p>
<p class="n">Same for all <b>{n} polygons</b> ({km2:.2f} km²), each with a type, a facility and a sentence.</p></div>
<ul class="chips">{chips}</ul></div></section>
</body></html>"""


def main() -> None:
    page_html = ROOT / "docs/figures/.flow.html"
    page_html.write_text(build())
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 900}, device_scale_factor=1.25)
        page.goto(page_html.as_uri(), wait_until="networkidle")
        page.screenshot(path=str(OUT), full_page=True)
        browser.close()
    page_html.unlink()
    print(f"{OUT}: {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
