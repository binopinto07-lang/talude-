from __future__ import annotations

import numpy as np


def ground_confidence(
    plane_distance: np.ndarray,
    max_edge: np.ndarray,
    *,
    distance_limit: float,
    edge_limit: float,
    discontinuity: np.ndarray | None = None,
    normal_alignment: np.ndarray | None = None,
) -> np.ndarray:
    distance_limit = max(float(distance_limit), 1e-6)
    edge_limit = max(float(edge_limit), 1e-6)

    distance_score = np.exp(
        -np.square(plane_distance / distance_limit)
    )
    edge_score = np.clip(
        1.25 - (max_edge / edge_limit) * 0.25,
        0.25,
        1.0,
    )
    score = 0.78 * distance_score + 0.22 * edge_score

    if discontinuity is not None:
        score *= np.clip(
            1.0 - 0.45 * discontinuity,
            0.35,
            1.0,
        )

    if normal_alignment is not None:
        normal_score = np.clip(
            (normal_alignment - 0.45) / 0.50,
            0.0,
            1.0,
        )
        score = 0.72 * score + 0.28 * normal_score

    return np.clip(score, 0.0, 1.0)
