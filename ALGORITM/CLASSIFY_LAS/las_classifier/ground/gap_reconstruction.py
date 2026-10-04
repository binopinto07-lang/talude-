from __future__ import annotations

import numpy as np

from .terrain_tin import TerrainTIN


DEFAULT_MAX_SYNTHETIC_POINTS = 100_000_000
_GOLDEN_RATIO_CONJUGATE = 0.6180339887498949


def _raw_fill_counts(areas: np.ndarray, spacing: float) -> np.ndarray:
    spacing = max(float(spacing), 0.02)
    values = np.asarray(areas, dtype=np.float64)
    counts = np.zeros(values.shape, dtype=np.int64)
    valid = np.isfinite(values) & (values > 0.0)
    counts[valid] = np.ceil(
        values[valid] / (spacing * spacing)
    ).astype(np.int64)
    return counts


def _cap_fill_counts(counts: np.ndarray, max_points: int) -> np.ndarray:
    counts = np.asarray(counts, dtype=np.int64)
    total = int(np.sum(counts, dtype=np.int64))
    limit = max(0, int(max_points))
    if total <= limit:
        return counts.copy()
    if limit == 0 or total == 0:
        return np.zeros_like(counts)

    scaled = counts.astype(np.float64) * (limit / total)
    capped = np.floor(scaled).astype(np.int64)
    remainder = limit - int(np.sum(capped, dtype=np.int64))

    if remainder > 0:
        fractions = scaled - capped
        eligible = np.flatnonzero(counts > 0)
        order = eligible[
            np.argsort(-fractions[eligible], kind="mergesort")
        ]
        capped[order[:remainder]] += 1

    return capped


def _fill_counts(
    tin: TerrainTIN,
    supported_mask: np.ndarray,
    spacing: float,
    max_points: int,
) -> np.ndarray:
    areas = np.asarray(tin.areas[supported_mask], dtype=np.float64)
    raw = _raw_fill_counts(areas, spacing)
    return _cap_fill_counts(raw, max_points)


def estimate_fill_count(
    tin: TerrainTIN,
    supported_mask: np.ndarray,
    spacing: float,
    *,
    max_points: int = DEFAULT_MAX_SYNTHETIC_POINTS,
) -> int:
    if spacing <= 0:
        return 0
    counts = _fill_counts(
        tin,
        supported_mask,
        spacing,
        max_points,
    )
    return int(np.sum(counts, dtype=np.int64))


def _sample_triangle(triangle: np.ndarray, count: int) -> np.ndarray:
    if count <= 0:
        return np.empty((0, 3), dtype=np.float64)

    sequence = np.arange(count, dtype=np.float64)
    u = (sequence + 0.5) / count
    v = np.mod(
        (sequence + 0.5) * _GOLDEN_RATIO_CONJUGATE,
        1.0,
    )

    root_u = np.sqrt(u)
    a = 1.0 - root_u
    b = root_u * (1.0 - v)
    c = root_u * v

    return (
        a[:, None] * triangle[0]
        + b[:, None] * triangle[1]
        + c[:, None] * triangle[2]
    )


def iter_triangle_fill(
    tin: TerrainTIN,
    supported_mask: np.ndarray,
    spacing: float,
    *,
    max_points: int = DEFAULT_MAX_SYNTHETIC_POINTS,
    chunk_points: int = 500_000,
):
    triangles = np.asarray(
        tin.vertices[tin.simplices[supported_mask]],
        dtype=np.float64,
    )
    if triangles.size == 0:
        return

    counts = _fill_counts(
        tin,
        supported_mask,
        spacing,
        max_points,
    )

    buffer: list[np.ndarray] = []
    buffered = 0

    for triangle, count in zip(triangles, counts, strict=True):
        count = int(count)
        if count <= 0:
            continue

        points = _sample_triangle(triangle, count)
        buffer.append(points)
        buffered += count

        if buffered >= chunk_points:
            merged = np.concatenate(buffer, axis=0)
            yield (
                merged[:, 0],
                merged[:, 1],
                merged[:, 2],
            )
            buffer = []
            buffered = 0

    if buffer:
        merged = np.concatenate(buffer, axis=0)
        yield (
            merged[:, 0],
            merged[:, 1],
            merged[:, 2],
        )
