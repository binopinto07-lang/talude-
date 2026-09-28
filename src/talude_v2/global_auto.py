from __future__ import annotations

import hashlib
import json
import math
import shutil
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

from .engine import V2Config, _resample_polyline
from .edge_profile import extract_profile_edge_pair
from .profiling import add_stage_time, build_performance_profile, merge_stage_times
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
    """Refine one discovered face onto the real crest/toe terrain transitions.

    The 1.1.7 pair is used only as discovery/seed geometry. Published V2 lines
    come from the local terrain-profile edge extractor, which explicitly finds
    the upper and lower flat<->steep transitions of the selected slope face.
    """
    resolution = float(
        np.clip(float(v2_config.target_tin_spacing_m) * 0.60, 0.18, 0.28)
    )
    lines, record = extract_profile_edge_pair(
        np.asarray(raw_points, dtype=np.float64),
        candidate,
        min_line_length_m=float(min_line_length_m),
        grid_resolution_m=resolution,
    )
    record["corridor_radius_m"] = float(candidate.corridor_radius_m)
    record["baseline_width_median"] = float(candidate.baseline_width_median)
    record["baseline_width_p90"] = float(candidate.baseline_width_p90)
    return lines, record


def _fallback_pair(candidate: GlobalFaceCandidate, baseline_by_face: dict[int, dict[str, dict]]) -> list[dict]:
    pair = baseline_by_face[candidate.face_id]
    return [
        _baseline_line_payload(pair["CREST"], "BASELINE_1_1_7_FALLBACK"),
        _baseline_line_payload(pair["TOE"], "BASELINE_1_1_7_FALLBACK"),
    ]


def _review_pair(
    candidate: GlobalFaceCandidate,
    baseline_by_face: dict[int, dict[str, dict]],
    *,
    reason: str,
    source: str,
    message: str,
) -> list[dict]:
    """Keep uncertain discovery geometry available for operator review only."""
    pair = _fallback_pair(candidate, baseline_by_face)
    for line in pair:
        line["source"] = str(source)
        line["status"] = "REVIEW_REQUIRED"
        line["review_state"] = "PENDING"
        line["review_reason"] = str(reason)
        line["review_message"] = str(message)
        line["quality_score"] = min(float(line.get("quality_score", 0.0) or 0.0), 0.49)
        line["confidence"] = min(float(line.get("confidence", 0.0) or 0.0), 0.49)
    return pair


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


def _baseline_cache_dir(
    source: Path,
    output: Path,
    cfg: ExtractConfig,
) -> Path:
    stat = source.stat()
    payload = {
        "source": str(source.resolve()),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "baseline_sha": BASELINE_SHA,
        "config": cfg.to_dict(),
    }
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:20]
    # output = <project>/exports/<run>
    try:
        project_root = output.parents[1]
    except IndexError:
        project_root = output.parent
    return project_root / "cache" / "v1_baseline" / digest


def _load_baseline_cache(cache_dir: Path, baseline_dir: Path) -> dict | None:
    geo = cache_dir / "talude_breaklines.geojson"
    report = cache_dir / "talude_report.json"
    if not geo.exists() or not report.exists():
        return None
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
        baseline_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(geo, baseline_dir / geo.name)
        shutil.copy2(report, baseline_dir / report.name)
        return payload
    except Exception:
        return None


def _save_baseline_cache(cache_dir: Path, baseline_dir: Path) -> None:
    geo = baseline_dir / "talude_breaklines.geojson"
    report = baseline_dir / "talude_report.json"
    if not geo.exists() or not report.exists():
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    for source_file in (geo, report):
        target = cache_dir / source_file.name
        temp = cache_dir / (source_file.name + ".tmp")
        shutil.copy2(source_file, temp)
        temp.replace(target)


