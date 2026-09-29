from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.spatial import cKDTree


@dataclass(slots=True)
class SectionTrackConfig:
    station_spacing_m: float = 1.0
    along_half_width_m: float = 0.65
    profile_bin_m: float = 0.20
    min_points_per_bin: int = 2
    max_missing_stations: int = 3
    min_coverage_ratio: float = 0.35
    min_track_points: int = 6


def _line_length_2d(xyz: np.ndarray) -> float:
    pts = np.asarray(xyz, dtype=np.float64)
    if len(pts) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1).sum())


def _resample_count(xyz: np.ndarray, count: int) -> np.ndarray:
    pts = np.asarray(xyz, dtype=np.float64)
    if len(pts) < 2:
        return pts.copy()
    seg = np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1)
    keep = np.r_[True, seg > 1e-9]
    pts = pts[keep]
    if len(pts) < 2:
        return pts.copy()
    dist = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1))]
    total = float(dist[-1])
    if total <= 1e-9:
        return np.repeat(pts[:1], max(2, int(count)), axis=0)
    sample = np.linspace(0.0, total, max(2, int(count)))
    return np.column_stack(
        (
            np.interp(sample, dist, pts[:, 0]),
            np.interp(sample, dist, pts[:, 1]),
            np.interp(sample, dist, pts[:, 2]),
        )
    )


def _prefix(x: np.ndarray, z: np.ndarray) -> dict[str, np.ndarray]:
    return {
        "x": np.r_[0.0, np.cumsum(x)],
        "z": np.r_[0.0, np.cumsum(z)],
        "xx": np.r_[0.0, np.cumsum(x * x)],
        "xz": np.r_[0.0, np.cumsum(x * z)],
        "zz": np.r_[0.0, np.cumsum(z * z)],
    }


def _fit_range(
    prefix: dict[str, np.ndarray],
    start: int,
    end: int,
) -> tuple[float, float, float] | None:
    n = int(end - start + 1)
    if n < 2:
        return None
    sx = float(prefix["x"][end + 1] - prefix["x"][start])
    sz = float(prefix["z"][end + 1] - prefix["z"][start])
    sxx = float(prefix["xx"][end + 1] - prefix["xx"][start])
    sxz = float(prefix["xz"][end + 1] - prefix["xz"][start])
    szz = float(prefix["zz"][end + 1] - prefix["zz"][start])
    den = n * sxx - sx * sx
    if abs(den) <= 1e-12:
        return None
    slope = (n * sxz - sx * sz) / den
    intercept = (sz - slope * sx) / n
    sse = (
        szz
        + slope * slope * sxx
        + n * intercept * intercept
        + 2.0 * slope * intercept * sx
        - 2.0 * slope * sxz
        - 2.0 * intercept * sz
    )
    rmse = math.sqrt(max(sse / n, 0.0))
    return float(slope), float(intercept), float(rmse)


def _profile_bins(
    points: np.ndarray,
    origin_xy: np.ndarray,
    tangent: np.ndarray,
    across: np.ndarray,
    *,
    half_width_m: float,
    along_half_width_m: float,
    bin_size_m: float,
    min_points_per_bin: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int] | None:
    delta = points[:, :2] - origin_xy[None, :]
    along = delta @ tangent
    lateral = delta @ across
    keep = (
        (np.abs(along) <= along_half_width_m)
        & (np.abs(lateral) <= half_width_m)
    )
    if int(np.count_nonzero(keep)) < 40:
        return None

    lateral = lateral[keep]
    z = points[keep, 2]
    keys = np.floor((lateral + half_width_m) / bin_size_m).astype(np.int32)
    xs: list[float] = []
    zs: list[float] = []
    counts: list[int] = []
    for key in np.unique(keys):
        ids = np.flatnonzero(keys == key)
        if len(ids) < int(min_points_per_bin):
            continue
        xs.append(float(np.median(lateral[ids])))
        # Use a low robust quantile so sparse vegetation/outliers cannot drag
        # the terrain profile upward when Ground filtering is imperfect.
        zs.append(float(np.quantile(z[ids], 0.25)))
        counts.append(int(len(ids)))

    if len(xs) < 10:
        return None
    order = np.argsort(np.asarray(xs))
    return (
        np.asarray(xs, dtype=np.float64)[order],
        np.asarray(zs, dtype=np.float64)[order],
        np.asarray(counts, dtype=np.int32)[order],
        int(np.count_nonzero(keep)),
    )


