# ruff: noqa: E501  (the figure is an HTML/CSS template; its lines are markup, not code)
"""Render docs/figures/13_multi_year.png: change since 2023-08-12 at the same season, inside vs outside the 8 facilities.

    uv run --with playwright python scripts/fig_multi_year.py

Two sources, both real:
  * the 2-month pair (the assignment) is the pipeline run in data/processed/run_summary.json and change_features.geojson;
  * the 1-, 2- and 3-year pairs (Aug 2023 to 27 Aug 2024, 7 Aug 2025, 14 Aug 2026) were computed with s2cd.compare, the app's
    date comparison; their fixed results are the table in app/knowledge/07_business.md (their scenes are not on disk).
All four pairs are typed with the same rules and the same facility register, so their bars are comparable.
"""

from __future__ import annotations

import json
import re

import geopandas as gpd
from fig_common import CSS, ROOT, TEAL, app_types, icon, render, true_colour, uri
from PIL import Image

OUT = ROOT / "docs/figures/13_multi_year.png"
OUTSIDE = "#e8833a"  # the app's accent: change outside the register
LABELS = ["2-month pair", "1 year", "2 years", "3 years"]
FILL = 84  # the tallest bar fills this share of the plot; the rest holds its total label
WHEN = ["Aug–Sep 2023", "Aug 2024", "Aug 2025", "Aug 2026"]


def seg(v: float, h: float, color: str) -> str:
    return f'<div class="seg" style="height:{h:.2f}%;background:{color}">{f"<b>{v:.1f}</b>" if h > 7 else ""}</div>'


def yearly() -> list[dict]:
    """Rows of the multi-year table in app/knowledge/07_business.md: total, waste dumping, inside, outside (km²)."""
    rows = []
    for line in (ROOT / "app/knowledge/07_business.md").read_text().splitlines():
        if re.match(r"\| 20\d\d-\d\d-\d\d \(\d years?\)", line):
            c = [x.strip() for x in line.strip("|").split("|")]
            num = lambda s: float(re.match(r"[\d.]+", s).group())  # noqa: E731
            rows.append({"total": num(c[1]), "waste": num(c[2]), "inside": num(c[6]), "outside": num(c[7])})
    assert len(rows) == 3, rows
    return rows


def assignment_pair() -> dict:
    run = json.loads((ROOT / "data/processed/run_summary.json").read_text())
    total = run["changed_area_ha"]["robust_cva"] / 100
    share = run["footprint"]["register_all_facilities"]["share_of_change"]
    return {
        "total": total,
        "inside": total * share,
        "outside": total * (1 - share),
        "waste": run["change_type_ha"]["waste_dumping"] / 100,
    }


