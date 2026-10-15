import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin


@pytest.fixture
def synthetic_tile(tmp_path):
    """A 10 m raster and a 20 m raster on the same Sentinel-2-like grid (UTM 30N).

    10 m value at (row, col) = row * 1000 + col.
    20 m value at (row, col) = row * 1000 + col too, at 20 m indexing, so we can verify
    that upsampling maps each 20 m pixel onto the right 2x2 block of 10 m pixels.
    """
    crs = CRS.from_epsg(32630)
    origin_x, origin_y = 399960.0, 4500000.0

    def write(path, size, res, dtype="uint16"):
        rows, cols = np.indices((size, size))
        data = (rows * 1000 + cols).astype(dtype)
        profile = dict(driver="GTiff", height=size, width=size, count=1, dtype=dtype, crs=crs,
                       transform=from_origin(origin_x, origin_y, res, res), nodata=0)
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data, 1)
        return str(path)

    return {
        "crs": crs,
        "transform10": from_origin(origin_x, origin_y, 10, 10),
        "path10": write(tmp_path / "b10.tif", 100, 10),
        "path20": write(tmp_path / "scl20.tif", 50, 20),
    }