def _section_candidates(
    points: np.ndarray,
    origin_xyz: np.ndarray,
    tangent: np.ndarray,
    across: np.ndarray,
    *,
    half_width_m: float,
    expected_width_m: float,
    cfg: SectionTrackConfig,
) -> list[dict[str, Any]]:
    profile = _profile_bins(
        points,
        origin_xyz[:2],
        tangent,
        across,
        half_width_m=half_width_m,
        along_half_width_m=cfg.along_half_width_m,
        bin_size_m=cfg.profile_bin_m,
        min_points_per_bin=cfg.min_points_per_bin,
    )
    if profile is None:
        return []

    x, z, _, support_points = profile
    prefix = _prefix(x, z)
    min_segment_bins = 3
    expected = max(float(expected_width_m), 1.0)
    candidates: list[dict[str, Any]] = []

    for upper_end in range(min_segment_bins - 1, len(x) - 2 * min_segment_bins):
        upper = _fit_range(prefix, 0, upper_end)
        if upper is None:
            continue

        for face_end in range(
            upper_end + min_segment_bins,
            len(x) - min_segment_bins,
        ):
            rough_width = float(x[face_end] - x[upper_end + 1])
            if rough_width < max(0.60, 0.16 * expected):
                continue
            if rough_width > max(2.0, 2.20 * expected):
                continue

            face = _fit_range(prefix, upper_end + 1, face_end)
            lower = _fit_range(prefix, face_end + 1, len(x) - 1)
            if face is None or lower is None:
                continue

            s_upper, b_upper, r_upper = upper
            s_face, b_face, r_face = face
            s_lower, b_lower, r_lower = lower

            # Local +across goes from the discovery crest side to its toe side.
            # The actual steep face must therefore descend in that direction.
            steepness = -float(s_face)
            side_slope = max(abs(float(s_upper)), abs(float(s_lower)))
            if steepness < 0.22 or steepness < side_slope + 0.12:
                continue
            if abs(s_upper) > 0.85 or abs(s_lower) > 0.85:
                continue

            den_crest = s_face - s_upper
            den_toe = s_face - s_lower
            if abs(den_crest) <= 1e-8 or abs(den_toe) <= 1e-8:
                continue

            crest_lateral = float((b_upper - b_face) / den_crest)
            toe_lateral = float((b_lower - b_face) / den_toe)
            if toe_lateral <= crest_lateral:
                continue

            # Reject remote extrapolated intersections. A valid break belongs
            # next to the split between the adjacent fitted profile segments.
            margin = cfg.profile_bin_m * 2.0
            if not (
                x[max(0, upper_end - 1)] - margin
                <= crest_lateral
                <= x[min(len(x) - 1, upper_end + 2)] + margin
            ):
                continue
            if not (
                x[max(0, face_end - 1)] - margin
                <= toe_lateral
                <= x[min(len(x) - 1, face_end + 2)] + margin
            ):
                continue

            crest_z = float(s_upper * crest_lateral + b_upper)
            toe_z = float(s_lower * toe_lateral + b_lower)
            width = float(toe_lateral - crest_lateral)
            relief = float(crest_z - toe_z)
            if width < 0.50 or relief < 0.20:
                continue

            residual = max(r_upper, r_face, r_lower)
            width_penalty = abs(width - expected) / expected
            score = (
                2.20 * steepness
                - 0.70 * side_slope
                + 0.30 * min(relief / max(width, 0.20), 2.0)
                - 3.00 * residual
                - 0.18 * width_penalty
            )
            candidates.append(
                {
                    "score": float(score),
                    "crest_lateral": crest_lateral,
                    "crest_z": crest_z,
                    "toe_lateral": toe_lateral,
                    "toe_z": toe_z,
                    "width_m": width,
                    "relief_m": relief,
                    "rmse_m": float(residual),
                    "slopes": (
                        float(s_upper),
                        float(s_face),
                        float(s_lower),
                    ),
                    "support_points": support_points,
                    "support_bins": int(len(x)),
                }
            )

    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    deduped: list[dict[str, Any]] = []
    for item in candidates:
        if any(
            abs(
                float(item["crest_lateral"])
                - float(other["crest_lateral"])
            ) < 0.25
            and abs(
                float(item["toe_lateral"])
                - float(other["toe_lateral"])
            ) < 0.25
            for other in deduped
        ):
            continue
        deduped.append(item)
        if len(deduped) >= 8:
            break
    return deduped


