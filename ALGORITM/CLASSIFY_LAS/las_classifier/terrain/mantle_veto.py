"""R20.1 post-classification veto using the preserved R20 inverted mantle.

A roof can *become* the measured low envelope when no returns reach the
terrain below it. Consequently proximity to the R20 cloth is insufficient.
This module adds a compact, multiscale, directional terrain-context test.
All candidates are geometric hypotheses; no semantic building claims.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import distance_transform_edt, uniform_filter

from .dense_spatial_evidence import DenseSpatialEvidenceGrid
from .inverted_mantle import InvertedGroundMantle

LOGGER = logging.getLogger("las_cafiisica.terrain.mantle_veto")

VETO_NONE = np.uint8(0)
VETO_HEIGHT = np.uint8(1)
VETO_ROOF_CANDIDATE = np.uint8(2)
VETO_CANOPY_CANDIDATE = np.uint8(3)


@dataclass(frozen=True, slots=True)
class MantleVetoConfig:
    # Permissive normal limit preserves measured talude faces on a 0.35-0.90m
    # 2.5-D cell. Field tests must verify this limit rather than tuning blindly.
    max_above_normal_m: float = 0.42
    canopy_spread_m: float = 1.35
    canopy_above_normal_m: float = 0.42
    elevated_context_m: float = 1.70
    roof_max_cell_spread_m: float = 0.70
    roof_max_gradient: float = 0.85
    minimum_lower_directions: int = 3
    roof_extension_m: float = 1.10
    roof_max_plateau_delta_m: float = 0.75


def _shift_with_fill(array: np.ndarray, dy: int, dx: int, fill) -> np.ndarray:
    """out[y,x] = array[y+dy,x+dx], without wrapping cloud boundaries."""
    h, w = array.shape
    result = np.full((h, w), fill, dtype=array.dtype)
    if abs(dy) >= h or abs(dx) >= w:
        return result
    dy0 = max(0, -dy)
    dy1 = h - max(0, dy)
    dx0 = max(0, -dx)
    dx1 = w - max(0, dx)
    result[dy0:dy1, dx0:dx1] = array[
        dy0 + dy:dy1 + dy,
        dx0 + dx:dx1 + dx,
    ]
    return result


def _directional_below(
    lower: np.ndarray, observed: np.ndarray, cell: float, delta: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Count cardinal directions where *measured* returns are lower.

    Checks several distances to catch a pitched/flat roof even when its own
    low envelope follows the roof. Requiring >=3 cardinal directions reduces
    false roof detections on elongated vineyard terraces (only downhill side).
    """
    shape = lower.shape
    support = np.where(observed, lower, np.inf).astype(np.float32)
    distances = tuple(dict.fromkeys(
        max(1, int(np.ceil(meters / cell))) for meters in (2.5, 4.5, 7.5, 11.0)
    ))
    directions = ((0, 1), (0, -1), (1, 0), (-1, 0))
    lower_count = np.zeros(shape, dtype=np.uint8)
    max_prominence = np.zeros(shape, dtype=np.float32)
    for ay, ax in directions:
        baseline = np.full(shape, np.inf, dtype=np.float32)
        for offset in distances:
            candidate = _shift_with_fill(
                support, ay * offset, ax * offset, np.float32(np.inf)
            )
            np.minimum(baseline, candidate, out=baseline)
        diff = lower - baseline
        valid = observed & np.isfinite(diff) & (diff > delta)
        lower_count += valid.astype(np.uint8)
        max_prominence = np.maximum(
            max_prominence,
            np.where(valid, diff, 0.0).astype(np.float32),
        )
    return lower_count, max_prominence


