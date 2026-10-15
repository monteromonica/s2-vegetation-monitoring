"""`s2veg access-demo`: the SAME pixels reached three different ways, with evidence.

Built to answer, with observations instead of memory:
  * What is the difference between reading a COG over HTTPS and over S3?
  * Why did the old code need AWS credentials ('requester pays')?
"""

from __future__ import annotations

import time

import boto3
import numpy as np
import rasterio
from botocore import UNSIGNED
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from rasterio.enums import Resampling

from .config import Config
from .raster import https_to_s3, read_on_grid, grid_for_aoi
from .stac import asset_grid_info, covering_items, search_items


def _timed(label: str, fn):
    t0 = time.perf_counter()
    result = fn()
    print(f"  {label:<44s} {time.perf_counter() - t0:6.2f} s")
    return result


def _head(client, bucket: str, key: str, **kwargs) -> str:
    try:
        r = client.head_object(Bucket=bucket, Key=key, **kwargs)
        return f"OK  size={r['ContentLength'] / 1e6:.1f} MB"
    except ClientError as exc:
        code = exc.response["Error"].get("Code")
        return f"DENIED ({code})"


def run_demo(cfg: Config) -> None:
    items = covering_items(search_items(cfg), cfg.bbox)
    if not items:
        raise RuntimeError("no scene covers the AOI in the configured dates")
    item = items[0]
    crs, transform, shape = asset_grid_info(item, "red")
    grid = grid_for_aoi(cfg.bbox, crs, transform, shape)
    href = item.assets["red"].href
    s3_href = https_to_s3(href)
    print(f"Scene {item.id}   AOI window {grid.width}x{grid.height} px\n")

    print("1) Same COG, same window, two transports")
    print(f"     https: {href}")
    print(f"     s3:    {s3_href}")
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        a = _timed("HTTPS range requests", lambda: read_on_grid(href, grid, Resampling.nearest))
    with rasterio.Env(AWS_NO_SIGN_REQUEST="YES", AWS_REGION="us-west-2"):
        b = _timed("S3 (unsigned) range requests", lambda: read_on_grid(s3_href, grid, Resampling.nearest))
    print(f"  identical pixels: {np.array_equal(a, b)}   (a COG is read by byte range: only the AOI is downloaded)\n")

    print("2) Who can read which bucket? (anonymous = no AWS credentials)")
    anon = boto3.client("s3", config=BotoConfig(signature_version=UNSIGNED))
    cog_bucket, cog_key = s3_href.removeprefix("s3://").split("/", 1)
    print(f"  COG bucket   s3://{cog_bucket}  anonymous: {_head(anon, cog_bucket, cog_key)}")

    jp2 = item.assets.get("red-jp2")
    if jp2 is not None:
        legacy_bucket, legacy_key = jp2.href.removeprefix("s3://").split("/", 1)
        anon_eu = boto3.client("s3", region_name="eu-central-1", config=BotoConfig(signature_version=UNSIGNED))
        print(f"  JP2 archive  s3://{legacy_bucket}  anonymous: {_head(anon_eu, legacy_bucket, legacy_key)}")
        print(f"  JP2 archive  s3://{legacy_bucket}  anonymous + RequestPayer: "
              f"{_head(anon_eu, legacy_bucket, legacy_key, RequestPayer='requester')}")
        print("  -> 'requester pays' buckets reject anonymous requests: you need an AWS account and you pay"
              " the data transfer. That is why the old S3 route needed credentials.")

    print("\n3) Sentinel Hub Process API: see `s2veg hub-fetch` (needs SH_CLIENT_ID / SH_CLIENT_SECRET)")
