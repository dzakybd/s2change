"""Visible-band (B02 blue, B03 green, B04 red) indices and pixel rules.

The brief provides only these three Sentinel-2 bands, so every interpretation step (facility register outlines, change-type
labels) is built from them, and all thresholds live here in one place. Inputs are surface reflectance (0-1) arrays in band order
(B02, B03, B04), shape (3, H, W).

Indices
- brightness: mean visible reflectance
- gcc: green chromatic coordinate G / (R + G + B), the RGB greenness index of digital-camera phenology
  (Richardson et al. 2007, Oecologia 152:323; Sonnentag et al. 2012, Agric. For. Meteorol. 152:159). Caveat: it was designed
  for camera DN under canopy-level illumination; on 10 m bottom-of-atmosphere reflectance it is only a relative greenness
  measure (vegetation is greener than bare soil, rock or tailings), so its thresholds are empirical
- redness: B04 / B02, an iron-oxide (ferric) proxy, high on oxidized waste rock and laterite
- whiteness: (max - min) / max over the three bands; low means grey or white (cloud, tailings)
- blue_red: (B02 - B04) / (B02 + B04); water absorbs red, so open water is positive
  (reservoir about +0.12, tailings pond about +0.37) while woodland, grass and soil are near or below 0

These are proxies. Without NIR/SWIR, dark wet soil, shadow and water are hard to tell apart.
Every threshold in ``RgbRules`` was set by eye on this AOI's two composites (a Zambian copper belt mine in the dry season) and is
specific to it; re-derive them before using the rules elsewhere. Apply the rules to radiometrically comparable inputs
(normalized after-image), not to a hazy as-delivered date.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS = 1e-6


def brightness(refl: np.ndarray) -> np.ndarray:
    return refl.mean(axis=0)


def gcc(refl: np.ndarray) -> np.ndarray:
    return refl[1] / (refl.sum(axis=0) + EPS)


def redness(refl: np.ndarray) -> np.ndarray:
    return refl[2] / (refl[0] + EPS)


def blue_red(refl: np.ndarray) -> np.ndarray:
    return (refl[0] - refl[2]) / (refl[0] + refl[2] + EPS)


def whiteness(refl: np.ndarray) -> np.ndarray:
    mx, mn = refl.max(axis=0), refl.min(axis=0)
    return (mx - mn) / (mx + EPS)


@dataclass(frozen=True)
class RgbRules:
    """Thresholds for the RGB pixel rules. AOI-specific: set by eye on this AOI's composites; see the notes per rule."""

    cloud_brightness: float = 0.30  # clouds are bright ...
    cloud_whiteness: float = 0.20  # ... and nearly colourless
    shadow_brightness: float = 0.03  # cloud/terrain shadow: very dark in all bands
    water_blue_red_min: float = 0.05  # water absorbs red: blue clearly above red
    water_brightness_max: float = 0.15  # and it is not bright (excludes white tailings)
    bare_gcc_max: float = 0.35  # bare ground is less green than vegetation (woodland gcc 0.37-0.38)
    bare_brightness_min: float = 0.06  # and brighter than woodland (0.037-0.044) and most dry grass; the
    # facility outlines additionally require bareness on both dates, which drops grass that is merely dry
    bright_surface_min: float = 0.18  # tailings beaches, roofs, concrete and graded roads: bright ...
    bright_surface_whiteness: float = 0.35  # ... and greyish, unlike red-orange waste rock and laterite


COVER = ("vegetation or dark ground", "bare ground", "bright surface (tailings, roofs, roads)", "water")


def cover(refl: np.ndarray, r: RgbRules = RgbRules()) -> np.ndarray:
    """A coarse RGB land-cover class per pixel, index into ``COVER``: water first, then bright and colourless surfaces,
    then bare ground; the rest is vegetation or dark ground (RGB cannot separate dense woodland from burnt or wet soil)."""
    out = np.zeros(refl.shape[1:], dtype=np.int8)
    bare = bare_mask(refl, r)
    out[bare] = 1
    out[bare & (brightness(refl) > r.bright_surface_min) & (whiteness(refl) < r.bright_surface_whiteness)] = 2
    out[water_mask(refl, r)] = 3
    return out


def cloud_mask(refl: np.ndarray, r: RgbRules = RgbRules()) -> np.ndarray:
    """Bright, colourless pixels (cloud) plus very dark pixels (shadow), to drop before compositing."""
    b = brightness(refl)
    cloud = (b > r.cloud_brightness) & (whiteness(refl) < r.cloud_whiteness)
    shadow = b < r.shadow_brightness
    return cloud | shadow


def water_mask(refl: np.ndarray, r: RgbRules = RgbRules()) -> np.ndarray:
    """Blue clearly above red and not bright. Dark hazy scenes and shadow can pass it; apply it to normalized reflectance."""
    return (blue_red(refl) > r.water_blue_red_min) & (brightness(refl) < r.water_brightness_max)


def bare_mask(refl: np.ndarray, r: RgbRules = RgbRules()) -> np.ndarray:
    """Bare surface (pits, dumps, tailings, roads, bare soil): low greenness and not dark, excluding water."""
    return (gcc(refl) < r.bare_gcc_max) & (brightness(refl) > r.bare_brightness_min) & ~water_mask(refl, r)
