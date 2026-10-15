"""`s2veg access-demo`: the SAME pixels reached different ways, with measured evidence.

Nothing printed here is hardcoded as a conclusion: every claim comes from a request made
at run time (HEAD/LIST/read), so the output stays truthful if bucket policies change.
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
from .raster import grid_for_aoi, https_to_s3, read_on_grid
from .stac import asset_grid_info, asset_scale_offset, covering_items, search_items


def _timed(label: str, fn):
    t0 = time.perf_counter()
    result = fn()
    print(f"  {label:<46s} {time.perf_counter() - t0:6.2f} s")
    return result


def _head(client, bucket: str, key: str, **kwargs) -> tuple[bool, str]:
    try:
        r = client.head_object(Bucket=bucket, Key=key, **kwargs)
        return True, f"OK, {r['ContentLength'] / 1e6:.1f} MB"
    except ClientError as exc:
        status = exc.response["ResponseMetadata"]["HTTPStatusCode"]
        return False, f"{exc.response['Error'].get('Code')} (HTTP {status})"


def _jp2_key_with_resolution_folder(stac_key: str) -> str:
    """The archive stores 10 m bands under .../<n>/R10m/<band>.jp2; STAC hrefs omit 'R10m/'."""
    folder, name = stac_key.rsplit("/", 1)
    return f"{folder}/R10m/{name}"


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

    # 1) Same COG, two transports ---------------------------------------------------------
    print("1) Same Cloud-Optimized GeoTIFF, same window, two transports")
    print(f"     https: {href}")
    print(f"     s3:    {s3_href}")
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        cog_https = _timed("COG over HTTPS (byte-range requests)", lambda: read_on_grid(href, grid, Resampling.nearest))
    with rasterio.Env(AWS_NO_SIGN_REQUEST="YES", AWS_REGION="us-west-2"):
        cog_s3 = _timed("COG over S3, unsigned (byte-range requests)", lambda: read_on_grid(s3_href, grid, Resampling.nearest))
    print(f"  identical pixels: {np.array_equal(cog_https, cog_s3)}")
    print("  -> a COG is internally tiled with an index, so only the bytes of the AOI cross the network.\n")

    # 2) Bucket access, measured --------------------------------------------------------
    print("2) Who can read which bucket without AWS credentials (boto3 UNSIGNED)?")
    anon_west = boto3.client("s3", region_name="us-west-2", config=BotoConfig(signature_version=UNSIGNED))
    cog_bucket, cog_key = s3_href.removeprefix("s3://").split("/", 1)
    ok_cog, msg = _head(anon_west, cog_bucket, cog_key)
    print(f"  COG bucket  s3://{cog_bucket}/...B04.tif                         {msg}")

    jp2 = item.assets.get("red-jp2")
    legacy_ok = None
    if jp2 is not None:
        legacy_bucket, stac_key = jp2.href.removeprefix("s3://").split("/", 1)
        real_key = _jp2_key_with_resolution_folder(stac_key)
        anon_eu = boto3.client("s3", region_name="eu-central-1", config=BotoConfig(signature_version=UNSIGNED))
        ok_stac, msg_stac = _head(anon_eu, legacy_bucket, stac_key)
        ok_real, msg_real = _head(anon_eu, legacy_bucket, real_key)
        ok_pay, msg_pay = _head(anon_eu, legacy_bucket, real_key, RequestPayer="requester")
        print(f"  JP2 bucket  s3://{legacy_bucket}  key as in STAC metadata       {msg_stac}")
        print(f"  JP2 bucket  s3://{legacy_bucket}  key with R10m/ folder         {msg_real}")
        print(f"  JP2 bucket  same key + RequestPayer=requester (unsigned)        {msg_pay}")
        legacy_ok = ok_real
        if ok_real:
            print("  -> this archive is ALSO readable anonymously today: no AWS account, no --request-payer needed.")
        else:
            print("  -> anonymous read denied: this archive would need AWS credentials (and possibly requester-pays).")
        if not ok_stac and ok_real:
            print("  -> the JP2 hrefs advertised by this catalogue omit the 'R10m/' folder (they do not resolve);"
                  " always verify a path before building a pipeline on it.")

    # 3) COG vs JP2: same band, same window, and a check of the radiometric offset --------
    if legacy_ok:
        print("\n3) COG vs original JP2 archive, same band and window")
        jp2_uri = f"s3://{legacy_bucket}/{real_key}"
        with rasterio.Env(AWS_NO_SIGN_REQUEST="YES", AWS_REGION="eu-central-1"):
            try:
                dn_jp2 = _timed("JP2 over S3, unsigned (window read)", lambda: read_on_grid(jp2_uri, grid, Resampling.nearest))
            except Exception as exc:  # GDAL raises several exception types for driver/network problems
                print(f"  JP2 read failed: {type(exc).__name__}: {exc}")
                dn_jp2 = None
        if dn_jp2 is not None:
            both = (cog_s3 > 0) & (dn_jp2 > 0)
            diff = cog_s3[both].astype(np.int32) - dn_jp2[both].astype(np.int32)
            scale, offset = asset_scale_offset(item, "red")
            print(f"  COG DN - JP2 DN: median={np.median(diff):.0f}  min={diff.min()}  max={diff.max()}")
            print(f"  STAC says reflectance = DN*{scale} + ({offset});  baseline={item.properties.get('s2:processing_baseline')};"
                  f" earthsearch:boa_offset_applied={item.properties.get('earthsearch:boa_offset_applied')}")
            print("  -> a constant median difference means the provider harmonised DN values; "
                  "this is why scale/offset must be read from the metadata of the SAME product you read.")

    print("\n4) Sentinel Hub Process API: see `s2veg hub-fetch` (needs SH_CLIENT_ID / SH_CLIENT_SECRET)")
