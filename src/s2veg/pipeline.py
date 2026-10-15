"""End-to-end run: search -> AOI-level cloud filter -> reflectance -> indices ->
monthly composites -> clustering -> validation -> outputs with provenance."""

from __future__ import annotations

import json
import platform
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pystac
import rasterio
from rasterio.enums import Resampling

from . import __version__
from .clustering import ClusterResult, run_kmeans, scan_k
from .composite import median_composite
from .config import Config
from .indices import mask_outside, scene_indices
from .raster import Grid, grid_for_aoi, read_on_grid, write_geotiff
from .reflectance import check_reflectance_scale, dn_to_reflectance
from .scl import valid_mask
from .stac import (asset_grid_info, asset_href, covering_items, deduplicate_acquisitions,
                   reflectance_scale_offset, search_items)
from .validation import (WORLDCOVER_CLASSES, binary_metrics, per_class_table,
                         read_worldcover, worldcover_href)

# STAC asset keys for the four 10 m bands we need (B02, B03, B04, B08).
BAND_ASSETS = {"blue": "blue", "green": "green", "red": "red", "nir": "nir"}
COMPOSITE_KEYS = ("ndvi", "ndwi", "evi", "red", "nir")


def gdal_env(cfg: Config) -> dict:
    env = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
        "GDAL_HTTP_MAX_RETRY": "5",
        "GDAL_HTTP_RETRY_DELAY": "1",
    }
    if cfg.access == "s3":
        # Public bucket: anonymous (unsigned) requests. The region must match the bucket.
        env.update({"AWS_NO_SIGN_REQUEST": "YES", "AWS_REGION": "us-west-2"})
    return env


@dataclass
class Scene:
    item: pystac.Item
    valid_fraction: float
    scl: np.ndarray
    valid: np.ndarray


def _month_key(item: pystac.Item) -> str:
    return f"{item.datetime.year}-{item.datetime.month:02d}"


def _load_scene_features(scene: Scene, grid: Grid, cfg: Config) -> tuple[dict[str, np.ndarray], dict]:
    """Read the four bands, convert to reflectance, mask invalid pixels, compute indices."""
    item = scene.item
    refl: dict[str, np.ndarray] = {}
    scale_offset: dict[str, tuple[float, float]] = {}
    for name, key in BAND_ASSETS.items():
        # Bands are already 10 m on our grid, so no resampling happens here.
        dn = read_on_grid(asset_href(item, key, cfg.access), grid, Resampling.nearest)
        scale, offset = reflectance_scale_offset(item, key)
        scale_offset[name] = (scale, offset)
        arr = dn_to_reflectance(dn, scale, offset)
        arr[~scene.valid] = np.nan
        refl[name] = arr
    check_reflectance_scale(refl)

    feats = scene_indices(refl)
    feats["evi"], dropped = mask_outside(feats["evi"], -1.0, 1.0)
    feats["red"], feats["nir"] = refl["red"], refl["nir"]
    meta = {"scale_offset": scale_offset, "evi_out_of_range_fraction": dropped,
            "processing_baseline": item.properties.get("s2:processing_baseline")}
    return feats, meta


def run(cfg: Config, out_root: str | Path, log=print) -> Path:
    t0 = time.time()
    out = Path(out_root) / cfg.name
    (out / "rasters").mkdir(parents=True, exist_ok=True)
    (out / "figures").mkdir(parents=True, exist_ok=True)

    # 1. Discover scenes -------------------------------------------------------------
    found = search_items(cfg)
    covering = covering_items(found, cfg.bbox)
    items = deduplicate_acquisitions(covering)
    log(f"STAC: {len(found)} scenes matched, {len(covering)} fully cover the AOI (single tile), "
        f"{len(items)} unique acquisitions after dropping {len(covering) - len(items)} reprocessed duplicates")
    if not items:
        raise RuntimeError("no scene covers the AOI; widen the dates or the cloud pre-filter")

    # 2. One target grid, derived from product metadata (no file I/O needed) ----------
    crs, transform, shape = asset_grid_info(items[0], "red")
    grid = grid_for_aoi(cfg.bbox, crs, transform, shape, snap=2)
    log(f"Grid: {grid.crs} {grid.width}x{grid.height} px @ {grid.pixel_size:g} m, bounds={grid.bounds}")

    by_month: dict[str, list[pystac.Item]] = defaultdict(list)
    for it in items:
        by_month[_month_key(it)].append(it)

    wc = None
    if cfg.validate:
        wc = read_worldcover(worldcover_href(cfg.worldcover_url, cfg.bbox), grid)
        log(f"WorldCover read onto grid: classes present = {sorted(int(c) for c in np.unique(wc) if c)}")

    scene_rows: list[dict] = []
    summary_rows: list[dict] = []
    class_ndvi_rows: list[dict] = []
    validation: dict[str, dict] = {}
    cluster_info: dict[str, dict] = {}
    provenance: list[dict] = []

    with rasterio.Env(**gdal_env(cfg)):
        for month, month_items in sorted(by_month.items()):
            # 3. AOI-level cloud filter: read only the small 20 m SCL first ------------
            candidates: list[Scene] = []
            for it in month_items:
                scl = read_on_grid(asset_href(it, "scl", cfg.access), grid, Resampling.nearest)
                valid = valid_mask(scl, cfg.scl_valid)  # SCL already upsampled 20 m -> 10 m grid
                vf = float(valid.mean())
                scene_rows.append({
                    "month": month, "item_id": it.id, "datetime": it.datetime.isoformat(),
                    "tile_cloud_cover_pct": it.properties.get("eo:cloud_cover"),
                    "aoi_valid_fraction": round(vf, 4), "used": False,
                })
                if vf >= cfg.min_valid_fraction:
                    candidates.append(Scene(it, vf, scl, valid))

            candidates.sort(key=lambda s: s.valid_fraction, reverse=True)
            chosen = candidates[: cfg.max_scenes_per_month]
            chosen_ids = {s.item.id for s in chosen}
            for row in scene_rows:
                if row["item_id"] in chosen_ids:
                    row["used"] = True
            if not chosen:
                log(f"[{month}] no usable scene (AOI valid fraction < {cfg.min_valid_fraction}); skipped")
                continue

            # 4. Reflectance + indices per scene, then composite ------------------------
            per_scene: dict[str, list[np.ndarray]] = {k: [] for k in COMPOSITE_KEYS}
            for sc in chosen:
                feats, meta = _load_scene_features(sc, grid, cfg)
                for k in COMPOSITE_KEYS:
                    per_scene[k].append(feats[k])
                provenance.append({"month": month, "item_id": sc.item.id, **meta,
                                   "aoi_valid_fraction": sc.valid_fraction})

            comp: dict[str, np.ndarray] = {}
            counts: dict[str, np.ndarray] = {}
            for k in COMPOSITE_KEYS:
                comp[k], counts[k] = median_composite(per_scene[k])
            count = counts["ndvi"]  # observations behind each NDVI pixel
            log(f"[{month}] {len(chosen)} scene(s); composite NDVI valid share = "
                f"{float(np.isfinite(comp['ndvi']).mean()):.1%}")

            for k in ("ndvi", "ndwi", "evi"):
                write_geotiff(out / "rasters" / f"{month}_{k}.tif", comp[k], grid, nodata=np.nan)
            write_geotiff(out / "rasters" / f"{month}_obs_count.tif", count, grid, nodata=0)

            # 5. Clustering (exploratory) -------------------------------------------------
            cluster: ClusterResult | None = None
            try:
                cluster = run_kmeans(comp, cfg.cluster_features, cfg.cluster_k, cfg.cluster_sample, cfg.seed)
                write_geotiff(out / "rasters" / f"{month}_clusters.tif", cluster.labels, grid, nodata=-1)
                cluster_info[month] = {
                    "features": list(cluster.features),
                    "centers": cluster.centers.round(4).tolist(),
                    "sizes": cluster.sizes.tolist(),
                    "silhouette_by_k": scan_k(comp, cfg.cluster_features, range(2, 9), cfg.seed),
                }
            except ValueError as exc:
                log(f"[{month}] clustering skipped: {exc}")

            # 6. Validation against WorldCover ------------------------------------------
            ndvi = comp["ndvi"]
            finite = np.isfinite(ndvi)
            summary = {"month": month, "scenes": len(chosen),
                       "valid_share": float(finite.mean()), "mean_ndvi": float(np.nanmean(ndvi)) if finite.any() else None}
            if wc is not None and finite.any():
                truth = np.isin(wc, cfg.worldcover_vegetation_classes)
                ok = finite & (wc != 0)
                pred_ndvi = ndvi > cfg.vegetation_ndvi_threshold
                pred_cluster = None
                if cluster is not None:
                    veg_clusters = cluster.center("ndvi") > cfg.vegetation_ndvi_threshold
                    pred_cluster = np.zeros(ndvi.shape, dtype=bool)
                    has = cluster.labels >= 0
                    pred_cluster[has] = veg_clusters[cluster.labels[has]]

                validation[month] = {
                    "reference": "ESA WorldCover 2021 v200 (vegetation = classes "
                                 f"{list(cfg.worldcover_vegetation_classes)})",
                    "ndvi_threshold": binary_metrics(truth[ok], pred_ndvi[ok]),
                    "clusters": binary_metrics(truth[ok & (cluster.labels >= 0)], pred_cluster[ok & (cluster.labels >= 0)])
                    if cluster is not None else None,
                    "per_class": per_class_table(ndvi, wc, pred_ndvi, pred_cluster, ok),
                }
                summary["f1_ndvi_threshold"] = validation[month]["ndvi_threshold"]["f1"]
                for code, label in WORLDCOVER_CLASSES.items():
                    m = ok & (wc == code)
                    if m.sum() > 500:
                        class_ndvi_rows.append({"month": month, "worldcover_class": label,
                                                "mean_ndvi": float(np.mean(ndvi[m])), "pixels": int(m.sum())})
            summary_rows.append(summary)

            from .report import plot_month
            plot_month(out / "figures" / f"{month}_overview.png", grid, comp, cluster, month, len(chosen))

    # 7. Tables, time series figure, provenance --------------------------------------
    pd.DataFrame(scene_rows).to_csv(out / "scenes.csv", index=False)
    pd.DataFrame(summary_rows).to_csv(out / "summary_by_month.csv", index=False)
    if class_ndvi_rows:
        df = pd.DataFrame(class_ndvi_rows)
        df.to_csv(out / "ndvi_by_landcover_and_month.csv", index=False)
        from .report import plot_class_timeseries
        plot_class_timeseries(out / "figures" / "ndvi_by_landcover.png", df)
    (out / "validation.json").write_text(json.dumps(validation, indent=2, default=float))
    (out / "clusters.json").write_text(json.dumps(cluster_info, indent=2, default=float))

    meta = {
        "s2veg_version": __version__,
        "config": cfg.to_dict(),
        "grid": {"crs": grid.crs.to_string(), "transform": list(grid.transform)[:6],
                 "width": grid.width, "height": grid.height, "pixel_size_m": grid.pixel_size},
        "scenes_used": provenance,
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "rasterio": rasterio.__version__, "gdal": rasterio.__gdal_version__},
        "runtime_seconds": round(time.time() - t0, 1),
    }
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2, default=str))
    log(f"Done in {meta['runtime_seconds']} s -> {out}")
    return out
