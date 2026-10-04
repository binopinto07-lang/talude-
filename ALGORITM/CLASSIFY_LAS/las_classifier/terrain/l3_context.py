from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..cloud.model import CloudModel
from ..ground.density import deterministic_sample_indices, scaled_xyz
from ..ground.terrain_tin import TerrainTIN


LOGGER = logging.getLogger("las_cafiisica.terrain.l3_context")
ProgressCallback = Callable[[int, str], None]


@dataclass(slots=True)
class CoarseDetrendModel:
    tin: TerrainTIN
    cell_size: float
    representative_count: int

    @classmethod
    def build(
        cls,
        ground_vertices: np.ndarray,
        *,
        spacing: float,
    ) -> "CoarseDetrendModel | None":
        xyz = np.asarray(ground_vertices, dtype=np.float64)
        finite = np.all(np.isfinite(xyz), axis=1)
        xyz = xyz[finite]
        if xyz.shape[0] < 3:
            return None

        cell_size = max(
            6.0,
            min(
                18.0,
                float(spacing) * 120.0,
            ),
        )
        origin = (
            np.floor(
                np.min(xyz[:, :2], axis=0)
                / cell_size
            )
            * cell_size
        )
        ij = np.floor(
            (xyz[:, :2] - origin[None, :])
            / cell_size
        ).astype(np.int64)
        nx = int(np.max(ij[:, 0])) + 1
        keys = ij[:, 0] + nx * ij[:, 1]
        _, inverse = np.unique(
            keys,
            return_inverse=True,
        )
        counts = np.bincount(
            inverse
        ).astype(np.float64)
        x = np.bincount(
            inverse,
            weights=xyz[:, 0],
        ) / np.maximum(counts, 1.0)
        y = np.bincount(
            inverse,
            weights=xyz[:, 1],
        ) / np.maximum(counts, 1.0)
        z = np.bincount(
            inverse,
            weights=xyz[:, 2],
        ) / np.maximum(counts, 1.0)
        representatives = np.column_stack(
            (x, y, z)
        )
        if representatives.shape[0] < 3:
            return None

        try:
            tin = TerrainTIN.build(representatives)
        except Exception:
            LOGGER.exception(
                "COARSE_DETREND_BUILD_FAILED"
            )
            return None

        LOGGER.info(
            "COARSE_DETREND cell=%.3f representatives=%d triangles=%d",
            cell_size,
            representatives.shape[0],
            tin.triangle_count,
        )
        return cls(
            tin=tin,
            cell_size=float(cell_size),
            representative_count=int(
                representatives.shape[0]
            ),
        )

    def residual_xyz(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        metrics = self.tin.metrics(x, y, z)
        residual = np.asarray(
            metrics["vertical_residual"],
            dtype=np.float64,
        )
        residual[~metrics["valid"]] = np.nan
        return residual


@dataclass(frozen=True, slots=True)
class IntensityProfile:
    median: float
    mad: float
    sample_count: int

    def score(
        self,
        intensity: np.ndarray | None,
        count: int,
    ) -> np.ndarray:
        if intensity is None:
            return np.full(
                count,
                0.50,
                dtype=np.float64,
            )
        values = np.asarray(
            intensity,
            dtype=np.float64,
        )
        scale = max(
            1.0,
            self.mad * 6.0,
        )
        result = np.exp(
            -np.abs(values - self.median)
            / scale
        )
        result[~np.isfinite(values)] = 0.50
        return np.clip(
            result,
            0.0,
            1.0,
        )


@dataclass(slots=True)
class L3SpatialContext:
    origin: np.ndarray
    cell_size: float
    nx: int
    keys: np.ndarray
    point_count: np.ndarray
    neighbour_support: np.ndarray
    vertical_spread: np.ndarray
    roughness: np.ndarray
    intensity_profile: IntensityProfile | None
    sample_count: int

    def _keys_for_xy(
        self,
        x: np.ndarray,
        y: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        ix = np.floor(
            (x - self.origin[0])
            / self.cell_size
        ).astype(np.int64)
        iy = np.floor(
            (y - self.origin[1])
            / self.cell_size
        ).astype(np.int64)
        inside = (
            (ix >= 0)
            & (iy >= 0)
            & (ix < self.nx)
        )
        keys = ix + self.nx * iy
        return keys, inside

    def query(
        self,
        x: np.ndarray,
        y: np.ndarray,
    ) -> tuple[
        np.ndarray,
        np.ndarray,
        np.ndarray,
    ]:
        count = x.shape[0]
        support = np.zeros(
            count,
            dtype=np.float32,
        )
        spread = np.zeros(
            count,
            dtype=np.float32,
        )
        roughness = np.zeros(
            count,
            dtype=np.float32,
        )
        if self.keys.size == 0 or count == 0:
            return support, spread, roughness

        query_keys, inside = self._keys_for_xy(
            x,
            y,
        )
        ids = np.flatnonzero(inside)
        if ids.size == 0:
            return support, spread, roughness

        q = query_keys[ids]
        pos = np.searchsorted(
            self.keys,
            q,
        )
        safe = np.minimum(
            pos,
            self.keys.size - 1,
        )
        hit = (
            (pos < self.keys.size)
            & (self.keys[safe] == q)
        )
        if np.any(hit):
            target = ids[hit]
            source = safe[hit]
            support[target] = (
                self.neighbour_support[source]
            )
            spread[target] = (
                self.vertical_spread[source]
            )
            roughness[target] = (
                self.roughness[source]
            )
        return support, spread, roughness


def _dimension(
    points,
    name: str,
) -> np.ndarray | None:
    names = set(
        points.point_format.dimension_names
    )
    if name not in names:
        return None
    return np.asarray(points[name])


def build_l3_spatial_context(
    cloud: CloudModel,
    ptd_model,
    *,
    sample_target: int = 1_500_000,
    progress: ProgressCallback | None = None,
) -> L3SpatialContext:
    target = max(
        50_000,
        int(sample_target),
    )
    indices, _ = deterministic_sample_indices(
        cloud.point_count,
        target,
    )
    xyz = scaled_xyz(
        cloud,
        indices,
    )
    points = cloud.las.points[indices]
    if progress is not None:
        progress(
            48,
            (
                "L3 context: analysing "
                f"{indices.size:,} measured points"
            ),
        )

    score, metrics = ptd_model._confidence(
        xyz[:, 0],
        xyz[:, 1],
        xyz[:, 2],
        points=points,
    )
    valid = (
        metrics["valid"]
        & np.isfinite(
            metrics["vertical_residual"]
        )
        & np.isfinite(
            metrics["plane_distance"]
        )
    )

    spacing = max(
        float(
            ptd_model.analysis.median_spacing
        ),
        0.005,
    )
    cell_size = max(
        0.35,
        min(
            0.90,
            spacing * 8.0,
        ),
    )
    mins = np.asarray(
        cloud.las.header.mins[:2],
        dtype=np.float64,
    )
    maxs = np.asarray(
        cloud.las.header.maxs[:2],
        dtype=np.float64,
    )
    origin = (
        np.floor(mins / cell_size)
        * cell_size
    )
    nx = max(
        1,
        int(
            np.ceil(
                (maxs[0] - origin[0])
                / cell_size
            )
        )
        + 2,
    )

    ij = np.floor(
        (xyz[:, :2] - origin[None, :])
        / cell_size
    ).astype(np.int64)
    keys_all = (
        ij[:, 0]
        + nx * ij[:, 1]
    )
    valid_ids = np.flatnonzero(valid)
    if valid_ids.size == 0:
        return L3SpatialContext(
            origin=origin,
            cell_size=cell_size,
            nx=nx,
            keys=np.empty(
                0,
                dtype=np.int64,
            ),
            point_count=np.empty(
                0,
                dtype=np.int32,
            ),
            neighbour_support=np.empty(
                0,
                dtype=np.float32,
            ),
            vertical_spread=np.empty(
                0,
                dtype=np.float32,
            ),
            roughness=np.empty(
                0,
                dtype=np.float32,
            ),
            intensity_profile=None,
            sample_count=int(
                indices.size
            ),
        )

    keys, inverse = np.unique(
        keys_all[valid_ids],
        return_inverse=True,
    )
    counts = np.bincount(
        inverse,
        minlength=keys.size,
    ).astype(np.int32)
    strong = (
        score[valid_ids]
        >= max(
            0.68,
            float(
                ptd_model.params.confidence_threshold
            ),
        )
    )
    strong_counts = np.bincount(
        inverse,
        weights=strong.astype(
            np.float64
        ),
        minlength=keys.size,
    )
    support = (
        strong_counts
        / np.maximum(counts, 1)
    )

    residual = np.asarray(
        metrics[
            "vertical_residual"
        ][valid_ids],
        dtype=np.float64,
    )
    minimum = np.full(
        keys.size,
        np.inf,
        dtype=np.float64,
    )
    maximum = np.full(
        keys.size,
        -np.inf,
        dtype=np.float64,
    )
    np.minimum.at(
        minimum,
        inverse,
        residual,
    )
    np.maximum.at(
        maximum,
        inverse,
        residual,
    )
    spread = np.maximum(
        0.0,
        maximum - minimum,
    )

    plane_distance = np.asarray(
        metrics["plane_distance"][
            valid_ids
        ],
        dtype=np.float64,
    )
    roughness = np.bincount(
        inverse,
        weights=plane_distance,
        minlength=keys.size,
    ) / np.maximum(
        counts,
        1,
    )

    intensity_profile = None
    intensity = _dimension(
        points,
        "intensity",
    )
    if intensity is not None:
        intensity = np.asarray(
            intensity,
            dtype=np.float64,
        )
        profile_mask = (
            valid
            & (
                score
                >= max(
                    0.72,
                    ptd_model.params.confidence_threshold,
                )
            )
        )
        values = intensity[
            profile_mask
        ]
        values = values[
            np.isfinite(values)
        ]
        if values.size >= 32:
            median = float(
                np.median(values)
            )
            mad = float(
                np.median(
                    np.abs(
                        values - median
                    )
                )
            )
            intensity_profile = (
                IntensityProfile(
                    median=median,
                    mad=max(
                        mad,
                        1.0,
                    ),
                    sample_count=int(
                        values.size
                    ),
                )
            )

    LOGGER.info(
        "L3_CONTEXT sample=%d cell=%.3f cells=%d "
        "strong_mean=%.4f intensity_profile=%s",
        indices.size,
        cell_size,
        keys.size,
        (
            float(np.mean(support))
            if support.size
            else 0.0
        ),
        (
            "yes"
            if intensity_profile
            is not None
            else "no"
        ),
    )
    if progress is not None:
        progress(
            56,
            (
                "L3 context: "
                f"{keys.size:,} spatial evidence cells"
            ),
        )

    return L3SpatialContext(
        origin=origin.astype(
            np.float64,
            copy=False,
        ),
        cell_size=float(cell_size),
        nx=nx,
        keys=keys.astype(
            np.int64,
            copy=False,
        ),
        point_count=counts,
        neighbour_support=support.astype(
            np.float32,
            copy=False,
        ),
        vertical_spread=spread.astype(
            np.float32,
            copy=False,
        ),
        roughness=roughness.astype(
            np.float32,
            copy=False,
        ),
        intensity_profile=intensity_profile,
        sample_count=int(
            indices.size
        ),
    )
