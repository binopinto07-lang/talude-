from __future__ import annotations

import logging
from math import ceil, sqrt

import numpy as np
from scipy.spatial import cKDTree

from ..cloud.model import CloudModel
from .types import GroundAnalysis


LOGGER = logging.getLogger("las_cafiisica.ground.density")


def deterministic_sample_indices(
    point_count: int,
    target: int,
) -> tuple[np.ndarray, int]:
    if point_count <= 0:
        return np.empty(0, dtype=np.int64), 1
    stride = max(1, int(ceil(point_count / max(1, target))))
    return np.arange(0, point_count, stride, dtype=np.int64), stride


def scaled_xyz(
    cloud: CloudModel,
    indices: np.ndarray,
) -> np.ndarray:
    scales = cloud.las.header.scales
    offsets = cloud.las.header.offsets
    x = np.asarray(cloud.las.X[indices], dtype=np.float64) * scales[0] + offsets[0]
    y = np.asarray(cloud.las.Y[indices], dtype=np.float64) * scales[1] + offsets[1]
    z = np.asarray(cloud.las.Z[indices], dtype=np.float64) * scales[2] + offsets[2]
    return np.column_stack((x, y, z))


def estimate_sample_spacing(
    xy: np.ndarray,
    max_probe: int = 160_000,
) -> float:
    if xy.shape[0] < 2:
        return 1.0
    if xy.shape[0] > max_probe:
        stride = int(ceil(xy.shape[0] / max_probe))
        probe = xy[::stride]
    else:
        probe = xy
    tree = cKDTree(probe)
    distances, _ = tree.query(probe, k=2, workers=-1)
    nn = distances[:, 1]
    nn = nn[np.isfinite(nn) & (nn > 0)]
    return float(np.median(nn)) if nn.size else 1.0


def spatial_low_sample(
    cloud: CloudModel,
    target: int,
    *,
    chunk_size: int = 750_000,
) -> np.ndarray:
    """Order-independent, bounded low-envelope sample from the full cloud.

    One lexicographically stable measured point is retained per spatial cell.
    Point index/order is never used as terrain representation.
    """
    target = max(10_000, int(target))
    mins = np.asarray(cloud.las.header.mins[:2], dtype=np.float64)
    maxs = np.asarray(cloud.las.header.maxs[:2], dtype=np.float64)
    extent = np.maximum(maxs - mins, 1e-9)
    area = max(float(extent[0] * extent[1]), 1e-9)
    cell = max(np.sqrt(area / target), 1e-4)
    nx = max(1, int(np.ceil(extent[0] / cell)) + 1)
    ny = max(1, int(np.ceil(extent[1] / cell)) + 1)
    cells = int(nx) * int(ny)
    if cells > target * 2:
        cell *= np.sqrt(cells / (target * 2))
        nx = max(1, int(np.ceil(extent[0] / cell)) + 1)
        ny = max(1, int(np.ceil(extent[1] / cell)) + 1)
        cells = int(nx) * int(ny)

    best_x = np.full(cells, np.inf, dtype=np.float64)
    best_y = np.full(cells, np.inf, dtype=np.float64)
    best_z = np.full(cells, np.inf, dtype=np.float64)
    scales = cloud.las.header.scales
    offsets = cloud.las.header.offsets
    total = cloud.point_count

    for begin in range(0, total, max(1, int(chunk_size))):
        stop = min(begin + max(1, int(chunk_size)), total)
        points = cloud.las.points[begin:stop]
        x = np.asarray(points.X, dtype=np.float64) * scales[0] + offsets[0]
        y = np.asarray(points.Y, dtype=np.float64) * scales[1] + offsets[1]
        z = np.asarray(points.Z, dtype=np.float64) * scales[2] + offsets[2]
        valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        if not np.any(valid):
            continue
        x, y, z = x[valid], y[valid], z[valid]
        ix = np.floor((x - mins[0]) / cell).astype(np.int64)
        iy = np.floor((y - mins[1]) / cell).astype(np.int64)
        np.clip(ix, 0, nx - 1, out=ix)
        np.clip(iy, 0, ny - 1, out=iy)
        keys = ix + nx * iy

        order = np.lexsort((y, x, z, keys))
        ordered_keys = keys[order]
        first = np.r_[True, ordered_keys[1:] != ordered_keys[:-1]]
        pick = order[first]
        k = keys[pick]
        px, py, pz = x[pick], y[pick], z[pick]

        current_z = best_z[k]
        current_x = best_x[k]
        current_y = best_y[k]
        replace = (
            (pz < current_z)
            | ((pz == current_z) & (px < current_x))
            | ((pz == current_z) & (px == current_x) & (py < current_y))
        )
        if np.any(replace):
            kk = k[replace]
            best_x[kk] = px[replace]
            best_y[kk] = py[replace]
            best_z[kk] = pz[replace]

    occupied = np.isfinite(best_z)
    return np.column_stack((best_x[occupied], best_y[occupied], best_z[occupied]))


def analyze_cloud(
    cloud: CloudModel,
    sample_target: int = 2_500_000,
) -> tuple[GroundAnalysis, np.ndarray]:
    """Analyse density and return an order-independent spatial terrain sample."""
    mins = cloud.las.header.mins
    maxs = cloud.las.header.maxs
    area = max(
        float((maxs[0] - mins[0]) * (maxs[1] - mins[1])),
        1e-9,
    )
    density = float(cloud.point_count / area)
    spacing = sqrt(1.0 / density) if density > 0 else 1.0
    spacing = max(0.005, float(spacing))

    xyz = spatial_low_sample(cloud, sample_target)
    analysis = GroundAnalysis(
        point_count=cloud.point_count,
        median_spacing=spacing,
        xy_density=density,
        z_range=float(maxs[2] - mins[2]),
        sample_stride=0,
        sample_count=int(xyz.shape[0]),
    )
    LOGGER.info(
        "GROUND_ANALYSIS points=%d spatial_sample=%d density=%.6f spacing=%.6f",
        cloud.point_count,
        xyz.shape[0],
        density,
        spacing,
    )
    return analysis, xyz
