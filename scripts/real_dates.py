"""Re-run the comparison on true single-pass dates, to check what the mosaic "data trap" changes.

    uv run --extra stac python scripts/real_dates.py

Each provided "date" is a mosaic of two passes (2023-07-01/07-04 and 2023-08-25/09-02). Planetary Computer also has
single-pass scenes of relative orbit 121 that cover the whole AOI: 2023-07-04, 2023-08-08 (the clear date closest to the
nominal 08-12) and 2023-09-02. Each pair below is run through the same pipeline (no provenance zones: a single pass
has no seams) and the provided method, and the table is written to data/experiments/real_dates.json.
The 2023-08-08 -> 2023-09-02 pair is also exported to docs/web/true_passes/, the map's "Ours, true passes" result, and its
stacks and change maps are copied to data/processed/true_passes/ for the Downloads tab.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from s2cd import pipeline, scenes, viz

OUT = Path("data/experiments")
WEB = Path("docs/web/true_passes")
WEB_PAIR = "single pass 2023-08-08 -> 2023-09-02"
DOWNLOADS = Path("data/processed/true_passes")
DOWNLOAD_FILES = ("sentinel2_20230808_stack.tif", "sentinel2_20230902_stack.tif", "change_map.tif", "change_binary.tif")
PAIRS = {
    "provided mosaics (nominal 2023-08-12 -> 2023-09-02)": ("inputs/data/sentinel2_20230812", "inputs/data/sentinel2_20230902"),
    "single pass 2023-08-08 -> 2023-09-02": ("2023-08-08", "2023-09-02"),
    "single pass 2023-07-04 -> 2023-08-08": ("2023-07-04", "2023-08-08"),
}


def folder(label: str) -> Path:
    return Path(label) if "/" in label else scenes.fetch_scene(date.fromisoformat(label), OUT / "scenes")


def main() -> None:
    rows = []
    for name, (before, after) in PAIRS.items():
        out = OUT / f"{before}_{after}".replace("inputs/data/sentinel2_", "provided_")
        cfg = pipeline.Config(before=folder(before), after=folder(after), out_dir=out, sqlite_path=out / "db.sqlite")
        if "/" not in before:
            cfg.provenance_dir = out / "none"  # a single pass has no seams: do not borrow the mosaics' zones
        p = pipeline.run(cfg)
        if name == WEB_PAIR:
            viz.export_web(p, WEB)
            DOWNLOADS.mkdir(parents=True, exist_ok=True)
            for f in DOWNLOAD_FILES:
                shutil.copyfile(out / f, DOWNLOADS / f)
        s = p.summary
        rows.append(
            {
                "pair": name,
                "zones": s["zones"]["n_zones"],
                "provided_method_km2": round(s["changed_area_ha"]["euclidean_baseline"] / 100, 2),
                "this_method_km2": round(s["changed_area_ha"]["robust_cva"] / 100, 2),
                "dam_wall_fill_km2": round(s["change_type_ha"].get("tailings_dam_raise", 0) / 100, 2),
                "share_in_facilities": s["footprint"]["register_all_facilities"]["share_of_change"],
            }
        )
        print(json.dumps(rows[-1]), flush=True)
    (OUT / "real_dates.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
