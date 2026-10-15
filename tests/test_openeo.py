from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from s2veg import openeo_ndvi
from s2veg.config import Config
from s2veg.hub import snapped_utm_bounds


def test_spatial_extent_matches_process_api_utm_box():
    bbox = (-3.80, 40.37, -3.62, 40.48)
    ext = openeo_ndvi.spatial_extent(bbox)
    epsg, bounds = snapped_utm_bounds(bbox)
    assert ext["crs"] == f"EPSG:{epsg}"
    assert (ext["west"], ext["south"], ext["east"], ext["north"]) == bounds
    assert all(v % 20 == 0 for v in bounds)


def test_temporal_extent_is_half_open_and_includes_the_last_day():
    assert openeo_ndvi.temporal_extent("2024-03-01", "2024-03-31") == ["2024-03-01", "2024-04-01"]


def test_scl_invalid_is_the_complement_of_the_pipeline_keep_list():
    cfg = Config(name="t", bbox=(-3.8, 40.37, -3.62, 40.48),
                 start=date(2024, 3, 1), end=date(2024, 3, 31))
    invalid = openeo_ndvi.scl_invalid_classes(cfg.scl_valid)
    assert set(cfg.scl_valid) & set(invalid) == set()
    assert set(cfg.scl_valid) | set(invalid) == set(range(12))


def test_connect_refuses_a_planet_oauth_client(monkeypatch):
    monkeypatch.setenv("SH_PROVIDER", "sentinelhub")
    monkeypatch.setenv("SH_CLIENT_ID", "sh-x")
    monkeypatch.setenv("SH_CLIENT_SECRET", "secret")
    with pytest.raises(RuntimeError, match="SH_PROVIDER=cdse"):
        openeo_ndvi.connect()


def test_build_cube_loads_l2a_ndvi_and_median():
    conn = MagicMock()
    cube = MagicMock()
    ndvi = MagicMock()
    masked = MagicMock()
    reduced = MagicMock()
    conn.load_collection.return_value = cube
    cube.ndvi.return_value = ndvi
    ndvi.mask.return_value = masked
    masked.median_time.return_value = reduced

    out = openeo_ndvi.build_cube(conn, (-3.80, 40.37, -3.62, 40.48), "2024-03-01", "2024-03-31")

    kwargs = conn.load_collection.call_args
    assert kwargs.args[0] == "SENTINEL2_L2A"
    assert kwargs.kwargs["bands"] == ["B04", "B08", "SCL"]
    assert kwargs.kwargs["temporal_extent"] == ["2024-03-01", "2024-04-01"]
    assert kwargs.kwargs["spatial_extent"]["crs"] == "EPSG:32630"
    cube.ndvi.assert_called_once_with(nir="B08", red="B04")
    cube.band.assert_called_once_with("SCL")
    masked.median_time.assert_called_once_with()
    reduced.resample_spatial.assert_called_once()
    rs = reduced.resample_spatial.call_args.kwargs
    assert rs["projection"] == "EPSG:32630"
    assert rs["resolution"] == 10.0
    assert rs["method"] == "bilinear"
    assert out is reduced.resample_spatial.return_value


def test_drop_nonphysical_ndvi_nans_the_9_85_pixel(tmp_path):
    path = tmp_path / "ndvi.tif"
    data = np.array([[0.3, 9.85], [-0.2, np.nan]], dtype=np.float32)
    profile = dict(driver="GTiff", height=2, width=2, count=1, dtype="float32",
                   crs="EPSG:32630", transform=from_origin(0, 20, 10, 10), nodata=np.nan)
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(data, 1)
    out, n_bad = openeo_ndvi.drop_nonphysical_ndvi(path)
    assert n_bad == 1
    assert np.isnan(out[0, 1])
    assert out[0, 0] == pytest.approx(0.3)
    with rasterio.open(path) as ds:
        assert np.isnan(ds.read(1)[0, 1])
