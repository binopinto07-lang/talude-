from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from ..cloud.model import CloudModel
from .schema import RichPointChunk


def _dimension(points, *names: str) -> np.ndarray | None:
    available = set(points.point_format.dimension_names)
    for name in names:
        if name in available:
            return np.asarray(points[name])
    return None


def rich_chunk_from_points(
    points,
    scales,
    offsets,
) -> RichPointChunk:
    x = (
        np.asarray(points.X, dtype=np.float64)
        * float(scales[0])
        + float(offsets[0])
    )
    y = (
        np.asarray(points.Y, dtype=np.float64)
        * float(scales[1])
        + float(offsets[1])
    )
    z = (
        np.asarray(points.Z, dtype=np.float64)
        * float(scales[2])
        + float(offsets[2])
    )

    return RichPointChunk(
        xyz=np.column_stack((x, y, z)),
        classification=_dimension(points, "classification"),
        return_number=_dimension(points, "return_number"),
        number_of_returns=_dimension(points, "number_of_returns"),
        intensity=_dimension(points, "intensity"),
        scan_angle=_dimension(
            points,
            "scan_angle",
            "scan_angle_rank",
        ),
        gps_time=_dimension(points, "gps_time"),
        point_source_id=_dimension(points, "point_source_id"),
        red=_dimension(points, "red"),
        green=_dimension(points, "green"),
        blue=_dimension(points, "blue"),
        synthetic=_dimension(points, "synthetic"),
        key_point=_dimension(points, "key_point"),
        withheld=_dimension(points, "withheld"),
    )


def iter_rich_chunks(
    cloud: CloudModel,
    chunk_size: int = 2_000_000,
) -> Iterator[RichPointChunk]:
    scales = cloud.las.header.scales
    offsets = cloud.las.header.offsets
    total = cloud.point_count

    for start in range(0, total, chunk_size):
        stop = min(start + chunk_size, total)
        yield rich_chunk_from_points(
            cloud.las.points[start:stop],
            scales,
            offsets,
        )
