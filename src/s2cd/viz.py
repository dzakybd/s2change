"""Static figures (matplotlib), web overlays (PNG in EPSG:3857 + manifest) and a Folium map."""

from __future__ import annotations

import json
import os
from pathlib import Path

import folium
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds  # noqa: E402
from scipy import stats  # noqa: E402

from .io import Grid  # noqa: E402
from .pipeline import Products  # noqa: E402

DIRECTION_COLORS = {"brightening": "#f59e0b", "darkening": "#3b82f6", "spectral shift": "#a855f7"}


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
        (ax[0], p.refl_before, f"{p.before.acquired} (T1)"),
        (ax[1], p.refl_after, f"{p.after.acquired} (T2, as delivered)"),
        (ax[2], p.refl_after_norm, "T2 normalized to T1 per stripe zone"),
    ]:
        a.imshow(rgb(img), extent=ext)
        frame(a, t)
    paths.append(_save(fig, out_dir / "01_inputs_and_normalization.png"))

    # 2. Stripe diagnostics
    s = p.stripes
    fig, ax = plt.subplots(1, 2, figsize=(16, 6.4), gridspec_kw={"width_ratios": [1, 1.3]})
    diff = p.refl_after[0] - p.refl_before[0]
    im = ax[0].imshow(diff, cmap="RdBu_r", vmin=-0.02, vmax=0.08, extent=ext)
    ys = np.array([0, grid.height])
    t = np.deg2rad(s.angle_deg)
    for b in s.breakpoints:
        xs = (b - (ys + 0.5) * np.sin(t)) / np.cos(t) - 0.5
        ax[0].plot(ext[0] + xs * grid.transform.a, ext[3] + ys * grid.transform.e, "k--", lw=1)
    ax[0].set_xlim(ext[0], ext[1])
    ax[0].set_ylim(ext[2], ext[3])
    frame(ax[0], "Blue reflectance T2 - T1 with detected stripe boundaries")
    fig.colorbar(im, ax=ax[0], shrink=0.7, label="reflectance difference")
    if len(s.profile[0]):
        ax[1].plot(s.profile[0], s.profile[1], lw=0.8, color="0.3", label="median profile across stripes")
        edges = [s.profile[0][0], *s.breakpoints, s.profile[0][-1]]
        for i, off in enumerate(s.zone_offsets):
            ax[1].hlines(off, edges[i], edges[i + 1], color="C3", lw=2, label="zone offset" if i == 0 else None)
        for b in s.breakpoints:
            ax[1].axvline(b, color="k", ls="--", lw=0.8)
    ax[1].set_xlabel(f"position across stripes (px, normal angle {s.angle_deg:.1f} deg)")
    ax[1].set_ylabel("blue T2 - T1 reflectance")
    ax[1].legend()
    ax[1].set_title(
        f"{len(s.zone_offsets)} zones, piecewise-constant model explains {100 * s.explained_variance:.0f}% of variance"
    )
    paths.append(_save(fig, out_dir / "02_stripe_zones.png"))

    # 3. Method comparison
    rows = [
        (
            "Baseline: Euclidean distance (provided)",
            p.baseline_prob,
            p.baseline_binary,
            "magma",
            (0, np.nanpercentile(p.baseline_prob, 99.5)),
        ),
        ("Robust CVA (this work): log10 d2", np.log10(np.clip(p.cva.d2, 1e-3, None)), p.cva_binary, "magma", (0, 3)),
    ]
    if p.irmad is not None:
        rows.append(
            ("Zone-wise IR-MAD: log10 chi2", np.log10(np.clip(p.irmad.chi2, 1e-3, None)), p.irmad_binary, "magma", (0, 3))
        )
    fig, ax = plt.subplots(2, len(rows), figsize=(6 * len(rows), 12.4))
    area = p.summary["changed_area_ha"]
    for j, (title, cont, binary, cmap, (vmin, vmax)) in enumerate(rows):
        ax[0, j].imshow(cont, cmap=cmap, vmin=vmin, vmax=vmax, extent=ext)
        frame(ax[0, j], title)
        ax[1, j].imshow(binary, cmap=ListedColormap(["#111", "#fde047"]), extent=ext, interpolation="nearest")
        key = list(area)[j]
        frame(ax[1, j], f"binary: {area[key]:,.0f} ha changed")
    paths.append(_save(fig, out_dir / "03_method_comparison.png"))

    # 4. Change polygons over T2
    g = p.features["robust_cva"]
    fig, ax = plt.subplots(figsize=(11, 11))
    ax.imshow(rgb(p.refl_after_norm), extent=ext)
    for d, c in DIRECTION_COLORS.items():
        sub = g[g.change_direction == d]
        if len(sub):
            sub.plot(ax=ax, facecolor=c, edgecolor=c, alpha=0.55, lw=0.6)
    frame(ax, f"Change polygons ({len(g)}), robust CVA, {p.before.acquired} -> {p.after.acquired}")
    ax.legend(
        handles=[Patch(color=c, label=d) for d, c in DIRECTION_COLORS.items()]
        + [Patch(facecolor="none", edgecolor="white", ls="--", label="AOI")],
        loc="lower right",
    )
    paths.append(_save(fig, out_dir / "04_change_polygons.png"))

    # 5. Calibration of the chi2 noise model on PIF pixels
    d2 = p.cva.d2[p.valid & p.pif]
    q = np.linspace(0.01, 0.999, 300)
    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    ax[0].hist(np.log10(np.clip(p.cva.d2[p.valid], 1e-3, None)), bins=200, color="0.4")
    for (v, lab), y in zip(
        [(p.summary["thresholds"]["cva_d2_low"], "grow (FAR 1e-3)"), (p.summary["thresholds"]["cva_d2_high"], "seed (FAR 1e-5)")],
        (0.5, 0.15),
        strict=True,
    ):
        ax[0].axvline(np.log10(v), color="C3", ls="--")
        ax[0].text(np.log10(v), ax[0].get_ylim()[1] * y, " " + lab, color="C3", fontsize=9)
    ax[0].set_xlabel("log10 d2 (all valid pixels)")
    ax[0].set_yscale("log")
    ax[0].set_title("Distribution of the change statistic")
    ax[1].plot(stats.chi2.ppf(q, 3), np.quantile(d2, q), ".", ms=3)
    lim = stats.chi2.ppf(0.999, 3)
    ax[1].plot([0, lim], [0, lim], "k--", lw=0.8)
    ax[1].set_xlabel("chi2(3) theoretical quantile")
    ax[1].set_ylabel("d2 quantile on pseudo-invariant pixels")
    ax[1].set_title("Noise model fit on pseudo-invariant pixels (in-sample Q-Q)")
    paths.append(_save(fig, out_dir / "05_noise_model_qq.png"))
    return paths


