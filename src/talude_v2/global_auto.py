from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Callable

import numpy as np
from scipy.spatial import cKDTree

from talude_v1.config import ExtractConfig
from talude_v1.engine import _filter_stream_chunk, _select_points, extract as extract_v1
from talude_v1.io import (
    inspect_point_cloud,
    iter_point_chunks,
    load_point_cloud,
    save_dxf,
    save_geojson,
    save_vertices_csv,
)

from .engine import V2Config, extract_face_raw_tin
from .reasons import V2DetectionError, V2Reason


BASELINE_SHA = "bfc21944eb337b57f2d8c31905cb7a0e85b3ed7f"


@dataclass(slots=True)
class GlobalFaceCandidate:
    face_id: int
    crest: np.ndarray
    toe: np.ndarray
    centerline: np.ndarray
    seed_xyz: np.ndarray
    corridor_radius_m: float
    baseline_width_median: float
    baseline_width_p90: float


class _PriorityReservoir:
    """Deterministic bounded sample without favouring early LAS chunks."""

    def __init__(self, max_points: int, seed: int) -> None:
        self.max_points = max(100, int(max_points))
        self.rng = np.random.default_rng(int(seed))
        # Keep absolute survey coordinates in float64. UTM-scale northings in
        # float32 can lose decimetres, which is unacceptable for breaklines.
        self.points = np.empty((0, 3), dtype=np.float64)
        self.priority = np.empty((0,), dtype=np.float32)
        self.seen = 0

    def add(self, points: np.ndarray) -> None:
        pts = np.asarray(points, dtype=np.float64)
        if len(pts) == 0:
            return

        self.seen += int(len(pts))
        pri = self.rng.random(len(pts), dtype=np.float32)

        if len(self.points) == 0:
            combined_points = pts
            combined_pri = pri
        else:
            combined_points = np.concatenate((self.points, pts), axis=0)
            combined_pri = np.concatenate((self.priority, pri), axis=0)

        if len(combined_points) > self.max_points:
            keep = np.argpartition(
                combined_pri,
                len(combined_pri) - self.max_points,
            )[-self.max_points :]
            combined_points = combined_points[keep]
            combined_pri = combined_pri[keep]

        self.points = combined_points
        self.priority = combined_pri

    def array(self) -> np.ndarray:
        return np.asarray(self.points, dtype=np.float64)


def _check_cancel(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise V2DetectionError(V2Reason.CANCELLED, "Processamento V2 cancelado.")


def _line_length_2d(xyz: np.ndarray) -> float:
    pts = np.asarray(xyz, dtype=np.float64)
    if len(pts) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1).sum())


def _resample_count(xyz: np.ndarray, count: int) -> np.ndarray:
    pts = np.asarray(xyz, dtype=np.float64)
    if len(pts) <= 1:
        return pts.copy()

    step = np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1)
    keep = np.r_[True, step > 1e-9]
    pts = pts[keep]
    if len(pts) <= 1:
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


def _resample_spacing(xyz: np.ndarray, spacing_m: float) -> np.ndarray:
    pts = np.asarray(xyz, dtype=np.float64)
    total = _line_length_2d(pts)
    if len(pts) < 2 or total <= 1e-9:
        return pts.copy()
    count = max(2, int(math.ceil(total / max(float(spacing_m), 0.25))) + 1)
    return _resample_count(pts, count)


def _normalize_face_pair(
    face_id: int,
    crest: np.ndarray,
    toe: np.ndarray,
    *,
    corridor_margin_m: float,
) -> GlobalFaceCandidate:
    crest = np.asarray(crest, dtype=np.float64)
    toe = np.asarray(toe, dtype=np.float64)

    if len(crest) < 2 or len(toe) < 2:
        raise V2DetectionError(
            V2Reason.BOUNDARY_NOT_FOUND,
            f"FACE_{face_id:06d} não possui CRISTA/PÉ baseline suficientes.",
        )

    direct = (
        float(np.linalg.norm(crest[0, :2] - toe[0, :2]))
        + float(np.linalg.norm(crest[-1, :2] - toe[-1, :2]))
    )
    reverse = (
        float(np.linalg.norm(crest[0, :2] - toe[-1, :2]))
        + float(np.linalg.norm(crest[-1, :2] - toe[0, :2]))
    )
    if reverse < direct:
        toe = toe[::-1].copy()

    longest = max(_line_length_2d(crest), _line_length_2d(toe))
    pair_count = int(np.clip(math.ceil(longest / 2.0) + 1, 12, 180))
    crest_r = _resample_count(crest, pair_count)
    toe_r = _resample_count(toe, pair_count)

    center = 0.5 * (crest_r + toe_r)
    widths = np.linalg.norm(crest_r[:, :2] - toe_r[:, :2], axis=1)
    width_med = float(np.median(widths)) if len(widths) else 0.0
    width_p90 = float(np.percentile(widths, 90.0)) if len(widths) else width_med

    radius = float(
        np.clip(
            0.58 * max(width_p90, width_med) + float(corridor_margin_m),
            2.75,
            35.0,
        )
    )
    seed = center[len(center) // 2].copy()

    return GlobalFaceCandidate(
        face_id=int(face_id),
        crest=crest,
        toe=toe,
        centerline=center,
        seed_xyz=seed,
        corridor_radius_m=radius,
        baseline_width_median=width_med,
        baseline_width_p90=width_p90,
    )


def _load_baseline_lines(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict] = []
    for feature in payload.get("features", []):
        props = dict(feature.get("properties") or {})
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        xyz = np.asarray(coords, dtype=np.float64)
        if xyz.ndim != 2 or xyz.shape[1] < 3:
            continue
        face_id = props.get("face_id")
        try:
            face_id = int(face_id)
        except Exception:
            continue
        out.append(
            {
                **props,
                "face_id": face_id,
                "type": str(props.get("type") or "").upper(),
                "xyz": xyz[:, :3],
            }
        )
    return out


def _build_face_candidates(
    baseline_lines: list[dict],
    *,
    corridor_margin_m: float,
) -> tuple[list[GlobalFaceCandidate], list[dict]]:
    grouped: dict[int, dict[str, dict]] = {}
    for line in baseline_lines:
        face_id = int(line["face_id"])
        grouped.setdefault(face_id, {})[str(line["type"]).upper()] = line

    candidates: list[GlobalFaceCandidate] = []
    unpaired: list[dict] = []

    for face_id in sorted(grouped):
        pair = grouped[face_id]
        crest = pair.get("CREST")
        toe = pair.get("TOE")
        if crest is None or toe is None:
            unpaired.extend(pair.values())
            continue
        candidates.append(
            _normalize_face_pair(
                face_id,
                np.asarray(crest["xyz"], dtype=np.float64),
                np.asarray(toe["xyz"], dtype=np.float64),
                corridor_margin_m=corridor_margin_m,
            )
        )

    return candidates, unpaired


def _candidate_spatial_index(
    candidates: list[GlobalFaceCandidate],
    *,
    origin_xy: tuple[float, float],
    tile_size_m: float,
    nx_tiles: int,
    ny_tiles: int,
) -> dict[int, tuple[int, ...]]:
    x0, y0 = origin_xy
    mapping: dict[int, set[int]] = {}

    for index, candidate in enumerate(candidates):
        stations = _resample_spacing(
            candidate.centerline,
            max(4.0, float(tile_size_m) * 0.45),
        )
        r = candidate.corridor_radius_m

        for x, y, _ in stations:
            ix0 = max(0, int(math.floor((x - r - x0) / tile_size_m)))
            ix1 = min(nx_tiles - 1, int(math.floor((x + r - x0) / tile_size_m)))
            iy0 = max(0, int(math.floor((y - r - y0) / tile_size_m)))
            iy1 = min(ny_tiles - 1, int(math.floor((y + r - y0) / tile_size_m)))
            for iy in range(iy0, iy1 + 1):
                base = iy * nx_tiles
                for ix in range(ix0, ix1 + 1):
                    mapping.setdefault(base + ix, set()).add(index)

    return {key: tuple(sorted(value)) for key, value in mapping.items()}


def _collect_roi_points_stream(
    source: Path,
    cfg: ExtractConfig,
    candidates: list[GlobalFaceCandidate],
    *,
    max_roi_points: int,
    progress: Callable[[float, str], None] | None,
    cancel_check: Callable[[], bool] | None,
) -> tuple[list[np.ndarray], dict]:
    info = inspect_point_cloud(source)
    tile_size = 20.0
    nx_tiles = max(1, int(math.floor(max(info.width, tile_size) / tile_size)) + 1)
    ny_tiles = max(1, int(math.floor(max(info.height, tile_size) / tile_size)) + 1)
    x0, y0 = float(info.mins[0]), float(info.mins[1])

    spatial = _candidate_spatial_index(
        candidates,
        origin_xy=(x0, y0),
        tile_size_m=tile_size,
        nx_tiles=nx_tiles,
        ny_tiles=ny_tiles,
    )
    center_trees = [cKDTree(candidate.centerline[:, :2]) for candidate in candidates]
    reservoirs = [
        _PriorityReservoir(max_roi_points, seed=1701 + candidate.face_id * 7919)
        for candidate in candidates
    ]

    raw_seen = 0
    selected_seen = 0
    routed_seen = 0

    for xyz, classification in iter_point_chunks(source, chunk_size=750_000):
        _check_cancel(cancel_check)
        raw_seen += len(xyz)
        pts = _filter_stream_chunk(xyz, classification, cfg)
        selected_seen += len(pts)
        if len(pts):
            ix = np.clip(
                np.floor((pts[:, 0] - x0) / tile_size).astype(np.int64),
                0,
                nx_tiles - 1,
            )
            iy = np.clip(
                np.floor((pts[:, 1] - y0) / tile_size).astype(np.int64),
                0,
                ny_tiles - 1,
            )
            keys = iy * nx_tiles + ix
            order = np.argsort(keys, kind="mergesort")
            sorted_keys = keys[order]
            unique, starts, counts = np.unique(
                sorted_keys,
                return_index=True,
                return_counts=True,
            )

            for key, start, count in zip(unique, starts, counts):
                candidate_ids = spatial.get(int(key))
                if not candidate_ids:
                    continue
                bucket = pts[order[int(start) : int(start + count)]]
                for candidate_index in candidate_ids:
                    candidate = candidates[candidate_index]
                    dist, _ = center_trees[candidate_index].query(bucket[:, :2], k=1)
                    keep = np.asarray(dist) <= candidate.corridor_radius_m
                    if np.any(keep):
                        selected_bucket = bucket[keep]
                        routed_seen += int(len(selected_bucket))
                        reservoirs[candidate_index].add(selected_bucket)

        if progress is not None and info.point_count:
            frac = min(raw_seen / info.point_count, 1.0)
            progress(
                40.0 + 27.0 * frac,
                (
                    f"AUTO V2 · ROI RAW Ground · "
                    f"{raw_seen:,}/{info.point_count:,} pontos"
                ),
            )

    arrays = [reservoir.array() for reservoir in reservoirs]
    return arrays, {
        "points_total": int(info.point_count),
        "points_selected": int(selected_seen),
        "points_routed_with_overlap": int(routed_seen),
        "roi_max_points_per_face": int(max_roi_points),
        "roi_seen_total": int(sum(res.seen for res in reservoirs)),
        "roi_kept_total": int(sum(len(res.points) for res in reservoirs)),
        "spatial_index_cells": int(len(spatial)),
        "spatial_tile_size_m": float(tile_size),
        "crs_wkt": info.crs_wkt,
    }


def _collect_roi_points_memory(
    source: Path,
    cfg: ExtractConfig,
    candidates: list[GlobalFaceCandidate],
    *,
    max_roi_points: int,
    progress: Callable[[float, str], None] | None,
    cancel_check: Callable[[], bool] | None,
) -> tuple[list[np.ndarray], dict]:
    _check_cancel(cancel_check)
    cloud = load_point_cloud(source)
    selected, _ = _select_points(cloud, cfg)
    arrays: list[np.ndarray] = []

    for index, candidate in enumerate(candidates):
        _check_cancel(cancel_check)
        tree = cKDTree(candidate.centerline[:, :2])
        dist, _ = tree.query(selected[:, :2], k=1)
        pts = selected[np.asarray(dist) <= candidate.corridor_radius_m]
        reservoir = _PriorityReservoir(
            max_roi_points,
            seed=1701 + candidate.face_id * 7919,
        )
        reservoir.add(pts)
        arrays.append(reservoir.array())
        if progress is not None and candidates:
            progress(
                40.0 + 27.0 * (index + 1) / len(candidates),
                f"AUTO V2 · ROI {index + 1}/{len(candidates)}",
            )

    return arrays, {
        "points_total": int(len(cloud.xyz)),
        "points_selected": int(len(selected)),
        "points_routed_with_overlap": int(sum(len(a) for a in arrays)),
        "roi_max_points_per_face": int(max_roi_points),
        "roi_seen_total": int(sum(len(a) for a in arrays)),
        "roi_kept_total": int(sum(len(a) for a in arrays)),
        "spatial_index_cells": 0,
        "spatial_tile_size_m": None,
        "crs_wkt": cloud.crs_wkt,
    }


def _baseline_line_payload(line: dict, source: str) -> dict:
    xyz = np.asarray(line["xyz"], dtype=np.float64)
    return {
        "face_id": int(line["face_id"]),
        "type": str(line["type"]).upper(),
        "xyz": xyz,
        "length_m": float(line.get("length_m") or _line_length_2d(xyz)),
        "length_2d_m": float(line.get("length_m") or _line_length_2d(xyz)),
        "length_3d_m": float(
            np.linalg.norm(np.diff(xyz, axis=0), axis=1).sum()
            if len(xyz) >= 2
            else 0.0
        ),
        "confidence": float(line.get("confidence") or 0.0),
        "quality_score": float(line.get("confidence") or 0.0),
        "slope_mean_deg": float(line.get("slope_mean_deg") or 0.0),
        "median_rmse": (
            float(line["median_rmse"])
            if line.get("median_rmse") is not None
            else None
        ),
        "source": source,
        "status": "AUTO",
    }


def _agreement(v2_xyz: np.ndarray, baseline_xyz: np.ndarray) -> dict[str, float]:
    if len(v2_xyz) < 2 or len(baseline_xyz) < 2:
        return {"median_m": float("inf"), "p95_m": float("inf"), "max_m": float("inf")}
    tree = cKDTree(np.asarray(baseline_xyz, dtype=np.float64)[:, :2])
    dist, _ = tree.query(np.asarray(v2_xyz, dtype=np.float64)[:, :2], k=1)
    dist = np.asarray(dist, dtype=np.float64)
    return {
        "median_m": float(np.median(dist)),
        "p95_m": float(np.percentile(dist, 95.0)),
        "max_m": float(np.max(dist)),
    }


def refine_face_candidate_v2(
    candidate: GlobalFaceCandidate,
    raw_points: np.ndarray,
    *,
    v2_config: V2Config,
    min_line_length_m: float,
) -> tuple[list[dict], dict]:
    started = perf_counter()
    raw = np.asarray(raw_points, dtype=np.float64)
    if len(raw) < 100:
        raise V2DetectionError(
            V2Reason.LOW_GROUND_SUPPORT,
            f"FACE_{candidate.face_id:06d}: apenas {len(raw)} pontos na ROI.",
        )

    result = extract_face_raw_tin(
        raw,
        candidate.seed_xyz,
        config=v2_config,
    )

    out: list[dict] = []
    agreement: dict[str, dict[str, float]] = {}
    for kind, baseline_xyz in (
        ("CREST", candidate.crest),
        ("TOE", candidate.toe),
    ):
        src = result["crest"] if kind == "CREST" else result["toe"]
        xyz = np.asarray(src["vertices"], dtype=np.float64)
        length_2d = float(src.get("length_2d_m", src.get("length_m", 0.0)))
        if len(xyz) < 2 or length_2d < float(min_line_length_m):
            raise V2DetectionError(
                V2Reason.LINE_TOO_SHORT,
                f"FACE_{candidate.face_id:06d}: {kind} V2 demasiado curta.",
            )

        metrics = _agreement(xyz, baseline_xyz)
        agreement[kind] = metrics

        # Regression guard: V2 may improve the exact edge location, but it may
        # not silently jump to a neighbouring terrace/talude.
        median_limit = max(2.50, candidate.corridor_radius_m * 0.32)
        p95_limit = max(5.00, candidate.corridor_radius_m * 0.58)
        if metrics["median_m"] > median_limit or metrics["p95_m"] > p95_limit:
            raise V2DetectionError(
                V2Reason.REFINEMENT_FAILED,
                (
                    f"FACE_{candidate.face_id:06d}: {kind} divergiu da região "
                    f"baseline (median={metrics['median_m']:.2f} m, "
                    f"P95={metrics['p95_m']:.2f} m)."
                ),
            )

        out.append(
            {
                "face_id": int(candidate.face_id),
                "type": kind,
                "xyz": xyz,
                "length_m": length_2d,
                "length_2d_m": length_2d,
                "length_3d_m": float(src.get("length_3d_m", length_2d)),
                "confidence": float(src.get("quality_score", src.get("confidence", 0.0))),
                "quality_score": float(src.get("quality_score", src.get("confidence", 0.0))),
                "median_rmse": src.get("plane_rmse_median"),
                "source": "V2_RAW_TIN",
                "status": "AUTO",
                "v2_reason": result.get("reason", V2Reason.SUCCESS.value),
                "v2_refine_ratio": float(result.get("refine_ratio", 0.0)),
                "v2_local_normal_coherence": float(
                    result.get("local_normal_coherence", 0.0)
                ),
            }
        )

    return out, {
        "face_id": int(candidate.face_id),
        "status": "SUCCESS",
        "reason": V2Reason.SUCCESS.value,
        "roi_points": int(len(raw)),
        "corridor_radius_m": float(candidate.corridor_radius_m),
        "baseline_width_median": float(candidate.baseline_width_median),
        "baseline_width_p90": float(candidate.baseline_width_p90),
        "agreement": agreement,
        "v2_metrics": result.get("metrics", {}),
        "elapsed_s": float(perf_counter() - started),
    }


def _fallback_pair(candidate: GlobalFaceCandidate, baseline_by_face: dict[int, dict[str, dict]]) -> list[dict]:
    pair = baseline_by_face[candidate.face_id]
    return [
        _baseline_line_payload(pair["CREST"], "BASELINE_1_1_7_FALLBACK"),
        _baseline_line_payload(pair["TOE"], "BASELINE_1_1_7_FALLBACK"),
    ]


def _jsonable_record(record: dict) -> dict:
    out = {}
    for key, value in record.items():
        if isinstance(value, np.generic):
            out[key] = value.item()
        elif isinstance(value, np.ndarray):
            out[key] = value.tolist()
        elif isinstance(value, dict):
            out[key] = _jsonable_record(value)
        elif isinstance(value, (list, tuple)):
            out[key] = [
                _jsonable_record(v) if isinstance(v, dict) else (
                    v.item() if isinstance(v, np.generic) else v
                )
                for v in value
            ]
        else:
            out[key] = value
    return out


def run_auto_global_v2(
    input_path: str | Path,
    output_dir: str | Path,
    baseline_config: ExtractConfig | None = None,
    v2_config: V2Config | None = None,
    *,
    progress: Callable[[float, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    max_roi_points: int = 45_000,
) -> dict:
    """AUTO GLOBAL V2.

    Discovery remains the proven 1.1.7 detector. Every discovered face receives
    an automatic RAW-Ground corridor, local TIN V2 refinement and a regression
    guard. When V2 cannot safely improve a face, the baseline pair is preserved
    instead of losing that talude.
    """
    started = perf_counter()
    source = Path(input_path).expanduser().resolve()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    debug_dir = output / "debug"
    debug_dir.mkdir(exist_ok=True)
    baseline_dir = output / "_baseline_candidates"
    baseline_dir.mkdir(exist_ok=True)

    cfg = baseline_config or ExtractConfig()
    v2_cfg = v2_config or V2Config()

    _check_cancel(cancel_check)
    if progress is not None:
        progress(2.0, "AUTO V2 · a descobrir faces com baseline 1.1.7…")

    def baseline_progress(value: float, message: str) -> None:
        _check_cancel(cancel_check)
        if progress is not None:
            progress(
                3.0 + 0.36 * max(0.0, min(float(value), 100.0)),
                "AUTO V2 · descoberta global · " + str(message),
            )

    baseline_report = extract_v1(
        source,
        baseline_dir,
        cfg,
        progress=baseline_progress,
    )
    _check_cancel(cancel_check)

    baseline_lines = _load_baseline_lines(baseline_dir / "talude_breaklines.geojson")
    candidates, unpaired = _build_face_candidates(
        baseline_lines,
        corridor_margin_m=max(3.0, v2_cfg.patch_cross_m + 1.25),
    )
    baseline_by_face: dict[int, dict[str, dict]] = {}
    for line in baseline_lines:
        baseline_by_face.setdefault(int(line["face_id"]), {})[str(line["type"]).upper()] = line

    if progress is not None:
        progress(
            39.5,
            (
                f"AUTO V2 · {len(candidates)} faces candidatas · "
                "a recolher RAW Ground por ROI…"
            ),
        )

    if not candidates:
        # Do not scan a 200M+ point cloud a second time when discovery found
        # nothing to refine. Keep the empty/unpaired baseline result and make
        # the reason explicit in the report.
        if source.suffix.lower() in {".las", ".laz"}:
            info = inspect_point_cloud(source)
            crs_wkt = info.crs_wkt
            points_total = int(info.point_count)
        else:
            cloud_info = load_point_cloud(source)
            crs_wkt = cloud_info.crs_wkt
            points_total = int(len(cloud_info.xyz))
        roi_points = []
        roi_stats = {
            "points_total": points_total,
            "points_selected": 0,
            "points_routed_with_overlap": 0,
            "roi_max_points_per_face": int(max_roi_points),
            "roi_seen_total": 0,
            "roi_kept_total": 0,
            "spatial_index_cells": 0,
            "spatial_tile_size_m": None,
            "crs_wkt": crs_wkt,
        }
    elif source.suffix.lower() in {".las", ".laz"}:
        roi_points, roi_stats = _collect_roi_points_stream(
            source,
            cfg,
            candidates,
            max_roi_points=max_roi_points,
            progress=progress,
            cancel_check=cancel_check,
        )
    else:
        roi_points, roi_stats = _collect_roi_points_memory(
            source,
            cfg,
            candidates,
            max_roi_points=max_roi_points,
            progress=progress,
            cancel_check=cancel_check,
        )

    final_lines: list[dict] = []
    face_records: list[dict] = []
    reason_counts: dict[str, int] = {}
    v2_success = 0
    baseline_fallback = 0

    for index, (candidate, points) in enumerate(zip(candidates, roi_points), start=1):
        _check_cancel(cancel_check)
        try:
            lines, record = refine_face_candidate_v2(
                candidate,
                points,
                v2_config=v2_cfg,
                min_line_length_m=cfg.min_line_length_m,
            )
            final_lines.extend(lines)
            face_records.append(record)
            v2_success += 1
            reason = V2Reason.SUCCESS.value
        except Exception as exc:
            if isinstance(exc, V2DetectionError) and exc.reason == V2Reason.CANCELLED:
                raise

            if isinstance(exc, V2DetectionError):
                reason = exc.reason.value
                message = exc.message
            else:
                reason = V2Reason.INTERNAL_ERROR.value
                message = f"{type(exc).__name__}: {exc}"

            final_lines.extend(_fallback_pair(candidate, baseline_by_face))
            face_records.append(
                {
                    "face_id": int(candidate.face_id),
                    "status": "BASELINE_FALLBACK",
                    "reason": reason,
                    "message": message,
                    "roi_points": int(len(points)),
                    "corridor_radius_m": float(candidate.corridor_radius_m),
                }
            )
            baseline_fallback += 1

        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if progress is not None and candidates:
            progress(
                67.0 + 29.0 * index / len(candidates),
                (
                    f"AUTO V2 · faces {index}/{len(candidates)} · "
                    f"V2 {v2_success} · fallback {baseline_fallback}"
                ),
            )

    # Never discard an unpaired baseline result: completeness beats a silent
    # regression. These are logged separately for later diagnosis.
    for line in unpaired:
        final_lines.append(_baseline_line_payload(line, "BASELINE_1_1_7_UNPAIRED"))

    final_lines = [
        line
        for line in final_lines
        if len(np.asarray(line["xyz"])) >= 2
        and float(line.get("length_m", 0.0)) >= cfg.min_line_length_m
    ]
    final_lines.sort(key=lambda item: (int(item["face_id"]), str(item["type"])))
    for line_id, line in enumerate(final_lines, start=1):
        line["line_id"] = int(line_id)

    _check_cancel(cancel_check)
    if progress is not None:
        progress(97.0, "AUTO V2 · a exportar resultado global 3D…")

    crs_wkt = roi_stats.get("crs_wkt")
    save_geojson(output / "talude_breaklines.geojson", final_lines, crs_wkt)
    save_vertices_csv(output / "talude_vertices.csv", final_lines)
    save_dxf(output / "talude_breaklines.dxf", final_lines)

    with (debug_dir / "v2_faces.jsonl").open("w", encoding="utf-8") as fh:
        for record in face_records:
            fh.write(json.dumps(_jsonable_record(record), ensure_ascii=False) + "\n")

    faces_final = len({int(line["face_id"]) for line in final_lines})
    report = {
        "engine": "BREAKLINE_ENGINE_V2_GLOBAL_HYBRID",
        "experimental": True,
        "baseline_branch": "baseline-1.1.7-refine",
        "baseline_sha": BASELINE_SHA,
        "input": str(source),
        "faces_detected": int(faces_final),
        "baseline_faces_detected": int(baseline_report.get("faces_detected", 0)),
        "candidate_faces": int(len(candidates)),
        "v2_success_faces": int(v2_success),
        "baseline_fallback_faces": int(baseline_fallback),
        "unpaired_baseline_lines": int(len(unpaired)),
        "crest_lines": int(sum(1 for line in final_lines if line["type"] == "CREST")),
        "toe_lines": int(sum(1 for line in final_lines if line["type"] == "TOE")),
        "reason_counts": reason_counts,
        "roi": {k: v for k, v in roi_stats.items() if k != "crs_wkt"},
        "baseline_report": {
            key: value
            for key, value in baseline_report.items()
            if key not in {"lines"}
        },
        "v2_config": {
            key: getattr(v2_cfg, key)
            for key in v2_cfg.__dataclass_fields__
        },
        "baseline_config": cfg.to_dict(),
        "elapsed_s": float(perf_counter() - started),
        "lines": [
            {
                key: value
                for key, value in line.items()
                if key not in {"xyz", "vertex_rmse"}
            }
            for line in final_lines
        ],
    }

    (output / "talude_report.json").write_text(
        json.dumps(_jsonable_record(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (debug_dir / "summary.json").write_text(
        json.dumps(
            _jsonable_record(
                {
                    "candidate_faces": len(candidates),
                    "v2_success_faces": v2_success,
                    "baseline_fallback_faces": baseline_fallback,
                    "reason_counts": reason_counts,
                    "roi": {k: v for k, v in roi_stats.items() if k != "crs_wkt"},
                }
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if progress is not None:
        progress(
            100.0,
            (
                f"AUTO V2 concluído · {faces_final} faces · "
                f"V2 {v2_success} · fallback {baseline_fallback}"
            ),
        )

    return report
