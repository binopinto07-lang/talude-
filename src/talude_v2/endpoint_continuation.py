from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from .section_edge_tracker import (
    SectionTrackConfig,
    _fit_range,
    _prefix,
    _profile_bins,
    _section_candidates,
)


def _line_length_2d(xyz: np.ndarray) -> float:
    pts = np.asarray(xyz, dtype=np.float64)
    if len(pts) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1).sum())


def _unit(vector: np.ndarray) -> np.ndarray | None:
    vec = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(vec))
    if norm <= 1e-9:
        return None
    return vec / norm


def _endpoint_frame(
    crest: np.ndarray,
    toe: np.ndarray,
    *,
    at_start: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    center = 0.5 * (crest + toe)
    sample = min(5, len(center))
    if at_start:
        tangent = _unit(center[0, :2] - center[sample - 1, :2])
        index = 0
    else:
        tangent = _unit(center[-1, :2] - center[-sample, :2])
        index = -1
    if tangent is None:
        raise ValueError("Não foi possível estimar a direção terminal.")

    across = np.asarray(toe[index, :2] - crest[index, :2], dtype=np.float64)
    across -= float(np.dot(across, tangent)) * tangent
    across = _unit(across)
    if across is None:
        across = np.asarray((-tangent[1], tangent[0]), dtype=np.float64)
    if float(np.dot(across, toe[index, :2] - crest[index, :2])) < 0.0:
        across *= -1.0

    width_samples = np.linalg.norm(
        toe[:sample, :2] - crest[:sample, :2],
        axis=1,
    ) if at_start else np.linalg.norm(
        toe[-sample:, :2] - crest[-sample:, :2],
        axis=1,
    )
    width = float(np.median(width_samples))
    return center[index].copy(), tangent, across, max(width, 0.50)


def _local_bucket(
    tree: cKDTree,
    points: np.ndarray,
    origin_xy: np.ndarray,
    *,
    radius_m: float,
) -> np.ndarray:
    ids = tree.query_ball_point(origin_xy, r=float(radius_m))
    if not ids:
        return np.empty((0, 3), dtype=np.float64)
    return points[np.asarray(ids, dtype=np.int64)]


def _rank_pair_candidate(
    options: list[dict[str, Any]],
    *,
    predicted_crest: float,
    predicted_toe: float,
    predicted_width: float,
    gate_m: float,
) -> dict[str, Any] | None:
    ranked: list[tuple[float, dict[str, Any]]] = []
    for option in options:
        crest_delta = abs(float(option["crest_lateral"]) - predicted_crest)
        toe_delta = abs(float(option["toe_lateral"]) - predicted_toe)
        if crest_delta > gate_m or toe_delta > gate_m:
            continue
        width_delta = (
            abs(float(option["width_m"]) - predicted_width)
            / max(predicted_width, 1.0)
        )
        cost = (
            -float(option["score"])
            + 0.70 * (crest_delta + toe_delta) / max(gate_m, 1e-6)
            + 0.40 * width_delta
        )
        ranked.append((cost, option))
    return min(ranked, key=lambda item: item[0])[1] if ranked else None


def _single_edge_candidate(
    local_points: np.ndarray,
    origin_xyz: np.ndarray,
    tangent: np.ndarray,
    across: np.ndarray,
    *,
    predicted_lateral: float,
    kind: str,
    half_width_m: float,
    gate_m: float,
    cfg: SectionTrackConfig,
) -> dict[str, float] | None:
    profile = _profile_bins(
        local_points,
        origin_xyz[:2],
        tangent,
        across,
        half_width_m=half_width_m,
        along_half_width_m=cfg.along_half_width_m,
        bin_size_m=cfg.profile_bin_m,
        min_points_per_bin=cfg.min_points_per_bin,
    )
    if profile is None:
        return None

    x, z, _, support_points = profile
    prefix = _prefix(x, z)
    best: tuple[float, dict[str, float]] | None = None

    for split in range(2, len(x) - 3):
        if abs(float(x[split]) - predicted_lateral) > gate_m:
            continue

        left_min = max(0, split - 9)
        left_max = max(0, split - 2)
        right_min = split + 3
        right_max = min(len(x), split + 11)

        for left_start in range(left_min, left_max + 1):
            left = _fit_range(prefix, left_start, split)
            if left is None:
                continue
            for right_stop in range(right_min, right_max + 1):
                right = _fit_range(prefix, split + 1, right_stop - 1)
                if right is None:
                    continue

                s_left, b_left, r_left = left
                s_right, b_right, r_right = right

                if kind == "CREST":
                    steepness = -float(s_right)
                    side_slope = abs(float(s_left))
                    denominator = s_right - s_left
                    if (
                        steepness < 0.22
                        or steepness < side_slope + 0.12
                        or side_slope > 0.85
                        or abs(denominator) <= 1e-8
                    ):
                        continue
                    lateral = float((b_left - b_right) / denominator)
                    elevation = float(s_left * lateral + b_left)
                else:
                    steepness = -float(s_left)
                    side_slope = abs(float(s_right))
                    denominator = s_left - s_right
                    if (
                        steepness < 0.22
                        or steepness < side_slope + 0.12
                        or side_slope > 0.85
                        or abs(denominator) <= 1e-8
                    ):
                        continue
                    lateral = float((b_right - b_left) / denominator)
                    elevation = float(s_left * lateral + b_left)

                # The fitted intersection must stay next to the actual split,
                # not several metres away through extrapolation.
                if abs(lateral - float(x[split])) > max(
                    0.70,
                    cfg.profile_bin_m * 3.0,
                ):
                    continue
                if abs(lateral - predicted_lateral) > gate_m:
                    continue

                residual = max(float(r_left), float(r_right))
                score = (
                    2.0 * steepness
                    - 0.60 * side_slope
                    - 3.0 * residual
                    - 0.45
                    * abs(lateral - predicted_lateral)
                    / max(gate_m, 1e-6)
                )
                item = {
                    "score": float(score),
                    "lateral": lateral,
                    "z": elevation,
                    "rmse_m": residual,
                    "support_points": float(support_points),
                }
                if best is None or score > best[0]:
                    best = (float(score), item)

    return best[1] if best is not None else None


def _extend_one_direction(
    points: np.ndarray,
    tree: cKDTree,
    crest: np.ndarray,
    toe: np.ndarray,
    candidate,
    *,
    at_start: bool,
    cfg: SectionTrackConfig,
    max_extension_m: float,
    max_gap_stations: int,
) -> tuple[list[np.ndarray], list[np.ndarray], dict[str, Any]]:
    origin, tangent, across, width = _endpoint_frame(
        crest,
        toe,
        at_start=at_start,
    )
    endpoint_crest = crest[0].copy() if at_start else crest[-1].copy()
    endpoint_toe = toe[0].copy() if at_start else toe[-1].copy()
    previous_center = origin[:2].copy()

    half_width = float(
        np.clip(
            max(
                float(candidate.corridor_radius_m) * 1.10,
                float(candidate.baseline_width_p90) * 0.80 + 2.0,
                width * 0.80 + 2.0,
            ),
            3.0,
            35.0,
        )
    )
    query_radius = (
        math.hypot(half_width, cfg.along_half_width_m)
        + cfg.profile_bin_m
    )
    step_m = float(cfg.station_spacing_m)
    steps = max(1, int(math.ceil(max_extension_m / step_m)))

    crest_added: list[np.ndarray] = []
    toe_added: list[np.ndarray] = []
    crest_misses = 0
    toe_misses = 0
    crest_active = True
    toe_active = True
    crest_bridged = 0
    toe_bridged = 0
    pair_stations = 0
    independent_stations = 0
    terminal_reason = "MAX_EXTENSION"

    for _ in range(steps):
        origin = origin.copy()
        origin[:2] = previous_center + tangent * step_m
        local = _local_bucket(
            tree,
            points,
            origin[:2],
            radius_m=query_radius,
        )
        if len(local) < 35:
            crest_misses += 1
            toe_misses += 1
            if crest_misses > max_gap_stations:
                crest_active = False
            if toe_misses > max_gap_stations:
                toe_active = False
            previous_center = origin[:2].copy()
            if not crest_active and not toe_active:
                terminal_reason = "NO_POINT_SUPPORT"
                break
            continue

        predicted_crest = float(
            np.dot(endpoint_crest[:2] - origin[:2], across)
        )
        predicted_toe = float(
            np.dot(endpoint_toe[:2] - origin[:2], across)
        )
        predicted_width = max(
            predicted_toe - predicted_crest,
            width,
            0.50,
        )
        gate = float(
            np.clip(
                0.55 + 0.24 * predicted_width,
                0.85,
                2.75,
            )
        )
        gate += 0.30 * max(crest_misses, toe_misses)

        options = _section_candidates(
            local,
            origin,
            tangent,
            across,
            half_width_m=half_width,
            expected_width_m=max(
                predicted_width,
                float(candidate.baseline_width_median),
                1.0,
            ),
            cfg=cfg,
        )
        pair = _rank_pair_candidate(
            options,
            predicted_crest=predicted_crest,
            predicted_toe=predicted_toe,
            predicted_width=predicted_width,
            gate_m=gate,
        )

        found_crest = None
        found_toe = None

        if pair is not None:
            crest_xy = (
                origin[:2]
                + float(pair["crest_lateral"]) * across
            )
            toe_xy = (
                origin[:2]
                + float(pair["toe_lateral"]) * across
            )
            found_crest = np.asarray(
                (
                    crest_xy[0],
                    crest_xy[1],
                    float(pair["crest_z"]),
                ),
                dtype=np.float64,
            )
            found_toe = np.asarray(
                (
                    toe_xy[0],
                    toe_xy[1],
                    float(pair["toe_z"]),
                ),
                dtype=np.float64,
            )
            pair_stations += 1
        else:
            if crest_active:
                item = _single_edge_candidate(
                    local,
                    origin,
                    tangent,
                    across,
                    predicted_lateral=predicted_crest,
                    kind="CREST",
                    half_width_m=half_width,
                    gate_m=gate,
                    cfg=cfg,
                )
                if item is not None:
                    xy = origin[:2] + float(item["lateral"]) * across
                    found_crest = np.asarray(
                        (xy[0], xy[1], float(item["z"])),
                        dtype=np.float64,
                    )

            if toe_active:
                item = _single_edge_candidate(
                    local,
                    origin,
                    tangent,
                    across,
                    predicted_lateral=predicted_toe,
                    kind="TOE",
                    half_width_m=half_width,
                    gate_m=gate,
                    cfg=cfg,
                )
                if item is not None:
                    xy = origin[:2] + float(item["lateral"]) * across
                    found_toe = np.asarray(
                        (xy[0], xy[1], float(item["z"])),
                        dtype=np.float64,
                    )
            if found_crest is not None or found_toe is not None:
                independent_stations += 1

        if found_crest is not None and crest_active:
            if crest_misses:
                crest_bridged += int(crest_misses)
            crest_added.append(found_crest)
            endpoint_crest = found_crest
            crest_misses = 0
        else:
            crest_misses += 1
            if crest_misses > max_gap_stations:
                crest_active = False

        if found_toe is not None and toe_active:
            if toe_misses:
                toe_bridged += int(toe_misses)
            toe_added.append(found_toe)
            endpoint_toe = found_toe
            toe_misses = 0
        else:
            toe_misses += 1
            if toe_misses > max_gap_stations:
                toe_active = False

        centers: list[np.ndarray] = []
        if found_crest is not None and found_toe is not None:
            new_center = 0.5 * (found_crest[:2] + found_toe[:2])
            centers.append(new_center)
            new_width = float(
                np.linalg.norm(found_toe[:2] - found_crest[:2])
            )
            width = 0.75 * width + 0.25 * max(new_width, 0.50)
            new_tangent = _unit(new_center - previous_center)
            if new_tangent is not None:
                blended = _unit(0.72 * tangent + 0.28 * new_tangent)
                if blended is not None:
                    tangent = blended
            new_across = np.asarray(
                found_toe[:2] - found_crest[:2],
                dtype=np.float64,
            )
            new_across -= float(np.dot(new_across, tangent)) * tangent
            new_across = _unit(new_across)
            if new_across is not None:
                if float(np.dot(new_across, across)) < 0.0:
                    new_across *= -1.0
                across = new_across
        elif found_crest is not None:
            centers.append(
                found_crest[:2] + across * (0.5 * width)
            )
        elif found_toe is not None:
            centers.append(
                found_toe[:2] - across * (0.5 * width)
            )

        if centers:
            previous_center = centers[-1]
            origin[:2] = previous_center
        else:
            previous_center = origin[:2].copy()

        if not crest_active and not toe_active:
            terminal_reason = "GEOMETRY_ENDED"
            break

    # A single isolated point is more likely noise than useful continuation.
    if len(crest_added) < 2:
        crest_added = []
    if len(toe_added) < 2:
        toe_added = []

    return crest_added, toe_added, {
        "crest_added": int(len(crest_added)),
        "toe_added": int(len(toe_added)),
        "crest_gap_stations_bridged": int(crest_bridged),
        "toe_gap_stations_bridged": int(toe_bridged),
        "pair_stations": int(pair_stations),
        "independent_stations": int(independent_stations),
        "terminal_reason": terminal_reason,
        "max_extension_m": float(max_extension_m),
    }


def continue_edge_pair_to_face_ends(
    raw_points: np.ndarray,
    crest_xyz: np.ndarray,
    toe_xyz: np.ndarray,
    candidate,
    *,
    station_spacing_m: float = 1.0,
    profile_bin_m: float = 0.20,
    max_gap_m: float = 4.0,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Extend validated crest/toe to the physical ends of the same slope face.

    The already validated geometry is never moved. New points are appended or
    prepended only when local transverse profiles keep the expected slope
    transition. CRISTA and PÉ may continue independently through partial
    occlusion; short unsupported gaps are bridged conservatively.
    """
    points = np.asarray(raw_points, dtype=np.float64)
    points = (
        points[np.all(np.isfinite(points[:, :3]), axis=1)]
        if points.ndim == 2
        else points
    )
    crest = np.asarray(crest_xyz, dtype=np.float64)
    toe = np.asarray(toe_xyz, dtype=np.float64)

    if (
        points.ndim != 2
        or points.shape[1] < 3
        or len(points) < 300
        or len(crest) < 4
        or len(toe) < 4
    ):
        raise ValueError("Dados insuficientes para prolongamento terminal.")

    cfg = SectionTrackConfig(
        station_spacing_m=float(np.clip(station_spacing_m, 0.60, 2.00)),
        along_half_width_m=float(
            np.clip(max(0.60, station_spacing_m * 0.72), 0.60, 1.35)
        ),
        profile_bin_m=float(np.clip(profile_bin_m, 0.12, 0.35)),
        min_points_per_bin=2,
        max_missing_stations=4,
        min_coverage_ratio=0.0,
        min_track_points=0,
    )
    tree = cKDTree(points[:, :2])
    core_length = max(_line_length_2d(crest), _line_length_2d(toe))
    width = float(
        np.median(
            np.linalg.norm(
                toe[: min(len(toe), len(crest)), :2]
                - crest[: min(len(toe), len(crest)), :2],
                axis=1,
            )
        )
    )
    max_extension_m = float(
        np.clip(
            max(12.0, 0.35 * core_length, 2.5 * max(width, 1.0)),
            12.0,
            40.0,
        )
    )
    max_gap_stations = max(
        2,
        int(math.ceil(float(max_gap_m) / cfg.station_spacing_m)),
    )

    start_crest, start_toe, start_meta = _extend_one_direction(
        points,
        tree,
        crest,
        toe,
        candidate,
        at_start=True,
        cfg=cfg,
        max_extension_m=max_extension_m,
        max_gap_stations=max_gap_stations,
    )
    end_crest, end_toe, end_meta = _extend_one_direction(
        points,
        tree,
        crest,
        toe,
        candidate,
        at_start=False,
        cfg=cfg,
        max_extension_m=max_extension_m,
        max_gap_stations=max_gap_stations,
    )

    crest_out = np.vstack(
        (
            np.asarray(start_crest[::-1], dtype=np.float64)
            if start_crest else np.empty((0, 3), dtype=np.float64),
            crest,
            np.asarray(end_crest, dtype=np.float64)
            if end_crest else np.empty((0, 3), dtype=np.float64),
        )
    )
    toe_out = np.vstack(
        (
            np.asarray(start_toe[::-1], dtype=np.float64)
            if start_toe else np.empty((0, 3), dtype=np.float64),
            toe,
            np.asarray(end_toe, dtype=np.float64)
            if end_toe else np.empty((0, 3), dtype=np.float64),
        )
    )

    crest_added = int(len(crest_out) - len(crest))
    toe_added = int(len(toe_out) - len(toe))
    return crest_out, toe_out, {
        "source": "ENDPOINT_CONTINUATION_GAP_BRIDGE",
        "applied": bool(crest_added or toe_added),
        "crest_added": crest_added,
        "toe_added": toe_added,
        "core_crest_vertices": int(len(crest)),
        "core_toe_vertices": int(len(toe)),
        "final_crest_vertices": int(len(crest_out)),
        "final_toe_vertices": int(len(toe_out)),
        "station_spacing_m": float(cfg.station_spacing_m),
        "max_gap_m": float(max_gap_m),
        "start": start_meta,
        "end": end_meta,
    }
