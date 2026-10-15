"""Run configuration, loaded from TOML and validated up front.

Everything that changes the science (area, dates, cloud policy, clustering
features, thresholds) lives here, so a run is reproducible from one file.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

ALLOWED_FEATURES = ("ndvi", "ndwi", "evi", "red", "nir")
ALLOWED_ACCESS = ("https", "s3")


@dataclass(frozen=True)
class Config:
    name: str
    bbox: tuple[float, float, float, float]  # lon_min, lat_min, lon_max, lat_max (WGS84)
    start: date
    end: date

    # Catalogue / access
    stac_url: str = "https://earth-search.aws.element84.com/v1"
    collection: str = "sentinel-2-l2a"
    access: str = "https"

    # Scene selection. max_scene_cloud is only a coarse pre-filter: it describes the
    # whole 100x100 km tile, not our AOI. The real filter is min_valid_fraction.
    max_scene_cloud: float = 80.0
    scl_valid: tuple[int, ...] = (4, 5, 6, 7)
    min_valid_fraction: float = 0.6
    max_scenes_per_month: int = 4

    # Clustering
    cluster_features: tuple[str, ...] = ("ndvi", "ndwi", "red", "nir")
    cluster_k: int = 5
    cluster_sample: int = 200_000
    seed: int = 42

    # Validation
    validate: bool = True
    vegetation_ndvi_threshold: float = 0.3
    worldcover_url: str = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
    worldcover_vegetation_classes: tuple[int, ...] = (10, 20, 30, 40)

    def __post_init__(self) -> None:
        lon0, lat0, lon1, lat1 = self.bbox
        if not (-180 <= lon0 < lon1 <= 180 and -90 <= lat0 < lat1 <= 90):
            raise ValueError(f"bbox must be (lon_min, lat_min, lon_max, lat_max) in WGS84, got {self.bbox}")
        if self.start > self.end:
            raise ValueError("start must be <= end")
        if self.access not in ALLOWED_ACCESS:
            raise ValueError(f"access must be one of {ALLOWED_ACCESS}")
        bad = [f for f in self.cluster_features if f not in ALLOWED_FEATURES]
        if bad:
            raise ValueError(f"unknown cluster features {bad}; allowed: {ALLOWED_FEATURES}")
        if not 0 < self.min_valid_fraction <= 1:
            raise ValueError("min_valid_fraction must be in (0, 1]")
        if self.cluster_k < 2:
            raise ValueError("cluster_k must be >= 2")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"], d["end"] = self.start.isoformat(), self.end.isoformat()
        return d


def load_config(path: str | Path) -> Config:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    aoi, time = raw["aoi"], raw["time"]
    kwargs: dict = {
        "name": aoi["name"],
        "bbox": tuple(float(v) for v in aoi["bbox"]),
        "start": date.fromisoformat(time["start"]),
        "end": date.fromisoformat(time["end"]),
    }

    def take(section: str, keys: dict[str, str]) -> None:
        for toml_key, field_name in keys.items():
            if toml_key in raw.get(section, {}):
                value = raw[section][toml_key]
                kwargs[field_name] = tuple(value) if isinstance(value, list) else value

    take("search", {"stac_url": "stac_url", "collection": "collection", "access": "access",
                    "max_scene_cloud": "max_scene_cloud"})
    take("processing", {"scl_valid": "scl_valid", "min_valid_fraction": "min_valid_fraction",
                        "max_scenes_per_month": "max_scenes_per_month"})
    take("clustering", {"features": "cluster_features", "k": "cluster_k",
                        "sample_size": "cluster_sample", "seed": "seed"})
    take("validation", {"enabled": "validate", "ndvi_threshold": "vegetation_ndvi_threshold",
                        "worldcover_url": "worldcover_url",
                        "vegetation_classes": "worldcover_vegetation_classes"})
    return Config(**kwargs)