def _warp_png(arr: np.ndarray, grid: Grid, path: Path, categorical: bool = False) -> list[list[float]]:
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
            resampling=Resampling.nearest if categorical else Resampling.bilinear,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.moveaxis(dst, 0, -1)).save(path, quality=88, method=6)
    w_, s_, e_, n_ = transform_bounds(dst_crs, "EPSG:4326", *rasterio.transform.array_bounds(dh, dw, tr))
    return [[s_, w_], [n_, e_]]


def export_web(p: Products, out_dir: Path) -> dict:
    """WebP overlays + GeoJSON + manifest.json, consumed by the Folium map and the MapLibre viewer."""
    out_dir = Path(out_dir)
    grid = p.before.grid
    alpha = (p.valid * 255).astype(np.uint8)[None]

    def rgba(img):
        return np.concatenate([np.moveaxis((rgb(img) * 255).astype(np.uint8), -1, 0), alpha])

    intensity = np.nan_to_num(np.clip(np.log10(np.clip(p.cva.d2, 1e-3, None)) / 3, 0, 1))
    cmap = matplotlib.colormaps["inferno"]
    heat = (cmap(np.nan_to_num(intensity)) * 255).astype(np.uint8)
    heat[..., 3] = np.where(p.valid, (intensity * 230).astype(np.uint8), 0)
    layers = {
        "before": (rgba(p.refl_before), f"Sentinel-2 RGB {p.before.acquired}"),
        "after": (rgba(p.refl_after), f"Sentinel-2 RGB {p.after.acquired} (as delivered)"),
        "after_normalized": (rgba(p.refl_after_norm), f"Sentinel-2 RGB {p.after.acquired} (normalized)"),
        "change_intensity": (np.moveaxis(heat, -1, 0), "Change intensity (log10 d2)"),
    }
    manifest = {
        "date_before": str(p.before.acquired),
        "date_after": str(p.after.acquired),
        "rasters": {},
        "summary": p.summary["changed_area_ha"],
    }
    for key, (arr, label) in layers.items():
        bounds = _warp_png(arr, grid, out_dir / f"{key}.webp")
        manifest["rasters"][key] = {"url": f"{key}.webp", "label": label, "bounds": bounds}
    p.features["robust_cva"].to_crs(4326).drop(columns=["region"]).to_file(
        out_dir / "change_features.geojson", driver="GeoJSON", layer_options={"COORDINATE_PRECISION": 6}
    )
    p.aoi.to_crs(4326).to_file(out_dir / "aoi.geojson", driver="GeoJSON")
    manifest["vectors"] = {"changes": "change_features.geojson", "aoi": "aoi.geojson"}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def folium_map(p: Products, web_dir: Path, out_html: Path) -> Path:
    """Interactive map: imagery overlays, change heatmap, attributed polygons, AOI.

    Overlays are referenced by relative URL (``web_dir`` relative to the HTML file), so keep the
    HTML next to its ``web/`` folder; that is how docs/ is laid out for GitHub Pages.
    """
    web_dir = Path(web_dir)
    manifest = json.loads((web_dir / "manifest.json").read_text())
    (s, w), (n, e) = manifest["rasters"]["before"]["bounds"]
    m = folium.Map(location=[(s + n) / 2, (w + e) / 2], zoom_start=13, tiles=None, control_scale=True)
    folium.TileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Esri World Imagery (undated)",
    ).add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    out_html = Path(out_html)
    rel = Path(os.path.relpath(web_dir, out_html.parent)).as_posix()
    for key, r in manifest["rasters"].items():
        overlay = folium.raster_layers.ImageOverlay(
            np.zeros((1, 1, 4)), bounds=r["bounds"], name=r["label"], show=key in ("after_normalized", "change_intensity")
        )
        overlay.url = f"{rel}/{r['url']}"  # folium would inline local files as base64; link them instead
        overlay.add_to(m)
    g = p.features["robust_cva"].to_crs(4326).drop(columns=["region"])
    g["area_ha"] = (g.area_m2 / 1e4).round(2)
    fields = ["area_ha", "confidence", "change_direction", "d_brightness", "in_disturbed_area", "zone"]
    folium.GeoJson(
        g[fields + ["geometry"]].round(4),
        name="Change polygons (robust CVA)",
        style_function=lambda f: {
            "color": DIRECTION_COLORS[f["properties"]["change_direction"]],
            "weight": 1.2,
            "fillOpacity": 0.35,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=fields, aliases=["area (ha)", "confidence", "direction", "d brightness", "in disturbed area", "stripe zone"]
        ),
    ).add_to(m)
    folium.GeoJson(
        p.aoi.to_crs(4326),
        name="AOI",
        style_function=lambda f: {"color": "white", "weight": 2, "dashArray": "6 4", "fillOpacity": 0},
    ).add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    m.save(out_html)
    return out_html
