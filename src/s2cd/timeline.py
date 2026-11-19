"""Monthly Sentinel-2 timeline (Jan 2023 onward) of the mine AOI: composites, KPIs, change and web assets.

Offline batch (needs the ``stac`` extra and network for months that are not cached yet)::

    uv run --all-extras python -m s2cd.timeline [--start 2023-01 --end 2026-10]

Per month:

1. **Composite.** All L2A items of the month with ``eo:cloud_cover < 70`` over the AOI are read onto the pipeline grid
   (EPSG:32735, 10 m; 20 m bands bilinear, SCL nearest). Reprocessed duplicates of one datatake (same tile, platform and
   sensing time, different processing baseline) are collapsed to the newest baseline. Pixels with SCL 0/1/3/8/9/10/11 (no data,
   saturated, cloud shadow, cloud, cirrus, snow) are masked; the composite is the per-pixel median of the clear observations,
   stored as uint16 DN (reflectance * 10000 + 1000, 0 = no data) plus a ``n_clear`` band in ``data/timeline/composites``.
   The -1000 BOA offset is applied per item from ``s2:processing_baseline`` (>= 04.00).
2. **Status.** ``clear_fraction`` = share of AOI pixels with >= 1 clear observation; ``ok`` if >= 0.9, else ``insufficient``
   (kept as a row, never interpolated).
3. **KPIs** of ok months: mean NDVI / NBR / MNDWI / BSI, ``bare_ha`` (BSI > 0.1, NDVI < 0.12, see ``Rules``), ``water_ha``
   (MNDWI > 0) and the mining headline ``footprint_ha``: the bare pixels closed and cleaned of patches below 1 ha.
4. **Change** between consecutive ok months with the main pipeline's chain (stripe zones, per-zone relative normalization,
   robust CVA on B02/B03/B04, ``vectorize.BinaryRule`` of ``configs/default.yaml``), plus a ``likely_cause`` per polygon.
5. **Outputs:** SpatiaLite ``data/timeline/timeline.sqlite``, ``timeline_months.csv``, WebP frames + ``manifest.json`` +
   ``timelapse.gif/.mp4`` in ``docs/web/monthly``, and ``docs/figures/08_timeline_kpis.png``.
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import rasterio
import rasterio.features
import typer
import yaml
from scipy import ndimage as ndi
from skimage import morphology

from . import db, methods, radiometry, vectorize
from .io import Grid, write_raster
from .stac_context import (
    COLLECTION,
    CauseRules,
    assign_cause,
    index_changes,
    norm_diff,
    open_catalog,
    rasterize_labels,
    zonal_fraction,
    zonal_mean,
)

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

log = logging.getLogger("s2cd.timeline")

BANDS = ("B02", "B03", "B04", "B08", "B11", "B12")
SCL_MASKED = (0, 1, 3, 8, 9, 10, 11)  # no data, saturated, shadow, cloud medium/high, cirrus, snow
MAX_CLOUD = 70  # eo:cloud_cover filter of the STAC search (%)
OK_CLEAR_FRACTION = 0.9
GRID_TIF = Path("data/processed/sentinel2_20230812_stack.tif")
DEFAULT_CONFIG = Path("configs/default.yaml")


@dataclass(frozen=True)
class Rules:
    """Thresholds of the monthly KPIs (reflectance indices of the median composite).

    Chosen on the composites themselves: BSI > 0 flags 6-7 k ha in the wet season but 14-22 k ha (55-80 % of the AOI) in the dry
    season, because senescent savanna and burn scars are also "bare" in BSI; requiring BSI > 0.1 and NDVI < 0.12 keeps the mine
    (pits, waste dumps, tailings beaches, roads: ~3.3-5 k ha) and cuts the seasonal swing from 3x to about 1.2x.
    """

    bare_bsi_min: float = 0.1
    bare_ndvi_max: float = 0.12
    footprint_close_px: int = 3  # closing radius that merges pit benches, haul roads and dumps
    footprint_min_ha: float = 1.0  # drop patches smaller than this (fields, roads, rock outcrops)
    min_obs: int = 1  # change detection only on pixels with at least this many clear observations in both months


@dataclass
class Composite:
    month: str
    dn: np.ndarray  # (6, H, W) uint16 DN with +1000 offset, 0 = no data
    n_clear: np.ndarray  # (H, W) uint16 clear observations per pixel
    n_items: int  # distinct acquisitions after removing reprocessed duplicates


# ---------------------------------------------------------------------------------------------------------
# Pure functions (unit-tested offline)
# ---------------------------------------------------------------------------------------------------------
def month_range(start: str, end: str) -> list[str]:
    """Inclusive list of 'YYYY-MM' strings."""
    (y0, m0), (y1, m1) = (tuple(map(int, s.split("-"))) for s in (start, end))
    return [f"{k // 12}-{k % 12 + 1:02d}" for k in range(y0 * 12 + m0 - 1, y1 * 12 + m1)]


def month_span(month: str) -> tuple[date, date]:
    """(first day, first day of the next month)."""
    y, m = map(int, month.split("-"))
    return date(y, m, 1), date(y + m // 12, m % 12 + 1, 1)


def boa_offset(baseline: str) -> float:
    """BOA_ADD_OFFSET of an item: -1000 DN from processing baseline 04.00, none before."""
    return 1000.0 if float(baseline) >= 4.0 else 0.0


def dedupe_items(items: list) -> list:
    """One item per datatake: reprocessed duplicates (same tile, platform and sensing time) -> newest processing baseline."""
    best: dict[tuple, object] = {}
    for it in items:
        p = it.properties
        key = (p.get("s2:mgrs_tile"), p.get("platform"), p["datetime"])
        if key not in best or float(p["s2:processing_baseline"]) > float(best[key].properties["s2:processing_baseline"]):
            best[key] = it
    return sorted(best.values(), key=lambda i: i.properties["datetime"])


def clear_mask(scl: np.ndarray, finite: np.ndarray) -> np.ndarray:
    """(T, H, W) clear observations: all bands finite and SCL not in ``SCL_MASKED`` (NaN SCL counts as masked)."""
    return finite & np.isfinite(scl) & ~np.isin(scl, SCL_MASKED)


def median_composite(refl: np.ndarray, clear: np.ndarray) -> np.ndarray:
    """Per-pixel median over time of the clear observations; NaN where there is none. refl is (T, H, W)."""
    stack = np.where(clear, refl, np.nan).astype(np.float32)
    out = np.full(refl.shape[1:], np.nan, np.float32)
    has = clear.any(axis=0)
    out[has] = np.nanmedian(stack[:, has], axis=0)
    return out


def refl_to_dn(refl: np.ndarray) -> np.ndarray:
    """Reflectance -> uint16 DN (x 10000 + 1000), 0 where NaN."""
    return np.where(np.isfinite(refl), np.clip(np.round(refl * 10000 + 1000), 1, 65535), 0).astype(np.uint16)


def dn_to_refl(dn: np.ndarray) -> np.ndarray:
    """uint16 DN stack -> reflectance float32, NaN where DN == 0."""
    r = (dn.astype(np.float32) - 1000) / 10000
    r[dn == 0] = np.nan
    return r


def clear_fraction(n_clear: np.ndarray, aoi: np.ndarray) -> float:
    """Share of AOI pixels with at least one clear observation."""
    return float((n_clear[aoi] >= 1).mean())


def month_status(fraction: float) -> str:
    return "ok" if fraction >= OK_CLEAR_FRACTION else "insufficient"


def spectral_indices(refl: np.ndarray) -> dict[str, np.ndarray]:
    """NDVI, NBR, MNDWI and BSI from a (6, H, W) reflectance stack in ``BANDS`` order (NaN where invalid)."""
    b02, b03, b04, b08, b11, b12 = refl
    return {
        "NDVI": norm_diff(b08, b04),
        "NBR": norm_diff(b08, b12),
        "MNDWI": norm_diff(b03, b11),
        "BSI": norm_diff(b11 + b04, b08 + b02),
    }


def bare_mask(idx: dict[str, np.ndarray], valid: np.ndarray, rules: Rules) -> np.ndarray:
    """Bare, unvegetated, non-water pixels (fixed thresholds, comparable between months)."""
    with np.errstate(invalid="ignore"):
        return valid & (idx["BSI"] > rules.bare_bsi_min) & (idx["NDVI"] < rules.bare_ndvi_max) & (idx["MNDWI"] < 0)


def footprint_mask(bare: np.ndarray, rules: Rules, pixel_area_m2: float = 100.0) -> np.ndarray:
    """Disturbed (mine) footprint: bare pixels closed with a ``footprint_close_px`` disk and cleaned of patches below
    ``footprint_min_ha``. Same idea as ``vectorize.disturbed_footprint`` but with fixed spectral thresholds instead of a
    per-image Otsu, which would drift with scene content and season.
    """
    m = ndi.binary_closing(bare, morphology.disk(rules.footprint_close_px))
    return morphology.remove_small_objects(m, max_size=max(int(rules.footprint_min_ha * 1e4 / pixel_area_m2) - 1, 0))


def month_kpis(refl: np.ndarray, valid: np.ndarray, rules: Rules, pixel_area_m2: float = 100.0) -> tuple[dict, np.ndarray]:
    """Mean indices and areas (ha) of an ok month, plus the footprint mask."""
    idx = spectral_indices(refl)
    ha = pixel_area_m2 / 1e4
    bare = bare_mask(idx, valid, rules)
    with np.errstate(invalid="ignore"):
        water = valid & (idx["MNDWI"] > 0)
    foot = footprint_mask(bare, rules, pixel_area_m2) & valid
    stats = {f"{k.lower()}_mean": float(np.nanmean(np.where(valid, v, np.nan))) for k, v in idx.items()}
    stats |= {"bare_ha": float(bare.sum() * ha), "water_ha": float(water.sum() * ha), "footprint_ha": float(foot.sum() * ha)}
    return stats, foot


def link_months(statuses: dict[str, str]) -> dict[str, str | None]:
    """prev_month of every ok month = the previous ok month; None for the first ok month and for insufficient months."""
    prev, out = None, {}
    for m, s in statuses.items():
        out[m] = prev if s == "ok" else None
        if s == "ok":
            prev = m
    return out


# ---------------------------------------------------------------------------------------------------------
# Composites (STAC)
# ---------------------------------------------------------------------------------------------------------
def read_grid(path: Path = GRID_TIF) -> Grid:
    with rasterio.open(path) as ds:
        return Grid(ds.crs, ds.transform, ds.width, ds.height)


def aoi_mask(aoi_path: Path, grid: Grid) -> np.ndarray:
    aoi = gpd.read_file(aoi_path).to_crs(grid.crs)
    return rasterio.features.geometry_mask(aoi.geometry, (grid.height, grid.width), grid.transform, invert=True)


def search_month(catalog, aoi_geom: dict, month: str) -> list:
    start, end = month_span(month)
    found = catalog.search(
        collections=[COLLECTION],
        intersects=aoi_geom,
        datetime=f"{start}T00:00:00Z/{end}T00:00:00Z",  # half-open in practice: nothing is sensed at 00:00:00
        query={"eo:cloud_cover": {"lt": MAX_CLOUD}},
    )
    return dedupe_items(list(found.item_collection()))


def build_composite(items: list, grid: Grid, month: str, retries: int = 3) -> Composite:
    """Read the items onto ``grid`` with odc-stac and reduce them to the clear-observation median composite."""
    h, w = grid.height, grid.width
    if not items:
        return Composite(month, np.zeros((len(BANDS), h, w), np.uint16), np.zeros((h, w), np.uint16), 0)
    from odc.geo.geobox import GeoBox
    from odc.stac import load

    gb = GeoBox((h, w), grid.transform, grid.crs)
    for attempt in range(1, retries + 1):
        try:
            ds = load(
                items, bands=[*BANDS, "SCL"], geobox=gb, resampling={"SCL": "nearest", "*": "bilinear"}, groupby="id", chunks={}
            )
            ds = ds.compute()
            break
        except Exception as e:  # network hiccups on COG reads; the signed URLs are valid for the whole month
            if attempt == retries:
                raise
            log.warning("%s: load failed (%s), retry %d", month, e, attempt)
            time.sleep(5 * attempt)
    # groupby="id" returns one slice per item, in the order of ``items``.
    offsets = np.array([boa_offset(it.properties["s2:processing_baseline"]) for it in items], np.float32)[:, None, None]
    refl = {b: (ds[b].values - offsets) / 10000 for b in BANDS}  # NaN where the item has no data
    finite = np.all([np.isfinite(ds[b].values) & (ds[b].values != 0) for b in BANDS], axis=0)  # DN 0 = no data
    clear = clear_mask(ds["SCL"].values, finite)
    dn = np.stack([refl_to_dn(median_composite(refl[b], clear)) for b in BANDS])
    return Composite(month, dn, clear.sum(axis=0).astype(np.uint16), len(items))


def composite_path(root: Path, month: str) -> Path:
    return Path(root) / f"{month}.tif"


def save_composite(root: Path, c: Composite, grid: Grid, item_ids: list[str]) -> None:
    write_raster(
        composite_path(root, c.month),
        np.concatenate([c.dn, c.n_clear[None]]),
        grid,
        0,
        [*BANDS, "n_clear"],
        {"MONTH": c.month, "N_ITEMS": c.n_items, "ITEM_IDS": json.dumps(item_ids), "NOTE": "DN = reflectance * 10000 + 1000"},
        overviews=False,
    )


def load_composite(root: Path, month: str) -> Composite | None:
    path = composite_path(root, month)
    if not path.exists():
        return None
    with rasterio.open(path) as ds:
        a = ds.read()
        return Composite(month, a[: len(BANDS)], a[len(BANDS)], int(ds.tags()["N_ITEMS"]))


def get_composite(catalog_factory, root: Path, month: str, grid: Grid, aoi_geom: dict, refresh: bool = False) -> Composite:
    """Cached composite, or search + download + build it (``catalog_factory`` is only called on a cache miss)."""
    c = None if refresh else load_composite(root, month)
    if c is None:
        items = search_month(catalog_factory(), aoi_geom, month)
        c = build_composite(items, grid, month)
        save_composite(root, c, grid, [i.id for i in items])
    return c


# ---------------------------------------------------------------------------------------------------------
# Change between consecutive ok months
# ---------------------------------------------------------------------------------------------------------
@dataclass
class MonthData:
    """An ok month held in memory while it is the 'previous' month of the next one."""

    month: str
    refl: np.ndarray  # (6, H, W) reflectance, NaN where no clear observation
    n_clear: np.ndarray
    valid: np.ndarray  # AOI pixels with >= 1 clear observation
    idx: dict[str, np.ndarray]
    footprint: np.ndarray


@dataclass
class ChangeResult:
    d2: np.ndarray
    valid: np.ndarray  # pixels where the change statistic is defined (both months observed, inside the AOI)
    binary: np.ndarray
    features: gpd.GeoDataFrame  # db.FEATURE_COLUMNS + likely_cause (empty if no polygon survives)
    summary: dict


def likely_cause_table(
    labels: np.ndarray, n: int, a: MonthData, b: MonthData, valid: np.ndarray, in_disturbed: pd.Series
) -> pd.Series:
    """Reuse the stac_context rule set (``assign_cause``) with composite-based inputs.

    Composites contain no cloud pixels, so the SCL-based shares of the two-date analysis are replaced by proxies: the
    ``scl_artifact_*`` input is the polygon share of single-observation pixels (one look cannot reject residual cloud shadow
    or haze by the median), ``scl_water_*`` the share of pixels with MNDWI > 0.
    """
    cols = {}
    for w, m in (("before", a), ("after", b)):
        cols[f"mndwi_{w}"] = zonal_mean(labels, n, np.where(valid, m.idx["MNDWI"], np.nan))
        cols[f"scl_water_{w}"] = zonal_fraction(labels, n, np.nan_to_num(m.idx["MNDWI"], nan=-1) > 0)
        cols[f"scl_artifact_{w}"] = zonal_fraction(labels, n, m.n_clear < 2)
    chg = index_changes(a.idx, b.idx)
    cols |= {
        k: zonal_mean(labels, n, np.where(valid, chg[v], np.nan))
        for k, v in (("d_ndvi", "dNDVI"), ("d_nbr", "dNBR"), ("d_mndwi", "dMNDWI"))
    }
    return assign_cause(pd.DataFrame(cols), in_disturbed.reset_index(drop=True), CauseRules())


def detect_change(a: MonthData, b: MonthData, grid: Grid, rule: vectorize.BinaryRule, rules: Rules) -> ChangeResult:
    """Main-pipeline chain on two composites: stripe zones, relative normalization, robust CVA, binary mask, polygons."""
    valid = a.valid & b.valid & (a.n_clear >= rules.min_obs) & (b.n_clear >= rules.min_obs)
    r1, r2 = (np.where(valid, m.refl[:3], np.nan).astype(np.float32) for m in (a, b))
    names = BANDS[:3]
    stripes = radiometry.detect_stripe_zones(r1, r2, valid)
    seam = radiometry.seam_distance(valid.shape, stripes.angle_deg, stripes.breakpoints)
    r2n, pif, _ = radiometry.relative_normalization(r1, r2, stripes.zones, names)
    cva = methods.robust_cva(r1, r2n, stripes.zones, pif)
    binary = vectorize.binary_from_d2(cva.d2, valid, seam, len(names), rule)
    foot = ndi.binary_dilation(a.footprint | b.footprint, morphology.disk(10)) & valid
    noise = float(np.sqrt(np.mean([np.trace(np.array(c)) / len(names) for c in cva.noise_cov.values()])))
    low, high = rule.thresholds(len(names))
    layers = {
        "d2": cva.d2,
        "p_change": cva.p_change,
        "delta": cva.delta,
        "bright_before": r1.mean(axis=0),
        "bright_after": r2n.mean(axis=0),
        "zone": np.clip(stripes.zones, 0, None),
        "disturbed": foot,
    }
    gdf = vectorize.polygonize(binary, grid, layers, noise, high, low)
    ha = grid.pixel_area_m2 / 1e4
    summary = {
        "changed_ha": float(binary.sum() * ha),
        "changed_ha_in_footprint": float((binary & foot).sum() * ha),
        "n_features": len(gdf),
        "n_zones": len(stripes.zone_offsets),
        "stripe_explained_variance": round(stripes.explained_variance, 3),
        "pif_fraction": round(float(pif[valid].mean()), 3),
        "noise_sigma_reflectance": round(noise, 5),
        "valid_fraction": round(float(valid.mean()), 4),
    }
    if len(gdf):
        labels = rasterize_labels(gdf, grid)
        gdf["likely_cause"] = likely_cause_table(labels, len(gdf), a, b, valid, gdf.in_disturbed_area.astype(bool)).to_numpy()
        summary["area_ha_by_cause"] = {k: round(float(v) / 1e4, 2) for k, v in gdf.groupby("likely_cause").area_m2.sum().items()}
    return ChangeResult(cva.d2, valid, binary, gdf, summary)


# ---------------------------------------------------------------------------------------------------------
# Web assets and figure
# ---------------------------------------------------------------------------------------------------------
MAX_FRAME_KB = 600
FRAME_WIDTH = 1000  # fallback width of a WebP frame that exceeds MAX_FRAME_KB at full resolution


def save_frame(arr: np.ndarray, grid: Grid, path: Path, max_kb: int = MAX_FRAME_KB) -> list[list[float]]:
    """Reproject an RGBA uint8 (4, H, W) to EPSG:3857 (``viz._warp_png``) and save a WebP below ``max_kb``.

    Tries full resolution, then ``FRAME_WIDTH`` px wide, each at decreasing quality. Returns the Leaflet bounds [[S, W], [N, E]].
    """
    from PIL import Image

    from .viz import _warp_png

    with tempfile.TemporaryDirectory() as tmp:
        bounds = _warp_png(arr, grid, Path(tmp) / "frame.png")  # lossless; WebP encoding is done here
        im = Image.open(Path(tmp) / "frame.png").convert("RGBA")
        im.load()
    for width in (im.width, FRAME_WIDTH):
        frame = im if width == im.width else im.resize((width, round(im.height * width / im.width)), Image.Resampling.LANCZOS)
        for quality in (88, 78, 68, 58):
            frame.save(path, quality=quality, alpha_quality=60, method=6)
            if path.stat().st_size <= max_kb * 1000:
                return bounds
    log.warning("%s is %d KB (> %d KB)", path.name, path.stat().st_size // 1000, max_kb)
    return bounds


def rgb_rgba(refl: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """True colour with one fixed stretch for every month; alpha 0 outside ``valid``."""
    from .viz import rgb

    img = np.moveaxis((rgb(refl[:3]) * 255).astype(np.uint8), -1, 0)
    return np.concatenate([img, (valid * 255).astype(np.uint8)[None]])


def change_rgba(d2: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Change intensity (log10 d2, inferno, alpha ~ intensity) exactly as ``viz.export_web``."""
    intensity = np.nan_to_num(np.clip(np.log10(np.clip(d2, 1e-3, None)) / 3, 0, 1))
    heat = (matplotlib.colormaps["inferno"](intensity) * 255).astype(np.uint8)
    heat[..., 3] = np.where(valid, (intensity * 230).astype(np.uint8), 0)
    return np.moveaxis(heat, -1, 0)


