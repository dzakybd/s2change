"""Sensitivity of the change result to its two user-chosen knobs: false-alarm rate and minimum mapping unit.

The change statistic (d2) is computed once; only the binary rule is varied. d2 carries the per-stratum empirical calibration
derived at the default nominal rate (1e-3), so the other rates are nominal rates relative to that calibration. If the conclusions
(total changed area, share inside the mine footprint) hold across a wide band of settings, they
are not artifacts of one threshold choice.

The pipeline is run into a temporary directory and SQLite file, so the shipped outputs (``data/processed``, the database) are
never touched; the default configuration (``configs/default.yaml``, with provenance zones when available) is used.

Run: ``uv run python -m s2cd.sensitivity`` (about a minute) ->
data/processed/sensitivity.csv
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import replace
from pathlib import Path

import pandas as pd
import rasterio.features

from . import register, vectorize
from .pipeline import Config, run

FARS = (1e-2, 1e-3, 1e-4, 1e-5)  # nominal per-pixel false-alarm rate
MMU_HA = (0.1, 0.2, 0.5, 1.0)


def sweep() -> pd.DataFrame:
    """Changed area and footprint shares over the FAR x MMU grid (the run itself goes to a temporary directory)."""
    base = Config.from_yaml(Path("configs/default.yaml"))
    with tempfile.TemporaryDirectory() as tmp:
        p = run(replace(base, out_dir=Path(tmp) / "processed", sqlite_path=Path(tmp) / "change_detection.sqlite"))
    grid = p.before.grid
    px_ha = grid.pixel_area_m2 / 1e4
    disturbed = vectorize.disturbed_footprint(p.refl_before, p.valid)
    in_mine = rasterio.features.geometry_mask([register.footprint(p.facilities)], p.valid.shape, grid.transform, invert=True)
    rows = []
    for far in FARS:
        for mmu in MMU_HA:
            rule = replace(base.rule, far=far, mmu_ha=mmu, fill_holes_ha=mmu)
            binary = vectorize.binary_from_d2(p.cva.d2, p.valid, p.seam_dist, len(base.bands), rule, grid.transform.a)
            n = max(binary.sum(), 1)
            rows.append(
                {
                    "far": far,
                    "mmu_ha": mmu,
                    "changed_ha": round(binary.sum() * px_ha, 1),
                    "changed_pct_of_aoi": round(100 * binary[p.valid].mean(), 2),
                    "share_in_mine_footprint": round((binary & disturbed).sum() / n, 3),
                    "share_in_register_mine_classes": round((binary & in_mine).sum() / n, 3),
                    "is_default": far == base.rule.far and mmu == base.rule.mmu_ha,
                }
            )
    return pd.DataFrame(rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    df = sweep()
    out = Path("data/processed/sensitivity.csv")
    df.to_csv(out, index=False)
    print(df.to_string(index=False))
