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

DIRECTION_COLORS = {"brightening": "#f59e0b", "darkening": "#3b82f6", "spectral shift": "#a855f7"}
TYPE_COLORS = {  # the viewer's legend (viewer/src/domain.ts CHANGE_TYPES), so figures, map and icons agree
    "tailings_deposition": "#fbbf24",
    "tailings_dam_raise": "#dc2626",
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
    grid, ext = p.before.grid, _extent(p.before.grid)
    paths = []
    aoi = p.aoi.boundary

    def frame(ax, title):
        ax.set_title(title, fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])
        aoi.plot(ax=ax, color="white", lw=0.8, ls="--")

    # 1. Inputs and normalization
    fig, ax = plt.subplots(1, 3, figsize=(18, 6.4))
    for a, img, t in [
        (ax[0], p.refl_before, f"T1: {p.date_labels['before']}"),
        (ax[1], p.refl_after, f"T2 as delivered: {p.date_labels['after']}"),
        (ax[2], p.refl_after_norm, "T2 normalized to T1 per zone"),
    ]:
        a.imshow(rgb(img), extent=ext)
        frame(a, t)
    paths.append(_save(fig, out_dir / "01_inputs_and_normalization.png"))

    # 2. Zone diagnostics: data-driven stripe detection, and the zones used (source-scene pairs when provenance exists)
    s = p.stripes
    fig, ax = plt.subplots(1, 2, figsize=(16, 6.4), gridspec_kw={"width_ratios": [1, 1.3]})
    diff = p.refl_after[0] - p.refl_before[0]
    im = ax[0].imshow(diff, cmap="RdBu_r", vmin=-0.02, vmax=0.08, extent=ext)
    ys = np.array([0, grid.height])  # row edges, i.e. continuous pixel coordinates
    t = np.deg2rad(s.angle_deg)
    for i, b in enumerate(s.breakpoints):
        xs = (b - ys * np.sin(t)) / np.cos(t)  # p = X cos t + Y sin t with continuous X, Y (pixel centres sit at +0.5)
        ax[0].plot(
            ext[0] + xs * grid.transform.a,
            ext[3] + ys * grid.transform.e,
            "k--",
            lw=1,
            label="data-driven boundary" if i == 0 else None,
        )
    handles = []
    if p.zone_source == "provenance":
        ax[0].contour(
            p.zones, levels=np.arange(p.zones.max()) + 0.5, extent=ext, origin="upper", colors="#16a34a", linewidths=1.4
        )
        handles.append(plt.Line2D([], [], color="#16a34a", lw=1.4, label="source-scene boundary (zones used)"))
    ax[0].set_xlim(ext[0], ext[1])
    ax[0].set_ylim(ext[2], ext[3])
    frame(ax[0], "Blue reflectance T2 - T1: detected stripe boundaries" + (" and source-scene seams" if handles else ""))
    ax[0].legend(handles=[*ax[0].get_legend_handles_labels()[0], *handles], loc="lower left", fontsize=8)
    fig.colorbar(im, ax=ax[0], shrink=0.7, label="reflectance difference")
    if len(s.profile[0]):
        ax[1].plot(s.profile[0], s.profile[1], lw=0.8, color="0.3", label="mean profile across stripes")
        edges = [s.profile[0][0], *s.breakpoints, s.profile[0][-1]]
        for i, off in enumerate(s.zone_offsets):
            ax[1].hlines(off, edges[i], edges[i + 1], color="C3", lw=2, label="zone offset" if i == 0 else None)
        for b in s.breakpoints:
            ax[1].axvline(b, color="k", ls="--", lw=0.8)
    ax[1].set_xlabel(f"position across stripes (px, normal angle {s.angle_deg:.1f} deg)")
    ax[1].set_ylabel("blue T2 - T1 reflectance")
    ax[1].legend()
    ax[1].set_title(
        f"data-driven: {len(s.zone_offsets)} zones, piecewise-constant fit explains {100 * s.explained_variance:.0f}% of variance"
    )
    paths.append(_save(fig, out_dir / "02_stripe_zones.png"))

    # 4. Change polygons over T2
    g = p.features["robust_cva"]
    fig, ax = plt.subplots(figsize=(11, 11))
    ax.imshow(rgb(p.refl_after_norm), extent=ext)
    for d, c in DIRECTION_COLORS.items():
        sub = g[g.change_direction == d]
        if len(sub):
            sub.plot(ax=ax, facecolor=c, edgecolor=c, alpha=0.55, lw=0.6)
    frame(ax, f"Change polygons ({len(g)}), robust CVA: {p.date_labels['before']} -> {p.date_labels['after']}")
    ax.legend(
        handles=[Patch(color=c, label=d) for d, c in DIRECTION_COLORS.items()]
        + [Patch(facecolor="none", edgecolor="white", ls="--", label="AOI")],
        loc="lower right",
    )
    paths.append(_save(fig, out_dir / "04_change_polygons.png"))

    # 5. Noise model: distribution of the statistic, and the realised false-alarm rate on held-out no-change pixels
    cal = p.cva.calibration
    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    ax[0].hist(np.log10(np.clip(p.cva.d2[p.valid], 1e-3, None)), bins=200, color="0.4")
    far = p.summary["noise_calibration"]["far"] if cal else 1e-3
    for (v, lab), y in zip(
        [
            (p.summary["thresholds"]["cva_d2_low"], f"grow (nominal {far:g})"),
            (p.summary["thresholds"]["cva_d2_high"], "seed (nominal 100x stricter)"),
        ],
        (0.5, 0.15),
        strict=True,
    ):
        ax[0].axvline(np.log10(v), color="C3", ls="--")
        ax[0].text(np.log10(v), ax[0].get_ylim()[1] * y, " " + lab, color="C3", fontsize=9)
    ax[0].set_xlabel("log10 d2 (all valid pixels)")
    ax[0].set_yscale("log")
    ax[0].set_title("Distribution of the change statistic")
    if cal:
        strata = [r for r in cal["strata"] if "realised_far_model" in r]
        x = np.arange(len(strata))
        for off, key, label, color in [
            (-0.27, "realised_far_previous_design", "previous: one model per zone, 2.5-sigma PIFs", "0.65"),
            (0.0, "realised_far_model", "brightness-aware model", "C0"),
            (0.27, "realised_far_final", f"final ({cal['thresholds_source']}), cross-fitted", "C2"),
        ]:
            ax[1].bar(x + off, [r[key] for r in strata], width=0.26, label=label, color=color)
        ax[1].axhline(cal["far"], color="k", ls="--", lw=1, label=f"nominal {cal['far']:g}")
        ax[1].set_xticks(x, [f"{r['brightness_before_mean']:.2f}" for r in strata])
        ax[1].set_yscale("log")
        ax[1].set_xlabel("mean T1 brightness of the stratum (reflectance)")
        ax[1].set_ylabel("realised exceedance of the nominal threshold")
        ax[1].legend(fontsize=8)
        ax[1].set_title("Realised vs nominal false-alarm rate on held-out no-change pixels")
    paths.append(_save(fig, out_dir / "05_noise_model_qq.png"))

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
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
