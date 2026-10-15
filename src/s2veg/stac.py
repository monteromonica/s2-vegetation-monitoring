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
    def tile_key(it: pystac.Item) -> str:
        p = it.properties
        return p.get("grid:code") or f"{p.get('mgrs:utm_zone')}{p.get('mgrs:latitude_band')}{p.get('mgrs:grid_square')}"

    tiles = Counter(tile_key(it) for it in covering)
    best, _ = tiles.most_common(1)[0]
    return [it for it in covering if tile_key(it) == best]


def asset_href(item: pystac.Item, key: str, access: str = "https") -> str:
    href = item.assets[key].href
    return https_to_s3(href) if access == "s3" else href


def asset_scale_offset(item: pystac.Item, key: str) -> tuple[float, float]:
    """Raw scale/offset advertised in `raster:bands`. No silent defaults: missing is an error."""
    bands = item.assets[key].extra_fields.get("raster:bands")
    if not bands or "scale" not in bands[0] or "offset" not in bands[0]:
        raise KeyError(f"asset '{key}' of {item.id} has no raster:bands scale/offset metadata")
    return float(bands[0]["scale"]), float(bands[0]["offset"])


def reflectance_scale_offset(item: pystac.Item, key: str) -> tuple[float, float]:
    """(scale, offset) to apply to the DN stored in THIS asset.

    Earth Search COGs carry `earthsearch:boa_offset_applied = True`: the provider already
    subtracted ESA's BOA_ADD_OFFSET (-1000 DN) when writing the COG, yet `raster:bands` still
    advertises offset -0.1 (the ESA value for raw data). Applying it again subtracts twice.
    Evidence (see `s2veg access-demo` and tests): COG DN = original JP2 DN - 1000, and with the
    extra -0.1 about 90% of water pixels get negative red reflectance, which is not physical.
    """
    scale, offset = asset_scale_offset(item, key)
    if item.properties.get("earthsearch:boa_offset_applied") is True:
        offset = 0.0
    return scale, offset


def asset_grid_info(item: pystac.Item, key: str) -> tuple[CRS, Affine, tuple[int, int]]:
    """CRS, affine transform and (rows, cols) of an asset, straight from STAC metadata."""
    fields = item.assets[key].extra_fields
    a, b, c, d, e, f = fields["proj:transform"][:6]
    rows, cols = fields["proj:shape"]
    return _item_crs(item), Affine(a, b, c, d, e, f), (int(rows), int(cols))


def _item_crs(item: pystac.Item) -> CRS:
    """STAC projection extension v2 uses `proj:code` ('EPSG:32630'); v1 used `proj:epsg` (32630).
    Catalogues migrate between versions, so accept both."""
    props = item.properties
    if "proj:code" in props:
        return CRS.from_string(props["proj:code"])
    if "proj:epsg" in props:
        return CRS.from_epsg(props["proj:epsg"])
    raise KeyError(f"{item.id}: no proj:code / proj:epsg in item properties")
