# ruff: noqa: E501  (the figure is an HTML/CSS template; its lines are markup, not code)
"""Render docs/figures/11_sensitivity.png: does the answer depend on the two tuning settings? (no, 16 runs say).

    uv run --with playwright python scripts/fig_sensitivity.py

Data: data/processed/sensitivity.csv (changed_ha, share_in_register_mine_classes, is_default) and the provided-example area from
run_summary.json. Two heat grids (area, share) and one scatter, drawn as HTML/SVG and printed by Chromium; no matplotlib.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
OUT = ROOT / "docs/figures/11_sensitivity.png"
TEAL, RED = "#0f766e", "#b91c1c"
LIGHT = (225, 243, 240)
DARK = (15, 118, 110)


def far_label(v: float) -> str:
    return f"1 in {round(1 / v):,}"


def shade(t: float) -> str:
    return "#" + "".join(f"{round(a + (b - a) * t):02x}" for a, b in zip(LIGHT, DARK, strict=True))


def grid(rows: list[dict], key: str, fmt, title: str, unit: str, rng: str) -> str:
    fars = sorted({r["far"] for r in rows}, reverse=True)
    mmus = sorted({r["mmu"] for r in rows})
    vals = [r[key] for r in rows]
    lo, hi = min(vals), max(vals)
    head = "".join(f"<span>{round(m * 1e4):,} m²</span>" for m in mmus)
    body = ""
    for f in fars:
        cells = ""
        for m in mmus:
            r = next(x for x in rows if x["far"] == f and x["mmu"] == m)
            t = (r[key] - lo) / (hi - lo)
            cls = " dflt" if r["default"] else ""
            badge = "<em>default</em>" if r["default"] else ""
            cells += f'<div class="cell{cls}" style="background:{shade(t)};color:{"#fff" if t > 0.5 else "#0f172a"}">{fmt(r[key])}{badge}</div>'
        body += f'<span class="rl">{far_label(f)}</span>{cells}'
    return f"""<section class="card"><div class="hd"><h2>{title}</h2><b class="rng">{rng}</b></div>
