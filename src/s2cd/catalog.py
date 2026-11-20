"""The curated date catalogue behind the app's date picker: one clear scene per month, fetched and compared in advance.

    uv run --extra stac python -m s2cd.catalog           # build data/scenes/catalog.json + precompute comparisons

For every month after the provided pair up to today, the dates with good STAC metadata (AOI coverage, tile cloud) are tried
clearest first; the first one that also passes the RGB cloud/shadow check on the AOI is downloaded (B02/B03/B04 only) and
kept. Months where no date passes (the rainy season) are skipped and listed in ``skipped``. Then the provided before date is
compared with every catalogue date, so those pairs are served from disk. The app only offers catalogue dates and never
searches or downloads at request time; other pairs between catalogue dates are computed on demand from the local scenes.
Re-running is incremental: kept scenes and finished comparisons are reused.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import date
from pathlib import Path

from . import rgb, scenes
from .io import load_scene
from .radiometry import to_reflectance

CATALOG = scenes.OUT_ROOT / "catalog.json"
FIRST = date(2023, 9, 3)  # the day after the provided after date
log = logging.getLogger("s2cd.catalog")


def load() -> list[dict]:
    """Catalogue entries ``[{date, aoi_cloud, items}]``, oldest first; empty if it was never built."""
    return json.loads(CATALOG.read_text())["dates"] if CATALOG.exists() else []


def folder(day: str) -> Path:
    """The local scene folder of a catalogue date. ValueError (shown to the user) for any other date."""
    if day not in {e["date"] for e in load()}:
        raise ValueError(f"{day} is not in the date catalogue; pick one of the listed dates.")
    return scenes.OUT_ROOT / f"sentinel2_{date.fromisoformat(day):%Y%m%d}"


def _aoi_cloud(path: Path) -> float:
    s = load_scene(path)
    return float(rgb.cloud_mask(to_reflectance(s.dn, s.valid))[s.valid].mean())


def _pick_month(candidates: list[dict]) -> dict | None:
    for c in sorted(candidates, key=lambda c: c["tile_cloud_max"]):
        try:
            path = scenes.fetch_scene(date.fromisoformat(c["date"]))
        except ValueError as e:  # cloudy in RGB or not covered: try the next date of the month
            log.info("skip %s: %s", c["date"], e)
            continue
        return {"date": c["date"], "aoi_cloud": round(_aoi_cloud(path), 4), "items": c["items"]}
    return None


def build(end: date | None = None) -> dict:
    end = end or date.today()
    known = {e["date"][:7]: e for e in load()}
    by_month: dict[str, list[dict]] = {}
    for c in scenes.available_dates(FIRST, end):
        by_month.setdefault(c["date"][:7], []).append(c)
    kept, skipped = [], []
    month = date(FIRST.year, FIRST.month, 1)
    while month <= end:
        key = f"{month:%Y-%m}"
        entry = known.get(key) or _pick_month(by_month.get(key, []))
        (kept.append(entry) if entry else skipped.append(key))
        log.info("%s: %s", key, entry["date"] if entry else "no clear date")
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    doc = {"built": date.today().isoformat(), "rules": vars(scenes.SceneRules()), "dates": kept, "skipped_months": skipped}
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(json.dumps(doc, indent=1) + "\n")
    keep = {f"sentinel2_{e['date'].replace('-', '')}" for e in kept}
    for stale in scenes.OUT_ROOT.glob("sentinel2_*"):  # scenes fetched by earlier versions or rejected
        if stale.name not in keep:
            shutil.rmtree(stale)
    shutil.rmtree(scenes.CACHE, ignore_errors=True)  # per-item download cache, not needed once scenes are written
    return doc


def priority(entries: list[dict]) -> list[dict]:
    """Order for precomputing: the same month as the provided before date each year (a season-matched yearly view) and the
    latest date first, then the rest newest first. Those first pairs are what the app links to."""
    month = "08"  # nominal before date 2023-08-12
    first = [e for e in entries if e["date"][5:7] == month] + [e for e in entries[-1:] if e["date"][5:7] != month]
    rest = sorted((e for e in entries if e not in first), key=lambda e: e["date"], reverse=True)
    return first + rest


def precompute(entries: list[dict]) -> None:
    from . import compare  # compare imports this module

    for e in priority(entries):
        log.info("compare provided-before -> %s", e["date"])
        compare.run("provided-before", e["date"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    doc = build()
    log.info("%d dates kept, months skipped: %s", len(doc["dates"]), ", ".join(doc["skipped_months"]) or "none")
    precompute(doc["dates"])
    log.info("done")
