"""Scene discovery via a STAC catalogue (Element84 Earth Search by default).

STAC separates *finding* data (a searchable catalogue of metadata) from *reading* it
(the assets are Cloud-Optimized GeoTIFFs we can read by byte range). The old code
hardcoded a tile ID and S3 paths; here the catalogue tells us what exists and where.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

import pystac
from affine import Affine
from pystac_client import Client
from rasterio.crs import CRS
from shapely.geometry import box, shape

from .config import Config
from .raster import https_to_s3


def search_items(cfg: Config) -> list[pystac.Item]:
    client = Client.open(cfg.stac_url)
    search = client.search(
        collections=[cfg.collection],
        bbox=cfg.bbox,
        datetime=f"{cfg.start.isoformat()}/{cfg.end.isoformat()}",
        query={"eo:cloud_cover": {"lt": cfg.max_scene_cloud}},
        limit=100,
    )
    items = list(search.items())
    items.sort(key=lambda it: it.datetime)
    return items


def covering_items(items: Sequence[pystac.Item], bbox: tuple[float, float, float, float]) -> list[pystac.Item]:
    """Keep scenes whose data footprint fully contains the AOI, from a single MGRS tile.

    Orbit-edge scenes can have a large no-data wedge; if the footprint does not contain the
    AOI we would only read zeros. When two tiles overlap the AOI we keep the one with more
    scenes, so every scene shares the exact same pixel grid.
    """
    aoi = box(*bbox)
    covering = [it for it in items if shape(it.geometry).contains(aoi)]
    if not covering:
        return []
    tiles = Counter(it.properties.get("grid:code", it.properties.get("proj:epsg")) for it in covering)
    best, _ = tiles.most_common(1)[0]
    return [it for it in covering if it.properties.get("grid:code", it.properties.get("proj:epsg")) == best]


def asset_href(item: pystac.Item, key: str, access: str = "https") -> str:
    href = item.assets[key].href
    return https_to_s3(href) if access == "s3" else href


def asset_scale_offset(item: pystac.Item, key: str) -> tuple[float, float]:
    """Scale/offset from the asset metadata. No silent defaults: missing metadata is an error."""
    bands = item.assets[key].extra_fields.get("raster:bands")
    if not bands or "scale" not in bands[0] or "offset" not in bands[0]:
        raise KeyError(f"asset '{key}' of {item.id} has no raster:bands scale/offset metadata")
    return float(bands[0]["scale"]), float(bands[0]["offset"])


def asset_grid_info(item: pystac.Item, key: str) -> tuple[CRS, Affine, tuple[int, int]]:
    """CRS, affine transform and (rows, cols) of an asset, straight from STAC metadata."""
    epsg = item.properties["proj:epsg"]
    fields = item.assets[key].extra_fields
    a, b, c, d, e, f = fields["proj:transform"][:6]
    rows, cols = fields["proj:shape"]
    return CRS.from_epsg(epsg), Affine(a, b, c, d, e, f), (int(rows), int(cols))
