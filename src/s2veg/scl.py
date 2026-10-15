"""Scene Classification Layer (SCL): the L2A per-pixel quality/cloud map (20 m)."""

from __future__ import annotations

import numpy as np

SCL_CLASSES = {
    0: "no data",
    1: "saturated or defective",
    2: "dark area pixels",
    3: "cloud shadows",
    4: "vegetation",
    5: "not vegetated",
    6: "water",
    7: "unclassified",
    8: "cloud medium probability",
    9: "cloud high probability",
    10: "thin cirrus",
    11: "snow or ice",
}

# Pixels we trust for vegetation analysis. Excluded: no data, defective, dark/topographic
# shadow, cloud shadow, all cloud classes, cirrus, snow.
DEFAULT_VALID = (4, 5, 6, 7)


def valid_mask(scl: np.ndarray, valid_classes: tuple[int, ...] = DEFAULT_VALID) -> np.ndarray:
    return np.isin(scl, valid_classes)


def class_fractions(scl: np.ndarray) -> dict[str, float]:
    total = scl.size
    return {SCL_CLASSES[c]: float((scl == c).sum()) / total for c in SCL_CLASSES if (scl == c).any()}
