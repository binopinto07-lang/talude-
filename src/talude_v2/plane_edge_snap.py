from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.spatial import cKDTree


def _mean_distance_k12(points_xyz: np.ndarray, tree_xy: cKDTree) -> float:
    points = np.asarray(points_xyz, dtype=np.float64)
    if len(points) < 3:
        return 0.0
    count = min(100, len(points))
    sample_ids = np.linspace(0, len(points) - 1, count, dtype=np.int64)
    k = min(13, len(points))
    distances, _ = tree_xy.query(points[sample_ids, :2], k=k)
    distances = np.asarray(distances, dtype=np.float64)
    if distances.ndim == 1:
        valid = distances[np.isfinite(distances) & (distances > 0.0)]
    else:
        valid = distances[:, 1:].reshape(-1)
        valid = valid[np.isfinite(valid) & (valid > 0.0)]
    return float(np.mean(valid)) if len(valid) else 0.0


def _fit_plane_robust(points_xyz: np.ndarray) -> tuple[np.ndarray, float, float]:
    points = np.asarray(points_xyz, dtype=np.float64)
    if len(points) < 6:
        raise ValueError("Poucos pontos para ajustar plano local.")
    keep = np.ones(len(points), dtype=bool)
    normal = None
    offset = 0.0
    for _ in range(3):
        current = points[keep]
        if len(current) < 6:
            break
        center = np.mean(current, axis=0)
        _, _, vh = np.linalg.svd(current - center, full_matrices=False)
        normal = np.asarray(vh[-1], dtype=np.float64)
        normal /= max(float(np.linalg.norm(normal)), 1e-12)
        if normal[2] < 0.0:
            normal *= -1.0
        offset = -float(np.dot(normal, center))
        residuals = np.abs(points @ normal + offset)
        median = float(np.median(residuals[keep]))
        mad = float(np.median(np.abs(residuals[keep] - median)))
        sigma = max(1.4826 * mad, 0.005)
        updated = residuals <= max(0.03, 3.0 * sigma)
        if np.array_equal(updated, keep):
            break
        keep = updated
    if normal is None or int(np.count_nonzero(keep)) < 6:
        raise ValueError("Plano local instável.")
    residual = points[keep] @ normal + offset
    rmse = float(np.sqrt(np.mean(residual * residual)))
    return normal, float(offset), rmse


def _intersect_three_planes(
    first: tuple[np.ndarray, float, float],
    second: tuple[np.ndarray, float, float],
    third: tuple[np.ndarray, float],
) -> np.ndarray:
    matrix = np.vstack((first[0], second[0], third[0]))
    rhs = -np.asarray((first[1], second[1], third[1]), dtype=np.float64)
    if abs(float(np.linalg.det(matrix))) < 1e-6:
        raise ValueError("Planos locais quase paralelos.")
    return np.linalg.solve(matrix, rhs)


def _frames(crest_xyz: np.ndarray, toe_xyz: np.ndarray):
    crest = np.asarray(crest_xyz, dtype=np.float64)
    toe = np.asarray(toe_xyz, dtype=np.float64)
    if len(crest) != len(toe) or len(crest) < 2:
        raise ValueError("CRISTA/PÉ precisam do mesmo número de estações.")
    center = 0.5 * (crest + toe)
    frames: list[tuple[np.ndarray, np.ndarray, np.ndarray] | None] = []
    for index in range(len(center)):
        before = center[max(0, index - 1), :2]
        after = center[min(len(center) - 1, index + 1), :2]
        tangent = np.asarray(after - before, dtype=np.float64)
        tangent_norm = float(np.linalg.norm(tangent))
        if tangent_norm <= 1e-9:
            frames.append(None)
            continue
        tangent /= tangent_norm
        across = np.asarray(toe[index, :2] - crest[index, :2], dtype=np.float64)
        across -= float(np.dot(across, tangent)) * tangent
        across_norm = float(np.linalg.norm(across))
        if across_norm <= 1e-9:
            across = np.asarray((-tangent[1], tangent[0]), dtype=np.float64)
        else:
            across /= across_norm
        if float(np.dot(across, toe[index, :2] - crest[index, :2])) < 0.0:
            across *= -1.0
        frames.append((center[index], tangent, across))
    return frames


