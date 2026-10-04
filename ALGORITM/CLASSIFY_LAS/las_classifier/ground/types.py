from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GroundAnalysis:
    point_count: int
    median_spacing: float
    xy_density: float
    z_range: float
    sample_stride: int
    sample_count: int


@dataclass(frozen=True, slots=True)
class GroundEngineParams:
    quality: str = "balanced"
    tile_size: float = 150.0
    tile_buffer: float = 20.0
    sample_target: int = 2_500_000
    seed_resolution: float = 0.0
    candidate_spacing: float = 0.0
    max_iteration_angle_deg: float = 13.0
    max_iteration_distance: float = 0.22
    max_iterations: int = 8
    confidence_threshold: float = 0.62
    max_triangle_edge: float = 0.0
    gap_max_size: float = 8.0
    synthetic_spacing: float = 0.0
    chunk_size: int = 2_000_000

    @classmethod
    def preset(cls, name: str) -> "GroundEngineParams":
        key = name.strip().lower()
        if key == "fast":
            return cls(
                quality="fast",
                sample_target=1_000_000,
                max_iterations=5,
                confidence_threshold=0.66,
            )
        if key == "high":
            return cls(
                quality="high",
                sample_target=4_000_000,
                max_iterations=10,
                max_iteration_distance=0.18,
                confidence_threshold=0.64,
            )
        if key == "extreme":
            return cls(
                quality="extreme",
                sample_target=7_000_000,
                max_iterations=12,
                max_iteration_distance=0.16,
                confidence_threshold=0.66,
            )
        return cls()