def _station_frames(candidate, station_spacing_m: float) -> list[dict[str, Any] | None]:
    longest = max(
        _line_length_2d(candidate.crest),
        _line_length_2d(candidate.toe),
    )
    count = max(
        8,
        int(math.ceil(longest / max(station_spacing_m, 0.50))) + 1,
    )
    crest = _resample_count(candidate.crest, count)
    toe = _resample_count(candidate.toe, count)
    center = 0.5 * (crest + toe)
    frames: list[dict[str, Any] | None] = []

    for index in range(count):
        before = center[max(0, index - 1), :2]
        after = center[min(count - 1, index + 1), :2]
        tangent = np.asarray(after - before, dtype=np.float64)
        tangent_norm = float(np.linalg.norm(tangent))
        if tangent_norm <= 1e-9:
            frames.append(None)
            continue
        tangent /= tangent_norm

        across = np.asarray(
            toe[index, :2] - crest[index, :2],
            dtype=np.float64,
        )
        across -= float(np.dot(across, tangent)) * tangent
        across_norm = float(np.linalg.norm(across))
        if across_norm <= 1e-9:
            across = np.asarray([-tangent[1], tangent[0]], dtype=np.float64)
        else:
            across /= across_norm
        if float(np.dot(across, toe[index, :2] - crest[index, :2])) < 0.0:
            across *= -1.0

        frames.append(
            {
                "index": index,
                "origin": center[index],
                "tangent": tangent,
                "across": across,
                "expected_width_m": float(
                    np.linalg.norm(toe[index, :2] - crest[index, :2])
                ),
            }
        )
    return frames


