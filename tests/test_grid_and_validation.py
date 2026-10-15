"""Tests for grid alignment, resampling of categorical data, validation metrics and Hub payload."""

import numpy as np
import pytest
from rasterio.enums import Resampling

from s2veg.config import Config
from s2veg.hub import build_request, snapped_utm_bounds
from s2veg.raster import Grid, grid_for_aoi, https_to_s3, read_on_grid
from s2veg.validation import binary_metrics, worldcover_href, worldcover_tile_name


def make_grid(tile, col0=10, row0=20, size=40):
    from rasterio.windows import Window, transform as wtransform
    win = Window(col0, row0, size, size)
    return Grid(tile["crs"], wtransform(win, tile["transform10"]), size, size)


def test_10m_read_returns_the_expected_window(synthetic_tile):
    grid = make_grid(synthetic_tile)
    arr = read_on_grid(synthetic_tile["path10"], grid, Resampling.nearest)
    assert arr.shape == (40, 40)
    assert arr[0, 0] == 20 * 1000 + 10          # row 20, col 10 of the full raster
    assert arr[39, 39] == 59 * 1000 + 49


def test_20m_mask_upsampled_exactly_onto_10m_grid(synthetic_tile):
    """Each 20 m pixel must become an exact 2x2 block of 10 m pixels (no half-pixel shift)."""
    grid = make_grid(synthetic_tile)  # even offsets: 10 and 20
    arr = read_on_grid(synthetic_tile["path20"], grid, Resampling.nearest)
    assert arr.shape == (40, 40)
    block = arr[0:2, 0:2]
    assert (block == block[0, 0]).all()
    assert arr[0, 0] == 10 * 1000 + 5            # 20 m pixel (row 10, col 5)
    assert arr[39, 39] == 29 * 1000 + 24


def test_nearest_never_invents_class_values_but_bilinear_does(synthetic_tile):
    """Categorical rasters (SCL, land cover) must use nearest: bilinear invents values that
    are not valid class codes."""
    import rasterio
    from rasterio.windows import Window

    grid = make_grid(synthetic_tile)  # 10 m window (10,20,40,40) == 20 m window (5,10,20,20)
    with rasterio.open(synthetic_tile["path20"]) as ds:
        original_values = set(np.unique(ds.read(1, window=Window(5, 10, 20, 20))).tolist())

    nearest = set(np.unique(read_on_grid(synthetic_tile["path20"], grid, Resampling.nearest)).tolist())
    bilinear = set(np.unique(read_on_grid(synthetic_tile["path20"], grid, Resampling.bilinear)).tolist())
    assert nearest <= original_values
    assert not bilinear <= original_values


def test_grid_for_aoi_is_snapped_and_contains_aoi(synthetic_tile):
    # AOI roughly in the middle of a 100x100 px tile in 30T; use the tile's own CRS bounds.
    from rasterio.warp import transform_bounds
    t = synthetic_tile["transform10"]
    # a window in UTM -> convert to WGS84 bbox
    west, north = t @ (21, 33)
    east, south = t @ (67, 71)
    bbox = transform_bounds(synthetic_tile["crs"], "EPSG:4326", west, south, east, north)
    grid = grid_for_aoi(bbox, synthetic_tile["crs"], t, (100, 100), snap=2)
    col0 = round((grid.transform.c - t.c) / 10)
    row0 = round((t.f - grid.transform.f) / 10)
    assert col0 % 2 == 0 and row0 % 2 == 0 and grid.width % 2 == 0 and grid.height % 2 == 0
    gw, gs, ge, gn = grid.bounds
    assert gw <= west + 1e-6 and ge >= east - 1e-6 and gs <= south + 1e-6 and gn >= north - 1e-6


def test_grid_for_aoi_rejects_aoi_outside_product(synthetic_tile):
    with pytest.raises(ValueError):
        grid_for_aoi((10.0, 10.0, 10.1, 10.1), synthetic_tile["crs"], synthetic_tile["transform10"], (100, 100))


def test_https_to_s3():
    url = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/30/T/VK/2024/3/X/B04.tif"
    assert https_to_s3(url) == "s3://sentinel-cogs/sentinel-s2-l2a-cogs/30/T/VK/2024/3/X/B04.tif"
    with pytest.raises(ValueError):
        https_to_s3("https://example.com/file.tif")


def test_binary_metrics():
    truth = np.array([1, 1, 1, 0, 0, 0, 0, 0], dtype=bool)
    pred = np.array([1, 1, 0, 1, 0, 0, 0, 0], dtype=bool)
    m = binary_metrics(truth, pred)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 1, 4)
    assert m["precision"] == pytest.approx(2 / 3)
    assert m["recall"] == pytest.approx(2 / 3)
    assert m["f1"] == pytest.approx(2 / 3)
    assert m["iou"] == pytest.approx(2 / 4)
    assert m["overall_accuracy"] == pytest.approx(6 / 8)


def test_worldcover_tile_for_madrid_and_other_hemispheres():
    assert worldcover_tile_name(-3.7, 40.4) == "N39W006"
    assert worldcover_tile_name(2.17, 41.39) == "N39E000"
    assert worldcover_tile_name(-70.0, -33.0) == "S33W072"


def test_worldcover_href_rejects_aoi_spanning_two_tiles():
    base = "https://example/map"
    assert worldcover_href(base, (-3.8, 40.37, -3.62, 40.48)).endswith("N39W006_Map.tif")
    with pytest.raises(NotImplementedError):
        worldcover_href(base, (-3.1, 40.0, -2.9, 40.1))   # crosses lon -3


def test_config_rejects_bad_input():
    from datetime import date
    with pytest.raises(ValueError):
        Config(name="x", bbox=(1, 1, 0, 0), start=date(2024, 1, 1), end=date(2024, 2, 1))
    with pytest.raises(ValueError):
        Config(name="x", bbox=(0, 0, 1, 1), start=date(2024, 3, 1), end=date(2024, 2, 1))
    with pytest.raises(ValueError):
        Config(name="x", bbox=(0, 0, 1, 1), start=date(2024, 1, 1), end=date(2024, 2, 1),
               cluster_features=("ndvi", "nonsense"))


def test_hub_request_pins_float32_and_true_10m_grid():
    """Regression for the original Hub script: AUTO sampleType gave 8-bit 0..255 values and a
    512x512 output gave non-square pixels."""
    req = build_request((-3.80, 40.37, -3.62, 40.48), "2024-03-01", "2024-03-31")
    assert 'sampleType: "FLOAT32"' in req["evalscript"]
    assert req["output"]["resx"] == req["output"]["resy"] == 10
    assert "width" not in req["output"] and "height" not in req["output"]
    assert req["input"]["bounds"]["properties"]["crs"].endswith("/32630")
    epsg, (w, s, e, n) = snapped_utm_bounds((-3.80, 40.37, -3.62, 40.48))
    assert epsg == 32630 and all(v % 20 == 0 for v in (w, s, e, n))
