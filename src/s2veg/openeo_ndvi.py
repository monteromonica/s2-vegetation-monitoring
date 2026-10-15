"""openEO on Copernicus Data Space: server-side NDVI from SENTINEL2_L2A.

This is the third access path in the project, alongside Sentinel Hub Process API
(`hub.py`) and STAC+COG (`stac.py` / `pipeline.py`).

  * You describe a process graph (load collection → mask → index → reduce).
  * The backend executes it next to the archive. You download the result, not the scenes.
  * Auth is OIDC. Browser login (`authenticate_oidc`) is the notebook default; here we use
    the same OAuth *client credentials* as Process API, which CDSE documents as valid for
    openEO machine-to-machine use.

What this is not: it does not replace `s2veg run`. The local pipeline still owns grids,
offset handling and WorldCover validation. This command is a small, comparable NDVI
composite so the three APIs can be discussed with the same AOI.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import rasterio

from .hub import credentials, snapped_utm_bounds, utm_epsg
from .scl import DEFAULT_VALID

OPENEO_URL = "https://openeo.dataspace.copernicus.eu"
COLLECTION = "SENTINEL2_L2A"
NDVI_RANGE = (-1.0, 1.0)


def require_cdse() -> None:
    name = os.getenv("SH_PROVIDER", "sentinelhub").lower()
    if name != "cdse":
        raise RuntimeError(
            "openEO in this project talks to CDSE (openeo.dataspace.copernicus.eu). "
            f"Set SH_PROVIDER=cdse (got '{name}'). A Planet OAuth client will not work here."
        )


def spatial_extent(bbox: tuple[float, float, float, float]) -> dict:
    """Same snapped UTM box as Process API, so the two cut-outs can share a grid."""
    epsg, (west, south, east, north) = snapped_utm_bounds(bbox)
    return {"west": west, "south": south, "east": east, "north": north, "crs": f"EPSG:{epsg}"}


def temporal_extent(start: str, end: str) -> list[str]:
    """openEO temporal intervals are half-open [start, end). Include the last calendar day."""
    return [start, (date.fromisoformat(end) + timedelta(days=1)).isoformat()]


def scl_invalid_classes(valid: tuple[int, ...] = DEFAULT_VALID) -> tuple[int, ...]:
    """Complement of the keep-list (default 4/5/6/7). Masking invalid classes avoids cube NOT."""
    keep = set(valid)
    return tuple(c for c in range(12) if c not in keep)


def connect():
    """Machine-to-machine OIDC. Reuses SH_CLIENT_ID / SH_CLIENT_SECRET. Never prints secrets."""
    require_cdse()
    import openeo

    client_id, client_secret = credentials()
    connection = openeo.connect(OPENEO_URL)
    connection.authenticate_oidc_client_credentials(
        client_id=client_id,
        client_secret=client_secret,
    )
    return connection


def scl_invalid_mask(scl, invalid_classes: tuple[int, ...] = scl_invalid_classes()):
    mask = None
    for cls in invalid_classes:
        cond = scl == cls
        mask = cond if mask is None else (mask | cond)
    return mask


def build_cube(connection, bbox: tuple[float, float, float, float], start: str, end: str,
               scl_valid: tuple[int, ...] = DEFAULT_VALID):
    """Median NDVI, SCL-masked, resampled to 10 m UTM. Returns an openEO DataCube (lazy)."""
    cube = connection.load_collection(
        COLLECTION,
        spatial_extent=spatial_extent(bbox),
        temporal_extent=temporal_extent(start, end),
        bands=["B04", "B08", "SCL"],
        max_cloud_cover=80,
    )
    ndvi = cube.ndvi(nir="B08", red="B04")
    masked = ndvi.mask(scl_invalid_mask(cube.band("SCL"), scl_invalid_classes(scl_valid)))
    lon0, lat0, lon1, lat1 = bbox
    epsg = utm_epsg((lon0 + lon1) / 2, (lat0 + lat1) / 2)
    return masked.median_time().resample_spatial(
        resolution=10.0, projection=f"EPSG:{epsg}", method="bilinear"
    )


def account_summary(connection) -> dict:
    """openEO account as returned by the backend (may be the linked personal login). No tokens."""
    info = connection.describe_account() or {}
    return {k: info.get(k) for k in ("user_id", "name", "default_plan") if info.get(k)}


def drop_nonphysical_ndvi(path: Path) -> tuple[np.ndarray, int]:
    """NaN-out NDVI outside [-1, 1] (backend bilinear smear / tiny denominators) and rewrite."""
    with rasterio.open(path) as ds:
        data = ds.read(1).astype(np.float32)
        profile = ds.profile
    finite = np.isfinite(data)
    bad = finite & ((data < NDVI_RANGE[0]) | (data > NDVI_RANGE[1]))
    n_bad = int(bad.sum())
    if n_bad:
        data[bad] = np.nan
        profile.update(dtype="float32", nodata=np.nan)
        with rasterio.open(path, "w", **profile) as ds:
            ds.write(data, 1)
    return data, n_bad


def fetch(bbox: tuple[float, float, float, float], start: str, end: str, out_path: str | Path,
          scl_valid: tuple[int, ...] = DEFAULT_VALID) -> Path:
    connection = connect()
    summary = account_summary(connection)
    print(f"openEO {OPENEO_URL}")
    print(f"collection={COLLECTION}  {start} → {end}  (interval half-open, last day included)")
    for k, v in summary.items():
        print(f"  account {k}: {v}")

    cube = build_cube(connection, bbox, start, end, scl_valid=scl_valid)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cube.download(str(out_path), format="GTiff")
    except Exception as exc:
        text = str(exc).lower()
        if any(w in text for w in ("credit", "quota", "budget", "payment")):
            raise RuntimeError(
                "openEO refused the job (credits/quota). Client-credentials is a *service account* "
                "with a separate balance from your personal CDSE login unless support has linked "
                f"them. Original error: {exc}"
            ) from exc
        raise

    data, n_bad = drop_nonphysical_ndvi(out_path)
    with rasterio.open(out_path) as ds:
        print(f"wrote {out_path}  CRS={ds.crs}  size={ds.width}x{ds.height}  res={ds.res}")
    if n_bad:
        print(f"dropped {n_bad} pixel(s) with NDVI outside [{NDVI_RANGE[0]}, {NDVI_RANGE[1]}] "
              "(not physical; typical of nodata smear after bilinear resampling)")
    finite = data[np.isfinite(data)]
    if finite.size:
        print(f"NDVI min={finite.min():.4f} max={finite.max():.4f} mean={finite.mean():.4f}  "
              f"valid={finite.size}/{data.size} ({100 * finite.size / data.size:.1f}%)")
    else:
        print("NDVI: no finite pixels (check clouds / SCL / date window)")
    return out_path
