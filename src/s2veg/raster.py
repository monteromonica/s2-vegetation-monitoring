"""Raster grid handling: one explicit target grid, everything is read onto it.

The old project mixed grids silently (512x512 pixels over an arbitrary bbox, a whole
tile on the other path). Here we define the grid once, from the product's own
georeferencing, and force every raster (10 m bands, 20 m SCL, 10 m WorldCover) onto it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import array_bounds
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds
from rasterio.windows import transform as window_transform


@dataclass(frozen=True)
class Grid:
    crs: CRS
    transform: Affine
    width: int
    height: int

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) in the grid CRS."""
        return array_bounds(self.height, self.width, self.transform)

    @property
    def pixel_size(self) -> float:
        return abs(self.transform.a)


def grid_for_aoi(
    bbox_wgs84: tuple[float, float, float, float],
    crs: CRS,
    product_transform: Affine,
    product_shape: tuple[int, int],
    snap: int = 2,
) -> Grid:
    """Smallest pixel-aligned window of the product grid that contains the AOI.

    The window is snapped outward to a multiple of `snap` pixels. With snap=2 a 10 m
    window maps exactly onto the 20 m grid (the SCL cloud mask), so upsampling the mask
    is an exact 2x2 replication instead of a half-pixel shift.
    """
    west, south, east, north = transform_bounds("EPSG:4326", crs, *bbox_wgs84, densify_pts=21)
    win = from_bounds(west, south, east, north, transform=product_transform)

    col0 = math.floor(win.col_off)
    row0 = math.floor(win.row_off)
    col1 = math.ceil(win.col_off + win.width)
    row1 = math.ceil(win.row_off + win.height)

    col0 -= col0 % snap
    row0 -= row0 % snap
    col1 += (-col1) % snap
    row1 += (-row1) % snap

    rows, cols = product_shape
    col0, row0 = max(col0, 0), max(row0, 0)
    col1, row1 = min(col1, cols), min(row1, rows)
    if col1 <= col0 or row1 <= row0:
        raise ValueError("AOI does not intersect the product grid")

    window = Window(col0, row0, col1 - col0, row1 - row0)
    return Grid(crs=crs, transform=window_transform(window, product_transform),
                width=int(window.width), height=int(window.height))


def read_on_grid(href: str, grid: Grid, resampling: Resampling) -> np.ndarray:
    """Read band 1 of `href` onto `grid`.

    Use Resampling.nearest for categorical data (SCL, land cover). Bilinear/cubic would
    invent classes that do not exist (e.g. 'class 5.5').
    """
    with rasterio.open(href) as ds:
        if ds.crs != grid.crs:
            raise ValueError(f"CRS mismatch: {ds.crs} vs {grid.crs} for {href}")
        win = from_bounds(*grid.bounds, transform=ds.transform)
        for off in (win.col_off, win.row_off):
            if abs(off - round(off)) > 1e-6:
                raise ValueError(f"window not pixel-aligned in {href}: offset {off}")
        win = Window(round(win.col_off), round(win.row_off), round(win.width), round(win.height))
        return ds.read(1, window=win, out_shape=grid.shape, resampling=resampling)


def write_geotiff(path: str | Path, array: np.ndarray, grid: Grid, nodata) -> None:
    arr = array if array.ndim == 3 else array[None]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff", "height": grid.height, "width": grid.width, "count": arr.shape[0],
        "dtype": arr.dtype, "crs": grid.crs, "transform": grid.transform,
        "nodata": nodata, "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr)


def https_to_s3(href: str) -> str:
    """https://<bucket>.s3.<region>.amazonaws.com/<key>  ->  s3://<bucket>/<key>"""
    from urllib.parse import urlparse

    parsed = urlparse(href)
    host = parsed.netloc
    if not host.endswith(".amazonaws.com") or ".s3" not in host:
        raise ValueError(f"not an S3 https URL: {href}")
    bucket = host.split(".s3", 1)[0]
    return f"s3://{bucket}{parsed.path}"
