"""Which Sentinel-2 acquisitions are the provided "dates" made of? RGB-only provenance from Planetary Computer (STAC).

Needs the ``stac`` extra (``uv run --extra stac s2cd provenance``) and network access. The core pipeline imports this module,
but the STAC libraries load only when called. Only B02/B03/B04 are read, the bands the brief provides. STAC item
metadata (platform, orbit, baseline, cloud cover) is used to describe the sources, nothing else is read from them.

Steps:

1. **Search.** All L2A items intersecting the AOI in the ``lookback_days`` before (and ``lookahead_days`` after) each nominal
   date. The provided rasters are mosaics, so the source of a pixel is not necessarily an item of the nominal date.
2. **Match.** Each candidate's B04 is read onto the pipeline grid (cached as ``.npy``) and compared with the provided B04:
   a pixel is explained by a candidate when the digital number is exactly equal. A greedy set cover picks the fewest
   candidates that explain the valid pixels.
3. **Verify.** The picked sources are composed pixel by pixel and compared with the provided B02, B03 and B04
   (``exact_match_fraction`` = 1.0 means the provided bands are reproduced bit for bit).
4. **Zone agreement.** Share of every radiometric stripe zone that comes from each source, and the share of valid pixels whose
   source is the dominant source of their zone. High agreement means the stripe-shaped steps are seams between source scenes.

Outputs: ``outputs/provenance/source_scene.tif`` (per date, index of the source scene per pixel, -1 = unexplained),
and ``provenance_summary.json``; ``pipeline/scripts/fig_provenance.py`` draws the report figure from them.

``pipeline.run`` reads these outputs when present (``load_existing``): the radiometric zones are then the unique
(before-source, after-source) pairs and the seams are their exact boundaries; ``acquisition_dates`` supplies the true
acquisition dates of every nominal date for tags, run parameters and figure captions.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from .io import Grid, parse_date, write_raster

log = logging.getLogger("s2cd.provenance")

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-2-l2a"
BANDS = ("B02", "B03", "B04")
MATCH_BAND = "B04"  # the band used to find the sources; the other two are verified afterwards


@dataclass(frozen=True)
class ProvenanceRules:
    """Search window and set-cover settings."""

    lookback_days: int = 75  # inputs are mosaics: search this far before the nominal date ...
    lookahead_days: int = 3  # ... and a few days after it
    min_gain: float = 0.001  # stop when the next source explains less than this share of the valid pixels
    max_sources: int = 8
    workers: int = 4  # parallel candidate reads


# Pure functions (unit-testable offline)
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
    """Share of each stripe zone's pixels that comes from each source scene."""
    out = {}
    for z in range(int(zones.max()) + 1):
        m = zones == z
        cnt = np.bincount(src[m & (src >= 0)], minlength=len(names))
        out[str(z)] = {n: round(float(c) / max(int(m.sum()), 1), 4) for n, c in zip(names, cnt, strict=True) if c}
    return out


def zone_source_agreement(src: np.ndarray, zones: np.ndarray) -> float:
    """Share of explained pixels whose source scene is the dominant source of their stripe zone (1 = zones are source scenes)."""
    ok = (src >= 0) & (zones >= 0)
    agree = 0
    for z in range(int(zones.max()) + 1):
        cnt = np.bincount(src[ok & (zones == z)].astype(int))
        agree += int(cnt.max()) if cnt.size else 0
    return round(agree / max(int(ok.sum()), 1), 4)


# STAC access
def open_catalog():
    try:
        import planetary_computer
        import pystac_client
    except ImportError as e:
        raise ImportError("This step needs the 'stac' extra: uv run --extra stac s2cd provenance") from e
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
    }


def assert_aligned(src: rasterio.DatasetReader, grid: Grid) -> None:
    """Raise unless ``src`` has the same CRS and pixel size as ``grid`` and its pixel edges coincide with the grid's.

    The provided rasters are exact copies of source pixels on the source scenes' own 10 m grid (the provenance step proves it:
    the digital numbers are bit-identical). Reading a source band is then a pure window copy; any misalignment would
    force interpolation and silently break the exact-match test.
    """
    t, g = src.transform, grid.transform
    if src.crs != grid.crs or not (np.isclose(t.a, g.a) and np.isclose(t.e, g.e) and np.isclose(t.b, 0) and np.isclose(t.d, 0)):
        raise ValueError(f"{src.name}: CRS or pixel size differs from the pipeline grid ({src.crs}, {t.a} vs {grid.crs}, {g.a})")
    for off in ((g.c - t.c) / t.a, (g.f - t.f) / t.e):
        if abs(off - round(off)) > 1e-6:
            raise ValueError(
                f"{src.name}: pixel edges are {off % 1:.3f} px off the pipeline grid; nearest resampling would shift data"
            )


