"""Tests for the pure scientific functions: reflectance, indices, masks, composites."""

import numpy as np
import pytest

from s2veg.composite import median_composite
from s2veg.indices import evi, mask_outside, ndvi, ndwi_mcfeeters, scene_indices
from s2veg.reflectance import check_reflectance_scale, dn_to_reflectance
from s2veg.scl import valid_mask


def test_dn_to_reflectance_applies_scale_and_offset():
    dn = np.array([[1000, 2000, 0]], dtype=np.uint16)
    out = dn_to_reflectance(dn, scale=0.0001, offset=-0.1, nodata=0)
    # Baseline >= 04.00: DN 1000 is reflectance 0.0, DN 2000 is 0.1
    assert out[0, 0] == pytest.approx(0.0, abs=1e-6)
    assert out[0, 1] == pytest.approx(0.1, abs=1e-6)
    assert np.isnan(out[0, 2])  # DN 0 is no-data, never a real reflectance


def test_ndvi_known_value():
    assert ndvi(np.float32([0.5]), np.float32([0.1]))[0] == pytest.approx(0.4 / 0.6)


def test_ndvi_is_nan_when_denominator_is_zero():
    assert np.isnan(ndvi(np.float32([0.0]), np.float32([0.0]))[0])


def test_ndvi_water_is_negative_and_vegetation_positive():
    veg = ndvi(np.float32([0.45]), np.float32([0.05]))[0]
    water = ndvi(np.float32([0.02]), np.float32([0.05]))[0]
    assert veg > 0.5 and water < 0


def test_ndwi_mcfeeters_water_positive():
    assert ndwi_mcfeeters(np.float32([0.08]), np.float32([0.02]))[0] > 0   # water: green > nir
    assert ndwi_mcfeeters(np.float32([0.06]), np.float32([0.40]))[0] < 0   # leaves: nir >> green


def test_evi_matches_formula():
    nir, red, blue = np.float32([0.5]), np.float32([0.1]), np.float32([0.05])
    expected = 2.5 * (0.5 - 0.1) / (0.5 + 6 * 0.1 - 7.5 * 0.05 + 1)
    assert evi(nir, red, blue)[0] == pytest.approx(expected, rel=1e-5)


def test_scale_guard_rejects_raw_dn_and_8bit_values():
    """Regression for the original project: EVI maximum of 25,925 came from unscaled input."""
    with pytest.raises(ValueError):
        check_reflectance_scale({"nir": np.array([3000.0, 4200.0, 8000.0])})
    with pytest.raises(ValueError):
        check_reflectance_scale({"nir": np.array([20.0, 120.0, 255.0])})
    check_reflectance_scale({"nir": np.array([0.02, 0.4, 0.9])})  # plausible: no error


def test_indices_do_not_depend_on_band_order():
    """Regression for the original project: S3 wrote B,G,R,NIR and Hub wrote R,G,B,NIR."""
    bands = {k: np.full((2, 2), v, dtype=np.float32)
             for k, v in dict(blue=0.05, green=0.08, red=0.06, nir=0.40).items()}
    shuffled = dict(reversed(list(bands.items())))
    a, b = scene_indices(bands), scene_indices(shuffled)
    for key in a:
        np.testing.assert_allclose(a[key], b[key])


def test_mask_outside_reports_dropped_fraction():
    arr = np.array([0.2, 5.0, -3.0, np.nan], dtype=np.float32)
    out, frac = mask_outside(arr, -1, 1)
    assert np.isnan(out[1]) and np.isnan(out[2]) and out[0] == pytest.approx(0.2)
    assert frac == pytest.approx(2 / 3)


def test_scl_valid_mask_excludes_clouds_shadows_and_nodata():
    scl = np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
    expected = np.array([False, False, False, False, True, True, True, True, False, False, False, False])
    np.testing.assert_array_equal(valid_mask(scl), expected)


def test_median_composite_ignores_nan_and_counts_observations():
    a = np.array([[0.2, np.nan]], dtype=np.float32)
    b = np.array([[0.4, np.nan]], dtype=np.float32)
    c = np.array([[0.9, 0.5]], dtype=np.float32)
    med, count = median_composite([a, b, c])
    assert med[0, 0] == pytest.approx(0.4)          # median is robust to the 0.9 outlier
    assert med[0, 1] == pytest.approx(0.5)
    assert count.tolist() == [[3, 1]]
