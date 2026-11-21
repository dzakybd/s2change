"""Export the static content the web app reads on its Overview and Report tabs.

    uv run python -m s2cd.site

Writes, under docs/web/ (served at /web/ by the live site and by GitHub Pages):

* overview.json: headline numbers (data/processed/run_summary.json), the facility register
  (data/change_detection.sqlite), and the sensitivity grid (data/processed/sensitivity.csv);
* facilities/<ID>_before.webp and <ID>_after.webp: a square satellite crop of each register facility from the map
  overlays, centred on the facility (before, and normalized after with the detected change outlined in yellow), shown
  as thumbnails on the Overview;
* report/report.md and report/figures/*: report.md with its images copied (docs/figures, or any repo-relative path
  such as the logo in viewer/public/) and the links rewritten so the document renders from docs/web/report/.

Everything is read from files the pipeline already wrote, so this never recomputes anything.
"""

from __future__ import annotations

import csv
import json
import math
import re
import shutil
import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path

PROCESSED = Path("data/processed")
DB = Path("data/change_detection.sqlite")
WEB = Path("docs/web")
REPORT_SRC = Path("report.md")
FIGURES_SRC = Path("docs/figures")
GITHUB_BLOB = "https://github.com/dzakybd/s2change/blob/main/"

IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
HTML_IMAGE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")')
LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")


def _read_json(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def facilities(db: Path = DB) -> list[dict]:
    """The facility register with its change breakdown, from the SpatiaLite tables facilities + facility_changes.

    Only plain columns are selected, so no spatial extension is needed to read them.
    """
    if not db.exists():
        return []
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT f.facility_id, f.name, f.class, f.cover, f.area_ha, c.changed_ha, c.changed_share, c.change_types, c.summary "
            "FROM facilities f JOIN facility_changes c USING (facility_id) ORDER BY c.changed_ha DESC, f.facility_id"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        types = json.loads(d["change_types"]) if d["change_types"] else None
        d["change_types"] = types if isinstance(types, dict) else {}  # a facility without change is stored as NaN
        out.append(d)
    return out


THUMB_PX = 192  # stored size; the Overview shows them at about half that, so they stay sharp on 2x screens
CHANGE_OUTLINE = (250, 204, 21, 255)  # yellow: readable on both bare ground and vegetation


def _merc_y(lat: float) -> float:
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def _rings(geom: dict):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"] if geom["type"] == "MultiPolygon" else []
    for poly in polys:
        yield from poly


def thumbnails(out: Path = WEB / "facilities") -> dict[str, dict[str, str]]:
    """Crop each facility from the before / normalized-after overlays (EPSG:3857 WebP with lon/lat bounds) and outline the
    change polygons on the after crop. Returns {facility_id: {"before": url, "after": url}} relative to docs/web/."""
    from PIL import Image, ImageDraw

    manifest = _read_json(WEB / "manifest.json")
    fac_fc, chg_fc = _read_json(WEB / "facilities.geojson"), _read_json(WEB / "change_features.geojson")
    if not (manifest and fac_fc and chg_fc):
        return {}
    (s, w), (n, e) = manifest["rasters"]["before"]["bounds"]
    images = {
        k: Image.open(WEB / manifest["rasters"][src]["url"]).convert("RGBA")
        for k, src in (("before", "before"), ("after", "after_normalized"))
    }
    width, height = images["before"].size
    y_top, y_bottom = _merc_y(n), _merc_y(s)

    def px(lon: float, lat: float) -> tuple[float, float]:
        return (lon - w) / (e - w) * width, (y_top - _merc_y(lat)) / (y_top - y_bottom) * height

    out.mkdir(parents=True, exist_ok=True)
    result = {}
    for f in fac_fc["features"]:
        fid = f["properties"]["facility_id"]
        pts = [px(*c[:2]) for ring in _rings(f["geometry"]) for c in ring]
        if not pts:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        half = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 * 1.12 + 6  # square, with a margin
        half = min(half, width / 2, height / 2)
        cx, cy = min(max(cx, half), width - half), min(max(cy, half), height - half)  # keep the square inside the image
        box = (cx - half, cy - half, cx + half, cy + half)
        scale = THUMB_PX / (2 * half)
        for key, img in images.items():
            crop = img.crop(tuple(round(v) for v in box)).resize((THUMB_PX, THUMB_PX), Image.LANCZOS)
            draw = ImageDraw.Draw(crop)

            def local(c, box=box, scale=scale):
                x, y = px(*c[:2])
                return ((x - box[0]) * scale, (y - box[1]) * scale)

            if key == "after":
                for g in chg_fc["features"]:
                    if g["properties"].get("facility_id") != fid or g["properties"].get("change_type") == "noise_or_artifact":
                        continue
                    for ring in _rings(g["geometry"]):
                        draw.line([local(c) for c in ring], fill=CHANGE_OUTLINE, width=2)
            crop.convert("RGB").save(out / f"{fid}_{key}.webp", quality=82, method=4)
        result[fid] = {k: f"facilities/{fid}_{k}.webp" for k in images}
    return result


def sensitivity(path: Path = PROCESSED / "sensitivity.csv") -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [
            {
                "far_low": float(r["far_low"]),
                "mmu_ha": float(r["mmu_ha"]),
                "changed_ha": float(r["changed_ha"]),
                "changed_pct_of_aoi": float(r["changed_pct_of_aoi"]),
                "share_in_mine_footprint": float(r["share_in_mine_footprint"]),
                "share_in_register_mine_classes": float(r["share_in_register_mine_classes"]),
                "is_default": r["is_default"].strip().lower() == "true",
            }
            for r in csv.DictReader(f)
        ]


def overview() -> dict:
    run = _read_json(PROCESSED / "run_summary.json")
    if run is None:
        raise SystemExit("data/processed/run_summary.json is missing: run `s2cd run` first")
    grid = run["grid"]
    aoi_ha = grid["width"] * grid["height"] * grid["pixel_m"] ** 2 / 10_000
    sens = sensitivity()
    default = next((s for s in sens if s["is_default"]), None)
    changed = run["changed_area_ha"]
    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "pair": {
            "before": "2023-08-12",
            "after": "2023-09-02",
            "note": "Nominal dates: each is a mosaic of two Sentinel-2 scenes.",
            "acquisitions": run["acquisition"],
            "compared_gap_days": _compared_gaps(run),
        },
        "headline": {
            "changed_ha": changed["robust_cva"],
            "n_polygons": run["n_features"]["robust_cva"],
            "aoi_ha": round(aoi_ha, 1),
            "changed_pct_of_aoi": default["changed_pct_of_aoi"] if default else round(100 * changed["robust_cva"] / aoi_ha, 2),
            "baseline_ha": changed["euclidean_baseline"],
            "baseline_n_polygons": run["n_features"]["euclidean_baseline"],
            "share_in_mine_footprint": run["footprint"]["register_all_facilities"][
                "share_of_change"
            ],  # same definition as report.md (named facilities)
            "share_in_register_mine_classes": run["footprint"]["register_mine_classes"]["share_of_change"],
            "agreement_iou_with_baseline": run["agreement_cva_vs_baseline_iou"],
            "n_zones": run["zones"]["n_zones"],
            "runtime_s": run["runtime_s"],
        },
        "change_type_ha": run["change_type_ha"],
        "facilities": facilities(),
        "sensitivity": sens,
    }


