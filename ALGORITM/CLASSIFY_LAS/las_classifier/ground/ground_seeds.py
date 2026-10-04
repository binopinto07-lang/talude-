from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .terrain_tin import TerrainTIN


LOGGER = logging.getLogger("las_cafiisica.ground.ground_seeds")


@dataclass(frozen=True, slots=True)
class SeedSelection:
    indices: np.ndarray
    seed_resolution: float
    candidate_spacing: float


def _lowest_per_grid(
    xyz: np.ndarray,
    resolution: float,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> np.ndarray:
    if xyz.size == 0:
        return np.empty(0, dtype=np.int64)

    min_x = float(np.min(xyz[:, 0])) + offset_x
    min_y = float(np.min(xyz[:, 1])) + offset_y
    ix = np.floor((xyz[:, 0] - min_x) / resolution).astype(np.int64)
    iy = np.floor((xyz[:, 1] - min_y) / resolution).astype(np.int64)

    width = int(ix.max() - ix.min() + 1)
    keys = (ix - ix.min()) + width * (iy - iy.min())
    order = np.lexsort((xyz[:, 2], keys))
    sorted_keys = keys[order]
    first = np.r_[True, sorted_keys[1:] != sorted_keys[:-1]]
    return order[first]


def _dedupe_xy_lowest(
    xyz: np.ndarray,
    indices: np.ndarray,
    tolerance: float = 1e-5,
) -> np.ndarray:
    pts = xyz[indices]
    if pts.shape[0] <= 1:
        return indices
    x0 = float(np.min(pts[:, 0]))
    y0 = float(np.min(pts[:, 1]))
    ix = np.rint((pts[:, 0] - x0) / tolerance).astype(np.int64)
    iy = np.rint((pts[:, 1] - y0) / tolerance).astype(np.int64)
    width = int(ix.max() - ix.min() + 1)
    keys = (ix - ix.min()) + width * (iy - iy.min())
    order = np.lexsort((pts[:, 2], keys))
    sorted_keys = keys[order]
    first = np.r_[True, sorted_keys[1:] != sorted_keys[:-1]]
    return indices[order[first]]


def select_multiscale_seeds(
    xyz: np.ndarray,
    valid_mask: np.ndarray,
    spacing: float,
    requested_seed_resolution: float = 0.0,
) -> SeedSelection:
    valid_idx = np.flatnonzero(valid_mask)
    safe = xyz[valid_idx]
    if safe.shape[0] < 4:
        raise ValueError("Not enough valid points for ground seeds")

    seed_resolution = (
        requested_seed_resolution
        if requested_seed_resolution > 0
        else max(4.0, spacing * 30.0)
    )
    candidate_spacing = max(spacing * 6.0, 0.35)

    # Coarse seeds are deliberately conservative. Two shifted grids reduce
    # grid-origin bias without promoting fine-scale vegetation minima.
    coarse_sets: list[np.ndarray] = []
    for shift in (0.0, 0.5):
        local = _lowest_per_grid(
            safe,
            seed_resolution,
            offset_x=seed_resolution * shift,
            offset_y=seed_resolution * shift,
        )
        coarse_sets.append(valid_idx[local])
    coarse = _dedupe_xy_lowest(
        xyz,
        np.unique(np.concatenate(coarse_sets)),
    )

    # Remove extreme low spikes from the coarse set.
    if coarse.size >= 8:
        seed_xyz = xyz[coarse]
        tree = cKDTree(seed_xyz[:, :2])
        neighbours = tree.query_ball_point(
            seed_xyz[:, :2],
            r=seed_resolution * 1.6,
            workers=-1,
        )
        keep = np.ones(coarse.size, dtype=np.bool_)
        for i, ids in enumerate(neighbours):
            if len(ids) < 4:
                continue
            local_z = seed_xyz[np.asarray(ids, dtype=np.int64), 2]
            median = float(np.median(local_z))
            mad = float(np.median(np.abs(local_z - median)))
            if seed_xyz[i, 2] < median - max(
                2.0,
                8.0 * max(mad, 0.05),
            ):
                keep[i] = False
        coarse = coarse[keep]

    if coarse.size < 3:
        raise ValueError("Ground seed selection produced fewer than 3 seeds")

    # Medium-scale minima may become seeds only when they agree with the
    # coarse terrain plane. This gives multiscale detail without allowing an
    # isolated tree crown/branch to bootstrap itself as terrain.
    coarse_tin = TerrainTIN.build(xyz[coarse])
    medium_sets: list[np.ndarray] = []
    medium_resolution = max(
        seed_resolution * 0.5,
        candidate_spacing * 2.0,
    )
    for shift in (0.0, 0.5):
        local = _lowest_per_grid(
            safe,
            medium_resolution,
            offset_x=medium_resolution * shift,
            offset_y=medium_resolution * shift,
        )
        medium_sets.append(valid_idx[local])

    medium = _dedupe_xy_lowest(
        xyz,
        np.unique(np.concatenate(medium_sets)),
    )
    mxyz = xyz[medium]
    metrics = coarse_tin.metrics(
        mxyz[:, 0],
        mxyz[:, 1],
        mxyz[:, 2],
    )
    seed_distance = max(0.18, spacing * 3.0)
    medium_keep = (
        metrics["valid"]
        & (metrics["plane_distance"] <= seed_distance)
    )
    seeds = _dedupe_xy_lowest(
        xyz,
        np.unique(
            np.concatenate((coarse, medium[medium_keep]))
        ),
    )

    LOGGER.info(
        "GROUND_SEEDS=%d COARSE_SEEDS=%d SEED_RESOLUTION=%.3f "
        "CANDIDATE_SPACING=%.3f",
        int(seeds.size),
        int(coarse.size),
        seed_resolution,
        candidate_spacing,
    )
    return SeedSelection(
        indices=seeds.astype(np.int64, copy=False),
        seed_resolution=float(seed_resolution),
        candidate_spacing=float(candidate_spacing),
    )


def lowest_candidates(
    xyz: np.ndarray,
    valid_mask: np.ndarray,
    spacing: float,
) -> np.ndarray:
    valid_idx = np.flatnonzero(valid_mask)
    safe = xyz[valid_idx]
    local = _lowest_per_grid(safe, spacing)
    return valid_idx[local]