def _performance_candidate_score(candidate: GlobalFaceCandidate) -> tuple[float, float, int]:
    """Rank faces for selective V2 refinement without changing baseline geometry.

    Lower score is better. The score favours coherent, moderate-width faces
    where PROFILE-EDGE has the highest chance of improving the baseline rather than
    wasting minutes on geometrically ambiguous faces that would later fallback.
    """
    crest_len = _line_length_2d(np.asarray(candidate.crest, dtype=np.float64))
    toe_len = _line_length_2d(np.asarray(candidate.toe, dtype=np.float64))
    length = max(crest_len, toe_len)
    width = max(float(candidate.baseline_width_median), 0.10)
    width_ratio = float(candidate.baseline_width_p90) / width
    width_penalty = abs(width - 5.0) * 0.10
    coherence_penalty = max(0.0, width_ratio - 1.0) * 3.0
    length_penalty = max(0.0, length - 90.0) * 0.02
    return (
        float(coherence_penalty + width_penalty + length_penalty),
        float(length),
        int(candidate.face_id),
    )


def _partition_candidates_for_performance(
    candidates: list[GlobalFaceCandidate],
    *,
    mode: str,
    point_count: int | None,
) -> tuple[list[GlobalFaceCandidate], list[GlobalFaceCandidate], dict]:
    normalized = str(mode or "balanced").strip().lower()
    if normalized not in {"fast", "balanced", "precise"}:
        normalized = "balanced"

    if normalized == "fast":
        return [], list(candidates), {
            "mode": normalized,
            "attempt_limit": 0,
            "attempted_faces": 0,
            "skipped_faces": len(candidates),
            "selection": "BASELINE_ONLY",
        }

    if normalized == "precise":
        return list(candidates), [], {
            "mode": normalized,
            "attempt_limit": len(candidates),
            "attempted_faces": len(candidates),
            "skipped_faces": 0,
            "selection": "ALL_FACES",
        }

    # BALANCED now uses the lightweight terrain-profile edge detector rather
    # than hundreds of expensive local Delaunay solves. Process every
    # discovered face so quality is not sacrificed merely to satisfy a runtime
    # cap. Ground tiles and per-face fragments remain cached.
    points = int(point_count or 0)
    attempted = list(candidates)
    return attempted, [], {
        "mode": normalized,
        "attempt_limit": int(len(attempted)),
        "attempted_faces": len(attempted),
        "skipped_faces": 0,
        "selection": "ALL_PROFILE_EDGE",
        "point_count": points,
    }