def facility_map() -> tuple[str, float]:
    img, tf = true_colour("sentinel2_20230812_stack")
    h, w = img.shape[:2]
    fac = gpd.read_file(ROOT / "docs/web/facilities.geojson").to_crs(32735)
    d = ""
    for g in fac.geometry[~fac.geometry.is_empty]:
        for p in [g] if g.geom_type == "Polygon" else g.geoms:
            if p.area < 150_000:
                continue
            d += "M" + "L".join("{:.1f},{:.1f}".format(*(~tf * c[:2])) for c in p.simplify(25).exterior.coords) + "Z"
    svg = f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none"><path d="{d}" fill="{TEAL}" fill-opacity=".5" stroke="#5eead4" stroke-width="3" stroke-linejoin="round"/></svg>'
    small = Image.fromarray(img).resize((w // 2, h // 2), Image.Resampling.LANCZOS)
    return f'<div class="frame"><img src="{uri(small)}">{svg}</div>', w / h


def build() -> str:
    types = app_types()
    a, yrs = assignment_pair(), yearly()
    series = [a, *yrs]
    map_html, ratio = facility_map()
    top = max(s["total"] for s in series)
    waste_top = max(s["waste"] + s.get("dam", 0) for s in series)

    cols = ""
    for lab, when, s in zip(LABELS, WHEN, series, strict=True):
        out_h, in_h = s["outside"] / top * FILL, s["inside"] / top * FILL
        cols += f"""<div class="col"><div class="stack">{seg(s["inside"], in_h, TEAL)}{seg(s["outside"], out_h, OUTSIDE)}<div class="tot">{s["total"]:.1f}</div></div>
<div class="x"><b>{lab}</b><small>{when}</small></div></div>"""

    wc = ""
    for lab, when, s in zip(LABELS, WHEN, series, strict=True):
        dam = s.get("dam", 0)
        label = f"{s['waste'] + dam:.1f}"
        small = f'<span class="split">{s["waste"]:.2f} + {dam:.2f}</span>' if dam else ""
        hatch = f'<div class="seg hatch" style="height:{dam / waste_top * FILL:.2f}%"></div>' if dam else ""
        wc += f"""<div class="col"><div class="stack"><div class="seg" style="height:{s["waste"] / waste_top * FILL:.2f}%;background:{types["waste_dumping"]["color"]}"></div>{hatch}<div class="tot">{label}{small}</div></div>
<div class="x"><b>{lab}</b><small>{when}</small></div></div>"""

    inside_share = a["inside"] / a["total"] * 100
    share_aoi = json.loads((ROOT / "data/processed/run_summary.json").read_text())["footprint"]["register_all_facilities"][
        "share_of_aoi"
    ]
    first, last = yrs[0], yrs[-1]
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
body {{ width: 1500px; }}
.grid {{ display: grid; grid-template-columns: 360px 1fr 1fr; gap: 24px; align-items: stretch; }}
.frame {{ position: relative; border-radius: 12px; overflow: hidden; aspect-ratio: {ratio:.4f}; margin: 14px 0 12px; background: #0f172a; }}
.frame img, .frame svg {{ position: absolute; inset: 0; width: 100%; height: 100%; display: block; }}
.mapcard p {{ font-size: 16px; color: #334155; margin-top: 6px; }} .mapcard p b {{ color: {TEAL}; }}
.key {{ display: flex; align-items: center; gap: 8px; font-size: 15.5px; font-weight: 600; color: #334155; margin-top: 12px; }}
.sw {{ width: 15px; height: 15px; border-radius: 4px; flex: none; }}
.chart {{ display: flex; flex-direction: column; }}
.ih {{ display: flex; align-items: center; gap: 9px; }}
.plot {{ flex: 1; display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-top: 12px; min-height: 380px; }}
.col {{ display: flex; flex-direction: column; }}
.stack {{ flex: 1; display: flex; flex-direction: column-reverse; border-bottom: 2px solid #cbd5e1; }}
.seg {{ width: 100%; display: grid; place-items: center; color: #fff; font-size: 16px; font-weight: 700; flex: none; }}
.stack > .seg:nth-last-child(2) {{ border-radius: 8px 8px 0 0; }}
.hatch {{ background: repeating-linear-gradient(135deg, #dc2626 0 5px, #fecaca 5px 10px); }}
.tot {{ text-align: center; font-size: 24px; font-weight: 800; letter-spacing: -.01em; line-height: 1.1; padding-bottom: 5px; flex: none; }}
.x {{ text-align: center; padding-top: 8px; }} .x b {{ display: block; font-size: 16.5px; }} .x small {{ color: #64748b; font-size: 14px; }}
.take {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; margin-top: 24px; }}
.t {{ display: flex; gap: 14px; align-items: flex-start; }}
.t .n {{ font-size: 32px; font-weight: 800; letter-spacing: -.02em; color: {TEAL}; white-space: nowrap; }}
.t p {{ font-size: 16.5px; color: #334155; }} .t p b {{ color: #0f172a; }}
.note {{ margin-top: 20px; background: #f8fafc; border-radius: 14px; padding: 14px 18px; font-size: 15.5px; color: #334155; box-shadow: inset 0 0 0 1px #e2e8f0; }}
.note b {{ color: #0f172a; }}
.split {{ display: block; font-size: 13px; font-weight: 600; color: #64748b; }}
</style></head><body>
<span class="tag"><i></i>This work</span>
<h1>Over three years the footprint grows beyond the mine</h1>
<p class="sub">Change since the 12 Aug 2023 image, always compared in the same season (August), so the dry-season look matches. Area in km².</p>
<div class="grid">
<section class="card mapcard"><h2>Inside = the 8 facilities</h2>{map_html}
<p>The register covers <b>{share_aoi * 100:.0f} %</b> of the area. In the 2-month pair <b>{inside_share:.0f} %</b> of the change is inside them.</p>
<div class="key"><i class="sw" style="background:{TEAL}"></i>Inside the 8 named facilities</div>
<div class="key" style="margin-top:6px"><i class="sw" style="background:{OUTSIDE}"></i>Outside them (no licence boundaries in the data)</div></section>
<section class="card chart"><h2>All change <small>km², inside vs outside the facilities</small></h2><div class="plot">{cols}</div></section>
<section class="card chart"><h2 class="ih">{icon(types["waste_dumping"], 26)} Waste dumping <small>km², one type</small></h2><div class="plot">{wc}</div></section>
</div>
<div class="take">
<div class="t"><div class="n">{last["waste"] / first["waste"]:.1f}×</div><p><b>More waste dumping</b> after three years than after one ({first["waste"]:.1f} → {last["waste"]:.1f} km²): waste rock keeps being moved.</p></div>
<div class="t"><div class="n">{last["outside"] / first["outside"]:.1f}×</div><p><b>More change outside the facilities</b> ({first["outside"]:.1f} → {last["outside"]:.1f} km²): the footprint spreads past the registered regions.</p></div>
<div class="t"><div class="n">≈ {last["total"]:.0f}</div><p><b>Total levels off</b> after year two ({yrs[1]["total"]:.1f} → {last["total"]:.1f} km²), while the share inside falls from {yrs[1]["inside"] / yrs[1]["total"] * 100:.0f} % to {last["inside"] / last["total"] * 100:.0f} %.</p></div>
</div>
<p class="foot">Area of change is not new disturbance: re-worked ground counts too. The 2-month pair is the assignment's two images; the yearly pairs are the app's date comparison (Aug 2023 → 27 Aug 2024, 7 Aug 2025, 14 Aug 2026).</p>
</body></html>"""


def main() -> None:
    render(build(), OUT, width=1500)


if __name__ == "__main__":
    main()
