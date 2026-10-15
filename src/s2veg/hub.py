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

# Sentinel Hub is offered by two providers with separate accounts and separate OAuth clients.
# Token endpoints below are from the official docs (docs.sentinel-hub.com and
# documentation.dataspace.copernicus.eu). CDSE Process API has been exercised
# (`s2veg hub-fetch` with SH_PROVIDER=cdse).
PROVIDERS = {
    "sentinelhub": {   # Planet's commercial service (free trial, then paid)
        "token_url": "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token",
        "process_url": "https://services.sentinel-hub.com/api/v1/process",
    },
    "cdse": {          # Copernicus Data Space Ecosystem (free for registered users)
        "token_url": "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
        "process_url": "https://sh.dataspace.copernicus.eu/api/v1/process",
    },
}


def provider_urls() -> dict[str, str]:
    name = os.getenv("SH_PROVIDER", "sentinelhub").lower()
    if name not in PROVIDERS:
        raise RuntimeError(f"SH_PROVIDER must be one of {sorted(PROVIDERS)}, got '{name}'")
    return PROVIDERS[name]

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


def credentials() -> tuple[str, str]:
    client_id, client_secret = os.getenv("SH_CLIENT_ID"), os.getenv("SH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("Set SH_CLIENT_ID and SH_CLIENT_SECRET in .env (see README, 'Sentinel Hub').")
    return client_id, client_secret


def get_token(client_id: str, client_secret: str) -> str:
    """OAuth2 *client credentials* flow: machine-to-machine, no user login or browser.

    You exchange (client_id, client_secret) for a short-lived bearer token (a JWT) and send it
    as `Authorization: Bearer <token>`. Request ONE token and reuse it until it expires:
    token requests are rate-limited (HTTP 429) per the docs.
    """
    resp = requests.post(
        provider_urls()["token_url"],
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        timeout=30,
    )
    if resp.status_code != 200:
        hint = {400: "malformed request or wrong grant type", 401: "wrong client id/secret, or the client expired",
                429: "too many token requests; reuse the token"}.get(resp.status_code, "see response")
        raise RuntimeError(f"token request failed: HTTP {resp.status_code} ({hint}): {resp.text[:200]}")
    return resp.json()["access_token"]


def describe_token(token: str) -> dict:
    """Decode (WITHOUT verifying) the JWT payload to show when it expires. Never prints the token."""
    import base64
    import json
    from datetime import datetime, timezone

    payload_b64 = token.split(".")[1]
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    exp = payload.get("exp")
    return {
        "issuer": payload.get("iss"),
        "expires_utc": datetime.fromtimestamp(exp, tz=timezone.utc).isoformat() if exp else None,
        "lifetime_s": (exp - payload["iat"]) if exp and "iat" in payload else None,
    }


def check_auth() -> dict:
    """Authenticate only. Cheap way to debug OAuth before sending any data request."""
    token = get_token(*credentials())
    return describe_token(token)


def fetch(bbox: tuple[float, float, float, float], start: str, end: str, out_path: str | Path) -> Path:
    token = get_token(*credentials())
    resp = requests.post(
        provider_urls()["process_url"], json=build_request(bbox, start, end),
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
    print(f"SCL   unique={sorted(np.unique(data[4]).astype(int).tolist())}  (band 5; not reflectance)")
    return out_path