@dataclass(slots=True)
class MantleVeto:
    origin: np.ndarray
    cell_size: float
    nx: int
    ny: int
    surface: np.ndarray
    reliable: np.ndarray
    slope_x: np.ndarray
    slope_y: np.ndarray
    roof_candidate: np.ndarray
    canopy_candidate: np.ndarray
    directional_lower_count: np.ndarray
    config: MantleVetoConfig

    @property
    def roof_candidate_cell_count(self) -> int:
        return int(np.count_nonzero(self.roof_candidate))

    @property
    def canopy_candidate_cell_count(self) -> int:
        return int(np.count_nonzero(self.canopy_candidate))

    def classify_veto(
        self, x: np.ndarray, y: np.ndarray, z: np.ndarray,
    ) -> np.ndarray:
        """Return exclusive VETO_* codes for a bounded point chunk.

        Only these flags may invalidate a previously accepted point. Cells
        outside the mantle or without a reliable local reference are not
        automatically classed as vegetation/roof.
        """
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        z = np.asarray(z, dtype=np.float64)
        if x.shape != y.shape or x.shape != z.shape:
            raise ValueError("R20.1 mantle-veto query dimension mismatch")
        codes = np.zeros(x.shape, dtype=np.uint8)
        valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        idx = np.flatnonzero(valid)
        if not idx.size:
            return codes

        ix = np.floor((x[idx] - self.origin[0]) / self.cell_size).astype(np.int64)
        iy = np.floor((y[idx] - self.origin[1]) / self.cell_size).astype(np.int64)
        inside = (ix >= 0) & (ix < self.nx) & (iy >= 0) & (iy < self.ny)
        if not np.any(inside):
            return codes
        idx = idx[inside]
        ix, iy = ix[inside], iy[inside]
        roof = self.roof_candidate[iy, ix]
        canopy = self.canopy_candidate[iy, ix]
        reliable = self.reliable[iy, ix]

        # Local tangent compensates for steep slopes, instead of comparing
        # against the raw cell minimum vertically.
        ox = self.origin[0] + (ix + 0.5) * self.cell_size
        oy = self.origin[1] + (iy + 0.5) * self.cell_size
        gx = self.slope_x[iy, ix].astype(np.float64)
        gy = self.slope_y[iy, ix].astype(np.float64)
        predicted_z = (
            self.surface[iy, ix].astype(np.float64)
            + gx * (x[idx] - ox)
            + gy * (y[idx] - oy)
        )
        normal_above = (z[idx] - predicted_z) / np.sqrt(1.0 + gx * gx + gy * gy)
        # Priority: roof island (including when cloth sits ON the roof);
        # canopy island; elevated return above a reliable ground mantle.
        roof_hit = roof
        # Candidate canopy is an elevated island with >=3 lower directions
        # and large intra-cell spread. Tiny below-envelope outliers are spared.
        canopy_hit = ~roof_hit & canopy & (normal_above > -0.15)
        height_hit = (
            ~roof_hit & ~canopy_hit & reliable
            & (normal_above > self.config.max_above_normal_m)
        )
        selected = codes[idx]
        selected[roof_hit] = VETO_ROOF_CANDIDATE
        selected[canopy_hit] = VETO_CANOPY_CANDIDATE
        selected[height_hit] = VETO_HEIGHT
        codes[idx] = selected
        return codes