def read_on_grid(href: str, grid: Grid) -> np.ndarray:
    """Read one 10 m COG band onto ``grid`` with nearest-neighbour resampling; 0 = nodata.

    The source and the grid are pixel-aligned (checked by ``assert_aligned``), so nearest resampling copies the source pixels
    unchanged: digital numbers stay exact, which the exact-equality match against the provided rasters relies on.
    """
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MAX_RETRY="4")
    with rasterio.Env(**env), rasterio.open(href) as src:
        assert_aligned(src, grid)
        with WarpedVRT(
            src,
            crs=grid.crs,
            transform=grid.transform,
            width=grid.width,
            height=grid.height,
            resampling=Resampling.nearest,
            src_nodata=0,
            nodata=0,
        ) as vrt:
            return vrt.read(1)


class Fetcher:
    """Reads item bands onto the grid and caches every window as .npy under ``cache`` (re-runs need no downloads)."""

    def __init__(self, grid: Grid, cache: Path, refresh: bool = False):
        self.grid, self.cache, self.refresh = grid, Path(cache), refresh
        self.cache.mkdir(parents=True, exist_ok=True)
        t = grid.transform
        self.tag = f"{int(t.c)}_{int(t.f)}_{grid.width}x{grid.height}"

    def get(self, item, band: str) -> np.ndarray:
        path = self.cache / f"{item.id}_{band}_s1_{self.tag}.npy"
        if path.exists() and not self.refresh:
            return np.load(path)
        arr = read_on_grid(item.assets[band].href, self.grid)
        np.save(path, arr)
        return arr


# Pipeline steps
def source_label(r: dict) -> str:
    return f"{r['datetime'][:10]} S2{r['platform'][-1]} R{r['relative_orbit']:03d} N{r['processing_baseline']}"


def find_sources(items: list, fetch: Fetcher, provided: np.ndarray, rules: ProvenanceRules) -> tuple[list, np.ndarray]:
    """Which items does the provided ``MATCH_BAND`` consist of? (exact DN equality, greedy cover)"""
    valid = provided != 0
    with ThreadPoolExecutor(rules.workers) as ex:
        matches = list(ex.map(lambda it: (provided == fetch.get(it, MATCH_BAND)) & valid, items))
    picked, src = greedy_cover(matches, valid, rules.min_gain, rules.max_sources)
    return [items[k] for k in picked], src


def describe_date(d: date, cands: list, picked: list, src: np.ndarray, provided: np.ndarray, rules: ProvenanceRules) -> dict:
    """Per-date share table: the source items, the pixels each explains, and the unexplained remainder."""
    recs = [item_record(i) for i in picked]
    n_px = np.bincount(src[src >= 0], minlength=len(picked))
    n_valid = int((provided != 0).sum())
    return {
        "nominal_date_has_item": any(i.datetime.date() == d for i in cands),
        "n_candidates_searched": len(cands),
        "search_window": [str(d - timedelta(days=rules.lookback_days)), str(d + timedelta(days=rules.lookahead_days))],
        "sources": [
            {**r, "label": source_label(r), "pixels": int(n), "fraction_of_valid": round(n / n_valid, 4)}
            for r, n in zip(recs, n_px, strict=True)
        ],
        "unexplained_fraction": round(1 - n_px.sum() / n_valid, 5),
        "coverage": date_coverage(recs),
    }


# Orchestration
def acquisition_dates(summary: dict, nominal: date) -> dict:
    """The real acquisitions behind a nominal date (from provenance_summary.json): nominal, source_dates, source_items."""
    srcs = summary["stac"][str(nominal)]["sources"]
    return {
        "nominal": str(nominal),
        "source_dates": sorted({s["datetime"][:10] for s in srcs}),
        "source_items": [s["id"] for s in srcs],
    }


def date_label(info: dict) -> str:
    """Caption for a nominal date, e.g. ``nominal 2023-08-12 (mosaic of 2023-07-01/07-04)``."""
    days = info["source_dates"]
    if not days:
        return f"nominal {info['nominal']}"  # acquisition dates unknown (no provenance run)
    if days == [info["nominal"]]:
        return info["nominal"]
    return (
        f"nominal {info['nominal']} (mosaic of {days[0]}"
        + "".join(f"/{d[5:]}" if d[:4] == days[0][:4] else f"/{d}" for d in days[1:])
        + ")"
    )


