"""Sentinel Hub Process API: server-side processing (needs a free/paid account).

Difference with the STAC/COG route:
  * Process API: you send an AOI + an evalscript, the SERVER crops, mosaics and resamples,
    and returns pixels. Great for a quick answer, but it needs credentials, it consumes
    processing units, and the server decides the grid unless you pin it.
  * STAC + COG: you find scenes in a catalogue and read the raw bytes you need yourself.
    No credentials, fully reproducible, but you do the masking/compositing.

Two traps the first version of this project fell into, both fixed here:
  1. Output type. With the default `sampleType: AUTO` the server rescales reflectance 0..1
     to 8-bit 0..255. Reflectance indices (EVI has a '+1') then silently break. We request
     FLOAT32 explicitly.
  2. Grid. Passing width/height=512 over an arbitrary bbox gives non-square pixels that are
     not the native 10 m. We request resx=resy=10 in a UTM CRS and snap the box to 20 m.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.warp import transform_bounds

TOKEN_URL = "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token"
PROCESS_URL = "https://services.sentinel-hub.com/api/v1/process"

EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: ["B02", "B03", "B04", "B08", "SCL"],
    output: { bands: 5, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(s) {
  return [s.B02, s.B03, s.B04, s.B08, s.SCL];
}
"""


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def snapped_utm_bounds(bbox: tuple[float, float, float, float], step: int = 20):
    lon0, lat0, lon1, lat1 = bbox
    epsg = utm_epsg((lon0 + lon1) / 2, (lat0 + lat1) / 2)
    west, south, east, north = transform_bounds("EPSG:4326", f"EPSG:{epsg}", *bbox, densify_pts=21)
    west = math.floor(west / step) * step
    south = math.floor(south / step) * step
    east = math.ceil(east / step) * step
    north = math.ceil(north / step) * step
    return epsg, (west, south, east, north)


def build_request(bbox: tuple[float, float, float, float], start: str, end: str,
                  resolution: int = 10) -> dict:
    epsg, bounds = snapped_utm_bounds(bbox)
    return {
        "input": {
            "bounds": {
                "bbox": list(bounds),
                "properties": {"crs": f"http://www.opengis.net/def/crs/EPSG/0/{epsg}"},
            },
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {"from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"},
                    "mosaickingOrder": "leastCC",
                },
            }],
        },
        "output": {
            "resx": resolution, "resy": resolution,
            "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}],
        },
        "evalscript": EVALSCRIPT,
    }


def get_token(client_id: str, client_secret: str) -> str:
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        timeout=30,
    )
    resp.raise_for_status()  # fail loudly: the first version called .json()["access_token"] blindly
    return resp.json()["access_token"]


def fetch(bbox: tuple[float, float, float, float], start: str, end: str, out_path: str | Path) -> Path:
    client_id, client_secret = os.getenv("SH_CLIENT_ID"), os.getenv("SH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("Set SH_CLIENT_ID and SH_CLIENT_SECRET (e.g. in .env) to use Sentinel Hub.")

    token = get_token(client_id, client_secret)
    resp = requests.post(
        PROCESS_URL, json=build_request(bbox, start, end),
        headers={"Authorization": f"Bearer {token}", "Accept": "image/tiff"}, timeout=120,
    )
    resp.raise_for_status()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)

    with rasterio.open(out_path) as ds:
        data = ds.read()
    names = ("blue", "green", "red", "nir")
    for i, n in enumerate(names):
        print(f"{n:5s} min={np.nanmin(data[i]):.4f} max={np.nanmax(data[i]):.4f}  (expect reflectance ~0..1)")
    return out_path
