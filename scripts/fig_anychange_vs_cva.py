# ruff: noqa: E501  (the figure is an HTML/CSS template; its lines are markup, not code)
"""Render docs/figures/15_anychange_vs_cva.png: this method against the AnyChange zero-shot model on the same images.

    uv run --with torchange --with torch --with torchvision --with huggingface_hub --with scikit-image \
        python scripts/anychange_experiment.py         # once: saves data/validation/anychange/{masks.npz,stats.json}
    uv run --with playwright python scripts/fig_anychange_vs_cva.py

The panels are the saved masks drawn on the true-colour after image (per-zone normalised, as both methods saw it); the
table is how much of each of this method's typed changes each AnyChange mask covers, from the polygons in
data/processed/change_features.geojson.
"""

from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import rasterio
import rasterio.features
from fig_common import CSS, ROOT, TEAL, app_types, icon, render, true_colour, uri
from PIL import Image
from scipy.ndimage import binary_erosion

OUT = ROOT / "docs/figures/15_anychange_vs_cva.png"
DATA = ROOT / "data/validation/anychange"
ANY = "#2563eb"  # AnyChange: blue, so it never reads as this work (teal) or as a change type
VIEW = (slice(215, 1435), slice(395, 1585))  # the mine core and the reservoir, in pixels of the 10 m grid
TYPES = [
    "tailings_deposition",
    "waste_dumping",
    "tailings_dam_raise",
    "seasonal_vegetation_or_burn",
    "pit_excavation",
    "water_level_change",
]


def cell(v: float) -> str:
    return f'<div class="cv"><div class="bar"><i style="width:{max(v * 100, 0):.1f}%;background:{ANY}"></i></div><b>{v * 100:.0f} %</b></div>'


def paint(img: np.ndarray, mask: np.ndarray, color: str) -> Image.Image:
    rgb = np.array([int(color[i : i + 2], 16) for i in (1, 3, 5)])
    out = (img[VIEW] * 0.6).astype(float)
    m = mask[VIEW]
    out[m] = out[m] * 0.25 + rgb * 0.75
    out[m & ~binary_erosion(m, iterations=1)] = (255, 255, 255)
    return Image.fromarray(out.astype(np.uint8))


