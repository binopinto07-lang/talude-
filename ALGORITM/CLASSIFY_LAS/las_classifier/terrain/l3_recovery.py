from __future__ import annotations

import logging
from dataclasses import dataclass
from math import ceil
from typing import Callable

import numpy as np

from ..cloud.model import CloudModel
from .schema import SourceInspection, SourceType


LOGGER = logging.getLogger(
    "las_cafiisica.terrain.l3_recovery"
)
ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True, slots=True)
class L3RecoveryParams:
    sample_target: int = 3_000_000
    voxel_size: float = 0.50
    max_plane_distance: float = 0.28
    min_points_per_voxel: int = 3
    min_geometry_score: float = 0.28
    strong_geometry_score: float = 0.62
    min_strong_fraction: float = 0.20


@dataclass(slots=True)
class L3RecoveryModel:
    params: L3RecoveryParams
    origin: np.ndarray
    dimensions: tuple[int, int, int]
    approved_keys: np.ndarray
    candidate_sample_count: int
    approved_voxel_count: int
    source_inspection: SourceInspection

    def _encode_xyz(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        xyz = np.column_stack((x, y, z))
        ijk = np.floor(
            (xyz - self.origin[None, :])
            / self.params.voxel_size
        ).astype(np.int64)

        nx, ny, nz = self.dimensions
        inside = (
            (ijk[:, 0] >= 0)
            & (ijk[:, 1] >= 0)
            & (ijk[:, 2] >= 0)
            & (ijk[:, 0] < nx)
            & (ijk[:, 1] < ny)
            & (ijk[:, 2] < nz)
        )

        keys = np.full(
            x.shape[0],
            -1,
            dtype=np.int64,
        )
        if np.any(inside):
            local = ijk[inside]
            keys[inside] = (
                local[:, 0]
                + nx
                * (
                    local[:, 1]
                    + ny * local[:, 2]
                )
            )
        return keys, inside

    def recovered_mask(
        self,
        points,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        count = x.shape[0]
        if count == 0 or self.approved_keys.size == 0:
            return np.zeros(count, dtype=np.bool_)

        names = set(
            points.point_format.dimension_names
        )
        if not {
            "return_number",
            "number_of_returns",
        }.issubset(names):
            return np.zeros(count, dtype=np.bool_)

        rn = np.asarray(
            points.return_number,
            dtype=np.int16,
        )
        nr = np.asarray(
            points.number_of_returns,
            dtype=np.int16,
        )

        # Return position is evidence only. A last/only return can still be
        # vegetation, therefore it must additionally lie in a geometry-approved
        # voxel created from the PTD surface.
        last_or_only = (
            (nr > 0)
            & (rn > 0)
            & (rn == nr)
        )

        valid = last_or_only.copy()
        if "withheld" in names:
            valid &= ~np.asarray(
                points.withheld,
                dtype=np.bool_,
            )

        keys, inside = self._encode_xyz(
            x,
            y,
            z,
        )
        valid &= inside
        if not np.any(valid):
            return valid

        valid_ids = np.flatnonzero(valid)
        hit = np.isin(
            keys[valid_ids],
            self.approved_keys,
            assume_unique=False,
        )
        result = np.zeros(
            count,
            dtype=np.bool_,
        )
        result[valid_ids[hit]] = True
        return result


def _sample_indices(
    point_count: int,
    target: int,
) -> np.ndarray:
    if point_count <= 0:
        return np.empty(0, dtype=np.int64)
    stride = max(
        1,
        int(ceil(point_count / max(1, target))),
    )
    return np.arange(
        0,
        point_count,
        stride,
        dtype=np.int64,
    )


def _scaled_xyz(
    cloud: CloudModel,
    indices: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    points = cloud.las.points[indices]
    scales = cloud.las.header.scales
    offsets = cloud.las.header.offsets
    x = (
        np.asarray(points.X, dtype=np.float64)
        * scales[0]
        + offsets[0]
    )
    y = (
        np.asarray(points.Y, dtype=np.float64)
        * scales[1]
        + offsets[1]
    )
    z = (
        np.asarray(points.Z, dtype=np.float64)
        * scales[2]
        + offsets[2]
    )
    return x, y, z


def build_l3_recovery(
    cloud: CloudModel,
    ptd_model,
    inspection: SourceInspection,
    progress: ProgressCallback | None = None,
    params: L3RecoveryParams | None = None,
) -> L3RecoveryModel | None:
    if inspection.source_type is not SourceType.L3_LIDAR:
        LOGGER.info(
            "L3_RECOVERY_SKIPPED source=%s",
            inspection.source_type.value,
        )
        return None

    names = set(
        cloud.las.point_format.dimension_names
    )
    if not {
        "return_number",
        "number_of_returns",
    }.issubset(names):
        LOGGER.warning(
            "L3_RECOVERY_SKIPPED returns missing"
        )
        return None

    params = params or L3RecoveryParams()
    indices = _sample_indices(
        cloud.point_count,
        params.sample_target,
    )
    if indices.size == 0:
        return None

    if progress is not None:
        progress(
            62,
            (
                "L3 recovery: sampling measured "
                f"returns ({indices.size:,})"
            ),
        )

    points = cloud.las.points[indices]
    x, y, z = _scaled_xyz(
        cloud,
        indices,
    )

    rn = np.asarray(
        points.return_number,
        dtype=np.int16,
    )
    nr = np.asarray(
        points.number_of_returns,
        dtype=np.int16,
    )
    last_or_only = (
        (nr > 0)
        & (rn > 0)
        & (rn == nr)
    )

    allowed = last_or_only.copy()
    if "withheld" in names:
        allowed &= ~np.asarray(
            points.withheld,
            dtype=np.bool_,
        )

    # Classification is deliberately NOT used here. The input class is kept
    # only as provenance/diagnostics elsewhere in the application.
    score, metrics = ptd_model._confidence(
        x,
        y,
        z,
        points=points,
    )
    candidate = (
        allowed
        & metrics["valid"]
        & np.isfinite(metrics["plane_distance"])
        & (
            metrics["plane_distance"]
            <= params.max_plane_distance
        )
        & (
            score
            >= params.min_geometry_score
        )
    )

    candidate_ids = np.flatnonzero(candidate)
    if candidate_ids.size == 0:
        LOGGER.info(
            "L3_RECOVERY_CANDIDATES=0"
        )
        return None

    mins = np.asarray(
        cloud.las.header.mins,
        dtype=np.float64,
    )
    maxs = np.asarray(
        cloud.las.header.maxs,
        dtype=np.float64,
    )
    origin = np.floor(
        mins / params.voxel_size
    ) * params.voxel_size
    dims = np.maximum(
        1,
        np.ceil(
            (maxs - origin)
            / params.voxel_size
        ).astype(np.int64)
        + 2,
    )
    nx, ny, nz = (
        int(dims[0]),
        int(dims[1]),
        int(dims[2]),
    )

    candidate_xyz = np.column_stack(
        (
            x[candidate_ids],
            y[candidate_ids],
            z[candidate_ids],
        )
    )
    ijk = np.floor(
        (
            candidate_xyz
            - origin[None, :]
        )
        / params.voxel_size
    ).astype(np.int64)
    keys = (
        ijk[:, 0]
        + nx
        * (
            ijk[:, 1]
            + ny * ijk[:, 2]
        )
    )

    unique_keys, inverse = np.unique(
        keys,
        return_inverse=True,
    )
    counts = np.bincount(
        inverse,
        minlength=unique_keys.size,
    ).astype(np.int64)

    candidate_score = score[candidate_ids]
    score_sums = np.bincount(
        inverse,
        weights=candidate_score,
        minlength=unique_keys.size,
    )
    mean_score = score_sums / np.maximum(
        counts,
        1,
    )

    strong_flags = (
        candidate_score
        >= params.strong_geometry_score
    ).astype(np.float64)
    strong_counts = np.bincount(
        inverse,
        weights=strong_flags,
        minlength=unique_keys.size,
    )
    strong_fraction = strong_counts / np.maximum(
        counts,
        1,
    )

    # A voxel is accepted only from geometric evidence. This prevents an
    # incorrect source classification from propagating into the recovered
    # terrain while still allowing genuine last/only returns near the PTD.
    approved = (
        counts >= params.min_points_per_voxel
    ) & (
        mean_score >= params.min_geometry_score
    ) & (
        (
            strong_fraction
            >= params.min_strong_fraction
        )
        | (
            mean_score
            >= params.strong_geometry_score
        )
    )

    approved_keys = np.asarray(
        unique_keys[approved],
        dtype=np.int64,
    )
    approved_keys.sort()

    LOGGER.info(
        "L3_RECOVERY source=%s candidates=%d "
        "candidate_voxels=%d approved_voxels=%d "
        "strong_voxels=%d classification_input_used=0",
        inspection.source_type.value,
        int(candidate_ids.size),
        int(unique_keys.size),
        int(approved_keys.size),
        int(
            np.count_nonzero(
                strong_fraction
                >= params.min_strong_fraction
            )
        ),
    )

    if progress is not None:
        progress(
            68,
            (
                "L3 recovery: "
                f"{approved_keys.size:,} geometry-approved voxels"
            ),
        )

    return L3RecoveryModel(
        params=params,
        origin=origin.astype(
            np.float64,
            copy=False,
        ),
        dimensions=(nx, ny, nz),
        approved_keys=approved_keys,
        candidate_sample_count=int(
            candidate_ids.size
        ),
        approved_voxel_count=int(
            approved_keys.size
        ),
        source_inspection=inspection,
    )