def build_mantle_veto(
    grid: DenseSpatialEvidenceGrid,
    mantle: InvertedGroundMantle,
    config: MantleVetoConfig | None = None,
) -> MantleVeto:
    """Detect roofs/canopy from *measured* context, preserving R20 cloth."""
    cfg = config or MantleVetoConfig()
    shape = (grid.ny, grid.nx)
    observed = np.asarray(mantle.observed, dtype=np.bool_)
    cell_count = grid.point_count.reshape(shape)
    lower = np.where(
        observed, grid.min_z.reshape(shape), np.nan
    ).astype(np.float32)
    spread = np.where(
        observed,
        grid.max_z.reshape(shape) - grid.min_z.reshape(shape),
        0,
    ).astype(np.float32)
    dirs, prominence = _directional_below(
        lower, observed, grid.cell_size, cfg.elevated_context_m
    )
    norm_gradient = np.hypot(mantle.slope_x, mantle.slope_y)
    continuity = uniform_filter(observed.astype(np.float32), size=3, mode="nearest")

    roof_core = (
        observed
        & (cell_count >= 3)
        & (spread <= cfg.roof_max_cell_spread_m)
        & (norm_gradient <= cfg.roof_max_gradient)
        & (continuity >= 0.55)
        & (dirs >= cfg.minimum_lower_directions)
    )
    roof = roof_core.copy()
    if np.any(roof_core):
        distance_cells, nearest = distance_transform_edt(
            ~roof_core, return_indices=True
        )
        roof_z = lower[tuple(nearest)]  # Measured footprint, not sagging cloth
        roof |= (
            observed
            & (cell_count >= 3)
            & (spread <= 0.90)
            & ((distance_cells * grid.cell_size) <= cfg.roof_extension_m)
            & (np.abs(lower - roof_z) <= cfg.roof_max_plateau_delta_m)
        )
    # Dense canopy with possible local ground returns is resolved per-point
    # by the height gate, NOT rejected wholesale. A canopy-only island may
    # have its own elevated minimum, so require 3 lower directions and large
    # intra-cell spread before invoking a whole-cell canopy veto.
    canopy = (
        observed
        & ~roof
        & (cell_count >= 3)
        & (spread >= cfg.canopy_spread_m)
        & (dirs >= cfg.minimum_lower_directions)
    )
    LOGGER.info(
        "R20_1_MANTLE_VETO roof_cells=%d canopy_cells=%d "
        "reliable_reference_cells=%d high_prominence_cells=%d",
        int(np.count_nonzero(roof)),
        int(np.count_nonzero(canopy)),
        int(np.count_nonzero(mantle.reliable)),
        int(np.count_nonzero(prominence >= cfg.elevated_context_m)),
    )
    return MantleVeto(
        origin=mantle.origin.copy(),
        cell_size=mantle.cell_size,
        nx=mantle.nx,
        ny=mantle.ny,
        surface=mantle.surface,
        reliable=mantle.reliable,
        slope_x=mantle.slope_x,
        slope_y=mantle.slope_y,
        roof_candidate=roof,
        canopy_candidate=canopy,
        directional_lower_count=dirs,
        config=cfg,
    )


def apply_mantle_veto(
    evidence,
    guard: MantleVeto,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    invalid: np.ndarray,
) -> np.ndarray:
    """Veto already-accepted Ground, including mistaken PTD/class2 decisions.

    The return codes count only actual changes to accepted decisions.
    Rejected/invalid points are not relabelled. Recovery (R20) runs BEFORE
    this method so an elevated roof cannot be recovered again.
    """
    from .ground_evidence import (
        GroundDecision,
        PROV_MANTLE_VETO,
    )

    proposed = guard.classify_veto(x, y, z)
    invalid = np.asarray(invalid, dtype=np.bool_)
    if invalid.shape != proposed.shape:
        raise ValueError("R20.1 invalid-mask shape mismatch")
    accepted = evidence.classifications() == 2
    applied = (proposed != VETO_NONE) & accepted & ~invalid
    actual = np.where(applied, proposed, VETO_NONE).astype(np.uint8)
    evidence.mantle_veto_code = actual

    roof = actual == VETO_ROOF_CANDIDATE
    canopy = actual == VETO_CANOPY_CANDIDATE
    high = actual == VETO_HEIGHT
    evidence.decision[roof | high] = int(GroundDecision.NON_GROUND_OBJECT)
    evidence.decision[canopy] = int(GroundDecision.NON_GROUND_VEGETATION)
    evidence.provenance[applied] |= PROV_MANTLE_VETO
    evidence.score[applied] = np.minimum(evidence.score[applied], np.float32(0.20))
    return actual