def run_auto_global_v2(
    input_path: str | Path,
    output_dir: str | Path,
    baseline_config: ExtractConfig | None = None,
    v2_config: V2Config | None = None,
    *,
    progress: Callable[[float, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    max_roi_points: int = 45_000,
    performance_mode: str = "balanced",
    profile_run_id: str | None = None,
) -> dict:
    """AUTO GLOBAL V2.

    Discovery remains the proven 1.1.7 detector. Every discovered face receives
    an automatic RAW-Ground corridor and local terrain-profile edge extraction.
    Only geometrically validated crest/toe pairs are published as breaklines;
    uncertain discovery geometry is preserved separately for operator review.
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

    mode = str(performance_mode or "balanced").strip().lower()
    if mode not in {"fast", "balanced", "precise"}:
        mode = "balanced"

    stage_seconds: dict[str, float] = {}
    candidate_started = perf_counter()

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

    baseline_cache = _baseline_cache_dir(source, output, cfg)
    baseline_report = _load_baseline_cache(baseline_cache, baseline_dir)
    baseline_cache_hit = baseline_report is not None

    if baseline_report is None:
        baseline_report = extract_v1(
            source,
            baseline_dir,
            cfg,
            progress=baseline_progress,
        )
        _check_cancel(cancel_check)
        _save_baseline_cache(baseline_cache, baseline_dir)
    elif progress is not None:
        progress(
            39.0,
            "AUTO V2 · cache HIT · descoberta baseline reutilizada",
        )

    baseline_lines = _load_baseline_lines(baseline_dir / "talude_breaklines.geojson")
    candidates, unpaired = _build_face_candidates(
        baseline_lines,
        corridor_margin_m=max(3.0, v2_cfg.patch_cross_m + 1.25),
    )
    baseline_by_face: dict[int, dict[str, dict]] = {}
    for line in baseline_lines:
        baseline_by_face.setdefault(int(line["face_id"]), {})[str(line["type"]).upper()] = line

    source_info = (
        inspect_point_cloud(source)
        if source.suffix.lower() in {".las", ".laz"}
        else None
    )
    refine_candidates, performance_skipped, performance_stats = (
        _partition_candidates_for_performance(
            candidates,
            mode=mode,
            point_count=(int(source_info.point_count) if source_info is not None else None),
        )
    )
    add_stage_time(
        stage_seconds,
        "global_candidate_detection",
        perf_counter() - candidate_started,
    )

    if progress is not None:
        progress(
            39.5,
            (
                f"AUTO V2 {mode.upper()} · {len(candidates)} faces · "
                f"{len(refine_candidates)} para PROFILE-EDGE · "
                f"{len(performance_skipped)} baseline seguro"
            ),
        )

    final_lines: list[dict] = []
    review_lines: list[dict] = []
    face_records: list[dict] = []
    reason_counts: dict[str, int] = {}
    v2_success = 0
    baseline_fallback = 0
    tiled_stats: dict | None = None

    fallback_started = perf_counter()
    for candidate in performance_skipped:
        review_message = (
            "Face não validada geometricamente pelo perfil de desempenho "
            f"{mode.upper()}; mantida apenas para revisão."
        )
        review_lines.extend(
            _review_pair(
                candidate,
                baseline_by_face,
                reason="PERFORMANCE_BASELINE",
                source="BASELINE_1_1_7_REVIEW_PERFORMANCE",
                message=review_message,
            )
        )
        face_records.append(
            {
                "face_id": int(candidate.face_id),
                "status": "REVIEW_REQUIRED",
                "reason": "PERFORMANCE_BASELINE",
                "message": review_message,
                "corridor_radius_m": float(candidate.corridor_radius_m),
            }
        )
        baseline_fallback += 1
    add_stage_time(stage_seconds, "fallback", perf_counter() - fallback_started)

    if performance_skipped:
        reason_counts["PERFORMANCE_BASELINE"] = len(performance_skipped)

    if not candidates:
        # Do not scan a 200M+ point cloud a second time when discovery found
        # nothing to refine.
        if source.suffix.lower() in {".las", ".laz"}:
            info = inspect_point_cloud(source)
            crs_wkt = info.crs_wkt
            points_total = int(info.point_count)
        else:
            cloud_info = load_point_cloud(source)
            crs_wkt = cloud_info.crs_wkt
            points_total = int(len(cloud_info.xyz))
        roi_stats = {
            "mode": "NO_CANDIDATES",
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

    elif mode == "fast":
        # FAST intentionally stops after the proven global detector. It avoids
        # the second 241M-point RAW scan and thousands of local Delaunay solves.
        if source_info is not None:
            crs_wkt = source_info.crs_wkt
            points_total = int(source_info.point_count)
        else:
            cloud_info = load_point_cloud(source)
            crs_wkt = cloud_info.crs_wkt
            points_total = int(len(cloud_info.xyz))
        roi_stats = {
            "mode": "FAST_BASELINE_ONLY",
            "points_total": points_total,
            "points_selected": 0,
            "points_routed_with_overlap": 0,
            "roi_max_points_per_face": 0,
            "roi_seen_total": 0,
            "roi_kept_total": 0,
            "spatial_index_cells": 0,
            "spatial_tile_size_m": None,
            "crs_wkt": crs_wkt,
        }
        tiled_stats = {
            "mode": "FAST_BASELINE_ONLY",
            "performance_mode": mode,
            "candidate_faces": len(candidates),
            "attempted_faces": 0,
            "skipped_faces": len(performance_skipped),
        }
        if progress is not None:
            progress(
                96.0,
                (
                    f"AUTO V2 FAST · {len(candidates)} faces baseline prontas · "
                    "sem segunda passagem PROFILE-EDGE"
                ),
            )

    elif source.suffix.lower() in {".las", ".laz"}:
        # Phase 3: large clouds are processed as core tiles + halo. Ground is
        # streamed once to temporary tile spools, local PROFILE-EDGE fragments are
        # solved independently, then deduplicated/stiched per baseline face.
        from .tiled_auto import process_candidates_tiled

        info = source_info or inspect_point_cloud(source)
        tiled_results, tiled_stats = process_candidates_tiled(
            source,
            cfg,
            refine_candidates,
            v2_cfg,
            output_debug_dir=debug_dir,
            min_line_length_m=cfg.min_line_length_m,
            progress=progress,
            cancel_check=cancel_check,
        )
        tiled_stats = dict(tiled_stats or {})
        tiled_stats["performance"] = performance_stats
        merge_stage_times(
            stage_seconds,
            ((tiled_stats.get("profile") or {}).get("stages_s") or {}),
        )
        spool = dict((tiled_stats or {}).get("spool") or {})
        routed_points = int(
            spool.get("cached_points_available", spool.get("spooled_points", 0))
        )
        roi_stats = {
            "mode": "TILED_HALO_STITCH",
            "points_total": int(info.point_count),
            "points_selected": int(spool.get("selected_seen", routed_points)),
            "points_routed_with_overlap": routed_points,
            "roi_max_points_per_face": int(v2_cfg.max_tin_points),
            "roi_seen_total": routed_points,
            "roi_kept_total": routed_points,
            "spatial_index_cells": int((tiled_stats or {}).get("candidate_tiles", 0)),
            "spatial_tile_size_m": float(v2_cfg.tile_size_m),
            "crs_wkt": info.crs_wkt,
        }

        for index, candidate in enumerate(refine_candidates, start=1):
            _check_cancel(cancel_check)
            face_id = int(candidate.face_id)
            item = tiled_results.get(face_id) or {
                "status": "FAILED",
                "reason": V2Reason.MERGE_FAILED.value,
                "message": "Sem resultado tiled para esta face.",
                "lines": [],
                "record": {
                    "face_id": face_id,
                    "status": "FAILED",
                    "reason": V2Reason.MERGE_FAILED.value,
                },
            }

            if item.get("status") == "SUCCESS" and len(item.get("lines") or []) == 2:
                final_lines.extend(item["lines"])
                face_records.append(item.get("record") or {})
                v2_success += 1
                reason = V2Reason.SUCCESS.value
            else:
                reason = str(item.get("reason") or V2Reason.MERGE_FAILED.value)
                message = str(item.get("message") or "Falha tiled sem detalhe.")
                fallback_one_started = perf_counter()
                review_lines.extend(
                    _review_pair(
                        candidate,
                        baseline_by_face,
                        reason=reason,
                        source="BASELINE_1_1_7_REVIEW_TECHNICAL",
                        message=message,
                    )
                )
                add_stage_time(
                    stage_seconds,
                    "fallback",
                    perf_counter() - fallback_one_started,
                )
                record = dict(item.get("record") or {})
                record.update(
                    {
                        "face_id": face_id,
                        "status": "REVIEW_REQUIRED",
                        "reason": reason,
                        "message": message,
                        "corridor_radius_m": float(candidate.corridor_radius_m),
                    }
                )
                face_records.append(record)
                baseline_fallback += 1

            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if progress is not None and refine_candidates:
                progress(
                    88.0 + 8.0 * index / len(refine_candidates),
                    (
                        f"AUTO V2 TILED · validar faces {index}/{len(candidates)} · "
                        f"V2 {v2_success} · fallback {baseline_fallback}"
                    ),
                )

    else:
        # XYZ/TXT/CSV development inputs keep the in-memory path. Production
        # LAS/LAZ uses the tiled engine above.
        ground_started = perf_counter()
        roi_points, roi_stats = _collect_roi_points_memory(
            source,
            cfg,
            refine_candidates,
            max_roi_points=max_roi_points,
            progress=progress,
            cancel_check=cancel_check,
        )
        add_stage_time(
            stage_seconds,
            "ground_read_or_spool",
            perf_counter() - ground_started,
        )

        for index, (candidate, points) in enumerate(zip(refine_candidates, roi_points), start=1):
            _check_cancel(cancel_check)
            face_started = perf_counter()
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

                fallback_one_started = perf_counter()
                review_lines.extend(
                    _review_pair(
                        candidate,
                        baseline_by_face,
                        reason=reason,
                        source="BASELINE_1_1_7_REVIEW_TECHNICAL",
                        message=message,
                    )
                )
                add_stage_time(
                    stage_seconds,
                    "fallback",
                    perf_counter() - fallback_one_started,
                )
                face_records.append(
                    {
                        "face_id": int(candidate.face_id),
                        "status": "REVIEW_REQUIRED",
                        "reason": reason,
                        "message": message,
                        "roi_points": int(len(points)),
                        "corridor_radius_m": float(candidate.corridor_radius_m),
                    }
                )
                baseline_fallback += 1

            face_elapsed = perf_counter() - face_started
            add_stage_time(stage_seconds, "refinement", face_elapsed)
            if face_records:
                face_records[-1]["elapsed_ms"] = float(face_elapsed * 1000.0)

            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if progress is not None and refine_candidates:
                progress(
                    67.0 + 29.0 * index / len(refine_candidates),
                    (
                        f"AUTO V2 · faces {index}/{len(candidates)} · "
                        f"V2 {v2_success} · fallback {baseline_fallback}"
                    ),
                )

    # Never discard an unpaired baseline result: completeness beats a silent
    # regression. These are logged separately for later diagnosis.
    for line in unpaired:
        item = _baseline_line_payload(line, "BASELINE_1_1_7_REVIEW_UNPAIRED")
        item["status"] = "REVIEW_REQUIRED"
        item["review_state"] = "PENDING"
        item["review_reason"] = "UNPAIRED_BASELINE"
        item["review_message"] = "Linha baseline sem par CRISTA/PÉ; requer revisão."
        item["quality_score"] = min(float(item.get("quality_score", 0.0) or 0.0), 0.49)
        item["confidence"] = min(float(item.get("confidence", 0.0) or 0.0), 0.49)
        review_lines.append(item)

    # Output vertex spacing is a modelling choice, not a new detector.
    # Re-sample every final line (including protected-baseline fallbacks) along
    # its existing geometry so the Vector Document/DXF does not contain dense
    # centimetric zig-zag vertices. The default requested by the operator is 1 m.
    output_spacing = max(0.25, min(float(v2_cfg.station_spacing_m), 5.0))
    for line in [*final_lines, *review_lines]:
        xyz = np.asarray(line.get("xyz"), dtype=np.float64)
        if len(xyz) < 2:
            continue
        xyz = _resample_polyline(xyz, output_spacing)
        line["xyz"] = xyz
        line["length_2d_m"] = float(_line_length_2d(xyz))
        line["length_m"] = float(line["length_2d_m"])
        line["length_3d_m"] = float(
            np.linalg.norm(np.diff(xyz, axis=0), axis=1).sum()
            if len(xyz) >= 2
            else 0.0
        )
        line["vertex_spacing_m"] = float(output_spacing)

    final_lines = [
        line
        for line in final_lines
        if len(np.asarray(line["xyz"])) >= 2
        and float(line.get("length_m", 0.0)) >= cfg.min_line_length_m
    ]
    review_lines = [
        line
        for line in review_lines
        if len(np.asarray(line["xyz"])) >= 2
        and float(line.get("length_m", 0.0)) >= cfg.min_line_length_m
    ]
    final_lines.sort(key=lambda item: (int(item["face_id"]), str(item["type"])))
    review_lines.sort(key=lambda item: (int(item["face_id"]), str(item["type"])))
    for line_id, line in enumerate([*final_lines, *review_lines], start=1):
        line["line_id"] = int(line_id)

    _check_cancel(cancel_check)
    if progress is not None:
        progress(97.0, "AUTO V2 · a exportar resultado global 3D…")

    export_started = perf_counter()
    crs_wkt = roi_stats.get("crs_wkt")
    save_geojson(output / "talude_breaklines.geojson", final_lines, crs_wkt)
    save_vertices_csv(output / "talude_vertices.csv", final_lines)
    save_dxf(output / "talude_breaklines.dxf", final_lines)
    save_geojson(output / "talude_review.geojson", review_lines, crs_wkt)

    with (debug_dir / "v2_faces.jsonl").open("w", encoding="utf-8") as fh:
        for record in face_records:
            fh.write(json.dumps(_jsonable_record(record), ensure_ascii=False) + "\n")
    add_stage_time(stage_seconds, "export", perf_counter() - export_started)

    faces_final = len({int(line["face_id"]) for line in final_lines})
    review_faces = len({int(line["face_id"]) for line in review_lines})
    total_elapsed = float(perf_counter() - started)
    spool_stats = dict((tiled_stats or {}).get("spool") or {})
    tiled_profile = dict((tiled_stats or {}).get("profile") or {})
    profile_face_records = tiled_profile.get("slowest_faces") or face_records
    performance_profile = build_performance_profile(
        stage_seconds=stage_seconds,
        tile_records=tiled_profile.get("slowest_tiles", []),
        face_records=profile_face_records,
        cache={
            "baseline_hit": bool(baseline_cache_hit),
            "ground_tile_hits": int(spool_stats.get("cache_hit_tiles", 0) or 0),
            "ground_tile_misses": int(spool_stats.get("cache_miss_tiles", 0) or 0),
            "ground_tile_hit_ratio": float(
                int(spool_stats.get("cache_hit_tiles", 0) or 0)
                / max(
                    1,
                    int(spool_stats.get("cache_hit_tiles", 0) or 0)
                    + int(spool_stats.get("cache_miss_tiles", 0) or 0),
                )
            ),
            "fragment_hits": int((tiled_stats or {}).get("fragment_cache_hits", 0) or 0),
            "fragment_misses": int((tiled_stats or {}).get("fragment_cache_misses", 0) or 0),
            "fragment_hit_ratio": float(
                (tiled_stats or {}).get("fragment_cache_hit_ratio", 0.0) or 0.0
            ),
        },
        counters={
            "candidate_faces": int(len(candidates)),
            "v2_attempted": int(len(refine_candidates)),
            "v2_success": int(v2_success),
            "fallback": int(baseline_fallback),
            "performance_skipped": int(len(performance_skipped)),
        },
        total_s=total_elapsed,
    )
    safe_profile_id = "".join(
        ch if ch.isalnum() or ch in "-_" else "_"
        for ch in str(profile_run_id or "latest")
    )[:80] or "latest"
    performance_profile_path = debug_dir / f"performance_{safe_profile_id}.json"
    performance_profile_path.write_text(
        json.dumps(_jsonable_record(performance_profile), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = {
        "engine": "BREAKLINE_ENGINE_V2_GLOBAL_HYBRID",
        "experimental": True,
        "baseline_branch": "baseline-1.1.7-refine",
        "baseline_sha": BASELINE_SHA,
        "input": str(source),
        "faces_detected": int(faces_final),
        "baseline_faces_detected": int(baseline_report.get("faces_detected", 0)),
        "baseline_cache_hit": bool(baseline_cache_hit),
        "baseline_cache_dir": str(baseline_cache),
        "candidate_faces": int(len(candidates)),
        "performance_mode": mode,
        "v2_attempted_faces": int(len(refine_candidates)),
        "performance_skipped_faces": int(len(performance_skipped)),
        "performance": performance_stats,
        "output_vertex_spacing_m": float(output_spacing),
        "v2_success_faces": int(v2_success),
        "baseline_fallback_faces": int(baseline_fallback),
        "approved_faces": int(faces_final),
        "review_faces": int(review_faces),
        "review_line_count": int(len(review_lines)),
        "review_crest_lines": int(sum(1 for line in review_lines if line["type"] == "CREST")),
        "review_toe_lines": int(sum(1 for line in review_lines if line["type"] == "TOE")),
        "unpaired_baseline_lines": int(len(unpaired)),
        "crest_lines": int(sum(1 for line in final_lines if line["type"] == "CREST")),
        "toe_lines": int(sum(1 for line in final_lines if line["type"] == "TOE")),
        "reason_counts": reason_counts,
        "roi": {k: v for k, v in roi_stats.items() if k != "crs_wkt"},
        "tiled": tiled_stats,
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
        "performance_profile": performance_profile,
        "performance_profile_path": str(performance_profile_path),
        "elapsed_s": total_elapsed,
        "lines": [
            {
                key: value
                for key, value in line.items()
                if key not in {"xyz", "vertex_rmse"}
            }
            for line in final_lines
        ],
        "review_lines": [
            {
                key: value
                for key, value in line.items()
                if key not in {"xyz", "vertex_rmse"}
            }
            for line in review_lines
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
                    "tiled": tiled_stats,
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
                f"AUTO V2 concluído · {faces_final} aprovadas · "
                f"{review_faces} em revisão · V2 {v2_success}"
            ),
        )

    return report