def extract_section_edge_pair(
    raw_points: np.ndarray,
    candidate,
    *,
    station_spacing_m: float = 1.0,
    profile_bin_m: float = 0.20,
    min_line_length_m: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Trace both physical slope breaks from repeated transverse sections.

    Discovery geometry supplies the longitudinal frame and the crest->toe side
    orientation only. The published XYZ positions are solved from the point
    cloud as intersections of upper surface / steep face / lower surface.
    """
    points = np.asarray(raw_points, dtype=np.float64)
    if points.ndim == 2:
        points = points[np.all(np.isfinite(points[:, :3]), axis=1)]
    if points.ndim != 2 or points.shape[1] < 3 or len(points) < 500:
        raise ValueError("Poucos pontos Ground para tracking transversal.")

    cfg = SectionTrackConfig(
        station_spacing_m=float(np.clip(station_spacing_m, 0.60, 2.00)),
        along_half_width_m=float(
            np.clip(max(0.55, station_spacing_m * 0.65), 0.55, 1.20)
        ),
        profile_bin_m=float(np.clip(profile_bin_m, 0.12, 0.35)),
    )
    frames = _station_frames(candidate, cfg.station_spacing_m)
    half_width = float(
        np.clip(
            max(
                candidate.corridor_radius_m * 1.05,
                candidate.baseline_width_p90 * 0.75 + 2.0,
            ),
            3.0,
            35.0,
        )
    )

    tree = cKDTree(points[:, :2])
    radius = (
        math.hypot(half_width, cfg.along_half_width_m)
        + cfg.profile_bin_m
    )
    per_station: list[list[dict[str, Any]]] = []

    for frame in frames:
        if frame is None:
            per_station.append([])
            continue
        ids = tree.query_ball_point(frame["origin"][:2], r=radius)
        local = (
            points[np.asarray(ids, dtype=np.int64)]
            if ids
            else np.empty((0, 3), dtype=np.float64)
        )
        if len(local) < 40:
            per_station.append([])
            continue
        per_station.append(
            _section_candidates(
                local,
                frame["origin"],
                frame["tangent"],
                frame["across"],
                half_width_m=half_width,
                expected_width_m=max(
                    frame["expected_width_m"],
                    candidate.baseline_width_median,
                    1.0,
                ),
                cfg=cfg,
            )
        )

    middle = len(frames) // 2
    anchor = next(
        (
            index
            for index in sorted(
                range(len(frames)),
                key=lambda value: abs(value - middle),
            )
            if per_station[index]
        ),
        None,
    )
    if anchor is None:
        raise ValueError(
            "Nenhuma secção encontrou plano superior + face + plano inferior."
        )

    selected: dict[int, dict[str, Any]] = {
        int(anchor): per_station[int(anchor)][0]
    }

    def advance(indices) -> None:
        history: list[tuple[int, dict[str, Any]]] = []
        missing = 0

        for index in indices:
            options = per_station[index]
            if not options:
                missing += 1
                if missing > cfg.max_missing_stations:
                    break
                continue

            if len(history) >= 2:
                previous = history[-1][1]
                previous2 = history[-2][1]
                pred_crest = (
                    float(previous["crest_lateral"])
                    + float(previous["crest_lateral"])
                    - float(previous2["crest_lateral"])
                )
                pred_toe = (
                    float(previous["toe_lateral"])
                    + float(previous["toe_lateral"])
                    - float(previous2["toe_lateral"])
                )
                pred_width = float(previous["width_m"])
            elif history:
                pred_crest = float(history[-1][1]["crest_lateral"])
                pred_toe = float(history[-1][1]["toe_lateral"])
                pred_width = float(history[-1][1]["width_m"])
            else:
                pred_crest = float(selected[int(anchor)]["crest_lateral"])
                pred_toe = float(selected[int(anchor)]["toe_lateral"])
                pred_width = float(selected[int(anchor)]["width_m"])

            gate = float(
                np.clip(
                    0.50
                    + 0.25
                    * max(
                        pred_width,
                        candidate.baseline_width_median,
                        1.0,
                    ),
                    0.75,
                    2.50,
                )
            )
            allowed_gate = gate + 0.25 * missing
            ranked: list[tuple[float, dict[str, Any]]] = []

            for option in options:
                crest_delta = abs(
                    float(option["crest_lateral"]) - pred_crest
                )
                toe_delta = abs(
                    float(option["toe_lateral"]) - pred_toe
                )
                if crest_delta > allowed_gate or toe_delta > allowed_gate:
                    continue
                width_delta = (
                    abs(float(option["width_m"]) - pred_width)
                    / max(pred_width, 1.0)
                )
                cost = (
                    -float(option["score"])
                    + 0.65 * (crest_delta + toe_delta) / max(gate, 1e-6)
                    + 0.30 * width_delta
                )
                ranked.append((cost, option))

            if not ranked:
                missing += 1
                if missing > cfg.max_missing_stations:
                    break
                continue

            chosen = min(ranked, key=lambda item: item[0])[1]
            selected[int(index)] = chosen
            history.append((int(index), chosen))
            history = history[-3:]
            missing = 0

    advance(range(int(anchor) + 1, len(frames)))
    advance(range(int(anchor) - 1, -1, -1))

    ordered = sorted(selected)
    coverage = len(ordered) / max(len(frames), 1)
    if (
        len(ordered) < cfg.min_track_points
        or coverage < cfg.min_coverage_ratio
    ):
        raise ValueError(
            "Tracking transversal insuficiente: "
            f"{len(ordered)}/{len(frames)} secções ({coverage:.0%})."
        )

    crest_xyz: list[list[float]] = []
    toe_xyz: list[list[float]] = []
    widths: list[float] = []
    reliefs: list[float] = []
    residuals: list[float] = []

    for index in ordered:
        frame = frames[index]
        if frame is None:
            continue
        option = selected[index]
        crest_xy = (
            frame["origin"][:2]
            + float(option["crest_lateral"]) * frame["across"]
        )
        toe_xy = (
            frame["origin"][:2]
            + float(option["toe_lateral"]) * frame["across"]
        )
        crest_xyz.append(
            [
                float(crest_xy[0]),
                float(crest_xy[1]),
                float(option["crest_z"]),
            ]
        )
        toe_xyz.append(
            [
                float(toe_xy[0]),
                float(toe_xy[1]),
                float(option["toe_z"]),
            ]
        )
        widths.append(float(option["width_m"]))
        reliefs.append(float(option["relief_m"]))
        residuals.append(float(option["rmse_m"]))

    crest = np.asarray(crest_xyz, dtype=np.float64)
    toe = np.asarray(toe_xyz, dtype=np.float64)
    if (
        _line_length_2d(crest) < min_line_length_m
        or _line_length_2d(toe) < min_line_length_m
    ):
        raise ValueError(
            "Tracking transversal produziu uma breakline demasiado curta."
        )

    return crest, toe, {
        "source": "CROSS_SECTION_THREE_PLANE_TRACKER",
        "stations_total": int(len(frames)),
        "stations_accepted": int(len(ordered)),
        "coverage_ratio": float(coverage),
        "profile_bin_m": float(cfg.profile_bin_m),
        "station_spacing_m": float(cfg.station_spacing_m),
        "half_width_m": float(half_width),
        "median_width_m": float(np.median(widths)),
        "median_relief_m": float(np.median(reliefs)),
        "median_rmse_m": float(np.median(residuals)),
    }
