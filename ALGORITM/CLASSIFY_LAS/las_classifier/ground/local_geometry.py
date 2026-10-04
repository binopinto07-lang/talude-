from __future__ import annotations

import numpy as np


NORMAL_DIMENSIONS = ("normal x", "normal y", "normal z")


def _decode_normal_component(values) -> np.ndarray:
    source = np.asarray(values)
    out = source.astype(np.float64, copy=False)

    if source.dtype.kind == "u":
        limit = float(np.iinfo(source.dtype).max)
        if limit > 0:
            out = out / limit * 2.0 - 1.0
    elif source.dtype.kind == "i":
        info = np.iinfo(source.dtype)
        limit = float(max(abs(info.min), abs(info.max)))
        if limit > 0:
            out = out / limit

    return np.asarray(out, dtype=np.float64)


def point_normals(points) -> np.ndarray | None:
    names = set(points.point_format.dimension_names)
    if not set(NORMAL_DIMENSIONS).issubset(names):
        return None

    normals = np.column_stack(
        [_decode_normal_component(points[name]) for name in NORMAL_DIMENSIONS]
    )
    if normals.size == 0:
        return normals

    if np.nanmin(normals) >= 0.0 and np.nanmax(normals) <= 1.0:
        normals = normals * 2.0 - 1.0

    lengths = np.linalg.norm(normals, axis=1)
    valid = np.isfinite(lengths) & (lengths > 0.15)
    normals[valid] /= lengths[valid, None]
    normals[~valid] = np.nan
    return normals


def normal_alignment(
    point_normal: np.ndarray | None,
    terrain_normal: np.ndarray,
) -> np.ndarray | None:
    if point_normal is None:
        return None
    valid = np.all(np.isfinite(point_normal), axis=1)
    alignment = np.zeros(point_normal.shape[0], dtype=np.float64)
    alignment[valid] = np.abs(
        np.einsum(
            "ij,ij->i",
            point_normal[valid],
            terrain_normal[valid],
        )
    )
    return alignment


def triangle_planarity(vertices: np.ndarray) -> float:
    if vertices.shape[0] < 3:
        return 0.0
    centered = vertices - np.mean(vertices, axis=0)
    covariance = centered.T @ centered / max(1, vertices.shape[0] - 1)
    values = np.linalg.eigvalsh(covariance)
    total = float(np.sum(values))
    if total <= 1e-12:
        return 0.0
    return float((values[1] - values[0]) / total)
