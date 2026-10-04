from __future__ import annotations

import logging
from math import ceil
from typing import TYPE_CHECKING

import numpy as np

from .schema import SourceInspection, SourceType

if TYPE_CHECKING:
    from ..cloud.model import CloudModel


LOGGER = logging.getLogger(
    "las_cafiisica.terrain.source_inspector"
)


def _header_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


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


def inspect_source(
    cloud: CloudModel,
    *,
    override: SourceType | str | None = None,
    sample_target: int = 250_000,
) -> SourceInspection:
    header = cloud.las.header
    points = cloud.las.points
    names = set(points.point_format.dimension_names)

    producer = _header_text(
        getattr(header, "generating_software", "")
    )
    system = _header_text(
        getattr(header, "system_identifier", "")
    )
    producer_lower = producer.lower()
    system_lower = system.lower()

    indices = _sample_indices(
        cloud.point_count,
        sample_target,
    )
    sample_count = int(indices.size)

    has_returns = (
        "return_number" in names
        and "number_of_returns" in names
    )
    max_return = 0
    max_returns = 0
    multi_fraction = 0.0
    last_fraction = 0.0
    only_fraction = 0.0

    if has_returns and sample_count:
        rn = np.asarray(
            points.return_number[indices],
            dtype=np.int16,
        )
        nr = np.asarray(
            points.number_of_returns[indices],
            dtype=np.int16,
        )
        max_return = int(np.max(rn, initial=0))
        max_returns = int(np.max(nr, initial=0))
        valid = nr > 0
        if np.any(valid):
            multi_fraction = float(
                np.mean(nr[valid] > 1)
            )
            last_fraction = float(
                np.mean(rn[valid] == nr[valid])
            )
            only_fraction = float(
                np.mean(
                    (rn[valid] == 1)
                    & (nr[valid] == 1)
                )
            )

    has_rgb = {
        "red",
        "green",
        "blue",
    }.issubset(names)
    has_gps_time = "gps_time" in names
    has_intensity = "intensity" in names
    has_scan_angle = (
        "scan_angle" in names
        or "scan_angle_rank" in names
    )

    evidence: list[str] = []
    source_type = SourceType.UNKNOWN
    confidence = 0.35

    if override is not None:
        source_type = SourceType(str(override))
        confidence = 1.0
        evidence.append("manual override")
    elif (
        "metashape" in producer_lower
        and multi_fraction < 0.001
    ):
        source_type = SourceType.P1_PHOTOGRAMMETRY
        confidence = 0.98
        evidence.extend(
            (
                "generating software contains Metashape",
                "multi-return fraction < 0.1%",
            )
        )
    elif (
        (
            "dji terra" in producer_lower
            or "dji terra" in system_lower
        )
        and max_returns > 1
        and multi_fraction >= 0.01
    ):
        source_type = SourceType.L3_LIDAR
        confidence = 0.98
        evidence.extend(
            (
                "DJI Terra producer/system identifier",
                "measured multi-return population",
            )
        )
    elif (
        max_returns > 1
        and multi_fraction >= 0.01
        and has_intensity
        and has_scan_angle
    ):
        source_type = SourceType.L3_LIDAR
        confidence = 0.84
        evidence.extend(
            (
                "multi-return measurements present",
                "LiDAR-like intensity/scan-angle fields",
            )
        )
    elif (
        multi_fraction < 0.001
        and only_fraction > 0.995
        and has_rgb
    ):
        source_type = SourceType.P1_PHOTOGRAMMETRY
        confidence = 0.68
        evidence.extend(
            (
                "almost exclusively 1/1 returns",
                "RGB point cloud",
            )
        )
    else:
        evidence.append(
            "sensor type not proven by sampled LAS values"
        )

    result = SourceInspection(
        source_type=source_type,
        confidence=float(confidence),
        evidence=tuple(evidence),
        point_format_id=int(header.point_format.id),
        generating_software=producer,
        system_identifier=system,
        has_rgb=has_rgb,
        has_gps_time=has_gps_time,
        has_intensity=has_intensity,
        has_scan_angle=has_scan_angle,
        has_returns=has_returns,
        max_return_number=max_return,
        max_number_of_returns=max_returns,
        multi_return_fraction=multi_fraction,
        last_return_fraction=last_fraction,
        only_return_fraction=only_fraction,
        sample_count=sample_count,
    )

    LOGGER.info(
        "SOURCE_INSPECTION type=%s confidence=%.3f "
        "sample=%d max_return=%d max_returns=%d "
        "multi_fraction=%.6f only_fraction=%.6f producer=%r",
        result.source_type.value,
        result.confidence,
        result.sample_count,
        result.max_return_number,
        result.max_number_of_returns,
        result.multi_return_fraction,
        result.only_return_fraction,
        result.generating_software,
    )
    return result
