from __future__ import annotations

import logging

import numpy as np
from scipy.spatial import cKDTree


LOGGER = logging.getLogger("las_cafiisica.ground.noise_filter")


def robust_noise_mask(
    xyz: np.ndarray,
    spacing: float,
    k: int = 9,
) -> np.ndarray:
    """Conservative local-plane outlier rejection for seed/candidate building."""

    count = xyz.shape[0]
    if count < max(8, k + 1):
        return np.ones(count, dtype=np.bool_)

    # PCA is intentionally evaluated on a modest deterministic reference set;
    # the decision is then propagated to the full engine sample through the
    # nearest XY reference. This keeps 300M-point projects practical.
    probe_limit = 30_000
    if count > probe_limit:
        stride = int(np.ceil(count / probe_limit))
        ref_idx = np.arange(0, count, stride, dtype=np.int64)
    else:
        ref_idx = np.arange(count, dtype=np.int64)

    ref = xyz[ref_idx]
    tree = cKDTree(ref[:, :2])
    _, neighbours = tree.query(
        ref[:, :2],
        k=min(k, ref.shape[0]),
        workers=-1,
    )

    safe_ref = np.ones(ref.shape[0], dtype=np.bool_)
    if neighbours.ndim == 1:
        return np.ones(count, dtype=np.bool_)

    for i in range(ref.shape[0]):
        ids = neighbours[i]
        local = ref[ids]
        center = local.mean(axis=0)
        centered = local - center
        covariance = centered.T @ centered / max(1, local.shape[0] - 1)
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        normal = eigenvectors[:, 0]
        normal /= max(np.linalg.norm(normal), 1e-12)
        residual = abs(float(np.dot(ref[i] - center, normal)))
        local_residuals = np.abs(centered @ normal)
        median = float(np.median(local_residuals))
        mad = float(np.median(np.abs(local_residuals - median)))
        limit = max(
            0.12,
            spacing * 4.0,
            median + 7.0 * max(mad, 0.01),
        )
        if residual > limit:
            safe_ref[i] = False

    if ref.shape[0] == count:
        mask = safe_ref
    else:
        _, nearest = tree.query(xyz[:, :2], k=1, workers=-1)
        mask = safe_ref[nearest]

    LOGGER.info(
        "OUTLIERS_REMOVED=%d",
        int(mask.size - np.count_nonzero(mask)),
    )
    return mask