<p class="unit">{unit}</p>
<div class="grid"><span class="corner">stricter test ↓</span><div class="ch">{head}</div>{body}</div>
<p class="axis">smallest region kept →</p></section>"""


def scatter(rows: list[dict]) -> str:
    W, H = 760, 520
    L, R, T, B = 82, 24, 24, 74
    xs = lambda v: L + (v - 1) / 6.5 * (W - L - R)  # noqa: E731
    ys = lambda v: T + (80 - v) / (80 - 58) * (H - T - B)  # noqa: E731
    p = []
    for x in range(1, 8):
        p.append(f'<line x1="{xs(x)}" x2="{xs(x)}" y1="{T}" y2="{H - B}" stroke="#e2e8f0" stroke-width="2"/>')
        p.append(f'<text x="{xs(x)}" y="{H - B + 28}" text-anchor="middle" font-size="17" fill="#64748b">{x}</text>')
    for y in range(60, 81, 5):
        p.append(f'<line x1="{L}" x2="{W - R}" y1="{ys(y)}" y2="{ys(y)}" stroke="#e2e8f0" stroke-width="2"/>')
        p.append(f'<text x="{L - 12}" y="{ys(y) + 6}" text-anchor="end" font-size="17" fill="#64748b">{y} %</text>')
    a = [r["area"] for r in rows]
    s = [r["share"] for r in rows]
    p.append(
        f'<rect x="{xs(min(a)) - 16}" y="{ys(max(s)) - 14}" width="{xs(max(a)) - xs(min(a)) + 32}" height="{ys(min(s)) - ys(max(s)) + 28}" rx="18" fill="{TEAL}" fill-opacity=".06" stroke="{TEAL}" stroke-opacity=".5" stroke-width="2" stroke-dasharray="6 6"/>'
    )
    p.append(
        f'<text x="{xs(max(a)) + 16}" y="{ys(max(s)) - 24}" text-anchor="end" font-size="18" font-weight="800" fill="{TEAL}">box = all 16 settings</text>'
    )
    for f in sorted({r["far"] for r in rows}):
        grp = [r for r in rows if r["far"] == f]
        cx = sum(xs(r["area"]) for r in grp) / len(grp)
        above = f == 1e-4
        cy = min(ys(r["share"]) for r in grp) - 24 if above else max(ys(r["share"]) for r in grp) + 40
        cx += 34 if f == 1e-5 else 0
        p.append(
            f'<text x="{cx}" y="{cy}" text-anchor="middle" font-size="16.5" font-weight="700" fill="#475569">{far_label(f)}</text>'
        )
    for r in rows:
        if r["default"]:
            continue
        p.append(
            f'<circle cx="{xs(r["area"])}" cy="{ys(r["share"])}" r="9" fill="{TEAL}" fill-opacity=".8" stroke="#fff" stroke-width="2.5"/>'
        )
    d = next(r for r in rows if r["default"])
    dx, dy = xs(d["area"]), ys(d["share"])
    p.append(
        f'<circle cx="{dx}" cy="{dy}" r="19" fill="none" stroke="#0f172a" stroke-width="3.5"/><circle cx="{dx}" cy="{dy}" r="11" fill="{TEAL}" stroke="#fff" stroke-width="3"/>'
    )
    p.append(
        f'<rect x="{dx - 54}" y="{dy - 78}" width="108" height="32" rx="16" fill="#0f172a"/><text x="{dx}" y="{dy - 56}" text-anchor="middle" font-size="16.5" font-weight="800" fill="#fff">default</text>'
    )
    p.append(
        f'<text x="{(L + W - R) / 2}" y="{H - 14}" text-anchor="middle" font-size="19" font-weight="700" fill="#334155">area flagged as changed (km²)</text>'
    )
    p.append(
        f'<text transform="translate(22 {(T + H - B) / 2}) rotate(-90)" text-anchor="middle" font-size="19" font-weight="700" fill="#334155">inside the 5 mining facilities</text>'
    )
    return f'<svg viewBox="0 0 {W} {H}" width="100%">{"".join(p)}</svg>'


def build() -> str:
    rows = []
    with (PROC / "sensitivity.csv").open() as fh:
        for r in csv.DictReader(fh):
            rows.append(
                {
                    "far": float(r["far_low"]),
                    "mmu": float(r["mmu_ha"]),
                    "area": float(r["changed_ha"]) / 100,
                    "share": float(r["share_in_register_mine_classes"]) * 100,
                    "default": r["is_default"] == "True",
                }
            )
    run = json.loads((PROC / "run_summary.json").read_text())
    base = run["changed_area_ha"]["euclidean_baseline"] / 100
    d = next(r for r in rows if r["default"])
    amin, amax = min(r["area"] for r in rows), max(r["area"] for r in rows)
    smin, smax = min(r["share"] for r in rows), max(r["share"] for r in rows)
    rng_a, rng_s = f"{amin:.1f}–{amax:.1f} km²", f"{smin:.0f}–{smax:.0f} %"
    g1 = grid(rows, "area", lambda v: f"{v:.2f}", "Area flagged as changed", "km², per setting", rng_a)
    g2 = grid(
        rows, "share", lambda v: f"{v:.0f} %", "Inside the 5 mining facilities", "share of the flagged area, per setting", rng_s
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; margin: 0; }}
body {{ width: 1300px; font: 18px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #0f172a; background: #f6f7f9; padding: 30px 34px 34px; }}
h1 {{ font-size: 34px; letter-spacing: -.02em; }}
.sub {{ color: #475569; font-size: 19px; margin: 6px 0 22px; }}
.card {{ background: #fff; border-radius: 20px; padding: 22px; box-shadow: 0 0 0 1px rgb(15 23 42 / .08), 0 10px 28px rgb(15 23 42 / .10), 0 2px 6px rgb(15 23 42 / .06); border-top: 6px solid {TEAL}; }}
.row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 26px; }}
.hd {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }}
h2 {{ font-size: 24px; letter-spacing: -.01em; }}
.rng {{ font-size: 26px; color: {TEAL}; letter-spacing: -.01em; white-space: nowrap; }}
.unit {{ color: #64748b; font-size: 16.5px; margin: 2px 0 14px; }}
.grid {{ display: grid; grid-template-columns: 112px repeat(4, 1fr); gap: 8px; align-items: center; }}
.corner {{ font-size: 14px; font-weight: 700; color: #64748b; line-height: 1.2; }}
.ch {{ display: contents; }}
.ch span {{ text-align: center; font-size: 15.5px; font-weight: 700; color: #475569; }}
.rl {{ font-size: 16px; font-weight: 700; color: #475569; }}
.cell {{ position: relative; height: 72px; border-radius: 12px; display: grid; place-items: center; font-size: 22px; font-weight: 800; letter-spacing: -.01em; }}
.cell.dflt {{ box-shadow: 0 0 0 3px #fff, 0 0 0 6px #0f172a; z-index: 1; }}
.cell em {{ position: absolute; bottom: 6px; font-style: normal; font-size: 12px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; opacity: .9; }}
.cell.dflt {{ padding-bottom: 12px; }}
.axis {{ text-align: right; font-size: 14px; font-weight: 700; color: #64748b; margin-top: 8px; }}
.low {{ margin-top: 26px; display: grid; grid-template-columns: 1.55fr 1fr; gap: 26px; }}
.low .card h2 {{ margin-bottom: 8px; }}
.facts {{ display: grid; gap: 14px; align-content: start; }}
.fact {{ background: #f0fdfa; border-radius: 14px; padding: 14px 18px; }}
.fact b {{ display: block; font-size: 36px; line-height: 1.1; letter-spacing: -.02em; color: {TEAL}; }}
.fact span {{ font-size: 17px; color: #334155; }}
.fact.red b {{ color: {RED}; }} .fact.red {{ background: #fef2f2; }}
</style></head><body>
<h1>Does the answer depend on the settings? Not much</h1>
<p class="sub">16 runs of this work: 4 false-alarm rates × 4 smallest region sizes. Darker teal = larger value.</p>
<div class="row">{g1}{g2}</div>
<div class="low"><section class="card"><h2>All 16 settings together</h2>{scatter(rows)}</section>
<div class="facts">
<div class="fact"><b>{d["area"]:.2f} km² · {d["share"]:.0f} %</b><span>the default (1 in {round(1 / d["far"]):,}, {round(d["mmu"] * 1e4):,} m²): area and share inside the named facilities</span></div>
<div class="fact"><b>{rng_a}</b><span>area across all 16 settings, about a factor 3 end to end</span></div>
<div class="fact"><b>{rng_s}</b><span>share inside the 5 mining facilities, so the mine stays the main story</span></div>
<div class="fact red"><b>{base / amax:.0f}× less</b><span>than the provided example ({base:.1f} km²), even at the most lenient setting ({amax:.1f} km²)</span></div>
</div></div>
</body></html>"""


def main() -> None:
    page_html = ROOT / "docs/figures/.sensitivity.html"
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