def build() -> str:
    types = app_types()
    img, tf = true_colour("sentinel2_20230902_stack_normalized")
    masks = np.load(DATA / "masks.npz")
    stats = json.loads((DATA / "stats.json").read_text())
    shape, crs = masks["cva"].shape, "EPSG:32735"

    g = gpd.read_file(ROOT / "data/processed/change_features.geojson")
    g = g[g.method == "robust_cva"].to_crs(crs)
    fac = gpd.read_file(ROOT / "docs/web/facilities.geojson").to_crs(crs).set_index("facility_id")

    def rast(geoms) -> np.ndarray:
        return rasterio.features.geometry_mask(list(geoms), shape, tf, invert=True)

    cover = {
        k: {
            s: float((rast(g[g.change_type == k].geometry) & masks[s]).sum() / rast(g[g.change_type == k].geometry).sum())
            for s in ("145 (strict)", "auto (Otsu)")
        }
        for k in TYPES
    }
    dam = rast(fac.loc[["DAM"]].geometry)
    dam_flag = {s: float((dam & masks[s]).sum() / dam.sum()) for s in ("cva", "145 (strict)", "auto (Otsu)")}
    km2_dam = dam.sum() * 100 / 1e6

    h, w = img[VIEW].shape[:2]
    to_pct = lambda c: (f"{(c[0] - VIEW[1].start) / w * 100:.2f}%", f"{(c[1] - VIEW[0].start) / h * 100:.2f}%")  # noqa: E731
    wall = g[g.change_type == "tailings_dam_raise"].geometry.union_all().centroid
    wx, wy = to_pct(~tf * (wall.x, wall.y))
    rc = fac.loc["DAM"].geometry.representative_point()
    rx, ry = to_pct(~tf * (rc.x, rc.y))
    pin = f'<div class="pin" style="left:{wx};top:{wy}">{icon(types["tailings_dam_raise"], 34)}</div>'
    tag = f'<span class="rt" style="left:{rx};top:{ry}">reservoir</span>'

    def panel(key: str, who: str, how: str, color: str, accent: str, mine: bool = False) -> str:
        s = stats["cva" if key == "cva" else f"anychange {key}"]
        found = s["share_of_cva_found"]
        badge = '<span class="me">this work</span>' if mine else ""
        found_html = "all of it, by definition" if mine else f"<b>{found * 100:.0f} %</b> of this method's change"
        return f"""<section class="card pn" style="--c:{accent}"><div class="hd"><h2>{who}</h2>{badge}</div><p class="how">{how}</p>
<div class="frame"><img src="{uri(paint(img, masks[key], color))}">{pin}{tag}</div>
<div class="st"><div class="big"><b style="color:{accent}">{s["changed_km2"]:.2f}</b><span>km² flagged</span></div>
<ul><li>{found_html}</li><li><b>{s["share_inside_facilities"] * 100:.0f} %</b> inside the 8 facilities</li><li><b>{dam_flag[key] * 100:.0f} %</b> of the reservoir flagged</li></ul></div></section>"""

    panels = (
        panel("cva", "Calibrated robust CVA", "Colour change per pixel, scored against measured noise", TEAL, TEAL, mine=True)
        + panel("145 (strict)", "AnyChange, strict threshold", "Zero-shot Segment-Anything model, confidence ≥ 145", ANY, ANY)
        + panel("auto (Otsu)", "AnyChange, automatic threshold", "The same model with its own automatic threshold", ANY, ANY)
    )

    rows = ""
    for k in TYPES:
        t = types[k]
        rows += f'<div class="tr"><div class="nm">{icon(t, 30)}<span>{t["label"]}</span></div><div class="cv"><div class="bar"><i style="width:100%;background:{TEAL}"></i></div><b>all</b></div>{cell(cover[k]["145 (strict)"])}{cell(cover[k]["auto (Otsu)"])}</div>'

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
body {{ width: 1500px; }}
.panels {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 22px; }}
.pn {{ padding: 16px; border-top: 5px solid var(--c); }}
.hd {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }} .hd h2 {{ font-size: 20px; }}
.me {{ font-size: 12px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; color: #fff; background: {TEAL}; padding: 3px 10px; border-radius: 99px; white-space: nowrap; }}
.how {{ font-size: 15px; color: #64748b; margin: 2px 0 10px; min-height: 42px; }}
.frame {{ position: relative; border-radius: 12px; overflow: hidden; aspect-ratio: {w} / {h}; background: #0f172a; }}
.frame img {{ position: absolute; inset: 0; width: 100%; height: 100%; display: block; }}
.pin {{ position: absolute; transform: translate(-50%, -50%); filter: drop-shadow(0 2px 4px rgb(0 0 0 / .6)); }}
.rt {{ position: absolute; transform: translate(-50%, -50%); font-size: 13px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; color: #fff; text-shadow: 0 1px 3px #000, 0 0 6px #000; }}
.st {{ display: grid; grid-template-columns: auto 1fr; gap: 18px; align-items: center; margin-top: 12px; }}
.big b {{ display: block; font-size: 38px; letter-spacing: -.02em; line-height: 1; }} .big span {{ font-size: 14.5px; color: #64748b; }}
.st ul {{ list-style: none; padding: 0; display: grid; gap: 3px; font-size: 15.5px; color: #334155; }} .st ul b {{ color: #0f172a; }}
.lower {{ display: grid; grid-template-columns: 1.45fr 1fr; gap: 22px; margin-top: 22px; }}
.tb h2 {{ margin-bottom: 4px; }}
.th, .tr {{ display: grid; grid-template-columns: 1.6fr 1fr 1fr 1fr; gap: 16px; align-items: center; }}
.th {{ font-size: 13px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: #64748b; padding: 8px 0; border-bottom: 1px solid #e2e8f0; }}
.tr {{ padding: 9px 0; border-bottom: 1px solid #f1f5f9; }} .tr:last-child {{ border: 0; }}
.nm {{ display: flex; align-items: center; gap: 10px; font-size: 16.5px; font-weight: 600; }}
.cv {{ display: flex; align-items: center; gap: 10px; }} .cv b {{ font-size: 16px; min-width: 40px; text-align: right; font-variant-numeric: tabular-nums; }}
.bar {{ flex: 1; height: 10px; background: #f1f5f9; border-radius: 99px; overflow: hidden; }} .bar i {{ display: block; height: 100%; border-radius: 99px; }}
.why {{ background: #eff6ff; box-shadow: inset 0 0 0 1px #bfdbfe; }} .why h2 {{ color: #1d4ed8; }}
.why ul {{ list-style: none; padding: 0; margin-top: 10px; display: grid; gap: 12px; font-size: 16.5px; color: #334155; }}
.why li {{ padding-left: 20px; position: relative; }} .why li::before {{ content: ""; position: absolute; left: 0; top: 9px; width: 9px; height: 9px; border-radius: 50%; background: {ANY}; }}
.why b {{ color: #0f172a; }}
</style></head><body>
<span class="tag"><i></i>This work vs a zero-shot model</span>
<h1>AnyChange finds a tenth of the change and misses the mine</h1>
<p class="sub">The same two images, the same 8-bit stretch. AnyChange (NeurIPS 2024) is a Segment-Anything change model that needs no training.</p>
<div class="panels">{panels}</div>
<div class="lower">
<section class="card tb"><h2>How much of each change type is flagged <small>by area</small></h2>
<div class="th"><span>Change type</span><span>This work</span><span>AnyChange, strict</span><span>AnyChange, auto</span></div>{rows}</section>
<section class="card why"><h2>Why it fails here</h2><ul>
<li><b>It flags the reservoir, not the mine.</b> The automatic threshold marks {dam_flag["auto (Otsu)"] * 100:.0f} % of the {km2_dam:.1f} km² water reservoir, where this method finds {dam_flag["cva"] * 100:.0f} %.</li>
<li><b>The dam raise, tailings and dumping are mostly missed:</b> {cover["tailings_dam_raise"]["145 (strict)"] * 100:.0f} % of the dam wall and {cover["waste_dumping"]["auto (Otsu)"] * 100:.0f} % of the waste dumping are flagged, {cover["tailings_deposition"]["auto (Otsu)"] * 100:.0f} % of the tailings deposition.</li>
<li><b>It segments whole objects.</b> At 10 m, a tailings beach or a dam-wall strip is a part of an object, and the model resizes the area to about 16 m pixels.</li></ul></section>
</div>
<p class="foot">Percentages of the table are the share of each type's polygon area under the mask, from the saved masks (scripts/anychange_experiment.py). SAM ViT-B, 32 points per side.</p>
</body></html>"""


def main() -> None:
    render(build(), OUT, width=1500)


if __name__ == "__main__":
    main()
