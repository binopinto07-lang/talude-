from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .breaklines import boundary_triangles, triangle_discontinuity
from .terrain_tin import TerrainTIN


SUPPORTED_GAP = np.uint8(1)
OCCLUDED_GAP = np.uint8(2)
EDGE_GAP = np.uint8(3)
LARGE_UNKNOWN_GAP = np.uint8(4)
DISCONTINUITY_GAP = np.uint8(5)


@dataclass(frozen=True, slots=True)
class GapAnalysis:
    kind: np.ndarray
    supported_mask: np.ndarray
    detected_count: int
    supported_count: int
    occluded_count: int
    rejected_count: int


def detect_gaps(
    tin: TerrainTIN,
    *,
    dense_edge: float,
    max_gap_edge: float,
    discontinuity_limit: float = 0.18,
    evidence_xyz: np.ndarray | None = None,
    occlusion_hag: float = 0.50,
) -> GapAnalysis:
    """Classify sparse TIN triangles and identify safely reconstructable gaps.

    OCCLUDED_GAP is a supported gap with explicit above-ground point evidence
    over the triangle, typical of a tree/vegetation opening after rejection.
    """

    edge = tin.max_edges
    candidate = edge > dense_edge
    boundary = boundary_triangles(tin)
    discontinuity = triangle_discontinuity(tin)

    kind = np.zeros(tin.triangle_count, dtype=np.uint8)
    kind[candidate & boundary] = EDGE_GAP
    kind[candidate & (edge > max_gap_edge)] = LARGE_UNKNOWN_GAP
    kind[
        candidate
        & (~boundary)
        & (edge <= max_gap_edge)
        & (discontinuity > discontinuity_limit)
    ] = DISCONTINUITY_GAP

    supported = (
        candidate
        & (~boundary)
        & (edge <= max_gap_edge)
        & (discontinuity <= discontinuity_limit)
    )
    kind[supported] = SUPPORTED_GAP

    occluded = np.zeros(tin.triangle_count, dtype=np.bool_)
    if evidence_xyz is not None and evidence_xyz.size:
        xyz = np.asarray(evidence_xyz, dtype=np.float64)
        metrics = tin.metrics(
            xyz[:, 0],
            xyz[:, 1],
            xyz[:, 2],
        )
        simplex = metrics["simplex"]
        hag = metrics["vertical_residual"]
        evidence = (
            metrics["valid"]
            & np.isfinite(hag)
            & (hag >= float(occlusion_hag))
        )
        if np.any(evidence):
            triangle_ids = simplex[evidence]
            counts = np.bincount(
                triangle_ids,
                minlength=tin.triangle_count,
            )
            occluded = supported & (counts >= 2)
            kind[occluded] = OCCLUDED_GAP

    detected = int(np.count_nonzero(candidate))
    supported_count = int(np.count_nonzero(supported))
    occluded_count = int(np.count_nonzero(occluded))
    return GapAnalysis(
        kind=kind,
        supported_mask=supported,
        detected_count=detected,
        supported_count=supported_count,
        occluded_count=occluded_count,
        rejected_count=detected - supported_count,
    )
