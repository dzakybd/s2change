"""End-to-end pipeline: load -> zone & normalize -> detect -> vectorize -> interpret -> store.

Areas reported here are *map areas* of the detected change. There are no reference data in this task, so they are not
bias-corrected area estimates with confidence intervals (Olofsson et al. 2014, Remote Sensing of Environment 148:42); the
sensitivity study (``s2cd.sensitivity``) shows how much they move with the two user-chosen knobs instead.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio.features
import yaml
from rasterio.enums import Resampling

from . import db, interpret, methods, provenance, radiometry, register, vectorize
from .io import Scene, check_consistency, load_scene, write_raster

log = logging.getLogger("s2cd")


@dataclass
class Config:
    before: Path = Path("inputs/data/sentinel2_20230812")
    after: Path = Path("inputs/data/sentinel2_20230902")
    aoi: Path = Path("inputs/aoi.geojson")
    out_dir: Path = Path("data/processed")
    provenance_dir: Path = Path(
        "data/processed/provenance"
    )  # `s2cd provenance` output; when it matches, its seams define the zones
    facility_register: Path = register.REGISTER
    sqlite_path: Path = Path("data/change_detection.sqlite")
    figures_dir: Path = Path("docs/figures")
    database_url: str | None = None  # postgresql+psycopg://... -> also write to PostGIS
    bands: tuple[str, ...] = ("B02", "B03", "B04")
    rule: vectorize.BinaryRule = field(default_factory=vectorize.BinaryRule)
    noise: methods.NoiseRules = field(default_factory=methods.NoiseRules)
    stripe: dict = field(default_factory=dict)  # overrides for radiometry.detect_stripe_zones

    @classmethod
    def from_yaml(cls, path: Path) -> Config:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        rule = vectorize.BinaryRule(**raw.pop("rule", {}))
        noise = methods.NoiseRules(**raw.pop("noise", {}))
        for k in ("before", "after", "aoi", "out_dir", "provenance_dir", "facility_register", "sqlite_path", "figures_dir"):
            if k in raw:
                raw[k] = Path(raw[k])
        if "bands" in raw:
            raw["bands"] = tuple(raw["bands"])
        return cls(rule=rule, noise=noise, **raw)


@dataclass
class Products:
    """Everything downstream steps (figures, web export, tests) need, kept in memory."""

    before: Scene
    after: Scene
    valid: np.ndarray
    refl_before: np.ndarray
    refl_after: np.ndarray
    refl_after_norm: np.ndarray
    stripes: radiometry.StripeZones  # the data-driven detection (always run)
    zones: np.ndarray  # the zones actually used: source-scene pairs if provenance is available, else ``stripes.zones``
    zone_source: str  # "provenance" or "data-driven"
    seam_dist: np.ndarray  # pixels to the nearest zone boundary
    pif: np.ndarray
    baseline_prob: np.ndarray
    baseline_binary: np.ndarray
    cva: methods.CvaResult
    cva_binary: np.ndarray
    features: dict[str, gpd.GeoDataFrame]
    facilities: gpd.GeoDataFrame  # register outlines (MultiPolygon, EPSG:32735)
    facility_changes: pd.DataFrame  # one row per facility: changed ha, share, change types, summary
    date_labels: dict[str, str]  # "before"/"after" -> caption such as "nominal 2023-08-12 (mosaic of 2023-07-01/07-04)"
    aoi: gpd.GeoDataFrame
    summary: dict


def _area_ha(mask: np.ndarray, pixel_area: float) -> float:
    return round(float(mask.sum()) * pixel_area / 1e4, 2)


def _footprint_share(footprint: np.ndarray, change: np.ndarray, valid: np.ndarray) -> dict:
    """Share of the valid area covered by ``footprint`` and share of the changed pixels falling inside it."""
    return {
        "share_of_aoi": round(float(footprint[valid].mean()), 3),
        "share_of_change": round(float((change & footprint).sum() / max(change.sum(), 1)), 3),
    }


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
    px_m = grid.transform.a
    offsets = {s.name: radiometry.infer_offset(s.dn, valid) for s in (before, after)}
    prov = provenance.load_existing(cfg.provenance_dir, before.acquired, after.acquired, grid)
    acquisition = {
        str(s.acquired): provenance.acquisition_dates(prov[1], s.acquired)
        if prov
        else {"nominal": str(s.acquired), "source_dates": [], "source_items": []}
        for s in (before, after)
    }
    date_labels = {k: provenance.date_label(acquisition[str(s.acquired)]) for k, s in (("before", before), ("after", after))}

    names = [f"{b} ({n})" for b, n in zip(cfg.bands, ("blue", "green", "red"), strict=False)]
    for s in (before, after):
        info = acquisition[str(s.acquired)]
        write_raster(
            out / f"{s.name}_stack.tif",
            s.dn,
            grid,
            s.nodata,
            names,
            {
                "NOMINAL_DATE": s.acquired,  # the label of the provided raster (a multi-day mosaic), not an acquisition date
                "SOURCE_DATES": ",".join(info["source_dates"]),
                "SOURCE_ITEMS": ",".join(info["source_items"]),
                "BOA_ADD_OFFSET_DN": offsets[s.name],
            },
            scale_offset=(
                1 / radiometry.QUANTIFICATION,
                -offsets[s.name] / radiometry.QUANTIFICATION,
            ),  # reflectance = DN*scale+offset
        )
    log.info(
        "Loaded %s and %s on %s, %dx%d px, %.1f%% valid; zones from %s",
        before.name,
        after.name,
        grid.crs,
        grid.height,
        grid.width,
        100 * valid.mean(),
        "provenance" if prov else "data-driven detection",
    )
    r1 = radiometry.to_reflectance(before.dn, valid, offsets[before.name])
    r2 = radiometry.to_reflectance(after.dn, valid, offsets[after.name])

    # ---- Part 2a: radiometric zones + relative normalization ----------------------------------
    stripes = radiometry.detect_stripe_zones(r1, r2, valid, **cfg.stripe)  # always run: it validates the provenance zones
    if prov:
        zones, source_pairs = radiometry.zones_from_sources(prov[0], valid)
        seam_dist = radiometry.zone_boundary_distance(zones)
        zone_info = {
            "source": "provenance",
            "n_zones": len(source_pairs),
            "zone_source_pairs": [
                {"zone": i, "before_source_index": b, "after_source_index": a} for i, (b, a) in enumerate(source_pairs)
            ],
            "agreement_with_data_driven_zones": {
                "data_driven_zone_inside_one_source_zone": radiometry.zone_agreement(stripes.zones, zones),
                "source_zone_inside_one_data_driven_zone": radiometry.zone_agreement(zones, stripes.zones),
            },
        }
    else:
        zones = stripes.zones
        seam_dist = radiometry.seam_distance(valid.shape, stripes.angle_deg, stripes.breakpoints)
        zone_info = {"source": "data-driven", "n_zones": len(stripes.zone_offsets)}
    norm = radiometry.relative_normalization(r1, r2, zones, cfg.bands)
    r2n, pif = norm.normalized, norm.pif
    shifts = radiometry.registration_shifts(r1, r2n, zones, pif)
    log.info(
        "Zones: %d (%s); stripe fit angle %.2f deg, eta2 %.2f; PIF share %.1f%%; registration shift px %s",
        zone_info["n_zones"],
        zone_info["source"],
        stripes.angle_deg,
        stripes.explained_variance,
        100 * pif[valid].mean(),
        {z: v.get("shift_px") for z, v in shifts.items()},
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
        {
            "NOTE": "after-date mapped to before-date radiometry per zone; DN = reflectance * 10000 + 1000",
            "NOMINAL_DATE": after.acquired,
            "SOURCE_DATES": ",".join(acquisition[str(after.acquired)]["source_dates"]),
        },
        scale_offset=(1 / radiometry.QUANTIFICATION, -radiometry.L2A_OFFSET / radiometry.QUANTIFICATION),
    )
    nearest = Resampling.nearest
    write_raster(
        out / "radiometric_zones.tif", zones, grid, -1, [f"zone id ({zone_info['source']})"], overview_resampling=nearest
    )
    write_raster(
        out / "stripe_zones_detected.tif", stripes.zones, grid, -1, ["data-driven stripe zone id"], overview_resampling=nearest
    )

    # ---- Part 2b: change detection ------------------------------------------------------------
    rule = cfg.rule
    nb = len(cfg.bands)
    base_prob = methods.euclidean_baseline(r1, r2, valid)
    base_bin, otsu_t = vectorize.binary_from_otsu(base_prob, valid, rule, px_m)

    cva = methods.robust_cva(r1, r2n, zones, pif, norm.noise_pif, rule.far_low, cfg.noise)
    cva_bin = vectorize.binary_from_d2(cva.d2, valid, seam_dist, nb, rule, px_m)

    low, high = rule.thresholds(nb)
    tags = {
        "NOMINAL_DATE_BEFORE": before.acquired,
        "NOMINAL_DATE_AFTER": after.acquired,
        "SOURCE_DATES_BEFORE": ",".join(acquisition[str(before.acquired)]["source_dates"]),
        "SOURCE_DATES_AFTER": ",".join(acquisition[str(after.acquired)]["source_dates"]),
    }
    nodata_u8 = 255  # binary masks: 0 = no change, 1 = change, 255 = outside the valid area (not "no change")

    def binary(mask: np.ndarray) -> np.ndarray:
        return np.where(valid, mask, nodata_u8).astype(np.uint8)

    write_raster(
        out / "change_map_baseline.tif",
        np.where(valid, np.round(np.nan_to_num(base_prob) * 254), nodata_u8).astype(np.uint8),
        grid,
        nodata_u8,
        ["euclidean distance, min-max scaled 0-254"],
        tags,
    )
    write_raster(
        out / "change_binary_baseline.tif",
        binary(base_bin),
        grid,
        nodata_u8,
        ["1 = change (Otsu)"],
        tags,
        overview_resampling=nearest,
    )
    write_raster(
        out / "change_map.tif",
        np.where(valid, np.round(np.log10(np.clip(cva.d2, 1e-6, None)), 2), -9999).astype(np.float32),  # 0.01 log units << noise
        grid,
        -9999,
        ["change intensity: log10 Mahalanobis d2"],
        {**tags, "D2_THRESHOLD_LOW": round(low, 3), "LOG10_D2_THRESHOLD_LOW": round(float(np.log10(low)), 3)},
    )
    write_raster(
        out / "change_significance.tif",
        np.where(valid, np.round(cva.neglog10_p, 2), -9999).astype(np.float32),
        grid,
        -9999,
        [f"-log10 no-change p-value, chi2.logsf(d2, {nb}), capped at {methods.SIGNIFICANCE_CAP:g}"],
        tags,
    )
    write_raster(
        out / "change_binary.tif",
        binary(cva_bin),
        grid,
        nodata_u8,
        [f"1 = change (robust CVA, hysteresis nominal FAR {rule.far_low:g}/{rule.far_high:g})"],
        tags,
        overview_resampling=nearest,
    )

    # ---- Part 3: vectorize, interpret, store --------------------------------------------------
    traces = [np.trace(np.array(c)) / nb for covs in cva.noise_cov.values() for c in covs]
    noise_sigma = float(np.sqrt(np.mean(traces)))
    disturbed = vectorize.disturbed_footprint(r1, valid)
    layers = {
        "d2": cva.d2,
        "neglog10_p": cva.neglog10_p,
        "delta": cva.delta,
        "bright_before": r1.mean(axis=0),
        "bright_after": r2n.mean(axis=0),
        "zone": np.clip(zones, 0, None),
        "disturbed": disturbed,
        "valid": valid,
    }
    features = {"robust_cva": vectorize.polygonize(cva_bin, grid, layers, noise_sigma, high, low)}
    # The baseline's score is a min-max scaled distance, not a chi-square statistic: no confidence / significance for it.
    base_layers = {k: v for k, v in layers.items() if k not in ("d2", "neglog10_p")}
    features["euclidean_baseline"] = vectorize.polygonize(base_bin, grid, base_layers, noise_sigma, high, low)

    outlines = register.outlines(register.load(cfg.facility_register), r1, r2n, valid, grid)
    mine = register.footprint(outlines)
    polys = features["robust_cva"]
    irules = interpret.InterpretRules()
    if len(polys):
        evidence = interpret.pixel_evidence(
            polys, grid.transform, valid.shape, r1, r2n, seam_dist, rule.seam_buffer_px(px_m) + irules.seam_margin_px
        )
        context = interpret.facility_context(polys, outlines, mine, irules)
        labels = interpret.label(polys, evidence, context, irules)
        polys[["change_type", "brief_category", "explanation", "facility_id"]] = labels
        plain = interpret.describe(polys, labels, evidence, outlines)
        polys[list(plain.columns)] = plain
    fac_changes = register.change_by_facility(outlines, polys)

    px = grid.pixel_area_m2
    in_register = rasterio.features.geometry_mask(outlines.geometry, valid.shape, grid.transform, invert=True)
    in_mine = rasterio.features.geometry_mask([mine], valid.shape, grid.transform, invert=True)
    summary = {
        "grid": {"crs": str(grid.crs), "width": grid.width, "height": grid.height, "pixel_m": px_m},
        "valid_fraction": round(float(valid.mean()), 4),
        "acquisition": acquisition,
        "boa_offset": offsets,
        "zones": zone_info,
        "stripes": {  # the data-driven detection
            "angle_deg": round(stripes.angle_deg, 2),
            "explained_variance": round(stripes.explained_variance, 3),
            "breakpoints_px": [round(b, 1) for b in stripes.breakpoints],
            "zone_blue_offset": [round(o, 4) for o in stripes.zone_offsets],
        },
        "registration_shift_px": shifts,
        "calibration": [asdict(c) for c in norm.calibration],
        "pif_fraction": round(float(pif[valid].mean()), 3),
        "noise_pif_fraction": round(float(norm.noise_pif[valid].mean()), 3),
        "noise_sigma_reflectance": round(noise_sigma, 5),
        "noise_calibration": cva.calibration,
        "cva_changed_area_ha_by_noise_model": {  # same seams and cleanup, only the statistic differs
            "previous_design_nominal_thresholds": _area_ha(
                vectorize.binary_from_d2(cva.d2_previous, valid, seam_dist, nb, rule, px_m), px
            ),
            "brightness_aware_nominal_thresholds": _area_ha(
                vectorize.binary_from_d2(cva.d2_model, valid, seam_dist, nb, rule, px_m), px
            ),
            "final": _area_ha(cva_bin, px),
        },
        "thresholds": {"baseline_otsu": round(otsu_t, 4), "cva_d2_low": round(low, 2), "cva_d2_high": round(high, 2)},
        "seam_buffer_m": rule.seam_buffer_m,
        "valid_share_within_seam_buffer": round(float((valid & (seam_dist < rule.seam_buffer_px(px_m))).sum() / valid.sum()), 4),
        "changed_area_ha": {
            "euclidean_baseline": _area_ha(base_bin, px),
            "robust_cva": _area_ha(cva_bin, px),
        },
        "changed_fraction_by_zone": {
            m: [round(float(b[zones == z].mean()), 4) for z in range(zone_info["n_zones"])]
            for m, b in [("euclidean_baseline", base_bin), ("robust_cva", cva_bin)]
        },
        "n_features": {k: len(v) for k, v in features.items()},
        "agreement_cva_vs_baseline_iou": round(float((cva_bin & base_bin).sum() / max((cva_bin | base_bin).sum(), 1)), 3),
        "footprint": {
            "brightness_heuristic": _footprint_share(disturbed, cva_bin, valid),
            "register_mine_classes": _footprint_share(in_mine, cva_bin, valid),
            "register_all_facilities": _footprint_share(in_register, cva_bin, valid),
        },
        "change_type_ha": {
            t: round(float(a) / 1e4, 1)
            for t, a in polys.groupby("change_type").area_m2.sum().sort_values(ascending=False).items()
        }
        if len(polys)
        else {},
        "facility_changed_ha": dict(zip(fac_changes.facility_id, fac_changes.changed_ha, strict=True)),
    }

    run_rows, tables = [], []
    params = {
        "rule": asdict(rule),
        "noise": asdict(cfg.noise),
        "zones": zone_info["source"],
        "stripe": cfg.stripe,
        "bands": list(cfg.bands),
        "acquisition": acquisition,
    }
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
    db.write_sqlite(cfg.sqlite_path, feats, runs, outlines, fac_changes)
    feats.to_crs(4326).to_file(
        out / "change_features.geojson", driver="GeoJSON", layer_options={"RFC7946": "YES", "COORDINATE_PRECISION": 6}
    )
    if cfg.database_url:
        db.write_postgis(cfg.database_url, feats, runs)
        log.info("Wrote %d features to PostGIS", len(feats))

    aoi = gpd.read_file(cfg.aoi).to_crs(grid.crs)
    summary["runtime_s"] = round(time.perf_counter() - t0, 1)
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log.info("Changed area (ha): %s", summary["changed_area_ha"])
    return Products(
        before=before, after=after, valid=valid, refl_before=r1, refl_after=r2, refl_after_norm=r2n, stripes=stripes,
        zones=zones, zone_source=zone_info["source"], seam_dist=seam_dist, pif=pif, baseline_prob=base_prob,
        baseline_binary=base_bin, cva=cva, cva_binary=cva_bin, features=features, facilities=outlines,
        facility_changes=fac_changes, date_labels=date_labels, aoi=aoi, summary=summary,
    )  # fmt: skip
