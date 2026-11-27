# ruff: noqa: E501  (the figure CSS is markup, not code)
"""Shared pieces of the report figures drawn as HTML and printed by Chromium (fig_change_types):
the app's design tokens as CSS, true-colour crops as base64, and the Playwright print.

Also the legend read from viewer/src/domain.ts (``app_types``, ``icon``), so the figures, the map and the report share one
legend. ``python scripts/fig_common.py`` writes the per-type and per-facility icon SVGs under docs/figures/icons."""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
TEAL = "#0f766e"  # "this work" in the app's tokens (viewer/src/styles/base.css)
OTHER = {"label": "Noise, unexplained, other", "color": "#cbd5e1", "glyph": "M8 12h8"}  # the app's fallback type

CSS = """
* { box-sizing: border-box; margin: 0; }
body { font: 18px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #0f172a; background: #fff; padding: 30px 34px; }
h1 { font-size: 34px; letter-spacing: -.02em; }
.sub { color: #475569; font-size: 19px; margin: 6px 0 22px; }
.card { background: #fff; border-radius: 18px; padding: 20px 24px; box-shadow: 0 1px 2px rgb(15 23 42 / .08), 0 0 0 1px rgb(15 23 42 / .07); }
.card h2 { font-size: 21px; letter-spacing: -.01em; }
.card h2 small { font-weight: 500; color: #64748b; font-size: 16px; margin-left: 6px; }
.tag { display: inline-flex; align-items: center; gap: 10px; font-size: 14px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; color: #0f766e; }
.tag i { width: 13px; height: 13px; border-radius: 4px; background: #0f766e; }
.foot { margin-top: 18px; font-size: 15.5px; color: #475569; display: flex; gap: 10px; }
.foot::before { content: "i"; flex: none; width: 22px; height: 22px; border-radius: 50%; background: #e2e8f0; color: #475569; font: italic 700 14px/22px Georgia, serif; text-align: center; }
"""


def uri(img: Image.Image, fmt: str = "JPEG", **kw) -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **({"quality": 88} if fmt == "JPEG" else {}) | kw)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()


def true_colour(name: str, lo: float = 0.0, hi: float = 0.25, gamma: float = 0.8) -> tuple[np.ndarray, rasterio.Affine]:
    """(H, W, 3) uint8 true colour of a processed stack (B02, B03, B04 reflectance with the -1000 offset) and its transform."""
    with rasterio.open(PROC / f"{name}.tif") as s:
        r, t = (s.read().astype("float32") - 1000) / 10000, s.transform
    img = np.clip((r[::-1].transpose(1, 2, 0) - lo) / (hi - lo), 0, 1) ** gamma
    return (np.nan_to_num(img) * 255).astype(np.uint8), t


def render(html: str, out: Path, width: int, scale: float = 1.25) -> None:
    """Print the page with Chromium at its natural height."""
    from playwright.sync_api import sync_playwright

    page_html = out.parent / f".{out.stem}.html"
    page_html.write_text(html)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=scale)
            page.goto(page_html.as_uri(), wait_until="networkidle")
            page.screenshot(path=str(out), full_page=True)
            browser.close()
    finally:
        page_html.unlink(missing_ok=True)
    print(f"{out}: {out.stat().st_size / 1e6:.1f} MB")


def app_types() -> dict[str, dict[str, str]]:
    """{key: {label, color, glyph}} from the viewer's CHANGE_TYPES, the single source of the legend."""
    src = (ROOT / "viewer/src/domain.ts").read_text()
    rows = re.findall(r'\{ key: "(\w+)", label: "([^"]+)", color: "(#\w+)", blurb: "[^"]*", glyph: "([^"]+)" \}', src)
    return {k: {"label": label, "color": c, "glyph": g} for k, label, c, g in rows}


def icon(t: dict[str, str], size: int) -> str:
    return (
        f'<svg viewBox="-2 -2 28 28" width="{size}" height="{size}"><circle cx="12" cy="12" r="13" fill="#fff"/>'
        f'<circle cx="12" cy="12" r="11.3" fill="{t["color"]}"/><g transform="translate(4.6 4.6) scale(0.62)">'
        f'<path d="{t["glyph"]}" fill="none" stroke="#0f172a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></g></svg>'
    )


def app_facilities() -> dict[str, dict[str, str]]:
    """{facility_id: {class, color, glyph}} from viewer/src/domain.ts and the register, so report icons match the map."""
    src = (ROOT / "viewer/src/domain.ts").read_text()
    glyphs = dict(re.findall(r'  (\w+): \{ label: "[^"]+", glyph: "([^"]+)" \}', src))
    colors = dict(re.findall(r'  "?([\w-]+)"?: "(#[0-9a-f]{6})",', src.split("FACILITY_COLORS")[1].split("};")[0]))
    fac = json.loads((ROOT / "docs/web/facilities.geojson").read_text())["features"]
    return {
        f["properties"]["facility_id"]: {
            "class": f["properties"]["class"],
            "color": colors[f["properties"]["facility_id"]],
            "glyph": glyphs[f["properties"]["class"]],
        }
        for f in fac
    }


def write_icons() -> None:
    """One SVG per change type (icons/icon_<type>.svg) and per facility (icons/fac_<id>.svg), for the report's tables."""
    out = ROOT / "docs/figures/icons"
    out.mkdir(exist_ok=True)
    ns = '<svg xmlns="http://www.w3.org/2000/svg" '
    for k, t in app_types().items():
        (out / f"icon_{k}.svg").write_text(icon(t, 24).replace("<svg ", ns, 1) + "\n")
    for fid, f in app_facilities().items():
        badge = (
            f'{ns}viewBox="0 0 24 24" width="24" height="24"><rect x=".5" y=".5" width="23" height="23" rx="6" fill="{f["color"]}"/>'
            f'<g transform="translate(4.2 4.2) scale(0.65)"><path d="{f["glyph"]}" fill="none" stroke="#0f172a" stroke-width="2.4" '
            'stroke-linecap="round" stroke-linejoin="round"/></g></svg>\n'
        )
        (out / f"fac_{fid}.svg").write_text(badge)


if __name__ == "__main__":
    write_icons()
