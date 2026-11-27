"""List the distinct high-res captures Esri Wayback holds over the AOI, for the map's high-res date swipe.

    uv run --with requests python scripts/wayback.py

Esri World Imagery Wayback keeps every release of World Imagery since 2014. Most releases reuse the tiles of an earlier
one over a given place; the tile map of a release names the release a tile really comes from. For a few points across
the mine this keeps the releases whose tiles changed, reads each one's capture metadata (date, sensor, resolution) and
keeps one release per capture date. Writes docs/web/wayback.json; nothing else is downloaded.
"""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

CONFIG = "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"
TILEMAP = "https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tilemap/{rel}/17/{y}/{x}/1/1"
TILES = "https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/{rel}/{{z}}/{{y}}/{{x}}"
POINTS = {
    "TSF": (25.88, -12.235),
    "PIT-S": (25.89, -12.29),
    "PIT-NW": (25.82, -12.22),
    "PLANT": (25.856, -12.246),
    "WD-E": (25.925, -12.29),
}
OUT = Path("docs/web/wayback.json")
http = requests.Session()


def tile(lng: float, lat: float, z: int = 17) -> tuple[int, int]:
    n, r = 2**z, math.radians(lat)
    return int((lng + 180) / 360 * n), int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n)


def source_release(rel: str, lng: float, lat: float) -> int | None:
    x, y = tile(lng, lat)
    j = http.get(TILEMAP.format(rel=rel, x=x, y=y), timeout=30).json()
    return j["select"][0] if j.get("data") == [1] and j.get("select") else None


def capture(metadata_url: str, lng: float, lat: float) -> dict | None:
    d = 0.005
    params = {
        "geometry": f"{lng},{lat}", "geometryType": "esriGeometryPoint", "sr": 4326, "layers": "top", "tolerance": 1,
        "mapExtent": f"{lng - d},{lat - d},{lng + d},{lat + d}", "imageDisplay": "1000,1000,96",
        "returnGeometry": "false", "f": "json",
    }  # fmt: skip
    res = http.get(metadata_url + "/identify", params=params, timeout=30).json().get("results") or []
    if not res:
        return None
    a = res[0]["attributes"]
    raw = a.get("SRC_DATE") or a.get("DATE (YYYYMMDD)")
    return {"date": f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}", "sensor": a.get("SRC_DESC"), "res_m": float(a.get("SRC_RES") or "nan")}


def main() -> None:
    cfg = http.get(CONFIG, timeout=30).json()
    with ThreadPoolExecutor(16) as ex:
        used = set(ex.map(lambda job: source_release(job[0], *job[1]), [(rel, p) for rel in cfg for p in POINTS.values()]))
    # oldest release first (release numbers are not chronological), so a later reprocessing of a capture wins below
    used = sorted((str(r) for r in used if r is not None and str(r) in cfg), key=lambda r: cfg[r]["itemTitle"])
    by_date: dict[str, dict] = {}
    for rel in used:
        caps = [c for p in POINTS.values() if (c := capture(cfg[rel]["metadataLayerUrl"], *p))]
        if not caps:
            continue
        dates = sorted({c["date"] for c in caps})
        entry = {
            "release": int(rel),
            "date": dates[-1],
            "dates": dates,  # capture dates seen across the mine (one when the release is a single scene there)
            "sensor": caps[0]["sensor"],
            "res_m": caps[0]["res_m"],
            "tiles": TILES.format(rel=rel),
        }
        by_date[entry["date"]] = entry
        print(entry["date"], entry["dates"], entry["sensor"], entry["res_m"], rel)
    OUT.write_text(json.dumps(sorted(by_date.values(), key=lambda e: e["date"]), indent=2) + "\n")
    print(f"{OUT}: {len(by_date)} captures")


if __name__ == "__main__":
    main()