def _compared_gaps(run: dict) -> list[int]:
    """Day gaps of the scene pairs actually compared (one per provenance zone), not every before x after combination."""
    acq = list(run["acquisition"].values())
    b_dates, a_dates = acq[0]["source_dates"], acq[1]["source_dates"]
    gaps = set()
    for z in run["zones"].get("zone_source_pairs", []):
        b = date.fromisoformat(b_dates[z["before_source_index"]])
        a = date.fromisoformat(a_dates[z["after_source_index"]])
        gaps.add((a - b).days)
    return sorted(gaps)


def report(src: Path = REPORT_SRC, figures: Path = FIGURES_SRC, out: Path = WEB / "report") -> list[str]:
    """Copy report.md with figures under out/, rewriting image links to figures/<name> and other relative links to GitHub."""
    text = src.read_text()
    (out / "figures").mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    skipped: list[str] = []

    def copy_image(target: str) -> str | None:
        """Copy the image a relative link points to into out/figures and return its new link, or None if missing."""
        name = Path(target).name
        source = figures / name
        if not source.exists():
            source = Path(target)  # repo-relative, e.g. viewer/public/favicon.svg
        if not source.is_file():
            skipped.append(target)
            return None
        shutil.copyfile(source, out / "figures" / name)
        copied.append(name)
        return f"figures/{name}"

    def image(m: re.Match[str]) -> str:
        alt, target = m.groups()
        if target.startswith(("http://", "https://")):
            return m.group(0)
        new = copy_image(target)
        return f"![{alt}]({new})" if new else ""

    def html_image(m: re.Match[str]) -> str:
        head, target, tail = m.groups()
        if target.startswith(("http://", "https://", "data:")):
            return m.group(0)
        new = copy_image(target)
        return f"{head}{new}{tail}" if new else m.group(0)

    def link(m: re.Match[str]) -> str:
        label, target = m.groups()
        if target.startswith(("http://", "https://", "#", "mailto:")):
            return m.group(0)
        return f"[{label}]({GITHUB_BLOB}{target.lstrip('./')})"

    text = IMAGE.sub(image, text)
    text = HTML_IMAGE.sub(html_image, text)
    text = LINK.sub(link, text)
    (out / "report.md").write_text(text)
    for t in skipped:
        print(f"warning: figure {t} not found, image dropped from the web report", file=sys.stderr)
    return sorted(set(copied))


def main() -> None:
    WEB.mkdir(parents=True, exist_ok=True)
    data = overview()
    thumbs = thumbnails()
    for f in data["facilities"]:
        f["thumbnails"] = thumbs.get(f["facility_id"])
    (WEB / "overview.json").write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    figs = report()
    print(f"docs/web/overview.json: {len(data['facilities'])} facilities")
    print(f"docs/web/report/report.md: {len(figs)} figures copied")


if __name__ == "__main__":
    main()
