from __future__ import annotations

import logging
from dataclasses import dataclass
from math import sqrt

import numpy as np

from .crs import WORKING_CRS
from .model import CloudModel
from ..terrain.schema import SourceInspection
from ..terrain.source_inspector import inspect_source


LOGGER = logging.getLogger("las_cafiisica.cloud.statistics")


@dataclass(frozen=True, slots=True)
class CloudStatistics:
    file_name: str
    las_version: str
    point_format: int
    point_count: int
    min_xyz: tuple[float, float, float]
    max_xyz: tuple[float, float, float]
    height_range: float
    crs: str
    dimensions: tuple[str, ...]
    original_class_histogram: dict[int, int]
    approximate_xy_density: float | None
    approximate_point_spacing: float | None
    source_inspection: SourceInspection

    def as_display_lines(self) -> list[str]:
        density = (
            "n/a"
            if self.approximate_xy_density is None
            else f"{self.approximate_xy_density:.3f} pts/m²"
        )
        spacing = (
            "n/a"
            if self.approximate_point_spacing is None
            else f"{self.approximate_point_spacing:.3f} m"
        )
        histogram = (
            ", ".join(
                f"{key}: {value}"
                for key, value in sorted(
                    self.original_class_histogram.items()
                )
            )
            or "empty"
        )
        source = self.source_inspection
        evidence = (
            "; ".join(source.evidence)
            if source.evidence
            else "none"
        )
        return [
            f"File: {self.file_name}",
            f"LAS version: {self.las_version}",
            f"Point format: {self.point_format}",
            f"Point count: {self.point_count:,}",
            f"Min XYZ: {self.min_xyz}",
            f"Max XYZ: {self.max_xyz}",
            f"Height range: {self.height_range:.3f} m",
            f"CRS: {self.crs}",
            f"Approx. XY density: {density}",
            f"Approx. point spacing: {spacing}",
            f"Original classification: {histogram}",
            f"Dimensions: {', '.join(self.dimensions)}",
            (
                "Source detected: "
                f"{source.source_type.value} "
                f"({source.confidence * 100:.1f}%)"
            ),
            f"Source evidence: {evidence}",
            (
                "Returns sample: "
                f"max return={source.max_return_number}, "
                f"max returns={source.max_number_of_returns}, "
                f"multi={source.multi_return_fraction:.3%}, "
                f"last={source.last_return_fraction:.3%}, "
                f"only={source.only_return_fraction:.3%}"
            ),
            f"Generating software: {source.generating_software or 'n/a'}",
        ]


def _header_bounds(
    cloud: CloudModel,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    if not cloud.point_count:
        empty = (0.0, 0.0, 0.0)
        return empty, empty

    minimum = tuple(float(v) for v in cloud.las.header.mins)
    maximum = tuple(float(v) for v in cloud.las.header.maxs)
    return minimum, maximum


def _classification_histogram(cloud: CloudModel) -> dict[int, int]:
    if cloud.original_class.size == 0:
        return {}

    counts = np.bincount(cloud.original_class, minlength=256)
    return {
        int(class_id): int(count)
        for class_id, count in enumerate(counts)
        if count
    }


def calculate_statistics(cloud: CloudModel) -> CloudStatistics:
    min_xyz, max_xyz = _header_bounds(cloud)
    height_range = float(max_xyz[2] - min_xyz[2])
    area = float(
        (max_xyz[0] - min_xyz[0])
        * (max_xyz[1] - min_xyz[1])
    )

    density = (
        cloud.point_count / area
        if cloud.point_count and area > 0
        else None
    )
    spacing = sqrt(1.0 / density) if density and density > 0 else None

    source_inspection = inspect_source(cloud)

    result = CloudStatistics(
        file_name=cloud.path.name,
        las_version=str(cloud.las.header.version),
        point_format=int(cloud.las.header.point_format.id),
        point_count=cloud.point_count,
        min_xyz=min_xyz,
        max_xyz=max_xyz,
        height_range=height_range,
        crs=WORKING_CRS,
        dimensions=tuple(cloud.las.point_format.dimension_names),
        original_class_histogram=_classification_histogram(cloud),
        approximate_xy_density=density,
        approximate_point_spacing=spacing,
        source_inspection=source_inspection,
    )
    LOGGER.info("BOUNDS=%s -> %s", result.min_xyz, result.max_xyz)
    LOGGER.info("WORKING_CRS=%s", result.crs)
    LOGGER.info(
        "AVAILABLE_DIMENSIONS=%s",
        ",".join(result.dimensions),
    )
    LOGGER.info(
        "CLASS_HISTOGRAM_ORIGINAL=%s",
        result.original_class_histogram,
    )
    LOGGER.info(
        "SOURCE_TYPE=%s SOURCE_CONFIDENCE=%.3f",
        result.source_inspection.source_type.value,
        result.source_inspection.confidence,
    )
    LOGGER.info("POINT_SPACING=%s", result.approximate_point_spacing)
    LOGGER.info("DENSITY=%s", result.approximate_xy_density)
    return result
