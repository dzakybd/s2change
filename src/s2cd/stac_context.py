"""Optional enrichment from Microsoft Planetary Computer (STAC): B08/B11/B12/SCL/AOT for both dates.

Needs the ``stac`` extra (``uv run --extra stac s2cd enrich``) and network access; the core pipeline never imports this
module. What it adds to the three provided bands:

* which STAC items the provided B02/B03/B04 are made of: they are matched pixel-exactly against all L2A items of the
  preceding weeks (greedy cover), which shows whether a "date" is one acquisition or a mosaic of several,
* STAC metadata of those source items (baseline, orbit, platform, cloud cover, datatakes); all extra bands are read from
  the same item as each pixel,
* NDVI, NBR, MNDWI per date and their differences, written as GeoTIFFs,
* per-polygon zonal means and a rule-based ``likely_cause`` (table ``change_context`` in the SQLite database),
* AOT per radiometric stripe zone and date, to test the haze hypothesis,
* an AOI-mean timeline (cloud cover, NBR, brightness, AOT) of every L2A acquisition June to October 2023.

Sign convention: ``d_nbr = NBR_before - NBR_after`` (USGS dNBR, positive = burn/vegetation loss);
``d_ndvi`` and ``d_mndwi`` are after minus before (negative ``d_ndvi`` = greenness loss).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pyogrio  # noqa: E402
import rasterio  # noqa: E402
import rasterio.features  # noqa: E402
from affine import Affine  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402
from rasterio.vrt import WarpedVRT  # noqa: E402

from .io import Grid, parse_date, write_raster  # noqa: E402
from .radiometry import L2A_OFFSET, QUANTIFICATION  # noqa: E402

log = logging.getLogger("s2cd.stac")

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-2-l2a"
AOT_SCALE = 1 / 1000  # L2A AOT is stored as DN = AOT * 1000
SCL_SHADOW, SCL_WATER, SCL_CLOUD_MED, SCL_CLOUD_HIGH, SCL_CIRRUS = 3, 6, 8, 9, 10
SCL_ARTIFACT = (SCL_SHADOW, SCL_CLOUD_MED, SCL_CLOUD_HIGH, SCL_CIRRUS)  # classes SCL flags as cloud/shadow/cirrus
SCL_NOT_CLEAR = (0, 1, *SCL_ARTIFACT, 11)  # + nodata, saturated, snow: excluded from timeline means
CAUSES = [
    "cloud_shadow_haze_artifact",
    "water_change",
    "burn_scar",
    "vegetation_loss",
    "active_mining_or_tailings",
    "unexplained",
]
CAUSE_COLORS = dict(zip(CAUSES, ["#999999", "#0072B2", "#D55E00", "#009E73", "#E69F00", "#CC79A7"], strict=True))


@dataclass(frozen=True)
class CauseRules:
    """Thresholds of the likely_cause rule set; the first matching rule below wins."""

    artifact_frac: float = 0.3  # 1. SCL cloud/shadow/cirrus share of the polygon in either date
    water_frac: float = 0.3  # 2. SCL water share in either date ...
    d_mndwi: float = 0.2  #    ... or |dMNDWI| above this ...
    d_mndwi_sign: float = 0.1  #    ... or MNDWI changes sign with |dMNDWI| above this
    d_nbr: float = 0.1  # 3. dNBR (before - after) above this, polygon outside the disturbed (mine) footprint
    d_ndvi: float = -0.1  # 4. dNDVI (after - before) below this
    # 5. otherwise inside the disturbed footprint -> active mining / tailings; 6. otherwise unexplained


# ---------------------------------------------------------------------------------------------------------
# Pure functions (unit-tested offline)
# ---------------------------------------------------------------------------------------------------------
def reflectance(dn: np.ndarray) -> np.ndarray:
    """L2A DN (+1000 BOA offset, 0 = nodata) -> surface reflectance, NaN where nodata."""
    refl = (dn.astype(np.float32) - L2A_OFFSET) / QUANTIFICATION
    refl[dn == 0] = np.nan
    return refl


def norm_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(a - b) / (a + b), NaN where the sum is ~0 or an input is NaN."""
    s = a + b
    return np.where(np.abs(s) > 1e-4, (a - b) / np.where(s == 0, 1, s), np.nan).astype(np.float32)


