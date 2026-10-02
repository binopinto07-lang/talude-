"""Multi-resolution evidence collection for Ground-observed breaklines.

Experimental; this module cannot publish geometry or call the production AUTO.
Different cell sizes capture narrow and gentle slope transitions. Their output
is merged by kind, position and orientation; missing Ground remains missing.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .breakline_seed import SeedEvidence, build_breakline_seeds
from .curvature_seed import CurvatureSeedConfig, curvature_seed_candidates


@dataclass(frozen=True, slots=True)
class MultiScaleConfig:
    cell_sizes_m: tuple[float, ...] = (0.15, 0.25, 0.40)
    base: CurvatureSeedConfig = CurvatureSeedConfig(
        min_separation_m=0.60, max_seeds_per_kind=256
    )
    dedupe_radius_m: float = 0.35
    dedupe_along_m: float = 0.55
    dedupe_across_m: float = 0.35
    parallel_cosine: float = 0.80

    def __post_init__(self) -> None:
        if not self.cell_sizes_m or any(not np.isfinite(s) or s <= 0 for s in self.cell_sizes_m):
            raise ValueError("As escalas devem ser positivas e finitas.")
        if (not np.isfinite(self.dedupe_along_m) or self.dedupe_along_m < 0
            or not np.isfinite(self.dedupe_across_m) or self.dedupe_across_m < 0):
            raise ValueError("Limites de deduplicação anisotrópica inválidos.")
        if not np.isfinite(self.dedupe_radius_m) or self.dedupe_radius_m < 0:
            raise ValueError("Raio de deduplicação inválido.")
        if not 0.0 <= self.parallel_cosine <= 1.0:
            raise ValueError("Alinhamento de deduplicação inválido.")


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    observations_by_scale: tuple[tuple[float, int, int], ...]
    merged_crest: int
    merged_toe: int
    failed_scales: tuple[tuple[float, str], ...] = ()


def discover_multiscale_ground(
    ground_xyz: np.ndarray,
    *,
    config: MultiScaleConfig | None = None,
) -> tuple[list[SeedEvidence], DiscoveryReport]:
    """Return 3D candidate observations and transparent per-scale diagnostics.

    The caller must pass a bounded tile already filtered to observed Ground.
    A failed scale is recorded instead of silently claiming full coverage.
    """
    cfg = config or MultiScaleConfig()
    points = np.asarray(ground_xyz, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] < 3:
        raise ValueError("Ground deve ser Nx3 ou NxM, M >= 3.")
    all_observations: list[SeedEvidence] = []
    observations_by_scale: list[tuple[float, int, int]] = []
    failures: list[tuple[float, str]] = []
    for cell in cfg.cell_sizes_m:
        try:
            observations = curvature_seed_candidates(
                points,
                config=replace(cfg.base, cell_size_m=float(cell)),
            )
        except ValueError as exc:
            # Explicitly report oversized tiles: never auto-subsample the extent.
            failures.append((float(cell), str(exc)))
            continue
        crest = sum(item.kind == "CREST" for item in observations)
        toe = sum(item.kind == "TOE" for item in observations)
        observations_by_scale.append((float(cell), crest, toe))
        all_observations.extend(observations)

    # Prefer strongest observed response; each scale's confidence is relative.
    # Dedupe by type + XY + direction; never interpolate XYZ between scales.
    ranked = sorted(all_observations, key=lambda item: item.confidence, reverse=True)
    # Different rasters may observe the SAME edge at XY offset by half a cell
    # and Y offset by half a station. Euclidean dedupe alone turns these into
    # duplicate, nearly parallel traces. Compare tangent/normal components.
    consolidated: list[SeedEvidence] = []
    for candidate in ranked:
        duplicate = False
        for previous in consolidated:
            if candidate.kind != previous.kind:
                continue
            tangent = previous.direction_xy
            other = candidate.direction_xy
            if tangent is None or other is None:
                duplicate = (np.linalg.norm(candidate.xyz[:2] - previous.xyz[:2])
                             <= cfg.dedupe_radius_m)
            elif abs(float(np.dot(tangent, other))) >= cfg.parallel_cosine:
                delta = candidate.xyz[:2] - previous.xyz[:2]
                along = abs(float(np.dot(delta, tangent)))
                across = abs(float(np.linalg.det(np.stack((tangent, delta)))))
                duplicate = (along <= cfg.dedupe_along_m
                             and across <= cfg.dedupe_across_m
                             and abs(float(candidate.xyz[2] - previous.xyz[2])) <= 0.4)
            if duplicate:
                break
        if not duplicate:
            consolidated.append(candidate)
    merged = build_breakline_seeds(
        independent_evidence=consolidated,
        dedupe_radius_m=cfg.dedupe_radius_m,
        parallel_cosine=cfg.parallel_cosine,
    )
    evidence = [
        SeedEvidence(item.kind, item.xyz, item.direction_xy, item.confidence,
                     item.source, item.face_hint)
        for item in merged
    ]
    return evidence, DiscoveryReport(
        tuple(observations_by_scale),
        sum(item.kind == "CREST" for item in evidence),
        sum(item.kind == "TOE" for item in evidence),
        tuple(failures),
    )
