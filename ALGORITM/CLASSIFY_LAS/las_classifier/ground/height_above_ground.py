from __future__ import annotations

import numpy as np

from .terrain_tin import TerrainTIN


def height_above_ground(
    tin: TerrainTIN,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return vertical HAG and true perpendicular distance to the TIN.

    HAG is evaluated against the containing Delaunay triangle, while
    plane_distance is the geometric point-to-plane distance. Both are needed:
    HAG is very effective at rejecting vegetation above steep terrain, and
    point-to-plane distance protects genuine steep faces from vertical-only
    filtering.
    """

    metrics = tin.metrics(x, y, z)
    hag = np.asarray(
        metrics["vertical_residual"],
        dtype=np.float64,
    )
    plane_distance = np.asarray(
        metrics["plane_distance"],
        dtype=np.float64,
    )
    return hag, plane_distance


def hag_confidence(
    hag: np.ndarray,
    *,
    positive_limit: float,
    negative_limit: float | None = None,
) -> np.ndarray:
    positive_limit = max(float(positive_limit), 1e-6)
    negative_limit = (
        max(float(negative_limit), 1e-6)
        if negative_limit is not None
        else positive_limit * 1.5
    )

    score = np.ones(hag.shape[0], dtype=np.float64)
    positive = np.isfinite(hag) & (hag > 0.0)
    negative = np.isfinite(hag) & (hag < 0.0)

    score[positive] = np.exp(
        -np.square(hag[positive] / positive_limit)
    )
    score[negative] = np.exp(
        -np.square(np.abs(hag[negative]) / negative_limit)
    )
    score[~np.isfinite(hag)] = 0.0
    return score
