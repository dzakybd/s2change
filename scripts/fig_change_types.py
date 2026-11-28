# ruff: noqa: E501  (the figure is an HTML/CSS template; its lines are markup, not code)
"""Render docs/figures/10_change_types.png: the change polygons by type on the true-colour image, and the area per type.

    uv run --with playwright python scripts/fig_change_types.py

Numbers come from data/processed/change_features.geojson (robust CVA polygons, so the report's section 2 table) and
run_summary.json; the facility outlines from docs/web/facilities.geojson. Type colours and pictograms are read from
viewer/src/domain.ts (fig_common.app_types), so the figure, the map and the report use one legend.
"""

from __future__ import annotations

import json

import geopandas as gpd
from fig_common import CSS, OTHER, ROOT, TEAL, app_types, icon, render, true_colour, uri
from PIL import Image
from shapely.geometry import MultiPolygon, Polygon

OUT = ROOT / "docs/figures/10_change_types.png"
ORDER = [
    "tailings_deposition",
    "waste_dumping",
    "tailings_dam_works",
    "seasonal_vegetation_or_burn",
    "pit_excavation",
    "water_level_change",
]
BLURB = {  # plain-language reading of each type, as in the report's table
    "tailings_deposition": "Wet slurry and drying beaches moving on the tailings facility",
    "waste_dumping": "Fresh, redder rock faces at dumps and pit edges",
    "tailings_dam_works": "Bare ground on the embankment where vegetation was",
    "seasonal_vegetation_or_burn": "Dry-season browning or burn scars outside the mine",
    "pit_excavation": "Bench and pit-floor changes",
    "water_level_change": "Pond and pit-lake edges moving",
    "other": "Seams, haze, small builds; 0.02 km² of land clearing",
}
ZOOM_HALF_PX = 140  # 2.8 km around the dam wall, as in scripts/step_frames.py


def path_d(geom, tf, x0=0, y0=0) -> str:
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)

    def pt(c):
        x, y = ~tf * c[:2]
        return f"{x - x0:.1f},{y - y0:.1f}"

    return "".join("M" + "L".join(pt(c) for c in ring.coords) + "Z" for p in polys for ring in (p.exterior, *p.interiors))


