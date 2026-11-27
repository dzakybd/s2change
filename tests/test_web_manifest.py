"""The viewer loads every raster in RASTER_KEYS (viewer/src/types.ts); a manifest missing one breaks the map."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEYS = re.search(r"RASTER_KEYS = \[([^\]]*)\]", (ROOT / "viewer/src/types.ts").read_text()).group(1)
RASTER_KEYS = re.findall(r'"(\w+)"', KEYS)


@pytest.mark.parametrize("folder", ["docs/web", "docs/web/true_passes"])
def test_manifest_has_every_viewer_raster(folder):
    rasters = json.loads((ROOT / folder / "manifest.json").read_text())["rasters"]
    for key in RASTER_KEYS:
        assert key in rasters, f"{folder}/manifest.json lacks {key}"
        assert (ROOT / folder / rasters[key]["url"]).exists()
