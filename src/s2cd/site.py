"""Export the static content the web app reads on its Overview and Report tabs.

    uv run python -m s2cd.site

Writes, under docs/web/ (served at /web/ by the live site and by GitHub Pages):

* overview.json: headline numbers (data/processed/run_summary.json), the facility register
  (data/change_detection.sqlite), and the sensitivity grid (data/processed/sensitivity.csv);
* report/report.md and report/figures/*.png: report.md with its images copied from docs/figures and the links
  rewritten so the document renders from docs/web/report/.

Everything is read from files the pipeline already wrote, so this never recomputes anything.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

PROCESSED = Path("data/processed")
DB = Path("data/change_detection.sqlite")
WEB = Path("docs/web")
REPORT_SRC = Path("report.md")
FIGURES_SRC = Path("docs/figures")
GITHUB_BLOB = "https://github.com/dzakybd/s2change/blob/main/"

IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")


def _read_json(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def facilities(db: Path = DB) -> list[dict]:
    """The facility register with its change breakdown, from the SpatiaLite tables facilities + facility_changes.

    Only plain columns are selected, so no spatial extension is needed to read them.
    """
    if not db.exists():
        return []
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT f.facility_id, f.name, f.class, f.cover, f.area_ha, c.changed_ha, c.changed_share, c.change_types, c.summary "
            "FROM facilities f JOIN facility_changes c USING (facility_id) ORDER BY c.changed_ha DESC, f.facility_id"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        types = json.loads(d["change_types"]) if d["change_types"] else None
        d["change_types"] = types if isinstance(types, dict) else {}  # a facility without change is stored as NaN
        out.append(d)
    return out


def sensitivity(path: Path = PROCESSED / "sensitivity.csv") -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        out.append(
            {
                "far_low": float(r["far_low"]),
                "mmu_ha": float(r["mmu_ha"]),
                "changed_ha": float(r["changed_ha"]),
                "changed_pct_of_aoi": float(r["changed_pct_of_aoi"]),
                "share_in_mine_footprint": float(r["share_in_mine_footprint"]),
                "share_in_register_mine_classes": float(r["share_in_register_mine_classes"]),
                "is_default": r["is_default"].strip().lower() == "true",
            }
        )
    return out


def overview() -> dict:
    run = _read_json(PROCESSED / "run_summary.json")
    if run is None:
        raise SystemExit("data/processed/run_summary.json is missing: run `s2cd run` first")
    grid = run["grid"]
    aoi_ha = grid["width"] * grid["height"] * grid["pixel_m"] ** 2 / 10_000
    sens = sensitivity()
    default = next((s for s in sens if s["is_default"]), None)
    changed = run["changed_area_ha"]
    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "pair": {
            "before": "2023-08-12",
            "after": "2023-09-02",
            "note": "Nominal dates: each is a mosaic of two Sentinel-2 scenes.",
            "acquisitions": run["acquisition"],
            "compared_gap_days": _compared_gaps(run),
        },
        "headline": {
            "changed_ha": changed["robust_cva"],
            "n_polygons": run["n_features"]["robust_cva"],
            "aoi_ha": round(aoi_ha, 1),
            "changed_pct_of_aoi": default["changed_pct_of_aoi"] if default else round(100 * changed["robust_cva"] / aoi_ha, 2),
            "baseline_ha": changed["euclidean_baseline"],
            "baseline_n_polygons": run["n_features"]["euclidean_baseline"],
            "irmad_ha": changed["irmad"],
            "share_in_mine_footprint": run["footprint"]["register_all_facilities"][
                "share_of_change"
            ],  # same definition as report.md (named facilities)
            "share_in_register_mine_classes": run["footprint"]["register_mine_classes"]["share_of_change"],
            "agreement_iou_with_baseline": run["agreement_cva_vs_baseline_iou"],
            "n_zones": run["zones"]["n_zones"],
            "runtime_s": run["runtime_s"],
        },
        "change_type_ha": run["change_type_ha"],
        "facilities": facilities(),
        "sensitivity": sens,
    }


def _compared_gaps(run: dict) -> list[int]:
    """Day gaps of the scene pairs actually compared (one per provenance zone), not every before x after combination."""
    from datetime import date

    acq = list(run["acquisition"].values())
    b_dates, a_dates = acq[0]["source_dates"], acq[1]["source_dates"]
    gaps = set()
    for z in run["zones"].get("zone_source_pairs", []):
        b = date.fromisoformat(b_dates[z["before_source_index"]])
        a = date.fromisoformat(a_dates[z["after_source_index"]])
        gaps.add((a - b).days)
    return sorted(gaps)


def report(src: Path = REPORT_SRC, figures: Path = FIGURES_SRC, out: Path = WEB / "report") -> list[str]:
    """Copy report.md with figures under out/, rewriting image links to figures/<name> and other relative links to GitHub."""
    text = src.read_text()
    (out / "figures").mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    skipped: list[str] = []

    def image(m: re.Match[str]) -> str:
        alt, target = m.groups()
        if target.startswith(("http://", "https://")):
            return m.group(0)
        name = Path(target).name
        source = figures / name
        if not source.exists():
            skipped.append(target)
            return ""
        shutil.copyfile(source, out / "figures" / name)
        copied.append(name)
        return f"![{alt}](figures/{name})"

    def link(m: re.Match[str]) -> str:
        label, target = m.groups()
        if target.startswith(("http://", "https://", "#", "mailto:")):
            return m.group(0)
        return f"[{label}]({GITHUB_BLOB}{target.lstrip('./')})"

    text = IMAGE.sub(image, text)
    text = LINK.sub(link, text)
    (out / "report.md").write_text(text)
    for t in skipped:
        print(f"warning: figure {t} not found, image dropped from the web report", file=sys.stderr)
    return sorted(set(copied))


def main() -> None:
    WEB.mkdir(parents=True, exist_ok=True)
    data = overview()
    (WEB / "overview.json").write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    figs = report()
    print(f"docs/web/overview.json: {len(data['facilities'])} facilities")
    print(f"docs/web/report/report.md: {len(figs)} figures copied")


if __name__ == "__main__":
    main()
