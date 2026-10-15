"""Copernicus Data Space Ecosystem catalogue (public STAC). No credentials needed.

CDSE is the official EU access layer for Copernicus data. It exposes several APIs; this
module uses only the *catalogue* (STAC), which is public. Processing APIs (Sentinel Hub
Process, openEO) and the original-file S3 archive (`eodata`) each have their own credentials
and are wired elsewhere (`hub.py`, `openeo_ndvi.py`, `cdse_s3.py`, README).

Why this is not the default data source for `s2veg run`:
  Earth Search items point at public Cloud-Optimized GeoTIFFs we can window-read anonymously.
  CDSE STAC items point at original SAFE JP2 files on `s3://eodata/...`, which need CDSE S3
  keys, and at HTTPS OData URLs, which need an OIDC token. Same scenes, different packaging.
"""

from __future__ import annotations

from collections import Counter

from pystac_client import Client

from .config import Config

CDSE_STAC = "https://stac.dataspace.copernicus.eu/v1"
# Earth Search names assets `red`/`nir`; CDSE names them `B04_10m`/`B08_10m`.
CDSE_COLLECTION = "sentinel-2-l2a"


def search(cfg: Config, limit: int = 20) -> list:
    client = Client.open(CDSE_STAC)
    items = list(client.search(
        collections=[CDSE_COLLECTION],
        bbox=cfg.bbox,
        datetime=f"{cfg.start.isoformat()}/{cfg.end.isoformat()}",
        limit=limit,
    ).items())
    items.sort(key=lambda it: it.datetime or "")
    return items


def summarise(item) -> dict:
    p = item.properties
    red = item.assets.get("B04_10m")
    href = red.href if red is not None else ""
    https = ""
    if red is not None:
        alt = red.extra_fields.get("alternate") or {}
        https = (alt.get("https") or {}).get("href", "")
    return {
        "id": item.id,
        "datetime": item.datetime.isoformat() if item.datetime else None,
        "cloud": p.get("eo:cloud_cover"),
        "baseline": p.get("processing:version"),
        "tile": p.get("grid:code"),
        "s3": href,
        "https_odata": https[:90] + ("..." if len(https) > 90 else ""),
        "n_assets": len(item.assets),
    }


def print_search(cfg: Config, limit: int = 12) -> None:
    items = search(cfg, limit=100)
    print(f"CDSE STAC {CDSE_STAC}")
    print(f"collection={CDSE_COLLECTION}  AOI={cfg.bbox}  {cfg.start} → {cfg.end}")
    print(f"{len(items)} items (showing {min(limit, len(items))})\n")
    print(f"{'id':<62s} {'cloud%':>7s} {'baseline':>8s} {'tile':>12s}")
    for it in items[:limit]:
        s = summarise(it)
        cloud = s["cloud"] if s["cloud"] is not None else float("nan")
        print(f"{s['id']:<62s} {cloud:7.1f} {str(s['baseline'] or '?'):>8s} {str(s['tile'] or '?'):>12s}")
    if items:
        s = summarise(items[0])
        print("\nFirst item packaging (this is why we still process via Earth Search COGs):")
        print(f"  assets: {s['n_assets']} (SAFE JP2s, not a 4-band COG)")
        print(f"  S3:     {s['s3']}")
        print(f"  HTTPS:  {s['https_odata']}")
        print("  S3 needs CDSE object-storage keys (s3.dataspace.copernicus.eu).")
        print("  HTTPS needs an OIDC token. Sentinel Hub Process API needs an OAuth *client*.")
        print("  Those three credentials are not User ID / Account ID, and not a Planet API key.")
    print("\nBaselines present:", dict(Counter(summarise(i)["baseline"] for i in items)))
