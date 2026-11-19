"""End-to-end pipeline: load -> zone & normalize -> detect -> vectorize -> store -> visualize."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml

from . import db, methods, radiometry, vectorize
from .io import Scene, check_consistency, load_scene, write_raster

log = logging.getLogger("s2cd")


@dataclass
class Config:
    before: Path = Path("inputs/data/sentinel2_20230812")
    after: Path = Path("inputs/data/sentinel2_20230902")
    aoi: Path = Path("inputs/aoi.geojson")
    out_dir: Path = Path("data/processed")
    sqlite_path: Path = Path("data/change_detection.sqlite")
    figures_dir: Path = Path("docs/figures")
    database_url: str | None = None  # postgresql+psycopg://... -> also write to PostGIS
    bands: tuple[str, ...] = ("B02", "B03", "B04")
    run_irmad: bool = True
    rule: vectorize.BinaryRule = field(default_factory=vectorize.BinaryRule)
    stripe: dict = field(default_factory=dict)  # overrides for radiometry.detect_stripe_zones

    @classmethod
    def from_yaml(cls, path: Path) -> Config:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        rule = vectorize.BinaryRule(**raw.pop("rule", {}))
        for k in ("before", "after", "aoi", "out_dir", "sqlite_path", "figures_dir"):
            if k in raw:
                raw[k] = Path(raw[k])
        if "bands" in raw:
            raw["bands"] = tuple(raw["bands"])
        return cls(rule=rule, **raw)


@dataclass
class Products:
    """Everything downstream steps (figures, web export, tests) need, kept in memory."""

    before: Scene
    after: Scene
    valid: np.ndarray
    refl_before: np.ndarray
    refl_after: np.ndarray
    refl_after_norm: np.ndarray
    stripes: radiometry.StripeZones
    seam_dist: np.ndarray
    pif: np.ndarray
    baseline_prob: np.ndarray
    baseline_binary: np.ndarray
    cva: methods.CvaResult
    cva_binary: np.ndarray
    irmad: methods.IrmadResult | None
    irmad_binary: np.ndarray | None
    features: dict[str, gpd.GeoDataFrame]
    aoi: gpd.GeoDataFrame
    summary: dict


def _area_ha(mask: np.ndarray, pixel_area: float) -> float:
    return round(float(mask.sum()) * pixel_area / 1e4, 2)


def run(cfg: Config) -> Products:
    t0 = time.perf_counter()
    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ---- Part 1: load, validate, stack -------------------------------------------------------
    before, after = load_scene(cfg.before, cfg.bands), load_scene(cfg.after, cfg.bands)
    grid = check_consistency({before.name: before.grid, after.name: after.grid})
    if before.acquired >= after.acquired:
        raise ValueError(f"'before' ({before.acquired}) must be earlier than 'after' ({after.acquired})")
    valid = before.valid & after.valid
    names = [f"{b} ({n})" for b, n in zip(cfg.bands, ("blue", "green", "red"), strict=False)]
    for s in (before, after):
        write_raster(out / f"{s.name}_stack.tif", s.dn, grid, s.nodata, names, {"ACQUISITION_DATE": s.acquired})
    log.info(
        "Loaded %s and %s on %s, %dx%d px, %.1f%% valid",
        before.name,
        after.name,
        grid.crs,
        grid.height,
        grid.width,
        100 * valid.mean(),
    )

    offsets = {s.name: radiometry.infer_offset(s.dn, valid) for s in (before, after)}
    r1 = radiometry.to_reflectance(before.dn, valid, offsets[before.name])
    r2 = radiometry.to_reflectance(after.dn, valid, offsets[after.name])

    # ---- Part 2a: radiometric zones + relative normalization ----------------------------------
    stripes = radiometry.detect_stripe_zones(r1, r2, valid, **cfg.stripe)
    seam_dist = radiometry.seam_distance(valid.shape, stripes.angle_deg, stripes.breakpoints)
    r2n, pif, cals = radiometry.relative_normalization(r1, r2, stripes.zones, cfg.bands)
    log.info(
        "Stripe zones: %d (angle %.2f deg, eta2 %.2f); PIF share %.1f%%",
        len(stripes.zone_offsets),
        stripes.angle_deg,
        stripes.explained_variance,
        100 * pif[valid].mean(),
    )
    dn_norm = np.where(valid, np.clip(np.round(r2n * radiometry.QUANTIFICATION + radiometry.L2A_OFFSET), 1, 65535), 0).astype(
        np.uint16
    )
    write_raster(
        out / f"{after.name}_stack_normalized.tif",
        dn_norm,
        grid,
        0,
        names,
        {"NOTE": "after-date mapped to before-date radiometry per stripe zone; DN = reflectance * 10000 + 1000"},
    )
    write_raster(out / "radiometric_zones.tif", stripes.zones, grid, -1, ["stripe zone id"])

    # ---- Part 2b: change detection ------------------------------------------------------------
    rule = cfg.rule
    base_prob = methods.euclidean_baseline(r1, r2, valid)
    base_bin, otsu_t = vectorize.binary_from_otsu(base_prob, valid, rule)

    cva = methods.robust_cva(r1, r2n, stripes.zones, pif)
    cva_bin = vectorize.binary_from_d2(cva.d2, valid, seam_dist, len(cfg.bands), rule)

    irm, irm_bin = None, None
    if cfg.run_irmad:
        irm = methods.irmad(r1, r2, valid, stripes.zones)
        irm_bin = vectorize.binary_from_d2(irm.chi2, valid, seam_dist, len(cfg.bands), rule)

    tags = {"DATE_BEFORE": before.acquired, "DATE_AFTER": after.acquired}
    write_raster(
        out / "change_map_baseline.tif",
        np.nan_to_num(base_prob * 255).astype(np.uint8),
        grid,
        None,
        ["euclidean distance, min-max scaled 0-255"],
        tags,
    )
    write_raster(out / "change_binary_baseline.tif", base_bin.astype(np.uint8), grid, None, ["1 = change (Otsu)"], tags)
    log_d2 = np.log10(np.clip(cva.d2, 1e-6, None))
    write_raster(
        out / "change_map.tif",
        np.where(valid, np.round(log_d2, 2), -9999).astype(np.float32),  # 0.01 log units is far below noise; compresses well
        grid,
        -9999,
        ["change intensity: log10 Mahalanobis d2"],
        {**tags, "D2_THRESHOLD_LOW": round(rule.thresholds(len(cfg.bands))[0], 3)},
    )
    write_raster(
        out / "change_probability.tif",
        np.where(valid, np.round(np.nan_to_num(cva.p_change) * 254), 255).astype(np.uint8),
        grid,
        255,
        ["change probability chi2.cdf(d2, 3) scaled 0-254"],
        tags,
    )
    write_raster(
        out / "change_binary.tif",
        cva_bin.astype(np.uint8),
        grid,
        None,
        ["1 = change (robust CVA, hysteresis FAR 1e-3/1e-5)"],
        tags,
    )
    if irm is not None:
        write_raster(
            out / "change_binary_irmad.tif", irm_bin.astype(np.uint8), grid, None, ["1 = change (zone-wise IR-MAD)"], tags
        )

    # ---- Part 3: vectorize + store ------------------------------------------------------------
    noise_sigma = float(np.sqrt(np.mean([np.trace(np.array(c)) / len(cfg.bands) for c in cva.noise_cov.values()])))
    low, high = rule.thresholds(len(cfg.bands))
    disturbed = vectorize.disturbed_footprint(r1, valid)
    layers = {
        "d2": cva.d2,
        "p_change": cva.p_change,
        "delta": cva.delta,
        "bright_before": r1.mean(axis=0),
        "bright_after": r2n.mean(axis=0),
        "zone": np.clip(stripes.zones, 0, None),
        "disturbed": disturbed,
    }
    features = {"robust_cva": vectorize.polygonize(cva_bin, grid, layers, noise_sigma, high, low)}
    # Baseline polygons carry the same descriptive attributes, but confidence = its own probability.
    base_layers = {**layers, "d2": base_prob / max(otsu_t, 1e-6) * low, "p_change": base_prob}
    features["euclidean_baseline"] = vectorize.polygonize(base_bin, grid, base_layers, noise_sigma, high, low)

    px = grid.pixel_area_m2
    summary = {
        "grid": {"crs": str(grid.crs), "width": grid.width, "height": grid.height, "pixel_m": grid.transform.a},
        "valid_fraction": round(float(valid.mean()), 4),
        "boa_offset": offsets,
        "stripes": {
            "angle_deg": round(stripes.angle_deg, 2),
            "explained_variance": round(stripes.explained_variance, 3),
            "breakpoints_px": [round(b, 1) for b in stripes.breakpoints],
            "zone_blue_offset": [round(o, 4) for o in stripes.zone_offsets],
        },
        "calibration": [asdict(c) for c in cals],
        "pif_fraction": round(float(pif[valid].mean()), 3),
        "noise_sigma_reflectance": round(noise_sigma, 5),
        "thresholds": {"baseline_otsu": round(otsu_t, 4), "cva_d2_low": round(low, 2), "cva_d2_high": round(high, 2)},
        "changed_area_ha": {
            "euclidean_baseline": _area_ha(base_bin, px),
            "robust_cva": _area_ha(cva_bin, px),
            **({"irmad": _area_ha(irm_bin, px)} if irm is not None else {}),
        },
        "changed_fraction_by_zone": {
            m: [round(float(b[stripes.zones == z].mean()), 4) for z in range(len(stripes.zone_offsets))]
            for m, b in [("euclidean_baseline", base_bin), ("robust_cva", cva_bin)]
            + ([("irmad", irm_bin)] if irm is not None else [])
        },
        "irmad_rho_by_zone": irm.rho if irm is not None else None,
        "n_features": {k: len(v) for k, v in features.items()},
        "agreement_cva_vs_baseline_iou": round(float((cva_bin & base_bin).sum() / max((cva_bin | base_bin).sum(), 1)), 3),
    }

    run_rows, tables = [], []
    params = {"rule": asdict(rule), "stripe": cfg.stripe, "bands": list(cfg.bands)}
    for method, gdf in features.items():
        run_id = f"{before.acquired:%Y%m%d}_{after.acquired:%Y%m%d}_{method}"
        tables.append(db.as_feature_table(gdf, run_id, method, before.acquired, after.acquired))
        run_rows.append(
            {
                "run_id": run_id,
                "method": method,
                "date_before": str(before.acquired),
                "date_after": str(after.acquired),
                "params": params,
                "summary": {"changed_area_ha": summary["changed_area_ha"][method], "n_features": len(gdf)},
            }
        )
    feats = gpd.GeoDataFrame(pd.concat(tables, ignore_index=True), crs=grid.crs)
    runs = db.runs_table(run_rows)
    db.write_sqlite(cfg.sqlite_path, feats, runs)
    feats.to_crs(4326).to_file(out / "change_features.geojson", driver="GeoJSON", layer_options={"COORDINATE_PRECISION": 6})
    if cfg.database_url:
        db.write_postgis(cfg.database_url, feats, runs)
        log.info("Wrote %d features to PostGIS", len(feats))

    aoi = gpd.read_file(cfg.aoi).to_crs(grid.crs)
    summary["runtime_s"] = round(time.perf_counter() - t0, 1)
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log.info("Changed area (ha): %s", summary["changed_area_ha"])
    return Products(
        before,
        after,
        valid,
        r1,
        r2,
        r2n,
        stripes,
        seam_dist,
        pif,
        base_prob,
        base_bin,
        cva,
        cva_bin,
        irm,
        irm_bin,
        features,
        aoi,
        summary,
    )
