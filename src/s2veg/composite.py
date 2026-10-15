"""Temporal compositing: combine several acquisitions into one cloud-free layer."""

from __future__ import annotations

import warnings

import numpy as np


def median_composite(stack: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel median of valid (non-NaN) observations, plus the number of observations.

    The median is robust to residual cloud/shadow that slipped through the mask.
    Pixels with zero valid observations stay NaN, and the count tells the user how much
    to trust each pixel (1 observation is not a composite, it is a single date).
    """
    if not stack:
        raise ValueError("empty stack")
    cube = np.stack(stack, axis=0).astype(np.float32, copy=False)
    count = np.isfinite(cube).sum(axis=0).astype(np.uint8)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)  # all-NaN slices are expected
        med = np.nanmedian(cube, axis=0).astype(np.float32)
    return med, count
