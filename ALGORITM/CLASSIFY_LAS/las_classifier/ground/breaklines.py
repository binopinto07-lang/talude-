from __future__ import annotations

import numpy as np

from .terrain_tin import TerrainTIN


def triangle_discontinuity(
    tin: TerrainTIN,
) -> np.ndarray:
    """Return the strongest normal change against adjacent triangles."""

    neighbours = np.asarray(tin.delaunay.neighbors, dtype=np.int64)
    score = np.zeros(tin.triangle_count, dtype=np.float64)

    for side in range(3):
        other = neighbours[:, side]
        valid = other >= 0
        if not np.any(valid):
            continue
        dot = np.abs(
            np.einsum(
                "ij,ij->i",
                tin.normals[valid],
                tin.normals[other[valid]],
            )
        )
        angle_score = 1.0 - np.clip(dot, 0.0, 1.0)
        score[valid] = np.maximum(score[valid], angle_score)

    return score


def boundary_triangles(
    tin: TerrainTIN,
) -> np.ndarray:
    return np.any(
        np.asarray(tin.delaunay.neighbors) < 0,
        axis=1,
    )
