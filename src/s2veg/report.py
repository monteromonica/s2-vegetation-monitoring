"""Figures. Maps are drawn with real map coordinates (UTM metres), not bare pixel indices."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .clustering import ClusterResult  # noqa: E402
from .raster import Grid  # noqa: E402


def plot_month(path: Path, grid: Grid, comp: dict[str, np.ndarray], cluster: ClusterResult | None,
               month: str, n_scenes: int) -> None:
    west, south, east, north = grid.bounds
    extent = (west, east, south, north)
    fig, axes = plt.subplots(2, 2, figsize=(14, 11), constrained_layout=True)

    panels = [
        (axes[0, 0], comp["ndvi"], "NDVI", "RdYlGn", -0.2, 0.9),
        (axes[0, 1], comp["ndwi"], "NDWI (McFeeters: open water)", "Blues", -0.8, 0.5),
        (axes[1, 0], comp["evi"], "EVI (reflectance 0-1)", "YlGn", -0.2, 1.0),
    ]
    for ax, data, title, cmap, vmin, vmax in panels:
        im = ax.imshow(data, cmap=cmap, vmin=vmin, vmax=vmax, extent=extent)
        ax.set_title(title)
        fig.colorbar(im, ax=ax, shrink=0.8)

    ax = axes[1, 1]
    if cluster is not None:
        labels = np.ma.masked_less(cluster.labels, 0)
        k = len(cluster.sizes)
        im = ax.imshow(labels, cmap="viridis", vmin=0, vmax=k - 1, extent=extent, interpolation="nearest")
        cb = fig.colorbar(im, ax=ax, shrink=0.8, ticks=range(k))
        cb.set_label("cluster (sorted by mean NDVI)")
        ax.set_title(f"K-means clusters on {', '.join(cluster.features)}\n(exploratory, not land-cover classes)")
    else:
        ax.axis("off")

    for ax in axes.ravel():
        if ax.axison:
            ax.set_xlabel(f"Easting (m, {grid.crs.to_string()})")
            ax.set_ylabel("Northing (m)")
            ax.ticklabel_format(style="plain", useOffset=False)
            ax.tick_params(labelsize=7)
    fig.suptitle(f"{month}: median composite of {n_scenes} scene(s), SCL-masked (grey/white = no valid data)")
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_class_timeseries(path: Path, df: pd.DataFrame) -> None:
    pivot = df.pivot(index="month", columns="worldcover_class", values="mean_ndvi").sort_index()
    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    for col in pivot.columns:
        ax.plot(pivot.index, pivot[col], marker="o", label=col)
    ax.set_ylabel("mean NDVI of the monthly median composite")
    ax.set_title("NDVI seasonality by ESA WorldCover class (reference: 2021, imagery: AOI dates)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.savefig(path, dpi=110)
    plt.close(fig)
