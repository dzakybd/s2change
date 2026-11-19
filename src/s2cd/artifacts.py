"""Download the large optional artifacts (enrichment rasters, timeline DB) kept out of git.

They live in a public Supabase Storage bucket; ``artifacts.json`` lists URL, size and SHA-256.
Files that already exist with the right checksum are skipped.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[2] / "artifacts.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(manifest: Path = MANIFEST, root: Path | None = None) -> list[str]:
    root = root or manifest.parent
    done = []
    for item in json.loads(manifest.read_text()):
        dest = root / item["path"]
        if dest.exists() and _sha256(dest) == item["sha256"]:
            done.append(f"ok (cached) {item['path']}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        req = urllib.request.Request(item["url"], headers={"User-Agent": "s2cd-fetch-artifacts"})
        with urllib.request.urlopen(req, timeout=300) as r, tmp.open("wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        if _sha256(tmp) != item["sha256"]:
            tmp.unlink()
            raise ValueError(f"checksum mismatch for {item['path']}")
        tmp.replace(dest)
        done.append(f"downloaded {item['path']} ({item['bytes'] / 1e6:.1f} MB)")
    return done
