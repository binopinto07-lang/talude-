from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np


class SourceType(StrEnum):
    P1_PHOTOGRAMMETRY = "P1_PHOTOGRAMMETRY"
    L3_LIDAR = "L3_LIDAR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SourceInspection:
    source_type: SourceType
    confidence: float
    evidence: tuple[str, ...]
    point_format_id: int
    generating_software: str
    system_identifier: str
    has_rgb: bool
    has_gps_time: bool
    has_intensity: bool
    has_scan_angle: bool
    has_returns: bool
    max_return_number: int
    max_number_of_returns: int
    multi_return_fraction: float
    last_return_fraction: float
    only_return_fraction: float
    sample_count: int


@dataclass(slots=True)
class RichPointChunk:
    xyz: np.ndarray
    classification: np.ndarray | None
    return_number: np.ndarray | None
    number_of_returns: np.ndarray | None
    intensity: np.ndarray | None
    scan_angle: np.ndarray | None
    gps_time: np.ndarray | None
    point_source_id: np.ndarray | None
    red: np.ndarray | None
    green: np.ndarray | None
    blue: np.ndarray | None
    synthetic: np.ndarray | None
    key_point: np.ndarray | None
    withheld: np.ndarray | None

    @property
    def point_count(self) -> int:
        return int(self.xyz.shape[0])
