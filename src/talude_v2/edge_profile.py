from __future__ import annotations

import math
from time import perf_counter
from typing import Any

import numpy as np

from core.terrain_face import extract_terrain_face_edge

from .reasons import V2DetectionError, V2Reason


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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract real upper/lower slope breaks using cross-slope terrain profiles."""
    started = perf_counter()
    raw = np.asarray(raw_points, dtype=np.float64)
    raw = raw[np.all(np.isfinite(raw[:, :3]), axis=1)] if raw.ndim == 2 else raw
    if raw.ndim != 2 or raw.shape[1] < 3 or len(raw) < 500:
        raise V2DetectionError(
            V2Reason.LOW_GROUND_SUPPORT,
            f"FACE_{int(candidate.face_id):06d}: poucos pontos Ground para perfil geométrico.",
        )

    crest_seed = _nearest_reference_seed(candidate.crest, candidate.seed_xyz)
    toe_seed = _nearest_reference_seed(candidate.toe, candidate.seed_xyz)
    resolution = float(np.clip(grid_resolution_m, 0.16, 0.30))

    try:
        crest_result = extract_terrain_face_edge(
            raw[:, :3],
            crest_seed,
            profile="ridge",
            grid_resolution=resolution,
        )
    except Exception as exc:
        raise V2DetectionError(
            V2Reason.CREST_NOT_FOUND,
            f"FACE_{int(candidate.face_id):06d}: crista por perfil falhou: {exc}",
        ) from exc

    try:
        toe_result = extract_terrain_face_edge(
            raw[:, :3],
            toe_seed,
            profile="toe",
            grid_resolution=resolution,
        )
    except Exception as exc:
        raise V2DetectionError(
            V2Reason.TOE_NOT_FOUND,
            f"FACE_{int(candidate.face_id):06d}: pé por perfil falhou: {exc}",
        ) from exc

    crest = _orient_like_reference(
        np.asarray(crest_result.vertices, dtype=np.float64),
        np.asarray(candidate.crest, dtype=np.float64),
    )
    toe = _orient_like_reference(
        np.asarray(toe_result.vertices, dtype=np.float64),
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

    detector_quality = float(
        np.clip(
            0.5 * float(crest_result.confidence)
            + 0.5 * float(toe_result.confidence),
            0.0,
            1.0,
        )
    )
    pair_quality = float(geometry.get("quality_score", 0.0))
    quality = float(np.clip(0.58 * detector_quality + 0.42 * pair_quality, 0.0, 0.995))

    def payload(kind: str, xyz: np.ndarray, edge_result) -> dict[str, Any]:
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
            "source": "V2_PROFILE_EDGE",
            "status": "AUTO_VALIDATED",
            "review_state": "APPROVED_AUTO",
            "edge_profile": "ridge" if kind == "CREST" else "toe",
            "edge_confidence": float(edge_result.confidence),
            "edge_snap_ratio": float(edge_result.snap_ratio),
            "edge_refinement_ratio": float(edge_result.refinement_ratio),
            "edge_face_slope_deg": float(edge_result.face_slope_deg),
            "edge_grid_resolution_m": float(edge_result.grid_resolution),
        }

    elapsed = float(perf_counter() - started)
    lines = [
        payload("CREST", crest, crest_result),
        payload("TOE", toe, toe_result),
    ]
    return lines, {
        "face_id": int(candidate.face_id),
        "status": "SUCCESS",
        "reason": V2Reason.SUCCESS.value,
        "detector": "PROFILE_EDGE_FLAT_FACE_FLAT",
        "roi_points": int(len(raw)),
        "geometry": geometry,
        "crest": {
            "confidence": float(crest_result.confidence),
            "snap_ratio": float(crest_result.snap_ratio),
            "refinement_ratio": float(crest_result.refinement_ratio),
            "face_slope_deg": float(crest_result.face_slope_deg),
            "vertices": int(len(crest)),
        },
        "toe": {
            "confidence": float(toe_result.confidence),
            "snap_ratio": float(toe_result.snap_ratio),
            "refinement_ratio": float(toe_result.refinement_ratio),
            "face_slope_deg": float(toe_result.face_slope_deg),
            "vertices": int(len(toe)),
        },
        "v2_metrics": {
            "profile_edge_quality": quality,
            "width_median_m": geometry.get("width_median_m"),
            "positive_relief_ratio": geometry.get("positive_relief_ratio"),
        },
        "v2_timing_s": {"refinement": elapsed},
        "elapsed_s": elapsed,
        "elapsed_ms": elapsed * 1000.0,
    }
