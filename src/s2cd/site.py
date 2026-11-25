"""Export the static content the web app reads on its Report and Downloads tabs.

    uv run python -m s2cd.site

Writes, under docs/web/ (served at /web/ by the live site and by GitHub Pages):

* facilities/<ID>_before.webp and <ID>_after.webp: a square satellite crop of each register facility from the map
  overlays, centred on the facility (before, and normalized after with the detected change outlined in yellow), shown
  in the report's facility table;
* downloads.json and previews/*: the Downloads catalogue with each file's real size and a preview rendered from the file
  (a quick-look image of every GeoTIFF and GeoJSON, the SpatiaLite tables, a copy of api.md);
* report/report.md and report/figures/*: report.md with its images copied (docs/figures, or any repo-relative path
  such as the logo in viewer/public/ and the facility thumbnails) and the links rewritten so the document renders from
  docs/web/report/; report/s2change-report.md is the downloadable copy, with images loaded from GitHub.

Everything is read from files the pipeline already wrote, so this never recomputes anything.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import sqlite3
import sys
from pathlib import Path

WEB = Path("docs/web")
REPORT_SRC = Path("report.md")
FIGURES_SRC = Path("docs/figures")
GITHUB_BLOB = "https://github.com/dzakybd/s2change/blob/main/"
GITHUB_RAW = "https://raw.githubusercontent.com/dzakybd/s2change/main/"

IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
HTML_IMAGE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")')
LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")


def _read_json(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


THUMB_PX = 192  # stored size; the report shows them at a third of that, so they stay sharp on 2x screens
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


# ---- Downloads: the file catalogue and a preview of each file ----------------------------------------------------

PREVIEW_PX = 560  # long side of a preview image
NO_DATA_RGB = (241, 243, 246)
SPATIALITE_TABLES = ("change_runs", "change_features", "facilities", "facility_changes")

# (group, blurb, [(repo path, description, preview kind)]). Preview kinds: rgb (B02/B03/B04 stack), magnitude (a
# continuous change layer), binary (0/1/255), vector (GeoJSON), sqlite, markdown, report (the Report tab itself).
DOWNLOADS = [
    (
        "Change maps",
        "GeoTIFFs on the 10 m grid of the scenes (EPSG:32735). Open in QGIS or rasterio.",
        [
            ("data/processed/change_map.tif", "Robust change magnitude per pixel (log10 d²).", "magnitude"),
            ("data/processed/change_significance.tif", "−log10 p of the change under the noise model.", "magnitude"),
            (
                "data/processed/change_binary.tif",
                "Changed (1) or not (0) after hysteresis and the 2,000 m² minimum mapping unit; 255 = no data.",
                "binary",
            ),
            (
                "data/processed/change_binary_baseline.tif",
                "The provided Euclidean-difference baseline, binarised the same way, for comparison.",
                "binary",
            ),
        ],
    ),
    (
        "Sentinel-2 stacks",
        "B02, B03, B04 as delivered, plus the later date normalised to the earlier one.",
        [
            ("data/processed/sentinel2_20230812_stack.tif", "Nominal 2023-08-12 (mosaic of 2023-07-01 and 07-04).", "rgb"),
            (
                "data/processed/sentinel2_20230902_stack.tif",
                "Nominal 2023-09-02 (mosaic of 2023-08-25 and 09-02), as delivered.",
                "rgb",
            ),
            (
                "data/processed/sentinel2_20230902_stack_normalized.tif",
                "The later date after per-zone radiometric normalisation.",
                "rgb",
            ),
        ],
    ),
    (
        "Database and vectors",
        "The SpatiaLite file holds the same tables the live PostGIS database serves.",
        [
            (
                "data/change_detection.sqlite",
                "SpatiaLite: runs, change features, facility register and per-facility changes.",
                "sqlite",
            ),
            (
                "docs/web/change_features.geojson",
                "The change polygons with type, plain-language description, confidence and facility (web export).",
                "vector",
            ),
            (
                "data/processed/change_features.geojson",
                "Full-precision export with every attribute the pipeline computed.",
                "vector",
            ),
            (
                "docs/web/facilities.geojson",
                "The facility register: outlines, area changed (hectares in the file) and the change mix.",
                "vector",
            ),
            ("docs/web/aoi.geojson", "The area of interest.", "vector"),
        ],
    ),
    (
        "Documents",
        "",
        [
            (
                "docs/api.md",
                "Every HTTP endpoint behind the map, database console and agent, with request and response shapes.",
                "markdown",
            ),
            ("report.md", "The full write-up, as shown in the Report tab.", "report"),
        ],
    ),
]
SAMPLE_COLUMNS = ["facility_id", "name", "class", "change_type", "headline", "area_m2", "area_ha", "changed_ha", "confidence"]


def _size(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} KB"


def _stretch(a, valid, lo: float = 2, hi: float = 98):
    import numpy as np

    p0, p1 = np.percentile(a[valid], [lo, hi]) if valid.any() else (0.0, 1.0)
    return np.clip((a - p0) / ((p1 - p0) or 1), 0, 1)


def _raster_preview(path: Path, kind: str, out: Path) -> list[list[str]]:
    import numpy as np
    import rasterio
    from matplotlib import colormaps
    from PIL import Image

    with rasterio.open(path) as src:
        scale = PREVIEW_PX / max(src.width, src.height)
        shape = (src.count, round(src.height * scale), round(src.width * scale))
        a = src.read(out_shape=shape).astype("float32")
        nodata = src.nodata
        facts = [
            ["Size", f"{src.width:,} × {src.height:,} px"],
            ["Bands", ", ".join(d or f"band {i + 1}" for i, d in enumerate(src.descriptions))],
            ["Type", src.dtypes[0]],
            ["CRS", str(src.crs)],
            ["Pixel", f"{src.res[0]:g} m"],
            ["No data", "none" if nodata is None else f"{nodata:g}"],
        ]
    valid = np.all(a != nodata, axis=0) & np.all(np.isfinite(a), axis=0) if nodata is not None else np.all(np.isfinite(a), axis=0)
    if kind == "rgb":
        img = np.stack([_stretch(a[b], valid) ** 0.8 for b in (2, 1, 0)], axis=-1)
    elif kind == "magnitude":
        img = colormaps["magma"](_stretch(a[0], valid, 2, 99.5) ** 0.6)[..., :3]
    else:  # binary: change in orange on slate
        img = np.where((a[0] == 1)[..., None], np.array([232, 131, 58]) / 255, np.array([51, 65, 85]) / 255)
    img[~valid] = np.array(NO_DATA_RGB) / 255
    Image.fromarray((img * 255).astype("uint8")).save(out, quality=80, method=4)
    return facts


def _vector_preview(path: Path, out: Path) -> tuple[list[list[str]], dict]:
    from PIL import Image, ImageDraw

    from .viz import CLASS_COLORS, TYPE_COLORS

    fc = json.loads(path.read_text())
    aoi = json.loads((WEB / "aoi.geojson").read_text())
    pts = [c[:2] for f in aoi["features"] for ring in _rings(f["geometry"]) for c in ring]
    w, e = min(p[0] for p in pts), max(p[0] for p in pts)
    s, n = min(p[1] for p in pts), max(p[1] for p in pts)
    kx = math.cos(math.radians((n + s) / 2))
    scale = PREVIEW_PX / max((e - w) * kx, n - s)
    size = (round((e - w) * kx * scale), round((n - s) * scale))
    img = Image.new("RGB", size, (248, 250, 252))
    draw = ImageDraw.Draw(img)

    def xy(c):
        return ((c[0] - w) * kx * scale, (n - c[1]) * scale)

    for f in aoi["features"]:
        for ring in _rings(f["geometry"]):
            draw.line([xy(c) for c in ring], fill=(148, 163, 184), width=2)

    # Untyped features (the AOI, the baseline run in the full export) go underneath, pale, so the typed change shows.
    def typed(f) -> bool:
        return bool(f["properties"].get("change_type") or f["properties"].get("class"))

    features = sorted(fc["features"], key=typed)
    for f in features:
        props = f["properties"]
        color = TYPE_COLORS.get(props.get("change_type"), CLASS_COLORS.get(props.get("class")))
        for ring in _rings(f["geometry"]):
            if len(ring) > 2:
                draw.polygon([xy(c) for c in ring], fill=color or (254, 215, 170), outline=color or (232, 131, 58))
    img.save(out, quality=82, method=4)
    props = [f["properties"] for f in sorted(fc["features"], key=lambda f: not typed(f))]
    columns = list(props[0]) if props else []
    shown = [c for c in SAMPLE_COLUMNS if c in columns] or columns[:6]
    types = sorted({f["geometry"]["type"] for f in fc["features"]})
    facts = [
        ["Features", f"{len(props):,}"],
        ["Geometry", ", ".join(types)],
        ["CRS", "EPSG:4326 (lon/lat)"],
        *([["Runs", ", ".join(sorted(runs))]] if (runs := {p["run_id"] for p in props if p.get("run_id")}) else []),
        ["Attributes", f"{len(columns)}: {', '.join(columns)}"],
    ]
    rows = [[_cell(p.get(c)) for c in shown] for p in props[:5]]
    return facts, {"columns": shown, "rows": rows}


def _cell(v) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}"
    text = "" if v is None else str(v)
    return text if len(text) <= 90 else text[:88] + "…"


def _sqlite_preview(path: Path) -> list[dict]:
    with sqlite3.connect(path) as con:
        return [
            {
                "name": t,
                "rows": con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0],
                "columns": [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')],
            }
            for t in SPATIALITE_TABLES
        ]


def downloads(out: Path = WEB / "previews") -> list[dict]:
    """The Downloads tab's catalogue (downloads.json): each file with its real size and a preview (an image, a table
    listing or a copy of the document) rendered from the file itself."""
    out.mkdir(parents=True, exist_ok=True)
    groups = []
    for title, blurb, files in DOWNLOADS:
        items = []
        for repo_path, desc, kind in files:
            path = Path(repo_path)
            if not path.is_file():
                print(f"warning: download {repo_path} not found, left out", file=sys.stderr)
                continue
            slug = re.sub(r"[^a-z0-9]+", "_", repo_path.lower()).strip("_")
            item = {"path": repo_path, "title": path.name, "desc": desc, "size": _size(path.stat().st_size), "kind": kind}
            if kind in ("rgb", "magnitude", "binary"):
                item["facts"] = _raster_preview(path, kind, out / f"{slug}.webp")
                item["image"] = f"previews/{slug}.webp"
            elif kind == "vector":
                item["facts"], item["sample"] = _vector_preview(path, out / f"{slug}.webp")
                item["image"] = f"previews/{slug}.webp"
            elif kind == "sqlite":
                item["tables"] = _sqlite_preview(path)
            elif kind == "markdown":
                shutil.copyfile(path, out / path.name)
                item["document"] = f"previews/{path.name}"
            items.append(item)
        groups.append({"title": title, "blurb": blurb, "items": items})
    (WEB / "downloads.json").write_text(json.dumps({"groups": groups}, indent=1, ensure_ascii=False) + "\n")
    return groups


def report(src: Path = REPORT_SRC, figures: Path = FIGURES_SRC, out: Path = WEB / "report") -> list[str]:
    """Copy report.md with figures under out/, rewriting image links to figures/<name> and other relative links to GitHub."""
    text = src.read_text()
    shutil.rmtree(out / "figures", ignore_errors=True)
    (out / "figures").mkdir(parents=True)
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
    # The Report tab's download: the same document, with images loaded from the repository so it stands alone.
    raw = f"{GITHUB_RAW}{out.as_posix()}/figures/"
    standalone = re.sub(r"(\]\(|src=\")figures/", lambda m: f"{m.group(1)}{raw}", text)
    (out / "s2change-report.md").write_text(standalone)
    for t in skipped:
        print(f"warning: figure {t} not found, image dropped from the web report", file=sys.stderr)
    return sorted(set(copied))


def main() -> None:
    WEB.mkdir(parents=True, exist_ok=True)
    thumbs = thumbnails()
    groups = downloads()
    figs = report()
    print(f"docs/web/facilities: {len(thumbs)} facility thumbnails")
    print(f"docs/web/downloads.json: {sum(len(g['items']) for g in groups)} files with previews")
    print(f"docs/web/report/report.md: {len(figs)} figures copied")


if __name__ == "__main__":
    main()
