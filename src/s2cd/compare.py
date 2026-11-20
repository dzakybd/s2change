"""Compare any two dates (the app's date picker): resolve the scenes, run the pipeline, export GeoJSON + overlays.

``run("provided-before", "2026-10-23")`` writes ``data/compare/<pair>/``:

    changes.geojson   robust-CVA polygons (RFC 7946, EPSG:4326) with change_type, explanation, facility_id, ...
    summary.json      changed_ha, by_change_type, by_facility, date labels
    manifest.json     the overlays and their Leaflet bounds
    before.webp, after.webp, intensity.webp
    db.sqlite         the pipeline's SpatiaLite output (change_runs, change_features, facilities, facility_changes)

Results are cached per pair: ``summary.json`` is written last, so its presence means the pair is complete.
A label is ``provided-before`` / ``provided-after`` (the assignment rasters) or a ``YYYY-MM-DD`` from the date catalogue
(``s2cd.catalog``): scenes downloaded and cloud-checked in advance, so a comparison never touches the network.
Only B02/B03/B04 are used throughout.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import matplotlib
import numpy as np

from . import catalog, pipeline, viz
from .io import parse_date

ROOT = Path("data/compare")
PROVIDED = {"provided-before": Path("inputs/data/sentinel2_20230812"), "provided-after": Path("inputs/data/sentinel2_20230902")}
PROPERTIES = [  # polygon properties exported to changes.geojson (same names as the app's /api/changes)
    *("area_m2", "confidence", "neglog10_p", "change_direction", "change_type", "brief_category"),
    *("explanation", "facility_id", "touches_aoi_edge"),
]
OVERLAYS = ("before", "after", "intensity")
PAIR = re.compile(r"\d{8}_\d{8}")


def parse_label(label: str) -> date:
    """The date a label stands for. Raises ValueError with a message fit for the user."""
    if label in PROVIDED:
        return parse_date(PROVIDED[label])
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", label):
        raise ValueError(f"{label!r} is not a date: use YYYY-MM-DD, 'provided-before' or 'provided-after'.")
    try:
        return date.fromisoformat(label)
    except ValueError:
        raise ValueError(f"{label!r} is not a calendar date.") from None


def validate_pair(before: str, after: str, today: date | None = None) -> tuple[date, date]:
    """Parse both labels; the dates must not be in the future and ``after`` must be later than ``before``."""
    d_before, d_after = parse_label(before), parse_label(after)
    for label in (before, after):
        if label not in PROVIDED:
            catalog.folder(label)  # only catalogue dates: their scenes are on disk
    if d_after > (today or date.today()):
        raise ValueError(f"{d_after} is in the future.")
    if d_after <= d_before:
        raise ValueError(f"The after date ({d_after}) must be later than the before date ({d_before}).")
    return d_before, d_after


def pair_id(d_before: date, d_after: date) -> str:
    return f"{d_before:%Y%m%d}_{d_after:%Y%m%d}"


def _folder(label: str, day: date) -> Path:
    return PROVIDED[label] if label in PROVIDED else catalog.folder(label)


def _overlays(p: pipeline.Products, out: Path) -> dict:
    """Before/after RGB and the change-intensity heat map as WebP (EPSG:3857) with Leaflet bounds [[S, W], [N, E]]."""
    grid = p.before.grid
    alpha = (p.valid * 255).astype(np.uint8)[None]

    def rgba(refl):
        return np.concatenate([np.moveaxis((viz.rgb(refl) * 255).astype(np.uint8), -1, 0), alpha])

    intensity = np.nan_to_num(np.clip(np.log10(np.clip(p.cva.d2, 1e-3, None)) / 3, 0, 1))
    heat = (matplotlib.colormaps["inferno"](intensity) * 255).astype(np.uint8)
    heat[..., 3] = np.where(p.valid, (intensity * 230).astype(np.uint8), 0)
    arrays = {"before": rgba(p.refl_before), "after": rgba(p.refl_after), "intensity": np.moveaxis(heat, -1, 0)}
    return {
        name: {"file": f"{name}.webp", "bounds": viz._warp_png(arr, grid, out / f"{name}.webp")} for name, arr in arrays.items()
    }


def _summary(p: pipeline.Products) -> dict:
    polys = p.features["robust_cva"]
    by_type = (polys.groupby("change_type").area_m2.sum() / 1e4).round(1).sort_values(ascending=False)
    fac = p.facility_changes[p.facility_changes.changed_ha > 0].sort_values("changed_ha", ascending=False)
    return {
        "date_before": str(p.before.acquired),
        "date_after": str(p.after.acquired),
        "date_labels": p.date_labels,
        "n_polygons": len(polys),
        "changed_ha": round(float(polys.area_m2.sum()) / 1e4, 1),
        "by_change_type": by_type.to_dict(),
        "by_facility": dict(zip(fac.facility_id, fac.changed_ha, strict=True)),
    }


def run(before: str, after: str, root: Path = ROOT) -> Path:
    """Compare two dates and return the pair's folder (cached: an existing result is returned untouched)."""
    d_before, d_after = validate_pair(before, after)
    out = Path(root) / pair_id(d_before, d_after)
    if (out / "summary.json").exists():
        return out
    cfg = pipeline.Config(
        before=_folder(before, d_before),
        after=_folder(after, d_after),
        out_dir=out,
        sqlite_path=out / "db.sqlite",
        run_irmad=False,
    )
    p = pipeline.run(cfg)  # no figures: viz.figures is only called by the CLI
    polys = p.features["robust_cva"].reindex(columns=[*PROPERTIES, "geometry"])
    polys.insert(0, "id", range(1, len(polys) + 1))
    viz._write_geojson(polys, out / "changes.geojson")
    manifest = {"overlays": _overlays(p, out)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    for stale in (*out.glob("*.tif"), out / "change_features.geojson"):  # large pipeline by-products the app does not serve
        stale.unlink(missing_ok=True)
    (out / "summary.json").write_text(json.dumps(_summary(p), indent=2))
    return out