def load_existing(provenance_dir: Path, before: date, after: date, grid: Grid) -> tuple[np.ndarray, dict] | None:
    """``(source_scene (2, H, W), summary)`` of a matching ``s2cd provenance`` run, or None if absent or for other dates/grid."""
    tif, js = Path(provenance_dir) / "source_scene.tif", Path(provenance_dir) / "provenance_summary.json"
    if not (tif.exists() and js.exists()):
        return None
    summary = json.loads(js.read_text())
    with rasterio.open(tif) as ds:
        same_grid = (ds.width, ds.height) == (grid.width, grid.height) and ds.transform.almost_equals(grid.transform)
        if summary.get("dates") != [str(before), str(after)] or not same_grid:
            log.warning("Ignoring %s: it belongs to other dates or another grid", tif)
            return None
        return ds.read(), summary


def _read_stack(path: Path) -> tuple[np.ndarray, Grid]:
    with rasterio.open(path) as ds:
        return ds.read(), Grid(ds.crs, ds.transform, ds.width, ds.height)


def provenance(
    before_dir: Path,
    after_dir: Path,
    aoi_path: Path,
    out_dir: Path,
    cache_dir: Path = Path(".cache/stac"),
    refresh: bool = False,
    rules: ProvenanceRules = ProvenanceRules(),
) -> dict:
    """Find the source items of both dates, write ``source_scene.tif`` and the summary JSON; returns the summary."""
    d0, d1 = parse_date(before_dir), parse_date(after_dir)
    stacks, grid = {}, None
    for d, folder in ((d0, before_dir), (d1, after_dir)):
        stacks[d], grid = _read_stack(Path(out_dir) / f"{Path(folder).name}_stack.tif")  # written by `s2cd run`
    out = Path(out_dir) / "provenance"
    out.mkdir(parents=True, exist_ok=True)
    detected = Path(out_dir) / "stripe_zones_detected.tif"  # the data-driven zones, not the provenance-derived ones used by `run`
    with rasterio.open(detected if detected.exists() else Path(out_dir) / "radiometric_zones.tif") as ds:
        zones = ds.read(1)
    aoi_geom = gpd.read_file(aoi_path).to_crs(4326).geometry.union_all().__geo_interface__
    catalog = open_catalog()
    fetch = Fetcher(grid, cache_dir, refresh)

    sources, band_check, src, names = {}, {}, {}, {}
    for d in (d0, d1):
        cands = search(catalog, aoi_geom, d - timedelta(days=rules.lookback_days), d + timedelta(days=rules.lookahead_days))
        picked, src[d] = find_sources(cands, fetch, stacks[d][BANDS.index(MATCH_BAND)], rules)
        if not picked:
            raise RuntimeError(f"None of {len(cands)} candidate items reproduces the provided {MATCH_BAND} of {d}")
        sources[str(d)] = describe_date(d, cands, picked, src[d], stacks[d][BANDS.index(MATCH_BAND)], rules)
        names[d] = [s["label"] for s in sources[str(d)]["sources"]]
        mosaic = {b: compose([fetch.get(i, b) for i in picked], src[d]) for b in BANDS}
        band_check[str(d)] = {b: compare_bands(stacks[d][k], mosaic[b]) for k, b in enumerate(BANDS)}
        log.info("%s <- %s", d, "; ".join(names[d]))

    write_raster(
        out / "source_scene.tif",
        np.stack([src[d0], src[d1]]),
        grid,
        -1,
        [f"source {d0}", f"source {d1}"],
        {"SOURCES_" + str(d).replace("-", ""): "; ".join(names[d]) for d in (d0, d1)},
        overview_resampling=Resampling.nearest,
    )
    summary = {
        "dates": [str(d0), str(d1)],
        "bands": list(BANDS),
        "rules": asdict(rules),
        "stac": sources,
        "provided_vs_planetary_computer_mosaic": band_check,
        "source_scene_by_stripe_zone": {str(d): provenance_by_zone(src[d], zones, names[d]) for d in (d0, d1)},
        "zone_source_agreement": {str(d): zone_source_agreement(src[d], zones) for d in (d0, d1)},
        "files": [str(out / "source_scene.tif")],
    }
    (out / "provenance_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary
