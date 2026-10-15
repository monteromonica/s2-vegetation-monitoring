"""Validation against an independent reference: ESA WorldCover 10 m land cover.

Honest framing (also for the interview):
  * WorldCover is itself a model product with its own error (map year 2021). It is a
    *sanity check*, not ground truth. Matching the imagery year (see madrid_2021.toml)
    removes one mismatch; phenology still dominates the remaining disagreement.
  * Nothing here is trained on WorldCover, so there is no train/test leakage. The
    vegetation decision uses a fixed NDVI threshold chosen a priori, not tuned on labels.
  * Pixel-level agreement is optimistic: neighbouring pixels are spatially autocorrelated,
    so the effective sample size is far smaller than the pixel count.
"""

from __future__ import annotations

import math

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from .raster import Grid

WORLDCOVER_CLASSES = {
    10: "tree cover", 20: "shrubland", 30: "grassland", 40: "cropland", 50: "built-up",
    60: "bare / sparse vegetation", 70: "snow and ice", 80: "permanent water",
    90: "herbaceous wetland", 95: "mangroves", 100: "moss and lichen",
}


def worldcover_tile_name(lon: float, lat: float) -> str:
    """WorldCover tiles are 3x3 degrees, named by their lower-left corner, e.g. N39W006."""
    lat0 = math.floor(lat / 3) * 3
    lon0 = math.floor(lon / 3) * 3
    ns = "N" if lat0 >= 0 else "S"
    ew = "E" if lon0 >= 0 else "W"
    return f"{ns}{abs(lat0):02d}{ew}{abs(lon0):03d}"


def worldcover_href(base_url: str, bbox: tuple[float, float, float, float]) -> str:
    lon0, lat0, lon1, lat1 = bbox
    names = {worldcover_tile_name(lon, lat) for lon in (lon0, lon1) for lat in (lat0, lat1)}
    if len(names) != 1:
        raise NotImplementedError(f"AOI spans several WorldCover tiles {sorted(names)}; split the AOI")
    name = names.pop()
    return f"{base_url}/ESA_WorldCover_10m_2021_v200_{name}_Map.tif"


def read_worldcover(href: str, grid: Grid) -> np.ndarray:
    """Read WorldCover onto our grid. Nearest neighbour: class codes must never be interpolated."""
    with rasterio.open(href) as src, WarpedVRT(
        src, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
        resampling=Resampling.nearest, nodata=0,
    ) as vrt:
        return vrt.read(1)


def binary_metrics(truth: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    """Metrics for the positive class ('vegetation'). Inputs are boolean arrays of equal shape."""
    truth, pred = truth.astype(bool), pred.astype(bool)
    tp = int((truth & pred).sum())
    fp = int((~truth & pred).sum())
    fn = int((truth & ~pred).sum())
    tn = int((~truth & ~pred).sum())
    n = tp + fp + fn + tn

    def ratio(a: float, b: float) -> float:
        return a / b if b else float("nan")

    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    f1 = ratio(2 * precision * recall, precision + recall) if tp else (0.0 if n else float("nan"))
    return {
        "n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1,
        "iou": ratio(tp, tp + fp + fn), "overall_accuracy": ratio(tp + tn, n),
    }


def per_class_table(ndvi: np.ndarray, wc: np.ndarray, pred_ndvi: np.ndarray,
                    pred_cluster: np.ndarray | None, valid: np.ndarray) -> list[dict]:
    """For each WorldCover class: pixel count, mean NDVI and share flagged as vegetation."""
    rows = []
    for code, label in WORLDCOVER_CLASSES.items():
        m = valid & (wc == code)
        n = int(m.sum())
        if n == 0:
            continue
        row = {
            "worldcover_class": code, "label": label, "pixels": n,
            "mean_ndvi": float(np.nanmean(ndvi[m])),
            "share_veg_ndvi_threshold": float(pred_ndvi[m].mean()),
        }
        if pred_cluster is not None:
            row["share_veg_clusters"] = float(pred_cluster[m].mean())
        rows.append(row)
    return rows
