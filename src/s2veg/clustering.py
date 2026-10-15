"""Unsupervised clustering of pixels (exploratory: clusters are NOT land-cover classes).

K-means groups pixels that are close in feature space. It knows nothing about vegetation:
the meaning of a cluster has to be read from its centroid and, ideally, checked against
reference data (see validation.py).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ClusterResult:
    labels: np.ndarray            # (h, w) int16, -1 = not clustered
    features: tuple[str, ...]
    centers: np.ndarray           # (k, n_features) in ORIGINAL units, ordered by ndvi if present
    sizes: np.ndarray             # pixels per cluster (same order)

    def center(self, feature: str) -> np.ndarray:
        return self.centers[:, self.features.index(feature)]


def _stack(features: dict[str, np.ndarray], names: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
    cube = np.stack([features[n] for n in names], axis=-1).astype(np.float32)
    valid = np.isfinite(cube).all(axis=-1)
    return cube, valid


def run_kmeans(features: dict[str, np.ndarray], names: tuple[str, ...], k: int,
               sample_size: int, seed: int) -> ClusterResult:
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    cube, valid = _stack(features, names)
    x = cube[valid]
    if len(x) < k:
        raise ValueError("not enough valid pixels to cluster")

    rng = np.random.default_rng(seed)
    idx = rng.choice(len(x), size=min(sample_size, len(x)), replace=False)

    # Standardise: otherwise features with larger numeric range dominate the distance.
    scaler = StandardScaler().fit(x[idx])
    km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(scaler.transform(x[idx]))

    raw = km.predict(scaler.transform(x))
    centers = scaler.inverse_transform(km.cluster_centers_)

    order = np.argsort(centers[:, names.index("ndvi")]) if "ndvi" in names else np.arange(k)
    remap = np.empty(k, dtype=np.int16)
    remap[order] = np.arange(k, dtype=np.int16)

    labels = np.full(valid.shape, -1, dtype=np.int16)
    labels[valid] = remap[raw]
    sizes = np.bincount(remap[raw], minlength=k)
    return ClusterResult(labels=labels, features=names, centers=centers[order], sizes=sizes)


def scan_k(features: dict[str, np.ndarray], names: tuple[str, ...], ks: range,
           seed: int, fit_sample: int = 20_000, score_sample: int = 4_000) -> dict[int, float]:
    """Silhouette score per k on a subsample. Higher is better, but it is only a hint:
    a 'good' silhouette does not mean the clusters are meaningful land-cover classes."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    cube, valid = _stack(features, names)
    x = cube[valid]
    rng = np.random.default_rng(seed)
    xs = x[rng.choice(len(x), size=min(fit_sample, len(x)), replace=False)]
    xs = StandardScaler().fit_transform(xs)
    scores: dict[int, float] = {}
    for k in ks:
        labels = KMeans(n_clusters=k, n_init=5, random_state=seed).fit_predict(xs)
        scores[k] = float(silhouette_score(xs, labels, sample_size=min(score_sample, len(xs)), random_state=seed))
    return scores
