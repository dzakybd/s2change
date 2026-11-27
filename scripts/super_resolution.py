"""2.5 m super-resolved true colour of the two analysed pairs, for the map only (never for the change analysis).

    uv run --extra stac --with sen2sr --with mlstac python scripts/super_resolution.py

SEN2SR (ESA OpenSR, CC0) lifts Sentinel-2 10 m RGB + NIR to 2.5 m and constrains the result to agree with the 10 m input
when averaged back down; the finer detail is still a model estimate. The analysis uses B02/B03/B04 only: B08 (NIR) is
read here because the model needs it, from the same source scenes as each analysed image:

* provided pair: the two passes behind each mosaic, picked per pixel by data/processed/provenance/source_scene.tif;
* true passes:   the scenes of data/experiments/scenes (scripts/real_dates.py), in the order they were mosaicked.

Writes sr_before.webp / sr_after.webp next to each pair's overlays (docs/web, docs/web/true_passes) and adds them to its
manifest.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

from s2cd import provenance as pv
from s2cd import viz
from s2cd.io import Grid, load_scene
from s2cd.radiometry import L2A_OFFSET, QUANTIFICATION

MODEL_URL = "https://huggingface.co/tacofoundation/sen2sr/resolve/main/SEN2SRLite/NonReference_RGBN_x4/mlm.json"
MODEL_DIR = Path("data/cache/sen2sr/SEN2SRLite_RGBN")
STAC_CACHE = Path("data/cache/stac")
SCALE = 4
PATCH, STRIDE = 128, 96
PROVENANCE = Path("data/processed/provenance")
PAIRS = {
    Path("docs/web"): {
        "before": (Path("inputs/data/sentinel2_20230812"), 0),
        "after": (Path("inputs/data/sentinel2_20230902"), 1),
    },
    Path("docs/web/true_passes"): {
        "before": (Path("data/experiments/scenes/sentinel2_20230808"), None),
        "after": (Path("data/experiments/scenes/sentinel2_20230902"), None),
    },
}


def load_model():
    import mlstac
    import torch

    if not (MODEL_DIR / "mlm.json").exists():
        mlstac.download(file=MODEL_URL, output_dir=str(MODEL_DIR))
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    return mlstac.load(str(MODEL_DIR)).compiled_model(device=device), device


def items_by_id(ids: list[str]) -> dict:
    found = pv.open_catalog().search(collections=[pv.COLLECTION], ids=ids).item_collection()
    return {it.id: it for it in found}


def nir(folder: Path, provenance_band: int | None, grid: Grid) -> np.ndarray:
    """B08 DN on the grid, from the same scene as each pixel of the analysed RGB."""
    fetch = pv.Fetcher(grid, STAC_CACHE)
    out = np.zeros((grid.height, grid.width), np.uint16)
    if provenance_band is not None:  # a provided mosaic: the source index of every pixel
        summary = json.loads((PROVENANCE / "provenance_summary.json").read_text())
        date = summary["dates"][provenance_band]
        ids = [s["id"] for s in summary["stac"][date]["sources"]]
        with rasterio.open(PROVENANCE / "source_scene.tif") as ds:
            src = ds.read(provenance_band + 1)
        items = items_by_id(ids)
        for k, item_id in enumerate(ids):
            out[src == k] = fetch.get(items[item_id], "B08")[src == k]
    else:  # a fetched scene: the items in mosaic order, first valid pixel wins (as s2cd.scenes.fetch_scene)
        with rasterio.open(folder / "B04.tif") as ds:
            ids = ds.tags()["SOURCE_ITEMS"].split(",")
        items = items_by_id(ids)
        for item_id in ids:
            empty = out == 0
            out[empty] = fetch.get(items[item_id], "B08")[empty]
    return out


def _offsets(n: int) -> list[int]:
    return sorted({*range(0, n - PATCH + 1, STRIDE), n - PATCH})


def tiled(model, x, batch: int = 16) -> np.ndarray:
    """Run the 128 px model over (C, H, W) in overlapping patches; keep each patch's centre (sen2sr.predict_large
    mishandles the last partial patch). Overlaps of STRIDE < PATCH hide the seams; image edges keep the patch border."""
    import torch

    _, h, w = x.shape
    out = np.zeros((x.shape[0], h * SCALE, w * SCALE), np.float32)
    m = (PATCH - STRIDE) // 2  # margin dropped on inner patch sides, in input pixels
    jobs = [(y, xo) for y in _offsets(h) for xo in _offsets(w)]
    for i in range(0, len(jobs), batch):
        chunk = jobs[i : i + batch]
        pred = model(torch.stack([x[:, y : y + PATCH, xo : xo + PATCH] for y, xo in chunk])).cpu().numpy()
        for (y, xo), p in zip(chunk, pred, strict=True):
            t, b = (0 if y == 0 else m), (PATCH if y + PATCH == h else PATCH - m)
            le, r = (0 if xo == 0 else m), (PATCH if xo + PATCH == w else PATCH - m)
            ys, xs = slice((y + t) * SCALE, (y + b) * SCALE), slice((xo + le) * SCALE, (xo + r) * SCALE)
            out[:, ys, xs] = p[:, t * SCALE : b * SCALE, le * SCALE : r * SCALE]
    return out


def super_resolve(model, device: str, folder: Path, provenance_band: int | None) -> tuple[np.ndarray, np.ndarray, Grid]:
    import torch

    scene = load_scene(folder)  # B02, B03, B04
    grid = scene.grid
    dn = np.concatenate([scene.dn[[2, 1, 0]], nir(folder, provenance_band, grid)[None]])  # B04, B03, B02, B08
    refl = np.clip((dn.astype(np.float32) - L2A_OFFSET) / QUANTIFICATION, 0, None)
    refl[:, ~scene.valid] = 0
    with torch.no_grad():
        sr = tiled(model, torch.from_numpy(refl).to(device))
    valid = np.repeat(np.repeat(scene.valid, SCALE, 0), SCALE, 1)
    t = grid.transform
    fine = Grid(grid.crs, Affine(t.a / SCALE, t.b, t.c, t.d, t.e / SCALE, t.f), grid.width * SCALE, grid.height * SCALE)
    return sr[[2, 1, 0]], valid, fine  # back to B02, B03, B04 for viz.rgb


def main() -> None:
    model, device = load_model()
    for web, sides in PAIRS.items():
        manifest_path = web / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for side, (folder, band) in sides.items():
            sr, valid, fine = super_resolve(model, device, folder, band)
            key = f"sr_{side}"
            bounds = viz.warp_webp(viz.rgba(sr, valid), fine, web / f"{key}.webp")
            label = manifest["date_labels"][side] if "date_labels" in manifest else side
            title = f"Super-resolved 2.5 m (SEN2SR), {label}"
            manifest["rasters"][key] = {"url": f"{key}.webp", "label": title, "bounds": bounds}
            print(f"{web / key}.webp  {(web / f'{key}.webp').stat().st_size / 1e6:.1f} MB", flush=True)
        manifest_path.write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