def spectral_indices(refl: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {
        "NDVI": norm_diff(refl["B08"], refl["B04"]),
        "NBR": norm_diff(refl["B08"], refl["B12"]),
        "MNDWI": norm_diff(refl["B03"], refl["B11"]),
    }


def index_changes(before: dict[str, np.ndarray], after: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {
        "dNDVI": after["NDVI"] - before["NDVI"],
        "dNBR": before["NBR"] - after["NBR"],
        "dMNDWI": after["MNDWI"] - before["MNDWI"],
    }


def compare_bands(provided: np.ndarray, pc: np.ndarray) -> dict:
    """How well a provided DN band reproduces the Planetary Computer band (same grid, both uint16, 0 = nodata)."""
    m = (provided != 0) & (pc != 0)
    p, q = provided[m].astype(np.float64), pc[m].astype(np.float64)
    diff = p - q
    return {
        "n_pixels": int(m.sum()),
        "correlation": round(float(np.corrcoef(p, q)[0, 1]), 5) if p.std() > 0 and q.std() > 0 else None,
        "median_diff_dn": float(np.median(diff)),  # provided - PC
        "mean_abs_diff_dn": round(float(np.abs(diff).mean()), 3),
        "exact_match_fraction": round(float((diff == 0).mean()), 4),
    }


def date_coverage(records: list[dict]) -> dict:
    """How many distinct datatakes/tiles cover the AOI; same datatake in several baselines = reprocessed duplicates."""
    takes = {(r["platform"], r["relative_orbit"], r["datetime"]) for r in records}
    return {
        "n_items": len(records),
        "n_datatakes": len(takes),
        "mgrs_tiles": sorted({r["mgrs_tile"] for r in records}),
        "processing_baselines": sorted({r["processing_baseline"] for r in records}),
        "reprocessed_duplicates": len(records) - len(takes),
        "more_than_one_datatake": len(takes) > 1,
    }


def greedy_cover(
    matches: list[np.ndarray], valid: np.ndarray, min_gain: float = 0.001, max_sources: int = 8
) -> tuple[list[int], np.ndarray]:
    """Explain the valid provided pixels with as few candidate rasters as possible.

    ``matches[k]`` marks the pixels where candidate k reproduces the provided DN exactly. Greedy set cover: repeatedly take the
    candidate explaining the most still-unexplained pixels. Returns (picked candidate indices, per-pixel position in that list,
    -1 = unexplained).
    """
    src = np.full(valid.shape, -1, np.int8)
    todo, picked = valid.copy(), []
    for _ in range(max_sources):
        gains = [int((m & todo).sum()) for m in matches]
        k = int(np.argmax(gains))
        if gains[k] < min_gain * valid.sum():
            break
        src[matches[k] & todo] = len(picked)
        todo &= ~matches[k]
        picked.append(k)
    return picked, src


def compose(layers: list[np.ndarray], src: np.ndarray, fill: float = 0) -> np.ndarray:
    """Mosaic: pixel (i, j) is taken from ``layers[src[i, j]]``; ``fill`` where src < 0."""
    out = np.full(src.shape, fill, layers[0].dtype)
    for k, a in enumerate(layers):
        out = np.where(src == k, a, out)
    return out


def provenance_by_zone(src: np.ndarray, zones: np.ndarray, names: list[str]) -> dict:
    """Share of each stripe zone's valid pixels that comes from each source scene."""
    out = {}
    for z in range(int(zones.max()) + 1):
        m = zones == z
        cnt = np.bincount(src[m & (src >= 0)], minlength=len(names))
        out[str(z)] = {n: round(float(c) / max(int(m.sum()), 1), 4) for n, c in zip(names, cnt, strict=True) if c}
    return out


def source_mix(
    labels: np.ndarray, n: int, src_before: np.ndarray, src_after: np.ndarray, names: tuple[list[str], list[str]]
) -> pd.DataFrame:
    """Per polygon: dominant source scene on each date and the share of pixels outside the dominant (before, after) pair."""
    m = labels > 0
    combo = (src_before[m].astype(int) + 1) * 16 + (src_after[m].astype(int) + 1)
    counts = pd.crosstab(labels[m], combo).reindex(range(1, n + 1), fill_value=0)
    top = counts.idxmax(axis=1).to_numpy()
    label = lambda c, nm: nm[c - 1] if c > 0 else "none"  # noqa: E731
    return pd.DataFrame(
        {
            "source_before": [label(c // 16, names[0]) for c in top],
            "source_after": [label(c % 16, names[1]) for c in top],
            "seam_mix": (1 - counts.max(axis=1) / counts.sum(axis=1).clip(lower=1)).to_numpy(),
        }
    )


def zonal_mean(labels: np.ndarray, n: int, values: np.ndarray) -> np.ndarray:
    """Mean of ``values`` per label 1..n over finite pixels; NaN for labels without finite pixels."""
    m = (labels > 0) & np.isfinite(values)
    cnt = np.bincount(labels[m], minlength=n + 1)[1:]
    tot = np.bincount(labels[m], values[m].astype(np.float64), minlength=n + 1)[1:]
    return np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan)


def zonal_fraction(labels: np.ndarray, n: int, mask: np.ndarray) -> np.ndarray:
    return zonal_mean(labels, n, mask.astype(np.float32))


def assign_cause(ctx: pd.DataFrame, in_disturbed: pd.Series, rules: CauseRules = CauseRules()) -> pd.Series:
    """Transparent first-match rule set (see CauseRules). NaN statistics never satisfy a condition."""
    artifact = (ctx.scl_artifact_before > rules.artifact_frac) | (ctx.scl_artifact_after > rules.artifact_frac)
    sign_change = (
        ctx.mndwi_before.notna()
        & ctx.mndwi_after.notna()
        & ((ctx.mndwi_before > 0) != (ctx.mndwi_after > 0))
        & (ctx.d_mndwi.abs() > rules.d_mndwi_sign)
    )
    water = (
        (ctx.scl_water_before > rules.water_frac)
        | (ctx.scl_water_after > rules.water_frac)
        | (ctx.d_mndwi.abs() > rules.d_mndwi)
        | sign_change
    )
    burn = (ctx.d_nbr > rules.d_nbr) & ~in_disturbed
    veg = ctx.d_ndvi < rules.d_ndvi
    conds = [artifact, water, burn, veg, in_disturbed]
    return pd.Series(np.select([c.to_numpy() for c in conds], CAUSES[:-1], CAUSES[-1]), index=ctx.index, name="likely_cause")


def eta2(values: np.ndarray, groups: np.ndarray) -> float:
    """Share of the variance of ``values`` explained by the group means (0 = no zone dependence, 1 = fully zone-driven)."""
    k = groups.max() + 1
    cnt = np.bincount(groups, minlength=k)
    mean = np.bincount(groups, values, minlength=k) / np.maximum(cnt, 1)
    total = ((values - values.mean()) ** 2).sum()
    return float((cnt * (mean - values.mean()) ** 2).sum() / total) if total > 0 else 0.0


# ---------------------------------------------------------------------------------------------------------
# STAC access
# ---------------------------------------------------------------------------------------------------------
def open_catalog():
    try:
        import planetary_computer
        import pystac_client
    except ImportError as e:
        raise ImportError("Enrichment needs the 'stac' extra: uv run --extra stac s2cd enrich") from e
    return pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)


def search(catalog, aoi_geom: dict, start: date, end: date) -> list:
    """All L2A items intersecting the AOI between two dates (inclusive), newest processing baseline first per datatake."""
    found = catalog.search(collections=[COLLECTION], intersects=aoi_geom, datetime=f"{start}/{end}").item_collection()
    return sorted(found, key=lambda i: (i.datetime, i.properties["s2:processing_baseline"]), reverse=True)


def item_record(item) -> dict:
    p = item.properties
    return {
        "id": item.id,
        "datetime": p["datetime"],
        "platform": p.get("platform"),
        "mgrs_tile": p.get("s2:mgrs_tile"),
        "relative_orbit": p.get("sat:relative_orbit"),
        "orbit_state": p.get("sat:orbit_state"),
        "datatake_id": p.get("s2:datatake_id"),
        "processing_baseline": p.get("s2:processing_baseline"),
        "generation_time": p.get("s2:generation_time"),
        "cloud_cover": p.get("eo:cloud_cover"),
        "nodata_pixel_percentage": p.get("s2:nodata_pixel_percentage"),
        "mean_solar_zenith": p.get("s2:mean_solar_zenith"),
        "mean_solar_azimuth": p.get("s2:mean_solar_azimuth"),
        "view_incidence_angle": p.get("view:incidence_angle"),
        "view_azimuth": p.get("view:azimuth"),
    }


def read_on_grid(href: str, grid: Grid, resampling: Resampling, scale: int = 1) -> np.ndarray:
    """Read one COG band reprojected/resampled onto ``grid`` (optionally coarsened by ``scale``); 0 = nodata."""
    t = grid.transform * Affine.scale(scale)
    w, h = -(-grid.width // scale), -(-grid.height // scale)
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MAX_RETRY="4")
    with (
        rasterio.Env(**env),
        rasterio.open(href) as src,
        WarpedVRT(src, crs=grid.crs, transform=t, width=w, height=h, resampling=resampling, src_nodata=0, nodata=0) as vrt,
    ):
        return vrt.read(1)


class Fetcher:
    """Reads item assets onto the grid and caches every window as .npy under ``cache`` (re-runs need no downloads)."""

    def __init__(self, grid: Grid, cache: Path, refresh: bool = False):
        self.grid, self.cache, self.refresh = grid, Path(cache), refresh
        self.cache.mkdir(parents=True, exist_ok=True)
        t = grid.transform
        self.tag = f"{int(t.c)}_{int(t.f)}_{grid.width}x{grid.height}"

    def get(self, item, asset: str, resampling: Resampling | None = None, scale: int = 1) -> np.ndarray:
        path = self.cache / f"{item.id}_{asset}_s{scale}_{self.tag}.npy"
        if path.exists() and not self.refresh:
            return np.load(path)
        resampling = resampling or (Resampling.nearest if asset == "SCL" else Resampling.bilinear)
        arr = read_on_grid(item.assets[asset].href, self.grid, resampling, scale)
        np.save(path, arr)
        return arr


# ---------------------------------------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------------------------------------
def source_label(r: dict) -> str:
    return f"{r['datetime'][:10]} S2{r['platform'][-1]} R{r['relative_orbit']:03d} N{r['processing_baseline']}"


def find_sources(items: list, fetch: Fetcher, provided_b04: np.ndarray) -> tuple[list, np.ndarray]:
    """Which Planetary Computer items does the provided B04 consist of? (exact DN equality, greedy cover)"""
    valid = provided_b04 != 0
    with ThreadPoolExecutor(4) as ex:
        matches = list(ex.map(lambda it: (provided_b04 == fetch.get(it, "B04")) & valid, items))
    picked, src = greedy_cover(matches, valid)
    return [items[k] for k in picked], src


def aot_by_zone(
    per_date: dict[str, tuple[np.ndarray, np.ndarray, list[str]]], zones: np.ndarray, blue_offset: list[float] | None
) -> dict:
    """AOT statistics per stripe zone, per zone and source scene, and the variance share explained by zone vs by scene."""
    out = {"zones": {}, "by_zone_and_source": {}, "eta2_zone": {}, "eta2_source_scene": {}, "overall_mean": {}}
    out["zone_blue_offset_after_minus_before"] = blue_offset
    for date_, (aot, src, names) in per_date.items():
        ok = (zones >= 0) & (aot > 0)
        out["overall_mean"][date_] = round(float(aot[ok].mean()), 4)
        out["eta2_zone"][date_] = round(eta2(aot[ok], zones[ok].astype(int)), 4)
        out["eta2_source_scene"][date_] = round(eta2(aot[ok & (src >= 0)], src[ok & (src >= 0)].astype(int)), 4)
        out["by_zone_and_source"][date_] = {}
        for z in range(int(zones.max()) + 1):
            m = ok & (zones == z)
            out["zones"].setdefault(str(z), {})[date_] = {
                "mean": round(float(aot[m].mean()), 4),
                "median": round(float(np.median(aot[m])), 4),
            }
            out["by_zone_and_source"][date_][str(z)] = {
                nm: {"mean": round(float(aot[m & (src == k)].mean()), 4), "n_pixels": int((m & (src == k)).sum())}
                for k, nm in enumerate(names)
                if (m & (src == k)).any()
            }
    return out


def rasterize_labels(gdf: gpd.GeoDataFrame, grid: Grid) -> np.ndarray:
    """Polygon i (row order) -> pixels labelled i + 1, 0 elsewhere."""
    return rasterio.features.rasterize(
        ((g, i + 1) for i, g in enumerate(gdf.geometry)),
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        dtype="int32",
    )


def zonal_context(labels: np.ndarray, index: pd.Index, fields: dict[str, np.ndarray], scl: dict[str, np.ndarray]) -> pd.DataFrame:
    """Per-polygon means of ``fields`` and SCL class shares."""
    n = len(index)
    cols = {name: zonal_mean(labels, n, a) for name, a in fields.items()}
    for k, s in scl.items():
        cols[f"scl_artifact_{k}"] = zonal_fraction(labels, n, np.isin(s, SCL_ARTIFACT))
        cols[f"scl_water_{k}"] = zonal_fraction(labels, n, s == SCL_WATER)
        cols[f"scl_cloud_{k}"] = zonal_fraction(labels, n, np.isin(s, (SCL_CLOUD_MED, SCL_CLOUD_HIGH)))
    return pd.DataFrame(cols, index=index)


def write_context_table(sqlite_path: Path, ctx: pd.DataFrame) -> None:
    """(Re)create ``change_context`` next to ``change_features``; ``change_features`` itself is not touched."""
    cols = ", ".join(f"{c} {'TEXT' if ctx[c].dtype == object else 'REAL'}" for c in ctx.columns)
    with closing(sqlite3.connect(sqlite_path)) as con, con:
        con.execute("DROP TABLE IF EXISTS change_context")
        con.execute(f"CREATE TABLE change_context (id INTEGER PRIMARY KEY REFERENCES change_features(id), {cols})")
        ctx.to_sql("change_context", con, if_exists="append", index=True, index_label="id")


def timeline(catalog, aoi_geom: dict, grid: Grid, cache: Path, start: date, end: date, scale: int = 8) -> pd.DataFrame:
    """AOI means per acquisition at ``scale`` x 10 m (80 m). Duplicate processings of a datatake: newest baseline only."""
    items = {}
    for it in search(catalog, aoi_geom, start, end):  # newest baseline first
        items.setdefault((it.properties["platform"], it.datetime), it)
    fetch = Fetcher(grid, cache)

    def one(item) -> dict:
        refl = {b: reflectance(fetch.get(item, b, Resampling.average, scale)) for b in ("B03", "B04", "B08", "B12")}
        scl = fetch.get(item, "SCL", Resampling.nearest, scale)
        aot = fetch.get(item, "AOT", Resampling.average, scale) * AOT_SCALE
        has_data = np.isfinite(refl["B08"]) & np.isfinite(refl["B12"]) & np.isfinite(refl["B04"]) & np.isfinite(refl["B03"])
        clear = has_data & ~np.isin(scl, SCL_NOT_CLEAR)
        r = item_record(item)
        row = {k: r[k] for k in ("id", "datetime", "platform", "relative_orbit", "processing_baseline", "cloud_cover")}
        row |= {"aoi_data_fraction": float(has_data.mean()), "aoi_clear_fraction": float(clear.mean())}
        if clear.any():
            row |= {
                "nbr_mean": float(norm_diff(refl["B08"], refl["B12"])[clear].mean()),
                "vis_brightness_mean": float(((refl["B03"] + refl["B04"]) / 2)[clear].mean()),
                "aot_mean": float(aot[clear & (aot > 0)].mean()) if (clear & (aot > 0)).any() else np.nan,
            }
        return row

    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(one, sorted(items.values(), key=lambda i: i.datetime)))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------------------------------------
def _extent(grid: Grid) -> tuple[float, float, float, float]:
    t = grid.transform
    return t.c, t.c + t.a * grid.width, t.f + t.e * grid.height, t.f


def plot_enrichment(
    path: Path, grid: Grid, changes: dict, aot_after: np.ndarray, zones: np.ndarray, base: np.ndarray, gdf, areas, sources
) -> None:
    """2 x 3 panels: dNBR, dMNDWI, AOT after (+ zone boundaries), likely_cause, source scene before / after."""
    ext = _extent(grid)
    fig, axs = plt.subplots(2, 3, figsize=(22, 14), constrained_layout=True)
    titles = {"dNBR": "dNBR = NBR before - after (red = loss / burn)", "dMNDWI": "dMNDWI = after - before (blue = wetter)"}
    for ax, key in zip(axs.flat[:2], titles, strict=True):
        im = ax.imshow(changes[key], extent=ext, cmap="RdBu_r" if key == "dNBR" else "RdBu", vmin=-0.4, vmax=0.4)
        gdf.boundary.plot(ax=ax, color="k", linewidth=0.3)
        ax.set_title(titles[key])
        fig.colorbar(im, ax=ax, shrink=0.7)
    ax = axs[0, 2]
    a = np.where(aot_after > 0, aot_after, np.nan)
    im = ax.imshow(a, extent=ext, cmap="viridis", vmin=np.nanpercentile(a, 1), vmax=np.nanpercentile(a, 99))
    ax.contour(zones, levels=np.arange(zones.max()) + 0.5, extent=ext, origin="upper", colors="w", linewidths=1.2)
    ax.set_title("AOT, after date (white: stripe-zone boundaries)")
    fig.colorbar(im, ax=ax, shrink=0.7)
    ax = axs[1, 0]
    ax.imshow(base, extent=ext, cmap="gray", vmin=0, vmax=0.4)
    gdf.plot(ax=ax, color=gdf.likely_cause.map(CAUSE_COLORS), edgecolor="none")
    ax.legend(
        handles=[
            Patch(
                color=CAUSE_COLORS[c], label=f"{c} ({areas.get(c, {}).get('n', 0)}, {areas.get(c, {}).get('area_ha', 0):.0f} ha)"
            )
            for c in CAUSES
        ],
        loc="lower left",
        fontsize=8,
    )
    ax.set_title("likely_cause of robust CVA polygons (n, area)")
    for ax, (title, src, names) in zip(axs[1, 1:], sources, strict=True):
        cmap = matplotlib.colors.ListedColormap(plt.get_cmap("Set2").colors[: len(names)])
        ax.imshow(
            np.where(src >= 0, src, np.nan), extent=ext, cmap=cmap, vmin=-0.5, vmax=len(names) - 0.5, interpolation="nearest"
        )
        ax.contour(
            zones, levels=np.arange(zones.max()) + 0.5, extent=ext, origin="upper", colors="k", linewidths=0.8, linestyles=":"
        )
        ax.legend(handles=[Patch(color=cmap(k), label=nm) for k, nm in enumerate(names)], loc="lower left", fontsize=8)
        ax.set_title(f"{title}: source scene of each pixel (dotted: stripe zones)")
    for ax in axs.flat:
        ax.set_xlabel("easting (m)")
        ax.set_ylabel("northing (m)")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=100)
    plt.close(fig)


