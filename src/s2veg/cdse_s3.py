"""Read original Sentinel-2 L2A JP2s from CDSE object storage (S3-compatible).

This is a *fourth* credential, not the OAuth client used by Process API / openEO,
and not anonymous AWS. Generate keys at
https://eodata-s3keysmanager.dataspace.copernicus.eu/ (secret shown once).

Why this is a demo and not `s2veg run`:
  JP2s are the ESA archive, so radiometry matches the SAFE product, but windowed
  reads are slower than COGs and GDAL must talk to a custom endpoint with path-style
  addressing (`AWS_VIRTUAL_HOSTING=FALSE`). The default pipeline stays on public COGs.
"""

from __future__ import annotations

import os
import time

import boto3
import numpy as np
import rasterio
from botocore.exceptions import ClientError
from rasterio.enums import Resampling

from .cdse import search
from .config import Config
from .raster import grid_for_aoi, read_on_grid
from .stac import covering_items

S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"
S3_HOST = "eodata.dataspace.copernicus.eu"
KEYS_URL = "https://eodata-s3keysmanager.dataspace.copernicus.eu/"
RED_ASSET = "B04_10m"


def credentials() -> tuple[str, str]:
    access, secret = os.getenv("CDSE_S3_ACCESS_KEY"), os.getenv("CDSE_S3_SECRET_KEY")
    if not access or not secret:
        raise RuntimeError(
            "Set CDSE_S3_ACCESS_KEY and CDSE_S3_SECRET_KEY in .env. "
            f"Create them at {KEYS_URL} (not SH_CLIENT_ID, not a Planet key)."
        )
    return access, secret


def parse_s3_uri(href: str) -> tuple[str, str]:
    """Return (bucket, key) from an eodata STAC href."""
    href = href.strip()
    if href.startswith("s3://"):
        rest = href[5:]
        bucket, _, key = rest.partition("/")
        if bucket and key:
            return bucket, key
    if href.startswith("/eodata/"):
        return "eodata", href[len("/eodata/"):]
    if href.startswith("eodata/"):
        return "eodata", href[len("eodata/"):]
    raise ValueError(f"not an eodata S3 href: {href[:120]}")


def rasterio_session(access_key: str, secret_key: str):
    """Rasterio 1.4+ refuses AWS_ACCESS_KEY_ID inside Env(); credentials go through boto3."""
    from rasterio.session import AWSSession
    return AWSSession(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        endpoint_url=S3_ENDPOINT,
        region_name="default",
    )


def gdal_options() -> dict[str, str]:
    """Path-style vsis3 on the CDSE host. Do not put access keys here."""
    return {
        "AWS_S3_ENDPOINT": S3_HOST,
        "AWS_HTTPS": "YES",
        "AWS_VIRTUAL_HOSTING": "FALSE",
        "AWS_DEFAULT_REGION": "default",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".jp2,.JP2,.tif,.tiff",
    }


def s3_client(access_key: str, secret_key: str):
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="default",
    )


def jp2_offset(baseline: str | None) -> float:
    """ESA BOA_ADD_OFFSET: −0.1 reflectance from processing baseline 04.00 onwards."""
    if not baseline:
        return -0.1
    try:
        return -0.1 if float(baseline) >= 4.0 else 0.0
    except ValueError:
        return -0.1


def _red_href(item) -> str:
    asset = item.assets.get(RED_ASSET)
    if asset is None or not asset.href:
        raise RuntimeError(f"{item.id} has no {RED_ASSET} asset")
    return asset.href


def run_demo(cfg: Config) -> None:
    access_key, secret_key = credentials()
    items = covering_items(search(cfg, limit=100), cfg.bbox)
    if not items:
        raise RuntimeError("no CDSE scene covers the AOI; widen dates or cloud pre-filter")
    item = items[0]
    href = _red_href(item)
    bucket, key = parse_s3_uri(href)
    uri = f"s3://{bucket}/{key}"
    baseline = item.properties.get("s2:processing_baseline") or item.properties.get("processing:version")
    offset = jp2_offset(str(baseline) if baseline is not None else None)

    print(f"CDSE S3 {S3_ENDPOINT}")
    print(f"scene {item.id}")
    print(f"  {uri}")
    print("  (OAuth SH_CLIENT_ID is not used here)")

    client = s3_client(access_key, secret_key)
    t0 = time.perf_counter()
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response["Error"].get("Code", "")
        status = exc.response["ResponseMetadata"]["HTTPStatusCode"]
        hint = {403: "wrong or expired S3 keys, or the key has no eodata access",
                404: "object not at this path; check the STAC href"}.get(status, "see response")
        raise RuntimeError(f"HEAD s3://{bucket}/{key} failed: HTTP {status} {code} ({hint})") from exc
    print(f"  HEAD {head['ContentLength'] / 1e6:.1f} MB  in {time.perf_counter() - t0:.2f} s")

    with rasterio.Env(session=rasterio_session(access_key, secret_key), **gdal_options()):
        t0 = time.perf_counter()
        with rasterio.open(uri) as ds:
            grid = grid_for_aoi(cfg.bbox, ds.crs, ds.transform, (ds.height, ds.width))
        dn = read_on_grid(uri, grid, Resampling.nearest)
        print(f"  JP2 window {grid.width}x{grid.height}  in {time.perf_counter() - t0:.2f} s  "
              f"(expect slower than a COG)")

    valid = dn > 0
    print(f"  DN min={int(dn[valid].min()) if valid.any() else 'n/a'}  "
          f"max={int(dn[valid].max()) if valid.any() else 'n/a'}  "
          f"baseline={baseline}  offset={offset}")
    if valid.any():
        refl = dn.astype(np.float32) * np.float32(0.0001) + np.float32(offset)
        print(f"  reflectance (DN*0.0001{offset:+})  "
              f"min={float(refl[valid].min()):.4f}  max={float(refl[valid].max()):.4f}  "
              f"(water should stay near 0, not −0.1)")
    print("  This archive is the original JP2. Earth Search COGs of the same scene may already "
          "have the offset applied; do not copy this offset onto those COGs.")
