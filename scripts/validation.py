"""Accuracy assessment of this map and the provided baseline from one stratified random sample (Olofsson et al. 2014).

    uv run python scripts/validation.py sample   # draw points, cut chips, write blind labelling sheets
    uv run python scripts/validation.py score    # combine labeller files, adjudicate, estimate accuracy and area with 95 % CIs
    uv run python scripts/validation.py sample true_passes   # the same for the true single-pass pair (scripts/real_dates.py)

Strata cross the two maps (both change, this map only, baseline only, neither), so one sample scores both maps without bias
(Stehman 2014). Reference labels come from visual interpretation of before/after chips of the same Sentinel-2 data by two
independent labellers; disagreements are adjudicated. This is model-assisted visual interpretation, not field truth.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parents[1]
PAIRS = {  # pair -> (outputs folder, before stack, after stack, validation folder)
    "provided": ("data/processed", "sentinel2_20230812_stack.tif", "sentinel2_20230902_stack.tif", "data/validation"),
    "true_passes": (
        "data/experiments/2023-08-08_2023-09-02",
        "sentinel2_20230808_stack.tif",
        "sentinel2_20230902_stack.tif",
        "data/validation/true_passes",
    ),
}
PAIR = sys.argv[2] if __name__ == "__main__" and len(sys.argv) > 2 else "provided"
PROC, OUT = ROOT / PAIRS[PAIR][0], ROOT / PAIRS[PAIR][3]
BEFORE, AFTER = PAIRS[PAIR][1], PAIRS[PAIR][2]
SEED = 20231202
N_PER_STRATUM = 50
CHIP = 48  # pixels (480 m); the label is for the 3 x 3 pixel square in the centre
ZOOM = 5
PER_SHEET = 10
STRATA = {1: "both change", 2: "this map only", 3: "baseline only", 4: "neither"}


def load_maps() -> tuple[np.ndarray, np.ndarray, rasterio.Affine, object]:
    with rasterio.open(PROC / "change_binary.tif") as d:
        ours, transform, crs = d.read(1), d.transform, d.crs
    with rasterio.open(PROC / "change_binary_baseline.tif") as d:
        base = d.read(1)
    return ours, base, transform, crs


def strata_of(ours: np.ndarray, base: np.ndarray) -> np.ndarray:
    s = np.zeros(ours.shape, np.uint8)
    valid = (ours != 255) & (base != 255)
    o, b = (ours == 1) & valid, (base == 1) & valid
    s[o & b], s[o & ~b], s[~o & b & valid], s[~o & ~b & valid] = 1, 2, 3, 4
    return s


def rgb(path: Path) -> np.ndarray:
    with rasterio.open(path) as d:
        a = d.read().astype("float32")
    return np.stack([a[2], a[1], a[0]], -1)


def stretch(chip: np.ndarray) -> np.ndarray:
    """Stretch each chip on its own, so whole-scene brightness offsets (seams, haze) do not read as change."""
    lo, hi = np.percentile(chip[chip > 0], [2, 98]) if (chip > 0).any() else (0, 1)
    return np.clip((chip - lo) / max(hi - lo, 1e-6), 0, 1)


def chip_image(img: np.ndarray, r: int, c: int) -> Image.Image:
    h = CHIP // 2
    pad = np.pad(img, ((h, h), (h, h), (0, 0)))
    a = stretch(pad[r : r + CHIP, c : c + CHIP])
    im = Image.fromarray((a * 255).astype("uint8")).resize((CHIP * ZOOM, CHIP * ZOOM), Image.NEAREST)
    d = ImageDraw.Draw(im)
    x0 = (h - 1) * ZOOM
    d.rectangle([x0 - 1, x0 - 1, x0 + 3 * ZOOM, x0 + 3 * ZOOM], outline=(255, 0, 255), width=2)
    return im


def sample() -> None:
    ours, base, transform, crs = load_maps()
    s = strata_of(ours, base)
    rng = np.random.default_rng(SEED)
    rows = []
    for k in STRATA:
        rr, cc = np.nonzero(s == k)
        idx = rng.choice(len(rr), N_PER_STRATUM, replace=False)
        rows += [(k, int(rr[i]), int(cc[i])) for i in idx]
    order = rng.permutation(len(rows))  # labellers see a shuffled order with neutral ids
    points = []
    for n, i in enumerate(order, 1):
        k, r, c = rows[i]
        x, y = rasterio.transform.xy(transform, r, c)
        points.append(
            {
                "id": f"P{n:03d}",
                "stratum": k,
                "row": r,
                "col": c,
                "x": x,
                "y": y,
                "ours": int(ours[r, c] == 1),
                "base": int(base[r, c] == 1),
            }
        )

    feats = gpd.read_file(PROC / "change_features.geojson")
    feats = feats[feats.method == "robust_cva"].to_crs(crs)
    pts = gpd.GeoDataFrame(points, geometry=[Point(p["x"], p["y"]) for p in points], crs=crs)
    joined = gpd.sjoin_nearest(pts, feats[["change_type", "geometry"]], how="left", max_distance=15)
    joined = joined[~joined.index.duplicated()]
    for p, t in zip(points, joined["change_type"], strict=False):
        p["ours_type"] = t if p["ours"] and isinstance(t, str) else ""

    OUT.mkdir(parents=True, exist_ok=True)
    counts = {k: int((s == k).sum()) for k in STRATA}
    (OUT / "strata.json").write_text(json.dumps({"seed": SEED, "pixel_counts": counts, "names": STRATA}, indent=2))
    with open(OUT / "sample.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(points[0]))
        w.writeheader()
        w.writerows(points)

    before, after = rgb(PROC / BEFORE), rgb(PROC / AFTER)
    sheets = OUT / "sheets"
    sheets.mkdir(exist_ok=True)
    font = ImageFont.load_default(size=22)
    side = CHIP * ZOOM
    for start in range(0, len(points), PER_SHEET):
        batch = points[start : start + PER_SHEET]
        sheet = Image.new("RGB", (2 * (2 * side + 20) + 20, len(batch[::2]) * (side + 40) + 10), "white")
        d = ImageDraw.Draw(sheet)
        for j, p in enumerate(batch):
            x0, y0 = 10 + (j % 2) * (2 * side + 30), 10 + (j // 2) * (side + 40)
            d.text((x0, y0), f"{p['id']}   before | after", fill="black", font=font)
            sheet.paste(chip_image(before, p["row"], p["col"]), (x0, y0 + 30))
            sheet.paste(chip_image(after, p["row"], p["col"]), (x0 + side + 4, y0 + 30))
        sheet.save(sheets / f"sheet_{start // PER_SHEET + 1:02d}.png")
    print(f"{len(points)} points, strata pixels {counts}; sheets in {sheets}")


def read_labels(path: Path) -> dict[str, dict]:
    with open(path) as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def stratified(y: np.ndarray, x: np.ndarray | None, strata: np.ndarray, weights: dict[int, float]) -> tuple[float, float]:
    """Stehman (2014): mean of y (x is None) or ratio y/x, with its standard error, under stratified random sampling."""

    def mean(v, h):
        return v[strata == h].mean()

    ybar = sum(weights[h] * mean(y, h) for h in weights)
    if x is None:
        var = sum(weights[h] ** 2 * y[strata == h].var(ddof=1) / (strata == h).sum() for h in weights)
        return ybar, float(np.sqrt(var))
    xbar = sum(weights[h] * mean(x, h) for h in weights)
    r = ybar / xbar
    var = 0.0
    for h in weights:
        m = strata == h
        yh, xh = y[m], x[m]
        cov = np.cov(yh, xh, ddof=1)[0, 1] if m.sum() > 1 else 0.0
        var += weights[h] ** 2 * (yh.var(ddof=1) + r**2 * xh.var(ddof=1) - 2 * r * cov) / m.sum()
    return r, float(np.sqrt(var / xbar**2))


def score() -> None:
    with open(OUT / "sample.csv") as f:
        pts = list(csv.DictReader(f))
    a, b = read_labels(OUT / "labels_a.csv"), read_labels(OUT / "labels_b.csv")
    adj = read_labels(OUT / "labels_adjudicated.csv") if (OUT / "labels_adjudicated.csv").exists() else {}
    meta = json.loads((OUT / "strata.json").read_text())
    counts = {int(k): v for k, v in meta["pixel_counts"].items()}
    total = sum(counts.values())
    weights = {k: v / total for k, v in counts.items()}

    ref, la, lb, unresolved = [], [], [], []
    for p in pts:
        ca, cb = int(a[p["id"]]["change"]), int(b[p["id"]]["change"])
        la.append(ca)
        lb.append(cb)
        if ca == cb:
            ref.append(ca)
        elif p["id"] in adj:
            ref.append(int(adj[p["id"]]["change"]))
        else:
            ref.append(-1)
            unresolved.append(p["id"])
    if unresolved:
        sys.exit(f"{len(unresolved)} disagreements need adjudication in labels_adjudicated.csv: {', '.join(unresolved)}")

    la, lb, ref = np.array(la), np.array(lb), np.array(ref)
    po = (la == lb).mean()
    pe = la.mean() * lb.mean() + (1 - la.mean()) * (1 - lb.mean())
    kappa = (po - pe) / (1 - pe)
    strata = np.array([int(p["stratum"]) for p in pts])
    result = {"n": len(pts), "labeller_agreement": round(float(po), 3), "cohen_kappa": round(float(kappa), 3), "maps": {}}
    aoi_km2 = total * 100 / 1e6
    for name in ("ours", "base"):
        m = np.array([int(p[name]) for p in pts])
        oa = stratified((m == ref).astype(float), None, strata, weights)
        ua = stratified((m & ref).astype(float), m.astype(float), strata, weights)
        pa = stratified((m & ref).astype(float), ref.astype(float), strata, weights)
        area = stratified(ref.astype(float), None, strata, weights)
        result["maps"][name] = {
            "overall_accuracy": oa,
            "user_accuracy_change": ua,
            "producer_accuracy_change": pa,
            "mapped_change_km2": float(
                sum(counts[k] for k in counts if (k in (1, 2) if name == "ours" else k in (1, 3))) * 100 / 1e6
            ),
        }
    result["reference_change_km2"] = (area[0] * aoi_km2, area[1] * aoi_km2)
    types = [(p["ours_type"], r) for p, r in zip(pts, ref, strict=False) if p["ours"] == "1" and p["ours_type"]]
    result["ours_change_points_confirmed_by_type"] = {
        t: [sum(1 for tt, r in types if tt == t and r), sum(1 for tt, _ in types if tt == t)]
        for t in sorted({t for t, _ in types})
    }
    (OUT / "accuracy.json").write_text(json.dumps(result, indent=2))
    for name, r in result["maps"].items():
        f = {k: (f"{v[0]:.2f} ± {1.96 * v[1]:.2f}" if isinstance(v, tuple) else v) for k, v in r.items()}
        print(name, f)
    ref_km2, ref_se = result["reference_change_km2"]
    print(f"reference change km²: {ref_km2:.2f} ± {1.96 * ref_se:.2f}")
    print(f"labeller agreement {po:.2f}, kappa {kappa:.2f}")


if __name__ == "__main__":
    {"sample": sample, "score": score}[sys.argv[1]]()