def write_timelapse(frames: list[tuple[str, Path]], out_dir: Path, width: int = 800, max_mb: float = 8.0) -> None:
    """GIF (< ``max_mb``) and H.264 MP4 of the ok months; the month is burned into each frame."""
    import imageio.v2 as imageio
    from PIL import Image, ImageDraw, ImageFont

    def render(w: int) -> list[Image.Image]:
        out = []
        for month, path in frames:
            im = Image.open(path).convert("RGBA")
            bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
            im = Image.alpha_composite(bg, im).convert("RGB")
            im = im.resize((w // 2 * 2, round(im.height * w / im.width) // 2 * 2), Image.LANCZOS)
            d = ImageDraw.Draw(im)
            font = ImageFont.load_default(size=max(16, w // 28))
            d.rectangle((8, 8, 8 + font.size * 4.2, 8 + font.size * 1.5), fill=(0, 0, 0))
            d.text((14, 12), month, fill=(255, 255, 255), font=font)
            out.append(im)
        return out

    gif = out_dir / "timelapse.gif"
    for colors, w in [(64, round(width * f)) for f in (1, 0.92, 0.85, 0.78, 0.72, 0.66, 0.6)]:
        ims = [im.quantize(colors, dither=Image.Dither.NONE) for im in render(w)]
        ims[0].save(gif, save_all=True, append_images=ims[1:], duration=500, loop=0, optimize=True)
        size_mb = gif.stat().st_size / 1024**2
        log.info("GIF %d colours, %d px: %.1f MB", colors, w, size_mb)
        if size_mb < max_mb:
            break
    mp4 = out_dir / "timelapse.mp4"
    with imageio.get_writer(
        mp4, fps=2, codec="libx264", pixelformat="yuv420p", macro_block_size=2, ffmpeg_params=["-crf", "27"]
    ) as wr:
        for im in render(width):
            wr.append_data(np.asarray(im))
    log.info("MP4 %.1f MB", mp4.stat().st_size / 1024**2)


def plot_kpis(path: Path, df: pd.DataFrame) -> None:
    """Footprint/bare area, water area, NDVI/NBR and changed area per month; insufficient months are shaded."""
    t = pd.to_datetime(df.month + "-01")
    ok = df.status == "ok"
    fig, axs = plt.subplots(4, 1, figsize=(12, 10), sharex=True, constrained_layout=True)
    for ax in axs:
        for ti in t[~ok]:
            ax.axvspan(ti, ti + pd.offsets.MonthBegin(1), color="0.85", lw=0)
        ax.grid(alpha=0.3)
    line = lambda ax, col, **kw: ax.plot(t[ok], df.loc[ok, col], marker="o", ms=3, **kw)  # noqa: E731
    line(axs[0], "footprint_ha", color="C3", label="disturbed footprint")
    line(axs[0], "bare_ha", color="C1", ls="--", label="bare (BSI > 0.1, NDVI < 0.12)")
    axs[0].set_ylabel("area (ha)")
    axs[0].legend(loc="upper left", ncol=2)
    axs[0].set_title("Monthly Sentinel-2 composites: mine footprint / bare area (grey = insufficient clear coverage)")
    line(axs[1], "water_ha", color="C0", label="water (MNDWI > 0)")
    axs[1].set_ylabel("water area (ha)")
    axs[1].legend(loc="upper left")
    line(axs[2], "ndvi_mean", color="C2", label="NDVI")
    line(axs[2], "nbr_mean", color="C4", label="NBR")
    axs[2].set_ylabel("AOI mean index")
    axs[2].legend(loc="upper left", ncol=2)
    ch = df[ok & df.changed_ha.notna()]
    w = 20
    axs[3].bar(pd.to_datetime(ch.month + "-01"), ch.changed_ha, width=w, color="C5", label="changed vs previous ok month")
    axs[3].bar(
        pd.to_datetime(ch.month + "-01"), ch.changed_ha_in_footprint, width=w, color="C3", label="of which inside footprint"
    )
    axs[3].set_ylabel("changed area (ha)")
    axs[3].legend(loc="upper left", ncol=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------------------------------------
MONTH_COLUMNS = [
    "month",
    "status",
    "n_items",
    "clear_fraction",
    "ndvi_mean",
    "nbr_mean",
    "mndwi_mean",
    "bsi_mean",
    "bare_ha",
    "water_ha",
    "footprint_ha",
    "changed_ha",
    "changed_ha_in_footprint",
    "prev_month",
]
MONTHS_SQL = """CREATE TABLE timeline_months (
    month TEXT PRIMARY KEY, status TEXT NOT NULL, n_items INTEGER, clear_fraction REAL,
    ndvi_mean REAL, nbr_mean REAL, mndwi_mean REAL, bsi_mean REAL, bare_ha REAL, water_ha REAL, footprint_ha REAL,
    changed_ha REAL, changed_ha_in_footprint REAL, prev_month TEXT)"""


def write_timeline_db(path: Path, features: gpd.GeoDataFrame, runs: pd.DataFrame, months: pd.DataFrame) -> None:
    """SpatiaLite file with change_runs + change_features (``db.write_sqlite``) and the plain table timeline_months."""
    import sqlite3
    from contextlib import closing

    # Pixel-staircase outlines simplified by one pixel (10 m): ~4x fewer vertices, total area -0.3 %.
    # Areas and attributes were computed before this, from the full-resolution masks.
    features = features.set_geometry(features.geometry.simplify(10, preserve_topology=True).apply(db._to_multi))
    db.write_sqlite(path, features, runs)
    rows = months[MONTH_COLUMNS].astype(object).where(months[MONTH_COLUMNS].notna(), None).values.tolist()
    with closing(sqlite3.connect(path)) as con, con:
        con.execute(MONTHS_SQL)
        con.executemany(f"INSERT INTO timeline_months VALUES ({','.join('?' * len(MONTH_COLUMNS))})", rows)


# ---------------------------------------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------------------------------------
def _round(v, nd: int):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def manifest_entry(row: pd.Series, rgb_path: str | None, change_path: str | None) -> dict:
    stat_keys = MONTH_COLUMNS[4:13]
    return {
        "month": row.month,
        "status": row.status,
        "n_items": int(row.n_items),
        "clear_fraction": round(float(row.clear_fraction), 4),
        "rgb": rgb_path,
        "change": change_path,
        "prev_month": None if pd.isna(row.prev_month) else row.prev_month,
        "stats": {k: _round(row[k], 4 if k.endswith("_mean") else 2) for k in stat_keys},
    }


def run_timeline(
    start: str = "2023-01",
    end: str = "2026-10",
    aoi: Path = Path("inputs/aoi.geojson"),
    config: Path = DEFAULT_CONFIG,
    grid_tif: Path = GRID_TIF,
    composites_dir: Path = Path("data/timeline/composites"),
    sqlite_path: Path = Path("data/timeline/timeline.sqlite"),
    csv_path: Path = Path("data/timeline/timeline_months.csv"),
    web_dir: Path = Path("docs/web/monthly"),
    figure_path: Path = Path("docs/figures/08_timeline_kpis.png"),
    rules: Rules = Rules(),
    refresh: bool = False,
    assets: bool = True,
) -> pd.DataFrame:
    """Build the monthly timeline for ``start``..``end`` (inclusive, 'YYYY-MM') and write every output; returns timeline_months.

    Composites are cached per month in ``composites_dir`` (network only for missing months, or all with ``refresh``).
    """
    t0 = time.perf_counter()
    grid = read_grid(grid_tif)
    inside = aoi_mask(aoi, grid)
    aoi_geom = gpd.read_file(aoi).geometry.union_all().__geo_interface__
    rule = vectorize.BinaryRule(**(yaml.safe_load(Path(config).read_text()) or {}).get("rule", {}))
    web_dir = Path(web_dir)
    web_dir.mkdir(parents=True, exist_ok=True)

    prev: MonthData | None = None
    rows, tables, runs, entries, frames, bounds = [], [], [], [], [], None
    for month in month_range(start, end):
        c = get_composite(open_catalog, composites_dir, month, grid, aoi_geom, refresh)
        valid = (c.n_clear >= 1) & inside
        cf = clear_fraction(c.n_clear, inside)
        row = dict.fromkeys(MONTH_COLUMNS) | {
            "month": month,
            "status": month_status(cf),
            "n_items": c.n_items,
            "clear_fraction": cf,
        }
        rgb_path = change_path = None
        if row["status"] == "ok":
            refl = dn_to_refl(c.dn)
            stats, foot = month_kpis(refl, valid, rules, grid.pixel_area_m2)
            row |= stats
            cur = MonthData(month, refl, c.n_clear, valid, spectral_indices(refl), foot)
            if assets:
                rgb_path = f"monthly/{month}_rgb.webp"
                bounds = save_frame(rgb_rgba(refl, valid), grid, web_dir / f"{month}_rgb.webp")
                frames.append((month, web_dir / f"{month}_rgb.webp"))
            if prev is not None:
                res = detect_change(prev, cur, grid, rule, rules)
                row |= {k: res.summary[k] for k in ("changed_ha", "changed_ha_in_footprint")} | {"prev_month": prev.month}
                run_id = f"monthly_{prev.month.replace('-', '')}_{month.replace('-', '')}"
                if len(res.features):
                    d0, d1 = month_span(prev.month)[0], month_span(month)[0]
                    t = db.as_feature_table(res.features, run_id, "robust_cva_monthly", d0, d1)
                    tables.append(t[[*db.FEATURE_COLUMNS, "likely_cause", "geometry"]])
                runs.append(
                    {
                        "run_id": run_id,
                        "method": "robust_cva_monthly",
                        "date_before": str(month_span(prev.month)[0]),
                        "date_after": str(month_span(month)[0]),
                        "params": {"rule": asdict(rule), "rules": asdict(rules), "bands": list(BANDS[:3])},
                        "summary": res.summary,
                    }
                )
                if assets:
                    change_path = f"monthly/{month}_change.webp"
                    save_frame(change_rgba(res.d2, res.valid), grid, web_dir / f"{month}_change.webp")
            prev = cur
        rows.append(row)
        entries.append((rgb_path, change_path))
        log.info(
            "%s %-12s items=%2d clear=%.3f footprint=%s changed=%s",
            month,
            row["status"],
            c.n_items,
            cf,
            "-" if row["footprint_ha"] is None else f"{row['footprint_ha']:.0f}ha",
            "-" if row["changed_ha"] is None else f"{row['changed_ha']:.1f}ha",
        )

    df = pd.DataFrame(rows, columns=MONTH_COLUMNS)
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)
    feats = (
        gpd.GeoDataFrame(pd.concat(tables, ignore_index=True), crs=grid.crs)
        if tables
        else gpd.GeoDataFrame(geometry=[], crs=grid.crs)
    )
    write_timeline_db(Path(sqlite_path), feats, db.runs_table(runs), df)
    plot_kpis(Path(figure_path), df)
    if assets:
        manifest = {
            "bounds": bounds,
            "months": [manifest_entry(r, *e) for (_, r), e in zip(df.iterrows(), entries, strict=True)],
        }
        (web_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
        write_timelapse(frames, web_dir)
    log.info("Timeline done in %.0fs: %s", time.perf_counter() - t0, df.status.value_counts().to_dict())
    return df


app = typer.Typer(add_completion=False, help=__doc__.split("\n")[0])


@app.command()
def main(
    start: str = typer.Option("2023-01", help="First month, YYYY-MM"),
    end: str = typer.Option("2026-10", help="Last month, YYYY-MM"),
    refresh: bool = typer.Option(False, help="Rebuild composites even if cached"),
    no_assets: bool = typer.Option(False, help="Skip WebP frames, manifest, GIF and MP4"),
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run_timeline(start, end, refresh=refresh, assets=not no_assets)


if __name__ == "__main__":
    app()
