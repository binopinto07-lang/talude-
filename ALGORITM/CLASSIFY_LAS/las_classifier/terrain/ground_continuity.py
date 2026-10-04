"""R20.2 measured Ground continuity graph guided by the R20 inverted mantle.

This module grows support ONLY through cells containing measured LAS returns,
compares adjacent local tangent planes in true 3D normal distance, and refuses
R20.1 roof/canopy cells. It never produces synthetic Ground points. The cloth,
PTD and veto models are kept read-only and unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from math import cos, radians

import numpy as np

from .dense_spatial_evidence import DenseSpatialEvidenceGrid
from .inverted_mantle import InvertedGroundMantle
from .mantle_veto import MantleVeto, VETO_NONE

LOGGER = logging.getLogger("las_cafiisica.terrain.ground_continuity")

_NEIGHBOURS = (
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
)


@dataclass(frozen=True, slots=True)
class ContinuityConfig:
    min_cell_returns: int = 2
    max_ptd_anchor_distance_m: float = 4.5
    max_cell_spread_m: float = 2.2
    max_cell_surface_offset_m: float = 0.48
    max_normal_step_m: float = 0.46
    max_step_z_m: float = 1.80
    max_normal_angle_deg: float = 60.0
    max_iterations: int = 18
    min_neighbour_support: int = 1
    max_point_above_normal_m: float = 0.36
    max_point_below_normal_m: float = 0.22
    additional_steep_face_m: float = 0.07


def _shift(source: np.ndarray, dy: int, dx: int, fill) -> np.ndarray:
    """Return target[y,x] == source[y+dy,x+dx], never wrapping edges."""
    h, w = source.shape
    output = np.full(source.shape, fill, dtype=source.dtype)
    if abs(dy) >= h or abs(dx) >= w:
        return output
    top = max(0, -dy)
    bottom = h - max(0, dy)
    left = max(0, -dx)
    right = w - max(0, dx)
    output[top:bottom, left:right] = source[
        top + dy:bottom + dy,
        left + dx:right + dx,
    ]
    return output


@dataclass(slots=True)
class GroundContinuity:
    """R20.2 connected cell evidence, never an inferred point cloud."""

    origin: np.ndarray
    cell_size: float
    nx: int
    ny: int
    surface: np.ndarray
    slope_x: np.ndarray
    slope_y: np.ndarray
    source_anchors: np.ndarray
    connected: np.ndarray
    eligible: np.ndarray
    blocked: np.ndarray
    breakline: np.ndarray
    expansion_steps: int
    config: ContinuityConfig

    @property
    def connected_cell_count(self) -> int:
        return int(np.count_nonzero(self.connected))

    @property
    def expanded_cell_count(self) -> int:
        return int(np.count_nonzero(self.connected & ~self.source_anchors))

    @property
    def anchor_cell_count(self) -> int:
        return int(np.count_nonzero(self.source_anchors))

    @property
    def blocked_cell_count(self) -> int:
        return int(np.count_nonzero(self.blocked))

    def recovery_mask(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
        *,
        veto_codes: np.ndarray | None = None,
    ) -> np.ndarray:
        """Decide ONLY on existing measured points, in bounded chunks.

        A connected 2.5D support cell is necessary but not sufficient:
        measured points must match a local 3D tangent plane. The R20.1
        post-classification veto must be applied again to rejected candidates,
        so a roof can never be reintroduced through continuity.
        """
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        z = np.asarray(z, dtype=np.float64)
        if x.shape != y.shape or x.shape != z.shape:
            raise ValueError("R20.2 recovery dimension mismatch")
        result = np.zeros(x.shape, dtype=np.bool_)
        finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        index = np.flatnonzero(finite)
        if index.size == 0:
            return result
        ix = np.floor((x[index] - self.origin[0]) / self.cell_size).astype(np.int64)
        iy = np.floor((y[index] - self.origin[1]) / self.cell_size).astype(np.int64)
        inside = (ix >= 0) & (ix < self.nx) & (iy >= 0) & (iy < self.ny)
        if not np.any(inside):
            return result
        index = index[inside]
        ix = ix[inside]
        iy = iy[inside]

        # Explicitly protect both vetoed cells and per-point vetoed returns.
        allowed = self.connected[iy, ix] & ~self.blocked[iy, ix]
        if veto_codes is not None:
            veto = np.asarray(veto_codes, dtype=np.uint8)
            if veto.shape != x.shape:
                raise ValueError("R20.2 veto code dimension mismatch")
            allowed &= veto[index] == VETO_NONE
        if not np.any(allowed):
            return result
        index = index[allowed]
        ix, iy = ix[allowed], iy[allowed]
        sx = self.slope_x[iy, ix].astype(np.float64)
        sy = self.slope_y[iy, ix].astype(np.float64)
        mid_x = self.origin[0] + (ix + 0.5) * self.cell_size
        mid_y = self.origin[1] + (iy + 0.5) * self.cell_size
        local_z = (
            self.surface[iy, ix].astype(np.float64)
            + sx * (x[index] - mid_x)
            + sy * (y[index] - mid_y)
        )
        normal_distance = (z[index] - local_z) / np.sqrt(1.0 + sx*sx + sy*sy)
        steep = np.hypot(sx, sy) >= 1.0
        upper = (
            self.config.max_point_above_normal_m
            + self.config.additional_steep_face_m * steep
        )
        accepted = (
            np.isfinite(normal_distance)
            & (normal_distance >= -self.config.max_point_below_normal_m)
            & (normal_distance <= upper)
        )
        result[index[accepted]] = True
        return result


def _linkable(
    surface: np.ndarray,
    sx: np.ndarray,
    sy: np.ndarray,
    cell_size: float,
    dy: int,
    dx: int,
    config: ContinuityConfig,
) -> np.ndarray:
    """Local bidirectional point-to-plane test across adjacent observed cells."""
    other_z = _shift(surface, dy, dx, np.float32(np.nan))
    other_sx = _shift(sx, dy, dx, np.float32(np.nan))
    other_sy = _shift(sy, dy, dx, np.float32(np.nan))

    norm_here = np.sqrt(1.0 + sx*sx + sy*sy)
    norm_other = np.sqrt(1.0 + other_sx*other_sx + other_sy*other_sy)
    angle_cosine = (
        1.0 + sx*other_sx + sy*other_sy
    ) / np.maximum(norm_here * norm_other, 1e-8)

    # The displacement to the neighbour centre is (dx,dy) in metric XY.
    ox = dx * cell_size
    oy = dy * cell_size
    forward = (other_z - surface - sx*ox - sy*oy) / norm_here
    reverse = (surface - other_z + other_sx*ox + other_sy*oy) / norm_other

    return (
        np.isfinite(other_z)
        & np.isfinite(other_sx)
        & np.isfinite(other_sy)
        & (np.abs(other_z - surface) <= config.max_step_z_m)
        & (angle_cosine >= cos(radians(config.max_normal_angle_deg)))
        & (np.abs(forward) <= config.max_normal_step_m)
        & (np.abs(reverse) <= config.max_normal_step_m)
    )


def build_ground_continuity(
    grid: DenseSpatialEvidenceGrid,
    mantle: InvertedGroundMantle,
    veto_guard: MantleVeto,
    *,
    config: ContinuityConfig | None = None,
) -> GroundContinuity:
    """Prepare an order-independent measured-only connectivity raster.

    R20 mantle is read without mutation. Expansion is bounded in iteration
    count and memory (eight 2D shifts, no cloud-wide Nx3 or per-point Python).
    Unobserved/missing cells do NOT become graph nodes or bridges.
    """
    cfg = config or ContinuityConfig()
    shape = (grid.ny, grid.nx)
    if mantle.surface.shape != shape:
        raise ValueError("R20.2 spatial grid and R20 mantle shape mismatch")
    count = grid.point_count.reshape(shape)
    spread = grid.max_z.reshape(shape) - grid.min_z.reshape(shape)
    geometry_count = grid.geometry_count.reshape(shape)
    strong = grid.strong_count.reshape(shape)
    surface = np.asarray(mantle.surface, dtype=np.float32)
    sx = np.asarray(mantle.slope_x, dtype=np.float32)
    sy = np.asarray(mantle.slope_y, dtype=np.float32)

    blocked = np.asarray(
        veto_guard.roof_candidate | veto_guard.canopy_candidate, dtype=np.bool_
    )
    measured = np.asarray(mantle.observed, dtype=np.bool_) & (count >= cfg.min_cell_returns)
    near_reference = (
        mantle.anchor_distance <= cfg.max_ptd_anchor_distance_m
    )
    not_artificial = (
        np.isfinite(grid.min_z.reshape(shape))
        & (np.abs(grid.min_z.reshape(shape) - surface) <= cfg.max_cell_surface_offset_m)
    )
    # Allow vegetation above a measured lower terrain return; point-level
    # normal gate will exclude vegetation. Do not propagate through a pure
    # elevated canopy/roof island flagged by R20.1.
    base_eligible = (
        measured & near_reference & not_artificial & ~blocked
        & (
            (spread <= cfg.max_cell_spread_m)
            | (strong >= 2)
        )
    )
    # R20.3: a terrace face/edge may be flagged as a raster breakline even
    # though its own measured returns are valid Ground.  Permit such cells as
    # TERMINAL recovery targets, but do not let them become propagation
    # bridges. This preserves the physical step between terrace levels.
    raw_breakline = getattr(mantle, "breakline", None)
    if raw_breakline is None:
        breakline = np.zeros(shape, dtype=np.bool_)
    else:
        breakline = np.asarray(raw_breakline, dtype=np.bool_)
        if breakline.shape != shape:
            raise ValueError("R20.3 breakline mask shape mismatch")
    face_eligible = (
        measured & near_reference & ~blocked & breakline
        & np.isfinite(surface)
        & (np.abs(grid.min_z.reshape(shape) - surface) <= max(
            cfg.max_cell_surface_offset_m, 0.62
        ))
        & (spread <= cfg.max_cell_spread_m)
    )
    eligible = base_eligible | face_eligible
    source_anchors = (
        eligible & mantle.reliable
        & (geometry_count >= 2) & (strong >= 2)
    )
    # R20.1 veto regions are also removed from anchor status even if their
    # source class2/PTD score is high.
    connected = source_anchors.copy()
    expansion_steps = 0
    if np.any(source_anchors):
        for iteration in range(max(1, int(cfg.max_iterations))):
            # Breakline cells can be recovered as terminal measured
            # faces, but they cannot be used as bridges to the opposite level.
            bridge_source = connected & ~breakline
            neighbour_votes = np.zeros(shape, dtype=np.uint8)
            for dy, dx in _NEIGHBOURS:
                from_connected = _shift(
                    bridge_source, dy, dx, np.bool_(False)
                )
                can_join = _linkable(surface, sx, sy, grid.cell_size, dy, dx, cfg)
                neighbour_votes += (from_connected & can_join).astype(np.uint8)
            update = (
                eligible & ~connected
                & (neighbour_votes >= cfg.min_neighbour_support)
            )
            if not np.any(update):
                break
            connected |= update
            expansion_steps = iteration + 1

    LOGGER.info(
        "R20_2_CONTINUITY anchors=%d eligible=%d connected=%d expanded=%d "
        "blocked=%d breakline=%d passes=%d unobserved_added=0 synthetic=0",
        int(np.count_nonzero(source_anchors)),
        int(np.count_nonzero(eligible)),
        int(np.count_nonzero(connected)),
        int(np.count_nonzero(connected & ~source_anchors)),
        int(np.count_nonzero(blocked)),
        int(np.count_nonzero(breakline)),
        expansion_steps,
    )
    return GroundContinuity(
        origin=mantle.origin.copy(),
        cell_size=float(grid.cell_size),
        nx=grid.nx,
        ny=grid.ny,
        surface=surface,
        slope_x=sx,
        slope_y=sy,
        source_anchors=source_anchors,
        connected=connected,
        eligible=eligible,
        blocked=blocked,
        breakline=breakline,
        expansion_steps=expansion_steps,
        config=cfg,
    )


def apply_continuity_recovery(
    evidence,
    continuity: GroundContinuity,
    veto_guard: MantleVeto,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    invalid: np.ndarray,
) -> np.ndarray:
    """Apply R20.2 after R20.1, never reaccepting an object/roof/invalid.

    Class2 and last/only returns are NOT sufficient. Every recovered return
    is measured, in a connected observed cell, tangent-plane supported,
    and must pass the R20.1 veto again.
    """
    from .ground_evidence import GroundDecision, PROV_GROUND_CONTINUITY

    invalid = np.asarray(invalid, dtype=np.bool_)
    veto_codes = veto_guard.classify_veto(x, y, z)
    candidate = continuity.recovery_mask(x, y, z, veto_codes=veto_codes)
    recovered = (
        candidate
        & (evidence.classifications() != 2)
        & (veto_codes == VETO_NONE)
        & ~invalid
        & (evidence.decision != int(GroundDecision.NOISE))
    )
    evidence.continuity_recovered = recovered
    evidence.decision[recovered] = int(
        GroundDecision.L3_GROUND_CONTINUITY_RECOVERED
    )
    evidence.provenance[recovered] |= PROV_GROUND_CONTINUITY
    return recovered
