"""Static figures (matplotlib), and web overlays (WebP in EPSG:3857 + manifest, GeoJSON).

Date captions use ``Products.date_labels`` ("nominal 2023-08-12 (mosaic of 2023-07-01/07-04)"): the provided rasters are
multi-day mosaics, so a bare date would pass a label for an acquisition date.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds  # noqa: E402

from .io import Grid  # noqa: E402
from .pipeline import Products  # noqa: E402

TYPE_COLORS = {  # the viewer's legend (webapp/viewer/src/domain.ts CHANGE_TYPES), so figures, map and icons agree
    "tailings_deposition": "#fbbf24",
    "tailings_dam_works": "#dc2626",
    "waste_dumping": "#fb7185",
    "pit_excavation": "#a78bfa",
    "water_level_change": "#38bdf8",
    "seasonal_vegetation_or_burn": "#4ade80",
    "infrastructure_or_settlement": "#2dd4bf",
    "land_clearing": "#fb923c",
    "mine_expansion_clearing": "#f0abfc",
    "unexplained": "#94a3b8",
    "noise_or_artifact": "#64748b",
}
CLASS_COLORS = {
    "pit_dump_complex": "#a855f7",
    "waste_dump": "#f59e0b",
    "tailings": "#06b6d4",
    "water_reservoir": "#3b82f6",
    "plant": "#ec4899",
    "camp": "#84cc16",
    "settlement": "#f43f5e",
}


def rgb(refl: np.ndarray, lo: float = 0.0, hi: float = 0.25, gamma: float = 0.8) -> np.ndarray:
    """(3, H, W) reflectance in B, G, R order -> (H, W, 3) display RGB in [0, 1]."""
    img = np.moveaxis(refl[[2, 1, 0]], 0, -1)
    return np.nan_to_num(np.clip((img - lo) / (hi - lo), 0, 1) ** gamma)


def _extent(grid: Grid) -> list[float]:
    t = grid.transform
    return [t.c, t.c + t.a * grid.width, t.f + t.e * grid.height, t.f]


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path


def figures(p: Products, out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    ext = _extent(p.before.grid)
    paths = []
    aoi = p.aoi.boundary

    def frame(ax, title):
        ax.set_title(title, fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])
        aoi.plot(ax=ax, color="white", lw=0.8, ls="--")

    # 9. Facility register: named outlines over true colour
    fig, ax = plt.subplots(figsize=(11, 11))
    ax.imshow(rgb(p.refl_before), extent=ext)
    fac = p.facilities
    for cls, c in CLASS_COLORS.items():
        sub = fac[fac["class"] == cls]
        if len(sub):
            sub.plot(ax=ax, facecolor="none", edgecolor=c, lw=1.8)
    for _, r in fac[~fac.geometry.is_empty].iterrows():
        pt = r.geometry.representative_point()
        ax.annotate(
            f"{r.facility_id}\n{r['name']}\n{r.area_ha / 100:.1f} km²",
            (pt.x, pt.y),
            fontsize=7,
            color="white",
            ha="center",
            bbox={"boxstyle": "round,pad=0.2", "fc": CLASS_COLORS[r["class"]], "ec": "none", "alpha": 0.85},
        )
    frame(ax, f"Facility register: analyst-drawn outlines over true colour, {p.date_labels['before']}")
    ax.legend(
        handles=[Patch(facecolor="none", edgecolor=c, label=k) for k, c in CLASS_COLORS.items()], loc="lower right", fontsize=8
    )
    paths.append(_save(fig, out_dir / "09_facility_register.png"))

    return paths


def rgba(refl: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """(3, H, W) reflectance -> (4, H, W) uint8 true colour, opaque where ``valid``."""
    alpha = (valid * 255).astype(np.uint8)[None]
    return np.concatenate([np.moveaxis((rgb(refl) * 255).astype(np.uint8), -1, 0), alpha])


def intensity_rgba(d2: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """(H, W) squared Mahalanobis distance -> (4, H, W) uint8 inferno heat map of log10 d2, transparent where not ``valid``."""
    intensity = np.nan_to_num(np.clip(np.log10(np.clip(d2, 1e-3, None)) / 3, 0, 1))
    heat = (matplotlib.colormaps["inferno"](intensity) * 255).astype(np.uint8)
    heat[..., 3] = np.where(valid, (intensity * 230).astype(np.uint8), 0)
    return np.moveaxis(heat, -1, 0)


def mask_rgba(mask: np.ndarray, color: str = "#ef4444", alpha: int = 170) -> np.ndarray:
    """(H, W) bool -> (4, H, W) uint8: ``color`` where ``mask``, transparent elsewhere."""
    rgb_ = (np.array(matplotlib.colors.to_rgb(color)) * 255).astype(np.uint8)
    out = np.zeros((4, *mask.shape), np.uint8)
    out[:3] = rgb_[:, None, None]
    out[3] = np.where(mask, alpha, 0)
    return out


def warp_webp(arr: np.ndarray, grid: Grid, path: Path) -> list[list[float]]:
    """Reproject (bands, H, W) uint8 RGBA to EPSG:3857, save as WebP, return Leaflet bounds [[S, W], [N, E]]."""
    from PIL import Image

    bands, h, w = arr.shape
    dst_crs = "EPSG:3857"
    left, bottom, right, top = rasterio.transform.array_bounds(h, w, grid.transform)
    tr, dw, dh = calculate_default_transform(grid.crs, dst_crs, w, h, left, bottom, right, top)
    dst = np.zeros((bands, dh, dw), np.uint8)
    for b in range(bands):
        reproject(
            arr[b],
            dst[b],
            src_transform=grid.transform,
            src_crs=grid.crs,
            dst_transform=tr,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.moveaxis(dst, 0, -1)).save(path, quality=88, method=4)  # method 6 is ~7x slower for the same size
    w_, s_, e_, n_ = transform_bounds(dst_crs, "EPSG:4326", *rasterio.transform.array_bounds(dh, dw, tr))
    return [[s_, w_], [n_, e_]]


def _round_coords(obj, nd: int = 6):
    """Round every coordinate in a GeoJSON geometry (nested lists of numbers) to ``nd`` decimals."""
    return round(obj, nd) if isinstance(obj, float) else [_round_coords(o, nd) for o in obj] if isinstance(obj, list) else obj


def write_geojson(gdf: gpd.GeoDataFrame, path: Path) -> None:
    """RFC 7946 GeoJSON (WGS 84, no legacy ``crs`` member); dict-valued columns stay nested JSON objects."""
    fc = json.loads(gdf.to_crs(4326).to_json(drop_id=True, na="null"))
    for f in fc["features"]:
        f["geometry"]["coordinates"] = _round_coords(f["geometry"]["coordinates"])
    Path(path).write_text(json.dumps(fc, allow_nan=False))


def export_web(p: Products, out_dir: Path) -> dict:
    """WebP overlays + GeoJSON + manifest.json, consumed by the MapLibre viewer.

    Also writes ``facilities.geojson`` (register outlines with their change statistics).
    """
    out_dir = Path(out_dir)
    grid = p.before.grid
    layers = {
        "before": (rgba(p.refl_before, p.valid), f"Sentinel-2 RGB, {p.date_labels['before']}"),
        "after_normalized": (rgba(p.refl_after_norm, p.valid), f"Sentinel-2 RGB, {p.date_labels['after']} (normalized)"),
        "change_intensity": (intensity_rgba(p.cva.d2, p.valid), "Change intensity (log10 d2)"),
        "baseline": (mask_rgba(p.baseline_binary.astype(bool) & p.valid), "Provided baseline change mask (Euclidean + Otsu)"),
    }
    manifest = {
        "date_before": str(p.before.acquired),
        "date_after": str(p.after.acquired),
        "date_labels": p.date_labels,
        "rasters": {},
        "summary": p.summary["changed_area_ha"],
    }
    for key, (arr, label) in layers.items():
        bounds = warp_webp(arr, grid, out_dir / f"{key}.webp")
        manifest["rasters"][key] = {"url": f"{key}.webp", "label": label, "bounds": bounds}

    write_geojson(p.features["robust_cva"].drop(columns=["region"]), out_dir / "change_features.geojson")
    facilities = p.facilities.merge(p.facility_changes.drop(columns=["name", "class", "area_ha"]), on="facility_id")
    write_geojson(facilities, out_dir / "facilities.geojson")
    p.aoi.to_crs(4326).to_file(out_dir / "aoi.geojson", driver="GeoJSON", layer_options={"RFC7946": "YES"})
    manifest["vectors"] = {"changes": "change_features.geojson", "facilities": "facilities.geojson", "aoi": "aoi.geojson"}
    path = out_dir / "manifest.json"
    if (
        path.exists()
    ):  # keep layers other scripts added (pipeline/scripts/super_resolution.py: sr_before, sr_after) while their file exists
        for key, entry in json.loads(path.read_text()).get("rasters", {}).items():
            if key not in manifest["rasters"] and (out_dir / entry["url"]).exists():
                manifest["rasters"][key] = entry
    path.write_text(json.dumps(manifest, indent=2))
    return manifest
