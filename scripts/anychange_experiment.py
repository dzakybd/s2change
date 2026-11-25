"""Zero-shot check: AnyChange (torchange, NeurIPS 2024; Segment Anything features) against the calibrated robust CVA.

    uv run --with torchange --with torch --with torchvision --with huggingface_hub --with scikit-image \
        python scripts/anychange_experiment.py          # about 1 min per setting on a laptop CPU

Both dates get the same 8-bit stretch (the after date is the per-zone normalised stack, the same input the CVA uses). SAM
resizes the whole AOI to 1024 px (about 16 m pixels). Writes the masks and agreement numbers to data/validation/anychange/
(masks.npz, stats.json); scripts/fig_anychange_vs_cva.py draws docs/figures/15_anychange_vs_cva.png from them. The SAM ViT-B
weights come from the Hugging Face hub. Result on this pair: report.md section 1.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import rasterio.features
from huggingface_hub import hf_hub_download
from torchange.models.segment_any_change import AnyChange
from torchange.models.segment_any_change.segment_anything.utils.amg import rle_to_mask

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
SETTINGS = {"145 (strict)": dict(change_confidence_threshold=145), "auto (Otsu)": dict(auto_threshold=True)}
OUT = ROOT / "data/validation/anychange"


def reflectance(name: str) -> np.ndarray:
    with rasterio.open(PROC / f"{name}.tif") as s:
        return (s.read().astype("float32") - 1000) / 10000


def main() -> None:
    r1, r2 = reflectance("sentinel2_20230812_stack"), reflectance("sentinel2_20230902_stack_normalized")
    valid = (r1[0] > -0.1) & (r2[0] > -0.1)
    lo = [np.percentile(r1[b][valid], 2) for b in range(3)]
    hi = [np.percentile(r1[b][valid], 98) for b in range(3)]

    def to8(r):
        rgb = np.stack([np.clip((r[b] - lo[b]) / (hi[b] - lo[b]), 0, 1) for b in (2, 1, 0)], -1)
        return (rgb**0.8 * 255).astype(np.uint8)

    img1, img2 = to8(r1), to8(r2)
    with rasterio.open(PROC / "change_binary.tif") as s:
        cva, transform, crs = s.read(1) == 1, s.transform, s.crs
    fac = gpd.read_file(ROOT / "docs/web/facilities.geojson").to_crs(crs)
    in_fac = rasterio.features.geometry_mask(fac.geometry, cva.shape, transform, invert=True)

    weights = hf_hub_download("ybelkada/segment-anything", "checkpoints/sam_vit_b_01ec64.pth")
    model = AnyChange("vit_b", sam_checkpoint=weights)
    model.make_mask_generator(points_per_side=32, stability_score_thresh=0.95)

    def stats(m):
        both = (m & cva).sum()
        return {
            "changed_km2": round(m.sum() * 100 / 1e6, 2),
            "share_inside_facilities": round(float((m & in_fac).sum() / max(m.sum(), 1)), 3),
            "iou_with_cva": round(float(both / max((m | cva).sum(), 1)), 3),
            "share_of_cva_found": round(float(both / cva.sum()), 3),
        }

    masks, out = {"cva": cva}, {"cva": stats(cva)}
    for label, hp in SETTINGS.items():
        model.set_hyperparameters(use_normalized_feature=True, bitemporal_match=True, **hp)
        cm, _, _ = model.forward(img1, img2)
        m = np.zeros(cva.shape, bool)
        for rle in cm["rles"]:
            m |= rle_to_mask(rle)
        m &= valid
        out[f"anychange {label}"] = stats(m)
        masks[label] = m
    print(json.dumps(out, indent=1))
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT / "masks.npz", **masks)
    (OUT / "stats.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