def simple_outlines(fac: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Display-only outlines: drop slivers and holes under 15 ha and smooth the 10 m stair-steps."""
    out = fac.copy()
    parts = []
    for g in fac.geometry:
        keep = [Polygon(p.exterior) for p in (g.geoms if g.geom_type == "MultiPolygon" else [g]) if p.area >= 150_000]
        parts.append(MultiPolygon(keep).simplify(25) if keep else g)
    out["geometry"] = parts
    return out


def svg(polys: gpd.GeoDataFrame, fac: gpd.GeoDataFrame, tf, size, colors, x0=0, y0=0, stroke=1.0, fac_w=1.8) -> str:
    shapes = "".join(
        f'<path d="{path_d(r.geometry, tf, x0, y0)}" fill="{colors.get(r.change_type, OTHER["color"])}" fill-opacity=".82" stroke="#fff" stroke-width="{stroke}" stroke-linejoin="round"/>'
        for r in polys.itertuples()
    )
    outlines = "".join(
        f'<path d="{path_d(g, tf, x0, y0)}" fill="none" stroke="#fff" stroke-opacity=".9" stroke-width="{fac_w}" stroke-dasharray="{fac_w * 4} {fac_w * 2.5}" stroke-linejoin="round"/>'
        for g in fac.geometry
    )
    return f'<svg viewBox="0 0 {size[0]} {size[1]}" preserveAspectRatio="none">{outlines}{shapes}</svg>'


def build() -> str:
    types = app_types()
    colors = {k: t["color"] for k, t in types.items()}
    img, tf = true_colour("sentinel2_20230902_stack_normalized")
    h, w = img.shape[:2]
    km = (w * tf.a / 1000, h * -tf.e / 1000)

    g = gpd.read_file(ROOT / "data/processed/change_features.geojson")
    g = g[g.method == "robust_cva"].to_crs(32735)
    fac = gpd.read_file(ROOT / "docs/web/facilities.geojson").to_crs(32735)
    fac = simple_outlines(fac[~fac.geometry.is_empty])
    run = json.loads((ROOT / "data/processed/run_summary.json").read_text())
    total_km2, n_poly = run["changed_area_ha"]["robust_cva"] / 100, run["n_features"]["robust_cva"]
    inside = run["footprint"]["register_all_facilities"]["share_of_change"]

    key = g.change_type.where(g.change_type.isin(ORDER), "other")
    area = (g.area_m2.groupby(key).sum() / 1e6).reindex([*ORDER, "other"])
    count = g.groupby(key).size().reindex([*ORDER, "other"])
    assert round(area.sum(), 2) == round(total_km2, 2), (area.sum(), total_km2)

    big = Image.fromarray(img)
    main_img = uri(big)
    # the main map
    xy = lambda geom: ~tf * (geom.x, geom.y)  # noqa: E731
    pins = ""
    for k in ORDER:
        sub = g[g.change_type == k]
        if sub.empty:
            continue
        c = xy(sub.sort_values("area_m2", ascending=False).iloc[0].geometry.centroid)
        pins += f'<div class="pin" style="left:{c[0] / w * 100:.2f}%;top:{c[1] / h * 100:.2f}%">{icon(types[k], 34)}</div>'
    labels = ""
    for r in fac.itertuples():
        c = xy(r.geometry.representative_point())
        labels += f'<span class="fl" style="left:{c[0] / w * 100:.2f}%;top:{c[1] / h * 100:.2f}%">{r.facility_id}</span>'
    scale_w = 2000 / (w * tf.a) * 100  # a 2 km bar, as a share of the map width
    main_svg = svg(g, fac, tf, (w, h), colors, stroke=1.6, fac_w=2.2)

    def crop_at(c) -> tuple[int, int]:
        return (
            min(max(int(c[0]) - ZOOM_HALF_PX, 0), w - 2 * ZOOM_HALF_PX),
            min(max(int(c[1]) - ZOOM_HALF_PX, 0), h - 2 * ZOOM_HALF_PX),
        )

    def densest(k: str) -> tuple[int, int]:
        """The window with the most area of type k, centred on one of its polygons."""
        sub = g[g.change_type == k]
        cs = [xy(c) for c in sub.geometry.centroid]
        side = ZOOM_HALF_PX * 10  # metres from the window centre to its edge
        best = max(
            cs,
            key=lambda c: sub[
                sub.geometry.centroid.apply(lambda p: abs(xy(p)[0] - c[0]) < side / 10 and abs(xy(p)[1] - c[1]) < side / 10)
            ].area_m2.sum(),
        )
        return crop_at(best)

    def zoom(k: str, x0: int, y0: int, text: str) -> str:
        crop = big.crop((x0, y0, x0 + 2 * ZOOM_HALF_PX, y0 + 2 * ZOOM_HALF_PX)).resize((480, 480), Image.Resampling.LANCZOS)
        vec = svg(g, fac, tf, (2 * ZOOM_HALF_PX,) * 2, colors, x0, y0, stroke=0.8, fac_w=1.2)
        return f"""<section class="card zoom"><div class="frame"><img src="{uri(crop)}">{vec}</div>
<div><h2>{icon(types[k], 28)}{types[k]["label"]}</h2><p>{text}</p></div></section>"""

    wall = g[g.change_type == "tailings_dam_works"]
    wall_x0, wall_y0 = crop_at(xy(wall.geometry.union_all().centroid))
    zooms = (
        zoom(
            "tailings_deposition",
            *densest("tailings_deposition"),
            f"<b>{area['tailings_deposition']:.2f} km²</b> of slurry and beaches moving on the tailings facility, the largest type.",
        )
        + zoom(
            "tailings_dam_works",
            wall_x0,
            wall_y0,
            f"<b>{area['tailings_dam_works']:.2f} km²</b> of bare ground where vegetation was: earthworks or clearing on the wall, not a documented raise. A dam-safety team would still check it first.",
        )
        + zoom(
            "waste_dumping",
            *densest("waste_dumping"),
            f"<b>{area['waste_dumping']:.2f} km²</b> of fresh, redder rock at dump and pit edges, in the pit and dump complexes.",
        )
    )

    rows = ""
    top = area.max()
    for k in [*ORDER, "other"]:
        t = types.get(k, OTHER | {"key": k})
        rows += f"""<div class="row"><div class="ic">{icon(t, 40)}</div><div class="tx"><b>{t["label"]}</b><small>{BLURB[k]}</small>
<div class="bar"><i style="width:{area[k] / top * 100:.1f}%;background:{t["color"]}"></i></div></div>
<div class="num"><b>{area[k]:.2f}<small> km²</small></b><small>{count[k]} polygons</small></div></div>"""

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}
body {{ width: 1500px; }}
.grid {{ display: grid; grid-template-columns: 860px 1fr; gap: 24px; align-items: stretch; }}
.map {{ padding: 14px; }}
.frame {{ position: relative; border-radius: 12px; overflow: hidden; background: #0f172a; aspect-ratio: {w} / {h}; }}
.frame > img, .frame > svg {{ position: absolute; inset: 0; width: 100%; height: 100%; display: block; }}
.pin {{ position: absolute; transform: translate(-50%, -50%); filter: drop-shadow(0 2px 4px rgb(0 0 0 / .55)); }}
.fl {{ position: absolute; transform: translate(-50%, -50%); font-size: 12px; font-weight: 800; letter-spacing: .04em; color: #fff; background: rgb(15 23 42 / .72); padding: 1px 6px; border-radius: 5px; border: 1px dashed rgb(255 255 255 / .75); }}
.cap {{ position: absolute; left: 14px; bottom: 14px; display: flex; gap: 10px; align-items: center; background: rgb(255 255 255 / .94); border-radius: 10px; padding: 7px 12px; font-size: 14.5px; font-weight: 600; color: #334155; box-shadow: 0 1px 4px rgb(0 0 0 / .25); }}
.cap .dash {{ width: 26px; height: 0; border-top: 2px dashed #64748b; }}
.scale {{ position: absolute; right: 16px; bottom: 16px; color: #fff; font-size: 14px; font-weight: 700; text-shadow: 0 1px 3px #000; text-align: right; }}
.scale i {{ display: block; height: 7px; width: calc({scale_w:.2f}% * 8.6); border: 2px solid #fff; border-top: 0; margin-bottom: 3px; }}
.col {{ display: grid; }} .col .card {{ display: flex; flex-direction: column; justify-content: space-between; }}
.row {{ display: grid; grid-template-columns: 40px 1fr auto; gap: 14px; align-items: center; padding: 10px 0; border-bottom: 1px solid #f1f5f9; }}
.row:last-child {{ border: 0; padding-bottom: 0; }}
.tx b {{ font-size: 18px; display: block; }} .tx small {{ font-size: 14.5px; color: #64748b; display: block; line-height: 1.3; }}
.bar {{ height: 9px; margin-top: 7px; background: #f1f5f9; border-radius: 99px; overflow: hidden; }} .bar i {{ display: block; height: 100%; border-radius: 99px; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }} .num b {{ font-size: 25px; letter-spacing: -.01em; }} .num b small {{ font-size: 15px; color: #475569; font-weight: 600; }}
.num > small {{ display: block; font-size: 14px; color: #64748b; }}
.zh {{ margin: 26px 0 12px; font-size: 21px; }} .zh small {{ font-weight: 500; color: #64748b; font-size: 16px; margin-left: 6px; }}
.zooms {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; }}
.zoom {{ display: grid; grid-template-columns: 200px 1fr; gap: 16px; align-items: center; padding: 14px; }}
.zoom .frame {{ aspect-ratio: 1; }}
.zoom h2 {{ display: flex; align-items: center; gap: 8px; font-size: 18px; margin-bottom: 6px; }}
.zoom p {{ font-size: 15.5px; color: #334155; line-height: 1.38; }} .zoom p b {{ color: #0f172a; }}
.kpis {{ display: flex; gap: 12px; margin-top: 2px; }}
.kpi {{ background: #f0fdfa; border-radius: 12px; padding: 8px 14px; box-shadow: inset 0 0 0 1px #99f6e4; }}
.kpi b {{ display: block; font-size: 24px; color: {TEAL}; letter-spacing: -.01em; }} .kpi span {{ font-size: 14px; color: #475569; }}
</style></head><body>
<span class="tag"><i></i>This work</span>
<h1>What changed, by type, on the real image</h1>
<p class="sub">{total_km2:.2f} km² in {n_poly} polygons between the two dates, each one typed by simple rules from the colour change and the place it sits in.</p>
<div class="grid">
<section class="card map"><div class="frame"><img src="{main_img}">{main_svg}{labels}{pins}
<div class="cap"><span class="dash"></span>Facility outline (8 in the register)</div>
<div class="scale"><i></i>2 km</div></div></section>
<div class="col">
<section class="card"><h2>Area per change type</h2>
<div class="kpis" style="margin: 10px 0 6px"><div class="kpi"><b>{total_km2:.2f} km²</b><span>changed in total</span></div><div class="kpi"><b>{inside * 100:.0f} %</b><span>inside the 8 facilities</span></div><div class="kpi"><b>{(area["tailings_deposition"] + area["waste_dumping"] + area["tailings_dam_works"] + area["pit_excavation"]) / total_km2 * 100:.0f} %</b><span>is mining activity</span></div></div>
{rows}</section>
</div></div>
<h2 class="zh">Zoom on three types <small>2.8 × 2.8 km each, same colours</small></h2>
<div class="zooms">{zooms}</div>
<p class="foot">Facility outlines are the register's analyst-drawn polygons, traced on very-high-resolution imagery and simplified here for display. Area is the area of change, not of new disturbance. The last row sums noise, unexplained, infrastructure and land clearing.</p>
<p class="foot" style="margin-top:6px">Image: {km[0]:.1f} × {km[1]:.1f} km, Sentinel-2 L2A true colour (B04, B03, B02), 10 m, after date normalised to the before date.</p>
</body></html>"""


def main() -> None:
    render(build(), OUT, width=1500)


if __name__ == "__main__":
    main()
