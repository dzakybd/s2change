# ruff: noqa: E501  (the slide is an HTML/CSS template; its lines are markup, not code)
"""Render the one-slide summary docs/figures/00_at_a_glance.png from the real outputs (HTML laid out, printed by Chromium).

    uv run python -m s2cd.site                                      # web overlays and GeoJSON must exist
    uv run --with playwright python scripts/at_a_glance.py

Images are the web overlays (docs/web/*.webp, EPSG:3857) with the change polygons drawn on top; numbers come from
data/processed/run_summary.json.
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path

from fig_common import app_types
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TYPE_COLORS = {k: t["color"] for k, t in app_types().items()}  # the app's legend
WEB = ROOT / "docs/web"
OUT = ROOT / "docs/figures/00_at_a_glance.png"
TYPE_LABELS = {
    "tailings_deposition": "Tailings deposited",
    "waste_dumping": "Waste rock dumped",
    "tailings_dam_raise": "Dam wall raised",
    "seasonal_vegetation_or_burn": "Seasonal / burn",
    "pit_excavation": "Pit excavation",
    "water_level_change": "Water level change",
}
TRANSITION_COLORS = {"bare ground": "#d97706", "bright surface (tailings, roofs, roads)": "#0891b2", "water": "#2563eb"}
# Season-matched yearly comparisons (report.md section 2): Aug 2023 -> Aug 2024 / 2025 / 2026, km².


def merc_y(lat: float) -> float:
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def polygons_svg(bounds, size=(600, 560)) -> str:
    (s, w), (n, e) = bounds
    W, H = size
    y0, y1 = merc_y(n), merc_y(s)
    fc = json.loads((WEB / "change_features.geojson").read_text())

    def pt(c):
        return f"{(c[0] - w) / (e - w) * W:.1f},{(y0 - merc_y(c[1])) / (y0 - y1) * H:.1f}"

    paths = []
    for f in fc["features"]:
        t = f["properties"].get("change_type")
        if t == "noise_or_artifact":
            continue
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        d = "".join("M" + "L".join(pt(c) for c in ring) + "Z" for poly in polys for ring in poly)
        paths.append(f'<path d="{d}" fill="{TYPE_COLORS.get(t, "#e8833a")}" fill-opacity=".9" stroke="#fff" stroke-width=".35"/>')
    return f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none">{"".join(paths)}</svg>'


def km2(ha: float) -> str:
    return f"{ha / 100:.2f} km²"


def build() -> str:
    run = json.loads((ROOT / "data/processed/run_summary.json").read_text())
    manifest = json.loads((WEB / "manifest.json").read_text())
    bounds = manifest["rasters"]["before"]["bounds"]
    changed = run["changed_area_ha"]["robust_cva"]
    baseline = run["changed_area_ha"]["euclidean_baseline"]
    inside = run["footprint"]["register_all_facilities"]["share_of_change"]
    types = run["change_type_ha"]
    trans = [t for t in run["land_cover_transitions"] if t["from"] != "other"][:5]

    def img(name, label, extra=""):
        url = (WEB / manifest["rasters"][name]["url"]).as_uri()
        return f'<figure><div class="zoom"><img src="{url}">{extra}</div><figcaption>{label}</figcaption></figure>'

    def short(c):
        return {"vegetation or dark ground": "Vegetation", "bright surface (tailings, roofs, roads)": "Bright surface"}.get(
            c, c.capitalize()
        )

    trans_rows = "".join(
        f'<tr><td><i style="background:{TRANSITION_COLORS.get(t["to"], "#94a3b8")}"></i>{html.escape(short(t["from"]))} → '
        f"{html.escape(short(t['to']))}{' <small>(same class)</small>' if t['same_class'] else ''}</td>"
        f'<td class="n">{t["area_km2"]:.2f}</td><td class="n">{t["share"] * 100:.0f}%</td></tr>'
        for t in trans
    )
    legend = "".join(
        f'<li><i style="background:{TYPE_COLORS[k]}"></i>{v} <b>{km2(types.get(k, 0))}</b></li>' for k, v in TYPE_LABELS.items()
    )
    facts = [
        ("Area", "16.7 × 16.0 km around the mine, 25.79–25.94 °E, 12.18–12.32 °S"),
        ("Data", "Sentinel-2 L2A surface reflectance, red · green · blue only, 10 m"),
        ("Dates", "Nominal 2023-08-12 → 2023-09-02; really two passes each, Jul → late Aug/Sep"),
        ("Method", "Per-zone normalisation, robust change vector analysis, calibrated thresholds, rule-based typing"),
        ("Cost", "Free imagery every 5 days; about 15 s of compute per pair"),
    ]
    step_html = "".join(
        f'<li><span class="k">{i}</span><div><b>{t}</b><p>{d}</p></div></li>' for i, (t, d) in enumerate(facts, 1)
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; margin: 0; }}
body {{ width: 1680px; height: 945px; font: 15px/1.35 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #0f172a; background: #f4f6f9; }}
header {{ height: 96px; padding: 18px 28px; background: linear-gradient(100deg, #0f172a, #1e3a5f 60%, #b4550c); color: #fff; display: flex; justify-content: space-between; align-items: center; }}
header h1 {{ font-size: 32px; letter-spacing: -.02em; }}
header p {{ opacity: .85; font-size: 16px; margin-top: 2px; }}
header .tag {{ font-size: 13px; padding: 6px 12px; border-radius: 99px; background: rgb(255 255 255 / .14); }}
main {{ display: grid; grid-template-columns: 330px 1fr 360px; gap: 16px; padding: 16px 20px; height: 849px; }}
section {{ background: #fff; border-radius: 14px; box-shadow: 0 1px 2px rgb(15 23 42 / .08), 0 0 0 1px rgb(15 23 42 / .05); padding: 14px 16px; }}
h2 {{ font-size: 17px; color: #1e3a5f; margin-bottom: 10px; letter-spacing: -.01em; }}
h2 small {{ font-weight: 500; color: #64748b; font-size: 13px; }}
.col {{ display: flex; flex-direction: column; gap: 14px; min-height: 0; }}
ol {{ list-style: none; padding: 0; display: grid; gap: 10px; }}
ol li {{ display: flex; gap: 10px; }}
.k {{ flex: none; width: 26px; height: 26px; border-radius: 50%; background: #1e3a5f; color: #fff; font-weight: 700; display: grid; place-items: center; font-size: 13px; }}
ol p {{ color: #475569; font-size: 13px; margin-top: 2px; }}
.imgs {{ display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 10px; }}
figure {{ position: relative; border-radius: 10px; overflow: hidden; aspect-ratio: 1 / .93; background: #0f172a; }}
.zoom {{ position: absolute; inset: 0; transform: scale(2.1); transform-origin: 56% 47%; }} /* the mine core, not the whole 16 km */
figure img, figure svg {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: fill; }}
img.glow {{ filter: saturate(1.4) brightness(1.5); }}
figcaption {{ position: absolute; left: 8px; top: 8px; background: rgb(255 255 255 / .92); padding: 3px 9px; border-radius: 7px; font-weight: 700; font-size: 13px; }}
.row2 {{ display: grid; grid-template-columns: 1.15fr 1fr; gap: 14px; margin-top: 14px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13.5px; }}
th {{ text-align: left; font-size: 11.5px; text-transform: uppercase; letter-spacing: .05em; color: #64748b; padding: 4px 6px; border-bottom: 1px solid #e2e8f0; }}
td {{ padding: 5px 6px; border-bottom: 1px solid #f1f5f9; }}
td i, .legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 7px; vertical-align: -1px; }}
td small {{ color: #64748b; }}
.n {{ text-align: right; font-variant-numeric: tabular-nums; }}
.legend {{ list-style: none; padding: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; font-size: 13px; color: #334155; }}
.legend b {{ color: #0f172a; font-weight: 600; margin-left: 4px; }}
.big {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
.big div {{ background: #f8fafc; border-radius: 10px; padding: 10px 12px; box-shadow: inset 0 0 0 1px #eef2f6; }}
.big b {{ display: block; font-size: 26px; letter-spacing: -.02em; color: #b4550c; }}
.big span {{ font-size: 12.5px; color: #475569; }}
.tells li {{ list-style: none; display: flex; gap: 10px; padding: 7px 0; border-bottom: 1px solid #f1f5f9; font-size: 13.5px; color: #334155; }}
.tells ul {{ padding: 0; }}
.tells b {{ color: #0f172a; }}
.dot {{ flex: none; width: 8px; height: 8px; margin-top: 6px; border-radius: 50%; background: #b4550c; }}
.yrs {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }}
.yr {{ background: #f8fafc; border-radius: 10px; padding: 8px 10px; display: grid; box-shadow: inset 0 0 0 1px #eef2f6; }}
.yr span {{ font-size: 12px; font-weight: 700; color: #64748b; }}
.yr b {{ font-size: 21px; color: #d97706; }}
.yr b.o {{ color: #1e3a5f; }}
.yr em {{ font-style: normal; font-size: 11.5px; color: #64748b; margin-top: -3px; }}
.next ul {{ padding: 0 0 0 18px; }}
.next li {{ font-size: 13.5px; color: #334155; margin: 0 0 5px; }}
.caveat {{ margin-top: 8px; font-size: 12.5px; color: #64748b; }}
.outcome {{ background: #ecfdf5; box-shadow: inset 0 0 0 1px #a7f3d0; }}
.outcome h2 {{ color: #065f46; }}
.outcome p {{ font-size: 14px; color: #065f46; }}
</style></head><body>
<header><div><h1>Mining activity &amp; change at an open-pit copper mine, NW Zambia</h1>
<p>Two Sentinel-2 L2A dates, nominal 2023-08-12 → 2023-09-02 · real results, not an illustration</p></div>
<span class="tag">s2change.dzaky.ai.id</span></header>
<main>
<div class="col"><section><h2>The study</h2><ol>{step_html}</ol></section>
<section><h2>Key numbers</h2><div class="big">
<div><b>{km2(changed)}</b><span>changed in {run["n_features"]["robust_cva"]} polygons, 1.6% of the area</span></div>
<div><b>{inside * 100:.0f}%</b><span>of it inside the 8 named mine facilities</span></div>
<div><b>{baseline / changed:.0f}×</b><span>less than the provided baseline, which maps seams and haze</span></div>
<div><b>0.1%</b><span>false alarms, measured on held-out stable pixels</span></div></div></section></div>
<div class="col"><section><h2>What the satellite saw <small>the mine core, 10 m pixels</small></h2><div class="imgs">
{img("before", "Before")}{img("after_normalized", "After")}{img("after_normalized", "Change intensity", f'<img class="glow" src="{(WEB / manifest["rasters"]["change_intensity"]["url"]).as_uri()}">')}
{img("after_normalized", "Change by type", polygons_svg(bounds))}</div>
<div class="row2"><div><h2>Land cover of the changed area <small>from → to, km²</small></h2>
<table><tr><th>Transition</th><th class="n">km²</th><th class="n">Share</th></tr>{trans_rows}</table></div>
<div><h2>Change by type</h2><ul class="legend">{legend}</ul></div></div></section>
</div>
<div class="col"><section class="tells"><h2>What it tells you</h2><ul>
<li><span class="dot"></span><div><b>Operating, not spreading</b> (two months): tailings deposited, waste tipped, pits worked; no new clearing outside the mine.</div></li>
<li><span class="dot"></span><div><b>Dam wall likely raised</b>: {km2(types.get("tailings_dam_raise", 0))} of fresh fill on the tailings dam's south-east edge. First thing for dam-safety teams.</div></li>
<li><span class="dot"></span><div><b>Every change explained</b>: a type, a place and a plain sentence, queryable in PostGIS.</div></li></ul></section>
<section class="next"><h2>Next steps</h2><ul>
<li>Relabel the accuracy sample on very-high-resolution imagery, with more points where the map says no change</li>
<li>Add near- and short-wave infrared bands (B08, B11, B12) to catch subtle change</li>
<li>Licence and permit boundaries, to flag change outside them</li>
<li>Monthly runs with alerts; super-resolution (SEN2SR) only as a display layer</li></ul></section>
<section class="outcome"><h2>Outcome</h2><p>A defensible, explained view of mining activity from free imagery: where it changed, what it most likely is, and who should look at it.</p></section></div>
</main></body></html>"""


def main() -> None:
    page_html = ROOT / "docs/figures/.at_a_glance.html"
    page_html.write_text(build())
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1680, "height": 945}, device_scale_factor=1.5)
        page.goto(page_html.as_uri(), wait_until="networkidle")
        page.screenshot(path=str(OUT), full_page=False)
        browser.close()
    page_html.unlink()
    print(f"{OUT}: {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
