"""Spectral indices. Inputs are reflectance in 0..1, addressed BY NAME, never by position.

The old code read bands by array index, and the two download scripts wrote them in
different orders (Sentinel Hub: R,G,B,NIR; S3: B,G,R,NIR). Named inputs make that class
of bug impossible.
"""

from __future__ import annotations

import numpy as np

EPS = 1e-6


def _normalized_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    num = a - b
    den = a + b
    out = np.full(np.broadcast(a, b).shape, np.nan, dtype=np.float32)
    ok = np.isfinite(den) & (np.abs(den) > EPS)
    np.divide(num, den, out=out, where=ok)
    return out


def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """(NIR - Red) / (NIR + Red). Healthy leaves reflect NIR and absorb red."""
    return _normalized_difference(nir, red)


def ndwi_mcfeeters(green: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """(Green - NIR) / (Green + NIR): open-water index (McFeeters 1996).

    NOT the same as Gao's NDWI (NIR - SWIR) / (NIR + SWIR), which measures vegetation
    water content. Always say which one you mean.
    """
    return _normalized_difference(green, nir)


def evi(nir: np.ndarray, red: np.ndarray, blue: np.ndarray,
        g: float = 2.5, c1: float = 6.0, c2: float = 7.5, l: float = 1.0) -> np.ndarray:
    """Enhanced Vegetation Index. Only valid for reflectance in 0..1."""
    den = nir + c1 * red - c2 * blue + l
    num = g * (nir - red)
    out = np.full(np.broadcast(nir, red, blue).shape, np.nan, dtype=np.float32)
    ok = np.isfinite(den) & (np.abs(den) > EPS)
    np.divide(num, den, out=out, where=ok)
    return out


def mask_outside(arr: np.ndarray, low: float, high: float) -> tuple[np.ndarray, float]:
    """NaN-out values outside [low, high]; return (masked, fraction of finite pixels removed).

    EVI has a denominator that can approach zero over very dark/blue pixels, producing
    huge spikes. We drop them explicitly and report how many, instead of hiding them.
    """
    finite = np.isfinite(arr)
    bad = finite & ((arr < low) | (arr > high))
    out = arr.copy()
    out[bad] = np.nan
    n = int(finite.sum())
    return out, (float(bad.sum()) / n if n else 0.0)


def scene_indices(refl: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Compute all indices from named reflectance bands: blue, green, red, nir."""
    return {
        "ndvi": ndvi(refl["nir"], refl["red"]),
        "ndwi": ndwi_mcfeeters(refl["green"], refl["nir"]),
        "evi": evi(refl["nir"], refl["red"], refl["blue"]),
    }
