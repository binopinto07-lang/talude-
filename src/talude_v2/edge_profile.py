from __future__ import annotations

import math
from time import perf_counter
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from core.terrain_face import extract_terrain_face_edge

from .reasons import V2DetectionError, V2Reason
from .section_edge_tracker import extract_section_edge_pair


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


def _orient_like_reference(xyz: np.ndarray, reference: np.ndarray) -> np.ndarray:
    pts = np.asarray(xyz, dtype=np.float64)
    ref = np.asarray(reference, dtype=np.float64)
    if len(pts) < 2 or len(ref) < 2:
        return pts.copy()
    direct = (
        float(np.linalg.norm(pts[0, :2] - ref[0, :2]))
        + float(np.linalg.norm(pts[-1, :2] - ref[-1, :2]))
    )
    reverse = (
        float(np.linalg.norm(pts[0, :2] - ref[-1, :2]))
        + float(np.linalg.norm(pts[-1, :2] - ref[0, :2]))
    )
    return pts[::-1].copy() if reverse < direct else pts.copy()


def _nearest_reference_seed(reference: np.ndarray, anchor: np.ndarray) -> np.ndarray:
    ref = np.asarray(reference, dtype=np.float64)
    seed = np.asarray(anchor, dtype=np.float64)
    if len(ref) == 0:
        return seed.copy()
    idx = int(np.argmin(np.linalg.norm(ref[:, :2] - seed[:2], axis=1)))
    return ref[idx, :3].copy()


def _turn_p95_deg(xyz: np.ndarray) -> float:
    pts = np.asarray(xyz, dtype=np.float64)
    if len(pts) < 3:
        return 0.0
    a = np.diff(pts[:, :2], axis=0)
    lengths = np.linalg.norm(a, axis=1)
    valid = lengths > 1e-8
    a = a[valid]
    if len(a) < 2:
        return 0.0
    a = a / np.linalg.norm(a, axis=1)[:, None]
    dots = np.sum(a[:-1] * a[1:], axis=1)
    angles = np.degrees(np.arccos(np.clip(dots, -1.0, 1.0)))
    return float(np.percentile(angles, 95.0)) if len(angles) else 0.0



def _edge_search_radius_m(candidate) -> float:
    width = max(
        float(getattr(candidate, "baseline_width_median", 0.0) or 0.0),
        float(getattr(candidate, "baseline_width_p90", 0.0) or 0.0),
    )
    return float(np.clip(0.80 * max(width, 1.0) + 2.0, 4.0, 18.0))


def _extract_face_centered_edges(
    raw: np.ndarray,
    candidate,
    *,
    resolution: float,
):
    """Solve ridge/toe from the interior of the steep face itself.

    The discovery baseline is not used as an edge seed. Both ridge and toe
    start from the candidate face centre, while core.terrain_face independently
    selects the nearby steep component and then the requested uphill/downhill
    boundary. This avoids the sparse V1 click-mask path that failed even on the
    synthetic 45-degree bench.
    """
    face_seed = np.asarray(candidate.seed_xyz, dtype=np.float64)
    search_radius = _edge_search_radius_m(candidate)

    crest_result = extract_terrain_face_edge(
        raw[:, :3],
        face_seed,
        profile="ridge",
        grid_resolution=float(resolution),
        face_seed_xyz=face_seed,
        edge_seed_max_distance_m=search_radius,
    )
    toe_result = extract_terrain_face_edge(
        raw[:, :3],
        face_seed,
        profile="toe",
        grid_resolution=float(resolution),
        face_seed_xyz=face_seed,
        edge_seed_max_distance_m=search_radius,
    )

    meta = {
        "source": "FACE_CENTER_SEEDED_CORE",
        "grid_resolution_m": float(
            max(crest_result.grid_resolution, toe_result.grid_resolution)
        ),
        "ridge_face_slope_deg": float(crest_result.face_slope_deg),
        "toe_face_slope_deg": float(toe_result.face_slope_deg),
        "ridge_face_cells": int(crest_result.face_cells),
        "toe_face_cells": int(toe_result.face_cells),
        "edge_search_radius_m": float(search_radius),
        # The face seed itself drives component selection. Core's selector may
        # snap to the nearest component internally; no baseline-edge distance is
        # used in this stage.
        "seed_to_face_distance_m": 0.0,
    }
    return crest_result, toe_result, meta


