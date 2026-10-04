"""R20 experimental inverted-ground mantle on *measured* L3 returns.

The inverted cloth is geometric evidence, never a replacement for observed
Ground. Inferred cells are a separate diagnostic layer and are not emitted by
Ground Only. Keep this module free of cloud-wide Nx3 allocations.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.ndimage import (
    convolve,
    distance_transform_edt,
    maximum_filter,
    median_filter,
    minimum_filter,
    uniform_filter,
)

from .dense_spatial_evidence import DenseSpatialEvidenceGrid

LOGGER = logging.getLogger("las_cafiisica.terrain.inverted_mantle")
ProgressCallback = Callable[[int, str], None]

# An inversion is performed explicitly: z_inv = reference_z - measured_z.
_CROSS = np.array(
    [[0.0, 0.25, 0.0], [0.25, 0.0, 0.25], [0.0, 0.25, 0.0]],
    dtype=np.float32,
)


@dataclass(frozen=True, slots=True)
class MantleConfig:
    iterations: int = 65
    cloth_rigidity: float = 0.78
    max_normal_above_m: float = 0.12
    max_normal_below_m: float = 0.08
    anchor_radius_m: float = 3.0
    min_occupancy_fraction: float = 0.65
    min_cell_returns: int = 3


@dataclass(slots=True)
class InvertedGroundMantle:
    """Conservative 2.5-D envelope; vertical cliffs remain uncertain."""

    origin: np.ndarray
    cell_size: float
    nx: int
    ny: int
    surface: np.ndarray
    observed: np.ndarray
    reliable: np.ndarray
    inferred: np.ndarray
    ambiguous: np.ndarray
    possible_no_ground_observation: np.ndarray
    anchor_distance: np.ndarray
    slope_x: np.ndarray
    slope_y: np.ndarray
    reference_z: float
    config: MantleConfig
    # Raster breakline used by R20.3 as a terminal face-recovery barrier.
    breakline: np.ndarray | None = None
    veto_guard: object | None = None  # Optional R20.1 post-decision evidence

    @property
    def observed_cell_count(self) -> int:
        return int(np.count_nonzero(self.observed))

    @property
    def reliable_cell_count(self) -> int:
        return int(np.count_nonzero(self.reliable))

    @property
    def inferred_cell_count(self) -> int:
        return int(np.count_nonzero(self.inferred))

    @property
    def ambiguous_cell_count(self) -> int:
        return int(np.count_nonzero(self.ambiguous))

    @property
    def possible_no_ground_observation_count(self) -> int:
        return int(np.count_nonzero(self.possible_no_ground_observation))

    def recovery_mask(
        self, x: np.ndarray, y: np.ndarray, z: np.ndarray,
    ) -> np.ndarray:
        """Recover only RETURNS physically measured near supported cloth.

        Never accepts unobserved/inferred cells. Source classification,
        last/only return, and the PTD decision are not authority here.
        """
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        z = np.asarray(z, dtype=np.float64)
        if x.shape != y.shape or x.shape != z.shape:
            raise ValueError("Mantle query dimension mismatch")
        result = np.zeros(x.shape, dtype=np.bool_)
        finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        indices = np.flatnonzero(finite)
        if not indices.size:
            return result
        ix = np.floor((x[indices] - self.origin[0]) / self.cell_size).astype(np.int64)
        iy = np.floor((y[indices] - self.origin[1]) / self.cell_size).astype(np.int64)
        inside = (ix >= 0) & (ix < self.nx) & (iy >= 0) & (iy < self.ny)
        if not np.any(inside):
            return result
        selected = indices[inside]
        cx = ix[inside]
        cy = iy[inside]
        valid_cell = self.reliable[cy, cx]
        if not np.any(valid_cell):
            return result
        selected = selected[valid_cell]
        cx = cx[valid_cell]
        cy = cy[valid_cell]
        centre_x = self.origin[0] + (cx + 0.5) * self.cell_size
        centre_y = self.origin[1] + (cy + 0.5) * self.cell_size
        gx = self.slope_x[cy, cx].astype(np.float64)
        gy = self.slope_y[cy, cx].astype(np.float64)
        z_plane = (
            self.surface[cy, cx].astype(np.float64)
            + gx * (x[selected] - centre_x)
            + gy * (y[selected] - centre_y)
        )
        normal_residual = (z[selected] - z_plane) / np.sqrt(1.0 + gx * gx + gy * gy)
        close_to_mantle = (
            np.isfinite(normal_residual)
            & (normal_residual >= -self.config.max_normal_below_m)
            & (normal_residual <= self.config.max_normal_above_m)
        )
        result[selected[close_to_mantle]] = True
        return result


def _nearest_numeric(surface: np.ndarray, observed: np.ndarray) -> np.ndarray:
    """Nearest fill is for cloth computation ONLY, not ground observation."""
    if not np.any(observed):
        raise ValueError("No measured returns for inverted mantle")
    if np.all(observed):
        return np.asarray(surface, dtype=np.float32).copy()
    missing = ~observed
    nearest = distance_transform_edt(
        missing, return_distances=False, return_indices=True
    )
    return np.asarray(surface, dtype=np.float32)[tuple(nearest)]


def build_inverted_ground_mantle(
    grid: DenseSpatialEvidenceGrid,
    *,
    config: MantleConfig | None = None,
    progress: ProgressCallback | None = None,
) -> InvertedGroundMantle:
    """Invert the measured low envelope; lower a cloth in inverted space.

    No original-class2 votes, no inference classified Ground, no point-index
    subsampling. A discontinuity fence preserves terrace steps for later
    local-3D processing instead of smoothing them across a global raster.
    """
    cfg = config or MantleConfig()
    shape = (grid.ny, grid.nx)
    counts = grid.point_count.reshape(shape)
    geometry_count = grid.geometry_count.reshape(shape)
    strong_count = grid.strong_count.reshape(shape)
    observed = counts > 0
    if not np.any(observed):
        raise ValueError("Cannot build Ground mantle without measured returns")

    lower = np.asarray(grid.min_z.reshape(shape), dtype=np.float32)
    lower = np.where(observed, lower, np.nan)
    numeric_lower = _nearest_numeric(lower, observed)
    local_median = median_filter(numeric_lower, size=3, mode="nearest")
    local_min = minimum_filter(numeric_lower, size=3, mode="nearest")
    local_max = maximum_filter(numeric_lower, size=3, mode="nearest")
    continuity = uniform_filter(observed.astype(np.float32), size=3, mode="nearest")
    # A real steep face (e.g. 45 degrees) is not a vertical terrace step.
    breakline = (local_max - local_min) > max(1.60, 4.0 * grid.cell_size)

    # Flag anomalous low spikes only where numerous observed neighbours agree.
    low_spike = (
        observed
        & (continuity >= 0.80)
        & ((local_median - numeric_lower) > max(0.35, grid.cell_size))
        & ~breakline
        & (strong_count == 0)
    )
    robust_lower = np.where(low_spike, local_median, numeric_lower).astype(np.float32)

    reference_z = float(
        np.max(grid.max_z.reshape(shape)[observed])
    )
    inverted_obstacle = np.asarray(reference_z - robust_lower, dtype=np.float32)
    # In inverted Z a cloth starts above the cloud, descends, and cannot
    # cross its upper obstacle. The original-Z equivalent is a lower mantle.
    ceiling = float(np.max(inverted_obstacle))
    floor = float(np.min(inverted_obstacle))
    cloth = np.full(shape, ceiling + 0.75, dtype=np.float32)
    iterations = max(1, int(cfg.iterations))
    gravity = max(0.05, (ceiling - floor + 1.0) / iterations)
    for step in range(iterations):
        lowered = cloth - gravity
        smoothed = convolve(lowered, _CROSS, mode="nearest")
        candidate = (
            cfg.cloth_rigidity * lowered
            + (1.0 - cfg.cloth_rigidity) * smoothed
        )
        cloth = np.maximum(candidate, inverted_obstacle)
        # Do not carry a cloth facet across a large abrupt drop/crest.
        cloth[breakline & observed] = inverted_obstacle[breakline & observed]
        if progress is not None and step % 12 == 0:
            progress(56, f"R20 inverted cloth: {step+1}/{iterations}")

    surface = np.asarray(reference_z - cloth, dtype=np.float32)
    # Bounded sag at a measured cell; missing cells remain interpolation
    # candidates rather than being mislabeled observed points.
    surface[observed] = np.maximum(
        surface[observed], robust_lower[observed] - 0.04
    )
    slope_y, slope_x = np.gradient(surface, grid.cell_size)
    slope_x = np.clip(slope_x, -3.0, 3.0).astype(np.float32)
    slope_y = np.clip(slope_y, -3.0, 3.0).astype(np.float32)

    anchors = (
        observed
        & (geometry_count >= 2)
        & (strong_count >= 2)
        & (strong_count / np.maximum(geometry_count, 1) >= 0.30)
    )
    anchor_distance = (
        distance_transform_edt(~anchors).astype(np.float32) * grid.cell_size
        if np.any(anchors)
        else np.full(shape, np.inf, dtype=np.float32)
    )
    near_anchor = anchor_distance <= cfg.anchor_radius_m
    local_spread = (
        grid.max_z.reshape(shape).astype(np.float32)
        - grid.min_z.reshape(shape).astype(np.float32)
    )
    # Missing PTD geometry is not automatically missing physical terrain.
    # Both measured and anchor evidence are mandatory for recovery.
    reliable = (
        observed
        & (counts >= cfg.min_cell_returns)
        & (continuity >= cfg.min_occupancy_fraction)
        & near_anchor
        & ~breakline
        & ~low_spike
        & ((local_spread <= 2.50) | (strong_count >= 2))
    )
    # Only small surrounded missing cells are candidate inferred mantle.
    # They are NEVER used to classify points, nor exported as real Ground.
    near_observation = distance_transform_edt(~observed) <= 2.0
    inferred = (
        ~observed
        & near_observation
        & near_anchor
        & (continuity >= 0.55)
        & ~breakline
    )
    # Returns may exist but the *terrain* may not have been observed (canopy).
    # This is a hypothesis only; never auto-label it Ground.
    possible_no_ground = (
        observed
        & ~reliable
        & (strong_count == 0)
        & near_anchor
        & ((robust_lower - local_median) > 0.50)
    )
    ambiguous = observed & ~reliable

    LOGGER.info(
        "R20_INVERTED_MANTLE observed=%d reliable=%d inferred=%d "
        "ambiguous=%d possible_no_ground=%d low_spikes=%d breakline=%d "
        "anchors=%d cell_m=%.4f synthetic=0",
        int(np.count_nonzero(observed)),
        int(np.count_nonzero(reliable)),
        int(np.count_nonzero(inferred)),
        int(np.count_nonzero(ambiguous)),
        int(np.count_nonzero(possible_no_ground)),
        int(np.count_nonzero(low_spike)),
        int(np.count_nonzero(breakline)),
        int(np.count_nonzero(anchors)),
        grid.cell_size,
    )
    return InvertedGroundMantle(
        origin=grid.origin.copy(),
        cell_size=float(grid.cell_size),
        nx=int(grid.nx),
        ny=int(grid.ny),
        surface=surface,
        observed=observed,
        reliable=reliable,
        inferred=inferred,
        ambiguous=ambiguous,
        possible_no_ground_observation=possible_no_ground,
        anchor_distance=anchor_distance,
        slope_x=slope_x,
        slope_y=slope_y,
        reference_z=reference_z,
        config=cfg,
        breakline=breakline.astype(np.bool_, copy=False),
    )
