"""Experimental observed-ground curvature discovery; not connected to AUTO.

Only run on spatially bounded Ground tiles. No points are interpolated across
unobserved terrain and each emitted XYZ is an actual input Ground sample.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates, uniform_filter

from .breakline_seed import SeedEvidence


@dataclass(frozen=True, slots=True)
class CurvatureSeedConfig:
    cell_size_m: float = 0.25
    smoothing_sigma_cells: float = 1.0
    min_support_fraction: float = 0.80
    support_window_cells: int = 5
    min_slope: float = 0.08
    min_abs_profile_curvature_m_inv: float = 0.12
    min_separation_m: float = 1.0
    max_grid_cells: int = 1_000_000
    max_seeds_per_kind: int = 128

    def __post_init__(self) -> None:
        if (self.cell_size_m <= 0 or self.smoothing_sigma_cells <= 0
            or self.support_window_cells < 3 or self.support_window_cells % 2 == 0
            or not 0 < self.min_support_fraction <= 1 or self.min_slope < 0
            or self.min_abs_profile_curvature_m_inv <= 0 or self.min_separation_m < 0
            or self.max_grid_cells < 25 or self.max_seeds_per_kind < 1):
            raise ValueError("Configuração de curvature seed inválida.")


def curvature_seed_candidates(
    ground_xyz: np.ndarray,
    *,
    config: CurvatureSeedConfig | None = None,
) -> list[SeedEvidence]:
    """Detect separate CREST (negative curvature) / TOE (positive curvature).

    Curvature is the directional second height derivative in the XY gradient
    direction. Candidates require local observations, slope and non-maxima
    suppression; output XYZ uses an existing Ground point (median-elevation
    representative of its observed cell). Scores are relative, not calibrated.
    """
    cfg = config or CurvatureSeedConfig()
    pts = np.asarray(ground_xyz, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] < 3:
        raise ValueError("ground_xyz deve ser matriz Nx3 (ou mais colunas).")
    pts = pts[:, :3]
    pts = pts[np.isfinite(pts).all(axis=1)]
    if len(pts) < 30:
        return []
    origin = pts[:, :2].min(axis=0)
    ij = np.floor((pts[:, :2] - origin) / cfg.cell_size_m).astype(np.int64)
    nx = int(ij[:, 0].max()) + 1
    ny = int(ij[:, 1].max()) + 1
    if nx * ny > cfg.max_grid_cells:
        raise ValueError("Tile demasiado grande: subdividir antes da análise de curvatura.")
    if nx < cfg.support_window_cells + 2 or ny < cfg.support_window_cells + 2:
        return []

    # Robust cell median, vectorised via lexicographic sort (cell, elevation).
    flat = ij[:, 1] * nx + ij[:, 0]
    order = np.lexsort((pts[:, 2], flat))
    unique, starts, counts = np.unique(flat[order], return_index=True, return_counts=True)
    lo = order[starts + (counts - 1) // 2]
    hi = order[starts + counts // 2]
    z_median = 0.5 * (pts[lo, 2] + pts[hi, 2])
    observed_xyz = np.full((ny * nx, 3), np.nan, dtype=np.float64)
    observed_xyz[unique] = pts[lo]
    elevation = np.full(ny * nx, np.nan, dtype=np.float64)
    elevation[unique] = z_median
    elevation = elevation.reshape((ny, nx))
    observed_xyz = observed_xyz.reshape((ny, nx, 3))
    valid = np.isfinite(elevation)
    # Suppress cells neighbouring holes rather than inventing a filled terrain.
    local_support = uniform_filter(valid.astype(float), size=cfg.support_window_cells,
                                   mode="constant", cval=0.0)
    supported = valid & (local_support >= cfg.min_support_fraction)
    if not np.any(supported):
        return []
    weights = gaussian_filter(valid.astype(float), cfg.smoothing_sigma_cells, mode="constant")
    weighted = gaussian_filter(np.where(valid, elevation, 0.0), cfg.smoothing_sigma_cells,
                               mode="constant")
    smooth = weighted / np.maximum(weights, 1e-12)
    dy, dx = np.gradient(smooth, cfg.cell_size_m)
    dyy, dyx = np.gradient(dy, cfg.cell_size_m)
    dxy, dxx = np.gradient(dx, cfg.cell_size_m)
    slope2 = dx * dx + dy * dy
    curvature = (dx * dx * dxx + dx * dy * (dxy + dyx) + dy * dy * dyy) / np.maximum(slope2, 1e-12)
    slope = np.sqrt(slope2)
    # Non-maximum suppression in the gradient (across-breakline) direction.
    yy, xx = np.indices(elevation.shape, dtype=float)
    nx_unit = dx / np.maximum(slope, 1e-12)
    ny_unit = dy / np.maximum(slope, 1e-12)
    plus = np.asarray((yy + ny_unit, xx + nx_unit))
    minus = np.asarray((yy - ny_unit, xx - nx_unit))
    result: list[SeedEvidence] = []
    for kind, response in (("CREST", -curvature), ("TOE", curvature)):
        prev = map_coordinates(response, minus, order=1, mode="nearest")
        next_ = map_coordinates(response, plus, order=1, mode="nearest")
        accepted = (supported & (slope >= cfg.min_slope)
                    & (response >= cfg.min_abs_profile_curvature_m_inv)
                    & (response >= prev) & (response > next_)
                    & np.isfinite(response))
        rows, cols = np.where(accepted)
        if not len(rows):
            continue
        ranking = np.argsort(-response[rows, cols], kind="stable")
        chosen_xy: list[np.ndarray] = []
        for k in ranking:
            iy, ix = int(rows[k]), int(cols[k])
            actual = observed_xyz[iy, ix]
            if chosen_xy and any(np.linalg.norm(actual[:2] - other) < cfg.min_separation_m
                                 for other in chosen_xy):
                continue
            tangent = np.array((-dy[iy, ix], dx[iy, ix]), dtype=float)
            strength = float(response[iy, ix])
            relative_quality = float(np.clip(strength / (strength + cfg.min_abs_profile_curvature_m_inv), 0, 1)
                                     * local_support[iy, ix])
            result.append(SeedEvidence(kind, actual, tangent, relative_quality, "curvature"))
            chosen_xy.append(actual[:2].copy())
            if len(chosen_xy) >= cfg.max_seeds_per_kind:
                break
    return result