def snap_edge_pair_to_local_planes(
    raw_points: np.ndarray,
    crest_seed: np.ndarray,
    toe_seed: np.ndarray,
    *,
    min_coverage_ratio: float = 0.55,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Refine section-tracker lines by intersecting three local 3D planes.

    Independent implementation inspired by the architecture found during
    static VRMesh research: mean point distance, radius neighbourhoods,
    local plane detection and edge tracking. No proprietary code is copied.
    """
    points = np.asarray(raw_points, dtype=np.float64)
    points = (
        points[np.all(np.isfinite(points[:, :3]), axis=1)]
        if points.ndim == 2
        else points
    )
    crest = np.asarray(crest_seed, dtype=np.float64)
    toe = np.asarray(toe_seed, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] < 3 or len(points) < 200:
        raise ValueError("Poucos pontos para edge snap local.")
    if len(crest) != len(toe) or len(crest) < 6:
        raise ValueError("Poucas estações CRISTA/PÉ para edge snap local.")

    tree = cKDTree(points[:, :2])
    mean_distance = _mean_distance_k12(points, tree)
    edge_radius = max(0.12, 2.0 * mean_distance)
    along_half = max(0.30, 2.0 * edge_radius)
    side_support = max(0.55, 3.0 * edge_radius)
    frames = _frames(crest, toe)

    snapped_crest: list[np.ndarray] = []
    snapped_toe: list[np.ndarray] = []
    used_indices: list[int] = []
    plane_rmse: list[float] = []
    seed_shifts: list[float] = []
    previous_crest = None
    previous_toe = None
    previous_index = None

    for index, frame in enumerate(frames):
        if frame is None:
            continue
        origin, tangent, across = frame
        width = float(np.linalg.norm(toe[index, :2] - crest[index, :2]))
        query_radius = math.hypot(
            width * 0.5 + side_support * 1.8,
            along_half,
        )
        ids = tree.query_ball_point(origin[:2], r=query_radius)
        if len(ids) < 30:
            continue
        local = points[np.asarray(ids, dtype=np.int64)]
        delta = local[:, :2] - origin[:2]
        along = delta @ tangent
        lateral = delta @ across
        keep = np.abs(along) <= along_half
        local = local[keep]
        lateral = lateral[keep]
        if len(local) < 30:
            continue

        crest_lateral = float(
            np.dot(crest[index, :2] - origin[:2], across)
        )
        toe_lateral = float(
            np.dot(toe[index, :2] - origin[:2], across)
        )
        if toe_lateral < crest_lateral:
            crest_lateral, toe_lateral = toe_lateral, crest_lateral
        width = max(toe_lateral - crest_lateral, 0.50)
        gap = max(edge_radius * 0.45, 0.08)
        upper_mask = (
            (lateral >= crest_lateral - side_support * 1.8)
            & (lateral <= crest_lateral - gap)
        )
        face_mask = (
            (lateral >= crest_lateral + gap)
            & (lateral <= toe_lateral - gap)
        )
        lower_mask = (
            (lateral >= toe_lateral + gap)
            & (lateral <= toe_lateral + side_support * 1.8)
        )
        if min(
            int(np.count_nonzero(upper_mask)),
            int(np.count_nonzero(face_mask)),
            int(np.count_nonzero(lower_mask)),
        ) < 8:
            continue

        try:
            upper_plane = _fit_plane_robust(local[upper_mask])
            face_plane = _fit_plane_robust(local[face_mask])
            lower_plane = _fit_plane_robust(local[lower_mask])
            section_plane = (
                np.asarray(
                    (tangent[0], tangent[1], 0.0),
                    dtype=np.float64,
                ),
                -float(np.dot(tangent, origin[:2])),
            )
            crest_point = _intersect_three_planes(
                upper_plane,
                face_plane,
                section_plane,
            )
            toe_point = _intersect_three_planes(
                face_plane,
                lower_plane,
                section_plane,
            )
        except Exception:
            continue

        gate = max(1.0, 0.32 * width, 3.0 * edge_radius)
        crest_shift = float(
            np.linalg.norm(crest_point[:2] - crest[index, :2])
        )
        toe_shift = float(
            np.linalg.norm(toe_point[:2] - toe[index, :2])
        )
        if crest_shift > gate or toe_shift > gate:
            continue

        if previous_crest is not None and previous_index is not None:
            seed_step = max(
                float(
                    np.linalg.norm(
                        crest[index, :2]
                        - crest[previous_index, :2]
                    )
                ),
                0.20,
            )
            continuity_gate = max(3.0 * seed_step, 2.0)
            if (
                float(
                    np.linalg.norm(
                        crest_point[:2] - previous_crest[:2]
                    )
                )
                > continuity_gate
                or float(
                    np.linalg.norm(
                        toe_point[:2] - previous_toe[:2]
                    )
                )
                > continuity_gate
            ):
                continue

        snapped_crest.append(crest_point)
        snapped_toe.append(toe_point)
        used_indices.append(index)
        plane_rmse.append(
            max(
                upper_plane[2],
                face_plane[2],
                lower_plane[2],
            )
        )
        seed_shifts.extend((crest_shift, toe_shift))
        previous_crest = crest_point
        previous_toe = toe_point
        previous_index = index

    coverage = len(used_indices) / max(len(frames), 1)
    if (
        len(used_indices) < 6
        or coverage < float(min_coverage_ratio)
    ):
        raise ValueError(
            "Edge snap local insuficiente: "
            f"{len(used_indices)}/{len(frames)} estações "
            f"({coverage:.0%})."
        )

    crest_out = np.asarray(snapped_crest, dtype=np.float64)
    toe_out = np.asarray(snapped_toe, dtype=np.float64)
    shift_array = np.asarray(seed_shifts, dtype=np.float64)
    return crest_out, toe_out, {
        "source": "ADAPTIVE_LOCAL_PLANE_SNAP",
        "applied": True,
        "stations_total": int(len(frames)),
        "stations_accepted": int(len(used_indices)),
        "coverage_ratio": float(coverage),
        "mean_distance_m": float(mean_distance),
        "search_radius_m": float(edge_radius),
        "plane_support_m": float(side_support),
        "median_plane_rmse_m": (
            float(np.median(plane_rmse))
            if plane_rmse
            else None
        ),
        "median_seed_shift_m": (
            float(np.median(shift_array))
            if len(shift_array)
            else None
        ),
        "p95_seed_shift_m": (
            float(np.percentile(shift_array, 95.0))
            if len(shift_array)
            else None
        ),
    }