def plot_timeline(path: Path, tl: pd.DataFrame, marks: list[date]) -> None:
    t = pd.to_datetime(tl.datetime).dt.tz_localize(None)
    fig, axs = plt.subplots(4, 1, figsize=(11, 9), sharex=True, constrained_layout=True)
    series = [
        ("cloud_cover", "tile cloud cover (%)"),
        ("aoi_clear_fraction", "AOI clear-pixel fraction"),
        ("nbr_mean", "AOI mean NBR (clear px)"),
        ("aot_mean", "AOI mean AOT (clear px)"),
    ]
    for ax, (col, label) in zip(axs, series, strict=True):
        ax.plot(t, tl[col], "o-", ms=4)
        ax.set_ylabel(label)
        for m in marks:
            ax.axvline(pd.Timestamp(m), color="C3", ls="--", lw=1)
    axs[0].set_title("Sentinel-2 L2A acquisitions over the AOI (dashed: the two analysed dates)")
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------------------------------------
def _read_stack(path: Path) -> tuple[np.ndarray, Grid]:
    with rasterio.open(path) as ds:
        return ds.read(), Grid(ds.crs, ds.transform, ds.width, ds.height)


def enrich(
    before_dir: Path,
    after_dir: Path,
    aoi_path: Path,
    out_dir: Path,
    sqlite_path: Path,
    figures_dir: Path,
    cache_dir: Path = Path("data/cache/stac"),
    lookback_days: int = 75,
    lookahead_days: int = 3,
    with_timeline: bool = True,
    refresh: bool = False,
    rules: CauseRules = CauseRules(),
) -> dict:
    d0, d1 = parse_date(before_dir), parse_date(after_dir)
    stacks, grid = {}, None
    for d, folder in ((d0, before_dir), (d1, after_dir)):
        stacks[d], grid = _read_stack(Path(out_dir) / f"{Path(folder).name}_stack.tif")  # written by `s2cd run`
    out = Path(out_dir) / "enrichment"
    out.mkdir(parents=True, exist_ok=True)
    aoi_geom = gpd.read_file(aoi_path).to_crs(4326).geometry.union_all().__geo_interface__
    catalog = open_catalog()
    fetch = Fetcher(grid, cache_dir, refresh)

    # 1-2. Which STAC items are the provided rasters made of? Read every extra band from those same items, per pixel.
    stac, band_check, refl, scl, aot, src, names = {}, {}, {}, {}, {}, {}, {}
    valid = np.ones((grid.height, grid.width), bool)
    for d in (d0, d1):
        cands = search(catalog, aoi_geom, d - timedelta(days=lookback_days), d + timedelta(days=lookahead_days))
        picked, src[d] = find_sources(cands, fetch, stacks[d][2])
        if not picked:
            raise RuntimeError(f"None of {len(cands)} candidate items reproduces the provided B04 of {d}")
        recs = [item_record(i) for i in picked]
        names[d] = [source_label(r) for r in recs]
        n_px = np.bincount(src[d][src[d] >= 0], minlength=len(picked))
        n_valid = int((stacks[d][2] != 0).sum())
        stac[str(d)] = {
            "nominal_date_has_item": any(i.datetime.date() == d for i in cands),
            "n_candidates_searched": len(cands),
            "search_window": [str(d - timedelta(days=lookback_days)), str(d + timedelta(days=lookahead_days))],
            "sources": [
                {**r, "label": nm, "pixels": int(n), "fraction_of_valid": round(n / n_valid, 4)}
                for r, nm, n in zip(recs, names[d], n_px, strict=True)
            ],
            "unexplained_fraction": round(1 - n_px.sum() / n_valid, 5),
            "coverage": date_coverage(recs),
        }
        dn = {
            b: compose([fetch.get(i, b) for i in picked], src[d])
            for b in ("B02", "B03", "B04", "B08", "B11", "B12", "SCL", "AOT")
        }
        band_check[str(d)] = {b: compare_bands(stacks[d][i], dn[b]) for i, b in enumerate(("B02", "B03", "B04"))}
        refl[d] = {b: reflectance(dn[b]) for b in ("B02", "B03", "B04", "B08", "B11", "B12")}
        scl[d], aot[d] = dn["SCL"], dn["AOT"].astype(np.float32) * AOT_SCALE
        valid &= (stacks[d] != 0).all(axis=0) & (src[d] >= 0) & np.stack([np.isfinite(v) for v in refl[d].values()]).all(axis=0)
        log.info("%s <- %s", d, "; ".join(names[d]))

    # 3. indices and their changes
    idx = {d: spectral_indices(refl[d]) for d in (d0, d1)}
    chg = index_changes(idx[d0], idx[d1])
    nodata = np.float32(-9999)
    f = lambda a: np.where(valid, a, nodata).astype(np.float32)  # noqa: E731
    written = [
        write_raster(
            out / f"indices_{d:%Y%m%d}.tif",
            np.stack([f(idx[d][k]) for k in ("NDVI", "NBR", "MNDWI")]),
            grid,
            nodata,
            ["NDVI", "NBR", "MNDWI"],
        )
        for d in (d0, d1)
    ]
    written += [
        write_raster(
            out / "index_changes.tif",
            np.stack([f(chg[k]) for k in ("dNDVI", "dNBR", "dMNDWI")]),
            grid,
            nodata,
            ["dNDVI (after-before)", "dNBR (before-after)", "dMNDWI (after-before)"],
        ),
        write_raster(out / "aot.tif", np.stack([aot[d0], aot[d1]]), grid, 0, [f"AOT {d0}", f"AOT {d1}"]),
        write_raster(out / "scl.tif", np.stack([scl[d0], scl[d1]]), grid, 0, [f"SCL {d0}", f"SCL {d1}"], overviews=False),
        write_raster(
            out / "source_scene.tif", np.stack([src[d0], src[d1]]), grid, -1, [f"source {d0}", f"source {d1}"], overviews=False
        ),
    ]

    # 5. AOT per stripe zone (and per source scene: the zones coincide with mosaic seams, see summary)
    with rasterio.open(Path(out_dir) / "radiometric_zones.tif") as ds:
        zones = ds.read(1)
    run_summary = Path(out_dir) / "run_summary.json"
    blue = json.loads(run_summary.read_text())["stripes"]["zone_blue_offset"] if run_summary.exists() else None
    aot_zone = aot_by_zone({str(d): (aot[d], src[d], names[d]) for d in (d0, d1)}, zones, blue)

    # 4. per-polygon context + likely cause
    feats = pyogrio.read_dataframe(sqlite_path, layer="change_features", where="method = 'robust_cva'", fid_as_index=True)
    labels = rasterize_labels(feats, grid)
    masked = lambda a: np.where(valid, a, np.nan)  # noqa: E731
    fields = {f"{k.lower()}_{w}": masked(idx[d][k]) for k in ("NDVI", "NBR", "MNDWI") for w, d in (("before", d0), ("after", d1))}
    fields |= {"d_ndvi": masked(chg["dNDVI"]), "d_nbr": masked(chg["dNBR"]), "d_mndwi": masked(chg["dMNDWI"])}
    fields |= {f"aot_{w}": np.where(aot[d] > 0, aot[d], np.nan) for w, d in (("before", d0), ("after", d1))}
    ctx = zonal_context(labels, feats.index, fields, {"before": scl[d0], "after": scl[d1]})
    ctx["d_aot"] = ctx.aot_after - ctx.aot_before
    ctx["likely_cause"] = assign_cause(ctx, feats.in_disturbed_area.astype(bool), rules)
    ctx = ctx.join(source_mix(labels, len(feats), src[d0], src[d1], (names[d0], names[d1])).set_index(feats.index))
    ctx = ctx.round(4)
    write_context_table(sqlite_path, ctx)
    enriched = feats.join(ctx)
    enriched.to_crs(4326).to_file(
        out / "change_features_enriched.geojson", driver="GeoJSON", layer_options={"COORDINATE_PRECISION": 6}
    )
    areas = {
        c: {"n": int(len(g)), "area_ha": round(float(g.area_m2.sum()) / 1e4, 2)} for c, g in enriched.groupby("likely_cause")
    }
    seam = enriched.seam_mix > 0.05

    # 6. timeline (optional, many small reads)
    tl_summary: dict = {"skipped": "disabled with --no-timeline"}
    if with_timeline:
        tl = timeline(catalog, aoi_geom, grid, cache_dir, date(d0.year, 6, 1), date(d0.year, 10, 31))
        tl.to_csv(out / "timeline.csv", index=False)
        plot_timeline(Path(figures_dir) / "07_timeline.png", tl, [d0, d1])
        tl_summary = {"n_acquisitions": len(tl), "csv": str(out / "timeline.csv"), "resolution_m": 80}

    # 7. figure + summary
    base = np.where(valid, ((stacks[d0].astype(np.float32) - L2A_OFFSET) / QUANTIFICATION).mean(axis=0), np.nan)
    plot_enrichment(
        Path(figures_dir) / "06_enrichment.png",
        grid,
        {k: masked(v) for k, v in chg.items()},
        aot[d1],
        zones,
        base,
        enriched,
        areas,
        [(str(d), src[d], names[d]) for d in (d0, d1)],
    )
    summary = {
        "dates": [str(d0), str(d1)],
        "sign_convention": "d_nbr = before - after; d_ndvi and d_mndwi = after - before",
        "stac": stac,
        "view_angles_in_stac_properties": any(
            s_["view_incidence_angle"] is not None for v in stac.values() for s_ in v["sources"]
        ),
        "provided_vs_planetary_computer_mosaic": band_check,
        "source_scene_by_stripe_zone": {str(d): provenance_by_zone(src[d], zones, names[d]) for d in (d0, d1)},
        "aot_by_zone": aot_zone,
        "cause_rules": asdict(rules),
        "area_by_likely_cause": areas,
        "n_polygons": len(enriched),
        "polygons_crossing_a_source_seam": {
            "n": int(seam.sum()),
            "area_ha": round(float(enriched.area_m2[seam].sum()) / 1e4, 2),
            "rule": "seam_mix > 0.05",
        },
        "timeline": tl_summary,
        "files": [str(p) for p in written] + [str(out / "change_features_enriched.geojson")],
    }
    (out / "enrichment_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary
