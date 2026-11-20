"""Accuracy assessment by stratified random sampling (Olofsson et al. 2014, Remote Sens. Environ. 148:42-57).

There is no reference data, so a human labels a sample of pixels as change / no change by comparing the
before and after RGB image chips. To compare our map with the provided baseline fairly, the strata are the
four combinations of the two binary maps:

    S11: both say change      S10: ours change, baseline not
    S01: baseline change only S00: neither

An equal number of points is drawn per stratum (rare strata would otherwise get no samples), and every
estimate is weighted by the stratum's share of the AOI, so the accuracies and areas refer to the whole map:
overall accuracy, user's accuracy (precision) and producer's accuracy (recall) of "change", and the
error-adjusted area of change with a 95 % confidence interval, for both maps.

    uv run python -m s2cd.validation sample   # -> docs/web/validation/sample.json + image chips
    (label in the app: /label.html, then save labels.json to docs/web/validation/)
    uv run python -m s2cd.validation assess   # -> data/processed/validation/accuracy.json + table
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer

from . import radiometry
from .io import load_scene

OUT = Path("docs/web/validation")
RESULT = Path("data/processed/validation")
STRATA = ("S11", "S10", "S01", "S00")
PER_STRATUM = 30
CHIP_PX = 48  # 480 m window around the point
SEED = 2023


def _maps(processed: Path = Path("data/processed")) -> tuple[np.ndarray, np.ndarray, np.ndarray, rasterio.Affine, str]:
    with rasterio.open(processed / "change_binary.tif") as s:
        ours, transform, crs = s.read(1), s.transform, s.crs
    with rasterio.open(processed / "change_binary_baseline.tif") as s:
        base = s.read(1)
    valid = (ours != 255) & (base != 255)
    return ours == 1, base == 1, valid, transform, str(crs)


def strata_map(ours: np.ndarray, base: np.ndarray) -> np.ndarray:
    """0..3 for S11, S10, S01, S00."""
    return np.select([ours & base, ours & ~base, ~ours & base], [0, 1, 2], 3)


def _chip(refl: np.ndarray, row: int, col: int) -> Image.Image:
    h = CHIP_PX // 2
    win = np.nan_to_num(np.moveaxis(refl[[2, 1, 0], max(row - h, 0) : row + h, max(col - h, 0) : col + h], 0, -1))
    # Per-chip 2-98 % stretch: the after date is hazier, and a shared stretch would make it look "changed" everywhere.
    lo, hi = np.percentile(win, [2, 98])
    img = (np.clip((win - lo) / max(hi - lo, 1e-6), 0, 1) ** 0.9 * 255).astype(np.uint8)
    return Image.fromarray(img).resize((CHIP_PX * 4, CHIP_PX * 4), Image.NEAREST)


def sample(per_stratum: int = PER_STRATUM, seed: int = SEED) -> dict:
    ours, base, valid, transform, crs = _maps()
    s = strata_map(ours, base)
    rng = np.random.default_rng(seed)
    before = load_scene(Path("inputs/data/sentinel2_20230812"))
    after = load_scene(Path("inputs/data/sentinel2_20230902"))
    rb = radiometry.to_reflectance(before.dn, before.valid & after.valid)
    ra = radiometry.to_reflectance(after.dn, before.valid & after.valid)
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    (OUT / "chips").mkdir(parents=True, exist_ok=True)
    points, weights = [], {}
    margin = CHIP_PX // 2
    inner = np.zeros_like(valid)
    inner[margin:-margin, margin:-margin] = True
    for k, name in enumerate(STRATA):
        rows, cols = np.nonzero((s == k) & valid & inner)
        weights[name] = float(((s == k) & valid).sum() / valid.sum())
        pick = rng.choice(len(rows), size=min(per_stratum, len(rows)), replace=False)
        for i in pick:
            r, c = int(rows[i]), int(cols[i])
            x, y = rasterio.transform.xy(transform, r, c)
            lon, lat = to_ll.transform(x, y)
            pid = f"{name}-{len(points):03d}"
            _chip(rb, r, c).save(OUT / "chips" / f"{pid}_before.png", optimize=True)
            _chip(ra, r, c).save(OUT / "chips" / f"{pid}_after.png", optimize=True)
            points.append({"id": pid, "stratum": name, "row": r, "col": c, "x": x, "y": y, "lon": lon, "lat": lat})
    rng.shuffle(points)  # label in random order, blind to the stratum
    doc = {
        "design": "stratified random, equal allocation, strata = ours x baseline",
        "strata_weights": weights,
        "chip_px": CHIP_PX,
        "chip_m": CHIP_PX * 10,
        "note": (
            "Label each point by comparing the before and after chips: "
            "did the land cover at the crosshair change between the dates?"
        ),
        "points": [{k: v for k, v in p.items() if k != "stratum"} | {"stratum": p["stratum"]} for p in points],
    }
    (OUT / "sample.json").write_text(json.dumps(doc, indent=1))
    return doc


def _estimates(map_says_change: dict, labels: dict, weights: dict, n_by_stratum: dict) -> dict:
    """Stratified estimators (Olofsson 2014, eqs. 1-12) for a binary map given labelled points."""
    # Proportion of reference "change" in each stratum, and the mapped class of each stratum.
    p_ref = {h: np.mean([labels[i] for i in ids]) if ids else 0.0 for h, ids in n_by_stratum.items()}
    W = weights
    mapped_change = [h for h in W if map_says_change[h]]
    agree = sum(W[h] * (p_ref[h] if map_says_change[h] else 1 - p_ref[h]) for h in W)
    ref_change_area = sum(W[h] * p_ref[h] for h in W)  # proportion of AOI that truly changed
    map_change_area = sum(W[h] for h in mapped_change)
    users = sum(W[h] * p_ref[h] for h in mapped_change) / map_change_area if map_change_area else float("nan")
    producers = sum(W[h] * p_ref[h] for h in mapped_change) / ref_change_area if ref_change_area else float("nan")
    # Standard error of the estimated change proportion (stratified).
    se = np.sqrt(sum(W[h] ** 2 * p_ref[h] * (1 - p_ref[h]) / max(len(ids) - 1, 1) for h, ids in n_by_stratum.items()))
    return {
        "overall_accuracy": round(agree, 3),
        "users_accuracy_change": round(users, 3),
        "producers_accuracy_change": round(producers, 3),
        "change_share_reference": round(ref_change_area, 4),
        "change_share_reference_ci95": [round(ref_change_area - 1.96 * se, 4), round(ref_change_area + 1.96 * se, 4)],
        "change_share_mapped": round(map_change_area, 4),
    }


def assess(labels_path: Path = OUT / "labels.json") -> dict:
    doc = json.loads((OUT / "sample.json").read_text())
    labels_raw = json.loads(Path(labels_path).read_text())  # {point_id: "change" | "no_change" | "unsure"}
    by_id = {p["id"]: p for p in doc["points"]}
    used = {pid: lab == "change" for pid, lab in labels_raw.items() if lab in ("change", "no_change") and pid in by_id}
    n_by_stratum = {h: [pid for pid in used if by_id[pid]["stratum"] == h] for h in STRATA}
    labels = {pid: float(v) for pid, v in used.items()}
    W = doc["strata_weights"]
    ours = _estimates({"S11": True, "S10": True, "S01": False, "S00": False}, labels, W, n_by_stratum)
    base = _estimates({"S11": True, "S10": False, "S01": True, "S00": False}, labels, W, n_by_stratum)
    with rasterio.open("data/processed/change_binary.tif") as s:
        valid_px = int((s.read(1) != 255).sum())
        px_ha = abs(s.transform.a * s.transform.e) / 1e4
    valid_ha = valid_px * px_ha
    for est in (ours, base):
        est["change_area_reference_ha"] = round(est["change_share_reference"] * valid_ha)
        est["change_area_reference_ha_ci95"] = [round(v * valid_ha) for v in est["change_share_reference_ci95"]]
        est["change_area_mapped_ha"] = round(est["change_share_mapped"] * valid_ha)
    result = {
        "n_labelled": len(used),
        "n_unsure_or_missing": len(by_id) - len(used),
        "per_stratum_n": {h: len(v) for h, v in n_by_stratum.items()},
        "per_stratum_change_rate": {
            h: round(float(np.mean([labels[i] for i in v])), 3) if v else None for h, v in n_by_stratum.items()
        },
        "robust_cva": ours,
        "euclidean_baseline": base,
        "reference": "Olofsson et al. 2014, Remote Sens. Environ. 148:42-57",
    }
    RESULT.mkdir(parents=True, exist_ok=True)
    (RESULT / "accuracy.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sample"
    print(json.dumps(sample() if cmd == "sample" else assess(), indent=1)[:2000])