def _discover_local_face_pair(
    raw: np.ndarray,
    candidate,
    *,
    resolution: float,
    min_line_length_m: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Compatibility wrapper returning the core face-centred ridge/toe pair."""
    crest_result, toe_result, meta = _extract_face_centered_edges(
        raw,
        candidate,
        resolution=resolution,
    )
    crest = np.asarray(crest_result.vertices, dtype=np.float64)
    toe = np.asarray(toe_result.vertices, dtype=np.float64)
    if (
        len(crest) < 2
        or len(toe) < 2
        or _line_length_2d(crest) < float(min_line_length_m)
        or _line_length_2d(toe) < float(min_line_length_m)
    ):
        raise V2DetectionError(
            V2Reason.BOUNDARY_NOT_FOUND,
            f"FACE_{int(candidate.face_id):06d}: limites locais demasiado curtos.",
        )
    return crest, toe, meta


def _reference_agreement(xyz: np.ndarray, reference: np.ndarray) -> dict[str, float]:
    pts = np.asarray(xyz, dtype=np.float64)
    ref = np.asarray(reference, dtype=np.float64)
    if len(pts) < 2 or len(ref) < 2:
        return {"median_m": float("inf"), "p95_m": float("inf")}
    tree = cKDTree(ref[:, :2])
    dist, _ = tree.query(pts[:, :2], k=1)
    return {
        "median_m": float(np.median(dist)),
        "p95_m": float(np.percentile(dist, 95.0)),
    }


def _local_reference_tangent(reference: np.ndarray, point_xy: np.ndarray) -> np.ndarray | None:
    ref = np.asarray(reference, dtype=np.float64)
    if len(ref) < 2:
        return None
    idx = int(np.argmin(np.linalg.norm(ref[:, :2] - point_xy[:2], axis=1)))
    a = max(0, idx - 1)
    b = min(len(ref) - 1, idx + 1)
    if b == a:
        return None
    tangent = ref[b, :2] - ref[a, :2]
    norm = float(np.linalg.norm(tangent))
    if norm <= 1e-9:
        return None
    return tangent / norm


def _trim_endpoint_hooks(xyz: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Remove lateral closure tails without moving the real edge body."""
    pts = np.asarray(xyz, dtype=np.float64).copy()
    ref = np.asarray(reference, dtype=np.float64)
    if len(pts) < 4:
        return pts

    max_drops = max(1, min(4, int(math.ceil(len(pts) * 0.34))))
    ref_tree = cKDTree(ref[:, :2]) if len(ref) >= 2 else None

    def unit(a: np.ndarray, b: np.ndarray) -> np.ndarray | None:
        vec = np.asarray(b[:2] - a[:2], dtype=np.float64)
        norm = float(np.linalg.norm(vec))
        if norm <= 1e-9:
            return None
        return vec / norm

    def endpoint_far(current: np.ndarray, *, start: bool) -> bool:
        if ref_tree is None or len(current) < 2:
            return False
        i0, i1 = (0, 1) if start else (-1, -2)
        d0 = float(ref_tree.query(current[i0, :2], k=1)[0])
        d1 = float(ref_tree.query(current[i1, :2], k=1)[0])
        # A closure tail normally moves sharply away from the longitudinal
        # reference while the following body immediately returns to it.
        return d0 > max(1.25, d1 + 0.75)

    def turn_bad_start(current: np.ndarray) -> bool:
        if len(current) < 4:
            return False
        if endpoint_far(current, start=True):
            return True
        first = unit(current[0], current[1])
        body = unit(current[1], current[min(3, len(current) - 1)])
        if first is None:
            return True
        if body is not None:
            alignment = abs(float(np.dot(first, body)))
            if alignment < math.cos(math.radians(38.0)):
                return True
        tangent = _local_reference_tangent(
            ref,
            0.5 * (current[0, :2] + current[1, :2]),
        )
        return (
            tangent is not None
            and abs(float(np.dot(first, tangent))) < math.cos(math.radians(42.0))
        )

    def turn_bad_end(current: np.ndarray) -> bool:
        if len(current) < 4:
            return False
        if endpoint_far(current, start=False):
            return True
        last = unit(current[-2], current[-1])
        body = unit(current[max(0, len(current) - 4)], current[-2])
        if last is None:
            return True
        if body is not None:
            alignment = abs(float(np.dot(last, body)))
            if alignment < math.cos(math.radians(38.0)):
                return True
        tangent = _local_reference_tangent(
            ref,
            0.5 * (current[-2, :2] + current[-1, :2]),
        )
        return (
            tangent is not None
            and abs(float(np.dot(last, tangent))) < math.cos(math.radians(42.0))
        )

    dropped = 0
    while len(pts) >= 4 and dropped < max_drops and turn_bad_start(pts):
        pts = pts[1:].copy()
        dropped += 1

    dropped = 0
    while len(pts) >= 4 and dropped < max_drops and turn_bad_end(pts):
        pts = pts[:-1].copy()
        dropped += 1

    return pts


def _pair_width_median(crest: np.ndarray, toe: np.ndarray) -> float:
    count = max(10, min(160, int(max(_line_length_2d(crest), _line_length_2d(toe))) + 1))
    a = _resample_count(crest, count)
    b = _resample_count(toe, count)
    direct = float(np.linalg.norm(a[0, :2] - b[0, :2]) + np.linalg.norm(a[-1, :2] - b[-1, :2]))
    reverse = float(np.linalg.norm(a[0, :2] - b[-1, :2]) + np.linalg.norm(a[-1, :2] - b[0, :2]))
    if reverse < direct:
        b = b[::-1].copy()
    return float(np.median(np.linalg.norm(a[:, :2] - b[:, :2], axis=1)))


def validate_edge_pair(
    crest_xyz: np.ndarray,
    toe_xyz: np.ndarray,
    *,
    expected_width_m: float | None = None,
    min_line_length_m: float = 2.0,
) -> dict[str, Any]:
    """Validate that a crest/toe pair brackets one coherent slope face.

    This deliberately validates the geometry of the pair itself, not its
    distance to the old baseline. The baseline is only a discovery seed.
    """
    crest = np.asarray(crest_xyz, dtype=np.float64)
    toe = np.asarray(toe_xyz, dtype=np.float64)
    result: dict[str, Any] = {
        "accepted": False,
        "reason": "INVALID_PAIR",
    }
    if len(crest) < 2 or len(toe) < 2:
        result["reason"] = "TOO_FEW_VERTICES"
        return result

    crest_length = _line_length_2d(crest)
    toe_length = _line_length_2d(toe)
    result["crest_length_m"] = crest_length
    result["toe_length_m"] = toe_length
    if crest_length < float(min_line_length_m) or toe_length < float(min_line_length_m):
        result["reason"] = "LINE_TOO_SHORT"
        return result

    count = int(
        np.clip(
            max(crest_length, toe_length) / 1.0 + 1,
            10,
            240,
        )
    )
    crest_r = _resample_count(crest, count)
    toe_r = _resample_count(toe, count)

    direct = (
        float(np.linalg.norm(crest_r[0, :2] - toe_r[0, :2]))
        + float(np.linalg.norm(crest_r[-1, :2] - toe_r[-1, :2]))
    )
    reverse = (
        float(np.linalg.norm(crest_r[0, :2] - toe_r[-1, :2]))
        + float(np.linalg.norm(crest_r[-1, :2] - toe_r[0, :2]))
    )
    if reverse < direct:
        toe_r = toe_r[::-1].copy()

    widths = np.linalg.norm(crest_r[:, :2] - toe_r[:, :2], axis=1)
    relief = crest_r[:, 2] - toe_r[:, 2]
    width_median = float(np.median(widths))
    width_p90 = float(np.percentile(widths, 90.0))
    width_p10 = float(np.percentile(widths, 10.0))
    positive_relief_ratio = float(np.mean(relief > 0.15))
    median_relief = float(np.median(relief))
    p10_relief = float(np.percentile(relief, 10.0))
    crest_turn_p95 = _turn_p95_deg(crest)
    toe_turn_p95 = _turn_p95_deg(toe)

    result.update(
        {
            "width_median_m": width_median,
            "width_p10_m": width_p10,
            "width_p90_m": width_p90,
            "positive_relief_ratio": positive_relief_ratio,
            "median_relief_m": median_relief,
            "relief_p10_m": p10_relief,
            "crest_turn_p95_deg": crest_turn_p95,
            "toe_turn_p95_deg": toe_turn_p95,
        }
    )

    if width_median < 0.55:
        result["reason"] = "PAIR_COLLAPSED"
        return result
    if width_p90 > max(3.0 * max(width_median, 0.25), width_median + 12.0):
        result["reason"] = "WIDTH_UNSTABLE"
        return result
    if positive_relief_ratio < 0.72 or median_relief < 0.25:
        result["reason"] = "CREST_NOT_ABOVE_TOE"
        return result
    if p10_relief < -0.30:
        result["reason"] = "PAIR_CROSSES_IN_Z"
        return result
    if crest_turn_p95 > 125.0 or toe_turn_p95 > 125.0:
        result["reason"] = "EXCESSIVE_ZIGZAG"
        return result

    expected = float(expected_width_m or 0.0)
    if expected > 0.75:
        min_width = max(0.55, expected * 0.24)
        max_width = min(55.0, max(expected * 2.8, expected + 5.0))
        if width_median < min_width or width_median > max_width:
            result["reason"] = "WIDTH_OUTSIDE_DISCOVERY_ENVELOPE"
            result["expected_width_m"] = expected
            return result

    width_spread = max(0.0, width_p90 - width_p10)
    width_quality = math.exp(-width_spread / max(width_median, 0.75))
    relief_quality = float(np.clip(positive_relief_ratio, 0.0, 1.0))
    turn_quality = float(
        np.clip(
            1.0 - max(crest_turn_p95, toe_turn_p95) / 140.0,
            0.0,
            1.0,
        )
    )
    result["quality_score"] = float(
        np.clip(0.45 * relief_quality + 0.35 * width_quality + 0.20 * turn_quality, 0.0, 1.0)
    )
    result["accepted"] = True
    result["reason"] = "SUCCESS"
    return result


def extract_profile_edge_pair(
    raw_points: np.ndarray,
    candidate,
    *,
    min_line_length_m: float,
    grid_resolution_m: float = 0.20,
    station_spacing_m: float = 1.00,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract physical crest/toe from tracked transverse terrain sections."""
    started = perf_counter()
    raw = np.asarray(raw_points, dtype=np.float64)
    raw = (
        raw[np.all(np.isfinite(raw[:, :3]), axis=1)]
        if raw.ndim == 2
        else raw
    )
    if raw.ndim != 2 or raw.shape[1] < 3 or len(raw) < 500:
        raise V2DetectionError(
            V2Reason.LOW_GROUND_SUPPORT,
            f"FACE_{int(candidate.face_id):06d}: poucos pontos Ground para perfil geométrico.",
        )

    profile_bin = float(np.clip(grid_resolution_m, 0.14, 0.30))
    try:
        crest, toe, tracker = extract_section_edge_pair(
            raw,
            candidate,
            station_spacing_m=float(station_spacing_m),
            profile_bin_m=profile_bin,
            min_line_length_m=float(min_line_length_m),
        )
    except Exception as exc:
        raise V2DetectionError(
            V2Reason.REFINEMENT_FAILED,
            (
                f"FACE_{int(candidate.face_id):06d}: tracking transversal "
                f"plano→face→plano falhou: {exc}"
            ),
        ) from exc

    # The discovery pair may define longitudinal orientation, but never the
    # physical XY/Z position of the published crest/toe.
    crest = _orient_like_reference(
        np.asarray(crest, dtype=np.float64),
        np.asarray(candidate.crest, dtype=np.float64),
    )
    toe = _orient_like_reference(
        np.asarray(toe, dtype=np.float64),
        np.asarray(candidate.toe, dtype=np.float64),
    )
    crest = _trim_endpoint_hooks(
        crest,
        np.asarray(candidate.crest, dtype=np.float64),
    )
    toe = _trim_endpoint_hooks(
        toe,
        np.asarray(candidate.toe, dtype=np.float64),
    )

    local_width = _pair_width_median(crest, toe)
    crest_agreement = _reference_agreement(
        crest,
        np.asarray(candidate.crest, dtype=np.float64),
    )
    toe_agreement = _reference_agreement(
        toe,
        np.asarray(candidate.toe, dtype=np.float64),
    )

    geometry = validate_edge_pair(
        crest,
        toe,
        expected_width_m=float(candidate.baseline_width_median),
        min_line_length_m=float(min_line_length_m),
    )
    if not bool(geometry.get("accepted")):
        raise V2DetectionError(
            V2Reason.REFINEMENT_FAILED,
            (
                f"FACE_{int(candidate.face_id):06d}: par CRISTA/PÉ rejeitado "
                f"pela qualidade geométrica ({geometry.get('reason')})."
            ),
        )

    coverage = float(tracker.get("coverage_ratio", 0.0))
    residual = float(tracker.get("median_rmse_m", profile_bin))
    detector_quality = float(
        np.clip(
            0.62 * coverage
            + 0.38 * math.exp(-residual / max(profile_bin, 0.08)),
            0.0,
            1.0,
        )
    )
    pair_quality = float(geometry.get("quality_score", 0.0))
    quality = float(
        np.clip(
            0.55 * detector_quality + 0.45 * pair_quality,
            0.0,
            0.995,
        )
    )
    median_width = max(
        float(tracker.get("median_width_m", local_width)),
        1e-6,
    )
    median_relief = max(
        float(tracker.get("median_relief_m", 0.0)),
        0.0,
    )
    face_slope_deg = float(
        math.degrees(math.atan2(median_relief, median_width))
    )

    def payload(kind: str, xyz: np.ndarray) -> dict[str, Any]:
        length_2d = _line_length_2d(xyz)
        length_3d = float(
            np.linalg.norm(np.diff(xyz[:, :3], axis=0), axis=1).sum()
            if len(xyz) >= 2
            else 0.0
        )
        return {
            "face_id": int(candidate.face_id),
            "type": kind,
            "xyz": xyz,
            "length_m": length_2d,
            "length_2d_m": length_2d,
            "length_3d_m": length_3d,
            "confidence": quality,
            "quality_score": quality,
            # Keep the public source contract stable for Vector Document/UI.
            "source": "V2_PROFILE_EDGE",
            "status": "AUTO_VALIDATED",
            "review_state": "APPROVED_AUTO",
            "edge_profile": "three-plane-cross-section",
            "edge_confidence": detector_quality,
            "edge_snap_ratio": coverage,
            "edge_refinement_ratio": coverage,
            "edge_face_slope_deg": face_slope_deg,
            "edge_grid_resolution_m": float(
                tracker.get("profile_bin_m", profile_bin)
            ),
        }

    elapsed = float(perf_counter() - started)
    lines = [
        payload("CREST", crest),
        payload("TOE", toe),
    ]
    return lines, {
        "face_id": int(candidate.face_id),
        "status": "SUCCESS",
        "reason": V2Reason.SUCCESS.value,
        "detector": "SECTION_PROFILE_THREE_PLANE_TRACKER",
        "roi_points": int(len(raw)),
        "geometry": geometry,
        "local_face": tracker,
        "local_width_median_m": float(local_width),
        "crest_agreement": crest_agreement,
        "toe_agreement": toe_agreement,
        "crest": {
            "confidence": detector_quality,
            "snap_ratio": coverage,
            "refinement_ratio": coverage,
            "face_slope_deg": face_slope_deg,
            "vertices": int(len(crest)),
        },
        "toe": {
            "confidence": detector_quality,
            "snap_ratio": coverage,
            "refinement_ratio": coverage,
            "face_slope_deg": face_slope_deg,
            "vertices": int(len(toe)),
        },
        "v2_metrics": {
            "profile_edge_quality": quality,
            "width_median_m": geometry.get("width_median_m"),
            "positive_relief_ratio": geometry.get("positive_relief_ratio"),
            "local_width_median_m": float(local_width),
            "crest_agreement_p95_m": crest_agreement.get("p95_m"),
            "toe_agreement_p95_m": toe_agreement.get("p95_m"),
            "section_coverage_ratio": coverage,
            "section_median_rmse_m": residual,
            "section_stations_total": tracker.get("stations_total"),
            "section_stations_accepted": tracker.get("stations_accepted"),
        },
        "v2_timing_s": {"refinement": elapsed},
        "elapsed_s": elapsed,
        "elapsed_ms": elapsed * 1000.0,
    }
