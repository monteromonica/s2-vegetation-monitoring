"""Digital numbers -> surface reflectance.

Sentinel-2 L2A bands are stored as uint16 digital numbers (DN). Physical reflectance is
    reflectance = DN * scale + offset
Since processing baseline 04.00 (Jan 2022) ESA adds a radiometric offset (BOA_ADD_OFFSET
= -1000 DN = -0.1 reflectance) to the raw JP2 products. Whether a given COPY of the data has
that offset already applied depends on the provider (see stac.reflectance_scale_offset), so
scale/offset come from metadata per asset AND the result is checked for physical
plausibility below. Metadata can be wrong; physics is the final check.

Pure numpy, no I/O, so it is trivially testable.
"""

from __future__ import annotations

import numpy as np


def dn_to_reflectance(dn: np.ndarray, scale: float, offset: float, nodata: int = 0) -> np.ndarray:
    """Convert DN to float32 reflectance; nodata pixels become NaN."""
    out = dn.astype(np.float32) * np.float32(scale) + np.float32(offset)
    out[dn == nodata] = np.nan
    return out


def check_reflectance_scale(arrays: dict[str, np.ndarray], max_plausible: float = 3.0,
                            max_negative_fraction: float = 0.05) -> None:
    """Fail loudly if the data does not look like surface reflectance.

    Two failure modes, both silent without this guard:
      * Too large (99.9th percentile > ~3): scale missing, or raw DN / 8-bit values. EVI has a
        '+1' that only makes sense for reflectance in 0..1.
      * Too many negative values: the radiometric offset was applied twice. A few slightly
        negative pixels are normal (over-corrected shadows); >5% is not.
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
        negative = float((finite < -0.01).mean())
        if negative > max_negative_fraction:
            raise ValueError(
                f"band '{name}': {negative:.0%} of pixels have negative reflectance. "
                "The radiometric offset is probably being applied twice."
            )
