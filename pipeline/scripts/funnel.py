"""How much flagged area each step of the method removes, from the provided method to the final map.

    uv run python pipeline/scripts/funnel.py   ->  outputs/funnel.csv

Each row adds one step to the previous row, on a fresh pipeline run in a temporary directory. Rows 1-3 use the provided
method's rule (colour distance, Otsu threshold) so only the input changes; from row 4 on, this work's noise test decides.
"Raw" is the flagged area before any cleanup; "cleaned" applies step 6 to that row on its own.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage, stats
from skimage.filters import threshold_otsu

from s2cd import methods, radiometry, vectorize
from s2cd.pipeline import Config, run

OUT = Path("outputs/funnel.csv")
PX_KM2 = 1e-4  # one 10 m pixel


def main() -> None:
    cfg = Config.from_yaml(Path("pipeline/config/default.yaml"))
    with tempfile.TemporaryDirectory() as tmp:
        p = run(replace(cfg, out_dir=Path(tmp) / "processed"))
    v, rule, px_m, nb = p.valid, cfg.rule, p.before.grid.transform.a, len(cfg.bands)
    r1 = p.refl_before
    r2 = radiometry.to_reflectance(p.after.dn, v, radiometry.infer_offset(p.after.dn, v))

    def otsu(after: np.ndarray) -> np.ndarray:
        score = methods.euclidean_baseline(r1, after, v)
        return v & (score > threshold_otsu(score[v]))

    one_zone = np.where(v, 0, -1).astype(p.zones.dtype)
    one_line = radiometry.relative_normalization(r1, r2, one_zone, cfg.bands).normalized
    test = v & (np.nan_to_num(p.cva.d2) > stats.chi2.isf(rule.far, nb))
    seams = test & (p.seam_dist >= rule.seam_buffer_px(px_m))
    final = vectorize.binary_from_d2(p.cva.d2, v, p.seam_dist, nb, rule, px_m)

    stages = [
        ("0", "Provided method: colour distance, Otsu threshold", otsu(r2)),
        ("2a", "Brightness matched with one line for the whole site", otsu(one_line)),
        ("1+2", "Three zones, brightness matched per zone", otsu(p.refl_after_norm)),
        ("3+4", "Noise test: what flicker passes 1 in 1,000", test),
        ("5", "Seams skipped (90 m)", seams),
        ("6", "Cleanup: specks under 0.2 ha dropped, holes filled", final),
    ]
    mmu = rule.mmu_px(px_m)
    rows = []
    for step, what, mask in stages:
        labels, n = ndimage.label(mask, structure=np.ones((3, 3)))
        sizes = np.bincount(labels.ravel())[1:]
        rows.append(
            {
                "step": step,
                "what": what,
                "raw_km2": round(mask.sum() * PX_KM2, 2),
                "cleaned_km2": round(vectorize.clean_mask(mask, rule, px_m).sum() * PX_KM2, 2),
                "patches": int(n),
                "patches_under_mmu": int((sizes < mmu).sum()),
            }
        )
    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
