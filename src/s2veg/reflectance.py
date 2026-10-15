"""Digital numbers -> surface reflectance.

Sentinel-2 L2A bands are stored as uint16 digital numbers (DN). Physical reflectance is
    reflectance = DN * scale + offset
Since processing baseline 04.00 (Jan 2022) ESA adds a radiometric offset (BOA_ADD_OFFSET
= -1000 DN = -0.1 reflectance). We never hardcode these numbers: they are read from the
product metadata (STAC `raster:bands`) for each asset.

Pure numpy, no I/O, so it is trivially testable.
"""

from __future__ import annotations

import numpy as np


def dn_to_reflectance(dn: np.ndarray, scale: float, offset: float, nodata: int = 0) -> np.ndarray:
    """Convert DN to float32 reflectance; nodata pixels become NaN."""
    out = dn.astype(np.float32) * np.float32(scale) + np.float32(offset)
    out[dn == nodata] = np.nan
    return out


def check_reflectance_scale(arrays: dict[str, np.ndarray], max_plausible: float = 3.0) -> None:
    """Fail loudly if the data does not look like reflectance (0..~1).

    This is the guard that would have caught the old bug: EVI has a '+1' that only makes
    sense for reflectance in 0..1. Fed raw DN (0..10000) or 8-bit values (0..255) it
    silently returns garbage such as an EVI maximum of 25,925.
    """
    for name, arr in arrays.items():
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            continue
        p999 = float(np.percentile(finite, 99.9))
        if p999 > max_plausible:
            raise ValueError(
                f"band '{name}' does not look like reflectance (99.9th percentile = {p999:.1f}). "
                "Did you forget scale/offset, or read 8-bit/DN data?"
            )
