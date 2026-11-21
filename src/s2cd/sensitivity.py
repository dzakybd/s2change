"""Sensitivity of the change result to its two user-chosen knobs: false-alarm rate and minimum mapping unit.

The change statistic (d2) is computed once; only the binary rule is varied. d2 carries the per-stratum empirical calibration
derived at the default nominal rate (1e-3), so the other rates are nominal rates relative to that calibration. If the conclusions
(total changed area, share inside the mine footprint) hold across a wide band of settings, they
are not artifacts of one threshold choice.

The pipeline is run into a temporary directory and SQLite file, so the shipped outputs (``data/processed``, the database) are
never touched; the default configuration (``configs/default.yaml``, with provenance zones when available) is used.

Run: ``uv run python -m s2cd.sensitivity`` (about a minute) ->
data/processed/sensitivity.csv and docs/figures/11_sensitivity.png
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio.features  # noqa: E402

from . import register, vectorize  # noqa: E402
from .pipeline import Config, run  # noqa: E402

FAR_LOW = (1e-2, 1e-3, 1e-4, 1e-5)  # nominal region-growing false-alarm rate; the seed rate is 100x stricter
MMU_HA = (0.1, 0.2, 0.5, 1.0)


def sweep(cfg: Config | None = None) -> pd.DataFrame:
    """Changed area and footprint shares over the FAR x MMU grid (the run itself goes to a temporary directory)."""
    base = cfg or Config.from_yaml(Path("configs/default.yaml"))
    with tempfile.TemporaryDirectory() as tmp:
        p = run(replace(base, out_dir=Path(tmp) / "processed", sqlite_path=Path(tmp) / "change_detection.sqlite"))
    grid = p.before.grid
    px_ha = grid.pixel_area_m2 / 1e4
    disturbed = vectorize.disturbed_footprint(p.refl_before, p.valid)
    in_mine = rasterio.features.geometry_mask([register.footprint(p.facilities)], p.valid.shape, grid.transform, invert=True)
    rows = []
    for far in FAR_LOW:
        for mmu in MMU_HA:
            rule = replace(base.rule, far_low=far, far_high=far / 100, mmu_ha=mmu, fill_holes_ha=mmu)
            binary = vectorize.binary_from_d2(p.cva.d2, p.valid, p.seam_dist, len(base.bands), rule, grid.transform.a)
            n = max(binary.sum(), 1)
            rows.append(
                {
                    "far_low": far,
                    "mmu_ha": mmu,
                    "changed_ha": round(binary.sum() * px_ha, 1),
                    "changed_pct_of_aoi": round(100 * binary[p.valid].mean(), 2),
                    "share_in_mine_footprint": round((binary & disturbed).sum() / n, 3),
                    "share_in_register_mine_classes": round((binary & in_mine).sum() / n, 3),
                    "is_default": far == base.rule.far_low and mmu == base.rule.mmu_ha,
                }
            )
    return pd.DataFrame(rows)


def plot(df: pd.DataFrame, path: Path) -> Path:
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
    for mmu, g in df.groupby("mmu_ha"):
        ax[0].plot(g.far_low, g.changed_ha / 100, "o-", label=f"MMU {mmu * 1e4:,.0f} m²")
        ax[1].plot(g.far_low, 100 * g.share_in_mine_footprint, "o-", label=f"MMU {mmu * 1e4:,.0f} m²")
    d = df[df.is_default].iloc[0]
    for a, y in ((ax[0], d.changed_ha / 100), (ax[1], 100 * d.share_in_mine_footprint)):
        a.set_xscale("log")
        a.invert_xaxis()
        a.axvline(d.far_low, color="0.6", ls="--", lw=0.8)
        a.plot(d.far_low, y, "k*", ms=12, label="default")
        a.set_xlabel("nominal per-pixel false-alarm rate (stricter →)")
    ax[0].set_ylabel("changed area (km²)")
    ax[0].set_title("Total change shrinks smoothly as the test gets stricter")
    ax[1].set_ylabel("% of change inside the mine footprint")
    ax[1].set_title("…while the share inside the mine stays high")
    ax[1].legend(fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    df = sweep()
    out = Path("data/processed/sensitivity.csv")
    df.to_csv(out, index=False)
    plot(df, Path("docs/figures/11_sensitivity.png"))
    print(df.to_string(index=False))
