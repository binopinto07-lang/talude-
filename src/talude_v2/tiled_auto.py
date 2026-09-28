from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import Callable

import numpy as np
from scipy.spatial import cKDTree

from talude_v1.config import ExtractConfig
from talude_v1.engine import _filter_stream_chunk
from talude_v1.io import inspect_point_cloud, iter_point_chunks

from .engine import V2Config, _resample_polyline
from .profiling import add_stage_time, build_performance_profile, merge_stage_times
from .reasons import V2DetectionError, V2Reason


@dataclass(slots=True, frozen=True)
class TileJob:
    tile_id: int
    ix: int
    iy: int
    candidate_index: int
    face_id: int
    seed_xyz: tuple[float, float, float]


def _check_cancel(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise V2DetectionError(V2Reason.CANCELLED, "Processamento tiled V2 cancelado.")


def _tile_shape(info, tile_size_m: float) -> tuple[int, int]:
    nx = max(1, int(math.floor(max(info.width, tile_size_m) / tile_size_m)) + 1)
    ny = max(1, int(math.floor(max(info.height, tile_size_m) / tile_size_m)) + 1)
    return nx, ny


def _tile_bounds(
    tile_id: int,
    *,
    nx: int,
    x0: float,
    y0: float,
    tile_size_m: float,
) -> tuple[float, float, float, float]:
    iy, ix = divmod(int(tile_id), int(nx))
    xmin = x0 + ix * tile_size_m
    ymin = y0 + iy * tile_size_m
    return xmin, ymin, xmin + tile_size_m, ymin + tile_size_m


def _resample_reference(xyz: np.ndarray, spacing_m: float) -> tuple[np.ndarray, np.ndarray]:
    pts = np.asarray(xyz, dtype=np.float64)
    if len(pts) < 2:
        return pts.copy(), np.zeros(len(pts), dtype=np.float64)

    step = np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1)
    keep = np.r_[True, step > 1e-9]
    pts = pts[keep]
    if len(pts) < 2:
        return pts.copy(), np.zeros(len(pts), dtype=np.float64)

    dist = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1))]
    total = float(dist[-1])
    count = max(2, int(math.ceil(total / max(float(spacing_m), 0.20))) + 1)
    sample = np.linspace(0.0, total, count)
    out = np.column_stack(
        (
            np.interp(sample, dist, pts[:, 0]),
            np.interp(sample, dist, pts[:, 1]),
            np.interp(sample, dist, pts[:, 2]),
        )
    )
    return out, sample


def _build_tile_jobs(
    candidates,
    info,
    cfg: V2Config,
) -> tuple[list[TileJob], dict[int, list[TileJob]], tuple[int, int]]:
    tile_size = max(float(cfg.tile_size_m), 10.0)
    nx, ny = _tile_shape(info, tile_size)
    x0, y0 = float(info.mins[0]), float(info.mins[1])

    jobs: list[TileJob] = []
    by_tile: dict[int, list[TileJob]] = {}

    for candidate_index, candidate in enumerate(candidates):
        stations, _ = _resample_reference(
            candidate.centerline,
            max(6.0, tile_size * 0.25),
        )
        seen: set[int] = set()

        for station in stations:
            ix = int(np.clip(math.floor((station[0] - x0) / tile_size), 0, nx - 1))
            iy = int(np.clip(math.floor((station[1] - y0) / tile_size), 0, ny - 1))
            tile_id = iy * nx + ix
            if tile_id in seen:
                continue
            seen.add(tile_id)

            xmin, ymin, xmax, ymax = _tile_bounds(
                tile_id,
                nx=nx,
                x0=x0,
                y0=y0,
                tile_size_m=tile_size,
            )
            center = np.array([(xmin + xmax) * 0.5, (ymin + ymax) * 0.5])
            ids = np.flatnonzero(
                (candidate.centerline[:, 0] >= xmin)
                & (candidate.centerline[:, 0] <= xmax)
                & (candidate.centerline[:, 1] >= ymin)
                & (candidate.centerline[:, 1] <= ymax)
            )
            if len(ids):
                local = candidate.centerline[ids]
                seed = local[int(np.argmin(np.linalg.norm(local[:, :2] - center, axis=1)))]
            else:
                seed = station

            job = TileJob(
                tile_id=int(tile_id),
                ix=int(ix),
                iy=int(iy),
                candidate_index=int(candidate_index),
                face_id=int(candidate.face_id),
                seed_xyz=(float(seed[0]), float(seed[1]), float(seed[2])),
            )
            jobs.append(job)
            by_tile.setdefault(tile_id, []).append(job)

    return jobs, by_tile, (nx, ny)


def _source_tiles_needed(
    tile_ids: set[int],
    *,
    nx: int,
    ny: int,
    tile_size_m: float,
    halo_m: float,
) -> set[int]:
    rings = max(1, int(math.ceil(max(float(halo_m), 0.0) / tile_size_m)))
    needed: set[int] = set()
    for tile_id in tile_ids:
        iy, ix = divmod(int(tile_id), nx)
        for dy in range(-rings, rings + 1):
            jy = iy + dy
            if jy < 0 or jy >= ny:
                continue
            for dx in range(-rings, rings + 1):
                jx = ix + dx
                if jx < 0 or jx >= nx:
                    continue
                needed.add(jy * nx + jx)
    return needed


def _persistent_spool_dir(
    source: Path,
    baseline_cfg: ExtractConfig,
    *,
    output_debug_dir: Path,
    nx: int,
    ny: int,
    tile_size_m: float,
    x0: float,
    y0: float,
) -> tuple[Path, dict]:
    stat = source.stat()
    payload = {
        "source": str(source.resolve()),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "baseline_filter": baseline_cfg.to_dict(),
        "nx": int(nx),
        "ny": int(ny),
        "tile_size_m": round(float(tile_size_m), 6),
        "x0": round(float(x0), 4),
        "y0": round(float(y0), 4),
        "format": "f64_xyz_v1",
    }
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:20]

    # output_debug_dir = <project>/exports/<run>/debug
    try:
        project_root = output_debug_dir.parents[2]
    except IndexError:
        project_root = output_debug_dir.parent

    cache_dir = project_root / "cache" / "v2_ground_tiles" / digest
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir, payload


def _fragment_cache_identity(candidate, cfg: V2Config, tile_id: int) -> str:
    payload = {
        "tile_id": int(tile_id),
        "face_id": int(candidate.face_id),
        "seed": [round(float(v), 4) for v in np.asarray(candidate.seed_xyz).reshape(-1)[:3]],
        "crest": np.asarray(candidate.crest, dtype=np.float64).round(4).tolist(),
        "toe": np.asarray(candidate.toe, dtype=np.float64).round(4).tolist(),
        "cfg": {
            "target_tin_spacing_m": float(cfg.target_tin_spacing_m),
            "max_tin_points": int(cfg.max_tin_points),
            "max_triangle_edge_m": float(cfg.max_triangle_edge_m),
            "graph_gap_m": float(cfg.graph_gap_m),
            "station_spacing_m": float(cfg.station_spacing_m),
            "patch_along_m": float(cfg.patch_along_m),
            "patch_cross_m": float(cfg.patch_cross_m),
            "tile_size_m": float(cfg.tile_size_m),
            "tile_halo_m": float(cfg.tile_halo_m),
            "tile_min_fragment_m": float(cfg.tile_min_fragment_m),
            "tile_stitch_gap_m": float(cfg.tile_stitch_gap_m),
            "min_face_slope_deg": float(cfg.min_face_slope_deg),
            "boundary_side_cos_min": float(cfg.boundary_side_cos_min),
            "max_local_normal_change_deg": float(cfg.max_local_normal_change_deg),
        },
        "schema": "v2_fragment_cache_v1",
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:24]


def _fragment_cache_path(
    spool_dir: Path,
    candidate,
    cfg: V2Config,
    tile_id: int,
) -> Path:
    root = spool_dir / "fragment_results"
    root.mkdir(parents=True, exist_ok=True)
    digest = _fragment_cache_identity(candidate, cfg, tile_id)
    return root / f"face_{int(candidate.face_id):06d}_tile_{int(tile_id):08d}_{digest}.json"


def _load_fragment_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "v2_fragment_cache_v1":
            return None
        fragments = payload.get("fragments")
        if not isinstance(fragments, list):
            return None
        return payload
    except Exception:
        return None


def _save_fragment_cache(path: Path, fragments: list[dict], record: dict) -> None:
    serializable = []
    for fragment in fragments:
        item = dict(fragment)
        item["xyz"] = np.asarray(item.get("xyz", []), dtype=np.float64).tolist()
        serializable.append(item)

    payload = {
        "schema": "v2_fragment_cache_v1",
        "fragments": serializable,
        "record": dict(record),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def _load_spool_manifest(spool_dir: Path) -> dict:
    path = spool_dir / "manifest.json"
    if not path.exists():
        return {"complete_tiles": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {"complete_tiles": []}
        return value
    except Exception:
        return {"complete_tiles": []}


def _save_spool_manifest(spool_dir: Path, payload: dict) -> None:
    path = spool_dir / "manifest.json"
    tmp = spool_dir / "manifest.json.tmp"
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def _valid_cached_tile(spool_dir: Path, tile_id: int) -> bool:
    path = spool_dir / f"tile_{int(tile_id):08d}.f64"
    # An absent file can legitimately represent an empty Ground tile, but only
    # when the manifest says the tile completed. Existing files must contain
    # complete float64 XYZ triplets.
    if not path.exists():
        return True
    size = int(path.stat().st_size)
    return size % 24 == 0


def _cached_point_count(spool_dir: Path, tile_ids: set[int]) -> int:
    total = 0
    for tile_id in tile_ids:
        path = spool_dir / f"tile_{int(tile_id):08d}.f64"
        if path.exists():
            total += int(path.stat().st_size // 24)
    return total


def _spool_tiles(
    source: Path,
    baseline_cfg: ExtractConfig,
    *,
    needed_tiles: set[int],
    spool_dir: Path,
    nx: int,
    ny: int,
    tile_size_m: float,
    x0: float,
    y0: float,
    progress: Callable[[float, str], None] | None,
    cancel_check: Callable[[], bool] | None,
) -> dict:
    info = inspect_point_cloud(source)
    spool_dir.mkdir(parents=True, exist_ok=True)
    raw_seen = 0
    selected_seen = 0
    spooled = 0
    tiles_written: set[int] = set()

    for xyz, classification in iter_point_chunks(source, chunk_size=750_000):
        _check_cancel(cancel_check)
        raw_seen += len(xyz)
        pts = _filter_stream_chunk(xyz, classification, baseline_cfg)
        selected_seen += len(pts)

        if len(pts):
            ix = np.clip(
                np.floor((pts[:, 0] - x0) / tile_size_m).astype(np.int64),
                0,
                nx - 1,
            )
            iy = np.clip(
                np.floor((pts[:, 1] - y0) / tile_size_m).astype(np.int64),
                0,
                ny - 1,
            )
            keys = iy * nx + ix
            order = np.argsort(keys, kind="mergesort")
            sorted_keys = keys[order]
            unique, starts, counts = np.unique(
                sorted_keys,
                return_index=True,
                return_counts=True,
            )

            for key, start, count in zip(unique, starts, counts):
                tile_id = int(key)
                if tile_id not in needed_tiles:
                    continue
                block = np.asarray(
                    pts[order[int(start) : int(start + count)]],
                    dtype=np.float64,
                )
                path = spool_dir / f"tile_{tile_id:08d}.f64"
                with path.open("ab") as fh:
                    block.tofile(fh)
                spooled += int(len(block))
                tiles_written.add(tile_id)

        if progress is not None and info.point_count:
            frac = min(raw_seen / info.point_count, 1.0)
            progress(
                42.0 + 20.0 * frac,
                (
                    f"AUTO V2 TILED · spool Ground · "
                    f"{raw_seen:,}/{info.point_count:,} pontos"
                ),
            )

    return {
        "raw_seen": int(raw_seen),
        "selected_seen": int(selected_seen),
        "spooled_points": int(spooled),
        "tiles_written": int(len(tiles_written)),
    }


def _load_tile_halo(
    tile_id: int,
    *,
    spool_dir: Path,
    nx: int,
    ny: int,
    x0: float,
    y0: float,
    tile_size_m: float,
    halo_m: float,
) -> np.ndarray:
    iy, ix = divmod(int(tile_id), nx)
    rings = max(1, int(math.ceil(max(float(halo_m), 0.0) / tile_size_m)))
    parts: list[np.ndarray] = []

    for dy in range(-rings, rings + 1):
        jy = iy + dy
        if jy < 0 or jy >= ny:
            continue
        for dx in range(-rings, rings + 1):
            jx = ix + dx
            if jx < 0 or jx >= nx:
                continue
            neighbour = jy * nx + jx
            path = spool_dir / f"tile_{neighbour:08d}.f64"
            if not path.exists() or path.stat().st_size < 24:
                continue
            data = np.fromfile(path, dtype=np.float64)
            usable = (len(data) // 3) * 3
            if usable:
                parts.append(data[:usable].reshape(-1, 3))

    if not parts:
        return np.empty((0, 3), dtype=np.float64)

    pts = np.concatenate(parts, axis=0)
    xmin, ymin, xmax, ymax = _tile_bounds(
        tile_id,
        nx=nx,
        x0=x0,
        y0=y0,
        tile_size_m=tile_size_m,
    )
    h = max(float(halo_m), 0.0)
    keep = (
        (pts[:, 0] >= xmin - h)
        & (pts[:, 0] <= xmax + h)
        & (pts[:, 1] >= ymin - h)
        & (pts[:, 1] <= ymax + h)
    )
    return pts[keep]


def _bounded_candidate_points(
    tile_points: np.ndarray,
    candidate,
    *,
    max_points: int,
    seed: int,
) -> np.ndarray:
    if len(tile_points) == 0:
        return tile_points

    tree = cKDTree(candidate.centerline[:, :2])
    dist, _ = tree.query(tile_points[:, :2], k=1)
    pts = tile_points[np.asarray(dist) <= candidate.corridor_radius_m]
    if len(pts) <= max_points:
        return pts

    rng = np.random.default_rng(int(seed))
    ids = rng.choice(len(pts), size=int(max_points), replace=False)
    return pts[np.sort(ids)]


def _clip_line_to_core(
    xyz: np.ndarray,
    tile_id: int,
    *,
    nx: int,
    x0: float,
    y0: float,
    tile_size_m: float,
    margin_m: float,
) -> np.ndarray:
    pts = np.asarray(xyz, dtype=np.float64)
    if len(pts) < 2:
        return np.empty((0, 3), dtype=np.float64)

    xmin, ymin, xmax, ymax = _tile_bounds(
        tile_id,
        nx=nx,
        x0=x0,
        y0=y0,
        tile_size_m=tile_size_m,
    )
    m = max(float(margin_m), 0.0)
    keep = (
        (pts[:, 0] >= xmin - m)
        & (pts[:, 0] <= xmax + m)
        & (pts[:, 1] >= ymin - m)
        & (pts[:, 1] <= ymax + m)
    )
    ids = np.flatnonzero(keep)
    if len(ids) < 2:
        return np.empty((0, 3), dtype=np.float64)

    # Keep the longest contiguous run rather than joining disjoint pieces.
    breaks = np.flatnonzero(np.diff(ids) > 1)
    starts = np.r_[0, breaks + 1]
    ends = np.r_[breaks + 1, len(ids)]
    spans = [(int(a), int(b)) for a, b in zip(starts, ends)]
    best = max(spans, key=lambda ab: ab[1] - ab[0])
    chosen = ids[best[0] : best[1]]
    return pts[chosen].copy()


def _reference_projection(
    xyz: np.ndarray,
    reference_xyz: np.ndarray,
) -> tuple[np.ndarray, float]:
    ref, s_ref = _resample_reference(reference_xyz, 0.35)
    if len(ref) < 2:
        return np.zeros(len(xyz), dtype=np.float64), 0.0
    tree = cKDTree(ref[:, :2])
    _, idx = tree.query(np.asarray(xyz, dtype=np.float64)[:, :2], k=1)
    return s_ref[np.asarray(idx, dtype=np.int64)], float(s_ref[-1])


def _interval_union_length(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    work = sorted((min(a, b), max(a, b)) for a, b in intervals)
    total = 0.0
    lo, hi = work[0]
    for a, b in work[1:]:
        if a <= hi:
            hi = max(hi, b)
        else:
            total += max(0.0, hi - lo)
            lo, hi = a, b
    total += max(0.0, hi - lo)
    return float(total)


def stitch_fragments(
    fragments: list[dict],
    reference_xyz: np.ndarray,
    cfg: V2Config,
) -> tuple[np.ndarray, dict]:
    if not fragments:
        raise V2DetectionError(V2Reason.MERGE_FAILED, "Sem fragmentos tiled para unir.")

    ref, s_ref = _resample_reference(reference_xyz, 0.35)
    if len(ref) < 2 or float(s_ref[-1]) <= 1e-9:
        raise V2DetectionError(V2Reason.MERGE_FAILED, "Linha baseline inválida para stitching.")

    tree = cKDTree(ref[:, :2])
    samples: list[tuple[float, float, np.ndarray]] = []
    intervals: list[tuple[float, float]] = []
    fragment_lengths = []

    for fragment in fragments:
        xyz = np.asarray(fragment["xyz"], dtype=np.float64)
        if len(xyz) < 2:
            continue
        _, idx = tree.query(xyz[:, :2], k=1)
        ss = s_ref[np.asarray(idx, dtype=np.int64)]
        if ss[-1] < ss[0]:
            xyz = xyz[::-1].copy()
            ss = ss[::-1].copy()

        intervals.append((float(np.min(ss)), float(np.max(ss))))
        fragment_lengths.append(float(fragment.get("length_m") or 0.0))
        quality = float(np.clip(fragment.get("quality_score", 0.5), 0.05, 1.0))
        for s_val, point in zip(ss, xyz):
            samples.append((float(s_val), quality, point.copy()))

    if len(samples) < 2:
        raise V2DetectionError(V2Reason.MERGE_FAILED, "Fragmentos tiled sem vértices suficientes.")

    ref_length = float(s_ref[-1])
    coverage = _interval_union_length(intervals) / ref_length
    if coverage < float(cfg.tile_min_coverage_ratio):
        raise V2DetectionError(
            V2Reason.MERGE_FAILED,
            f"Cobertura tiled insuficiente ({coverage:.2%}).",
        )

    samples.sort(key=lambda item: item[0])
    bin_size = max(0.30, float(cfg.station_spacing_m) * 0.55)
    merged: list[np.ndarray] = []
    merged_s: list[float] = []
    i = 0
    while i < len(samples):
        key = int(math.floor(samples[i][0] / bin_size))
        group = []
        while i < len(samples) and int(math.floor(samples[i][0] / bin_size)) == key:
            group.append(samples[i])
            i += 1
        weights = np.asarray([item[1] for item in group], dtype=np.float64)
        points = np.asarray([item[2] for item in group], dtype=np.float64)
        s_vals = np.asarray([item[0] for item in group], dtype=np.float64)
        merged.append(np.average(points, axis=0, weights=weights))
        merged_s.append(float(np.average(s_vals, weights=weights)))

    out = np.asarray(merged, dtype=np.float64)
    order = np.argsort(np.asarray(merged_s))
    out = out[order]
    merged_s_arr = np.asarray(merged_s, dtype=np.float64)[order]

    if len(out) < 2:
        raise V2DetectionError(V2Reason.MERGE_FAILED, "Stitching produziu linha demasiado curta.")

    ds = np.diff(merged_s_arr)
    dxy = np.linalg.norm(np.diff(out[:, :2], axis=0), axis=1)
    dangerous = (ds > float(cfg.tile_stitch_gap_m)) & (
        dxy > float(cfg.tile_stitch_gap_m) * 1.35
    )
    if np.any(dangerous):
        raise V2DetectionError(
            V2Reason.MERGE_FAILED,
            (
                "Stitching encontrou gap excessivo: "
                f"{float(np.max(dxy[dangerous])):.2f} m."
            ),
        )

    out = _resample_polyline(out, cfg.station_spacing_m)
    return out, {
        "fragment_count": int(len(fragments)),
        "coverage_ratio": float(np.clip(coverage, 0.0, 1.0)),
        "reference_length_m": ref_length,
        "input_fragment_length_m": float(sum(fragment_lengths)),
        "stitched_vertices": int(len(out)),
        "max_stitch_gap_m": float(np.max(dxy)) if len(dxy) else 0.0,
    }


def process_candidates_tiled(
    source: Path,
    baseline_cfg: ExtractConfig,
    candidates,
    v2_cfg: V2Config,
    *,
    output_debug_dir: Path,
    min_line_length_m: float,
    progress: Callable[[float, str], None] | None,
    cancel_check: Callable[[], bool] | None,
) -> tuple[dict[int, dict], dict]:
    """Phase 3: process all global candidates as tile+halo fragments.

    The source cloud is streamed into persistent, fingerprinted Ground core
    tiles. Halo is reconstructed from neighbouring core tiles. Re-running the
    same source/config reuses completed tiles and avoids a second full LAS scan.
    """
    started = perf_counter()
    stage_seconds: dict[str, float] = {}
    tile_profile_records: list[dict] = []
    face_profile: dict[int, dict] = {}

    info = inspect_point_cloud(source)
    tile_size = max(float(v2_cfg.tile_size_m), 10.0)
    halo = max(float(v2_cfg.tile_halo_m), 0.0)
    x0, y0 = float(info.mins[0]), float(info.mins[1])

    tile_generation_started = perf_counter()
    jobs, by_tile, (nx, ny) = _build_tile_jobs(candidates, info, v2_cfg)
    candidate_tile_ids = set(by_tile)
    needed_tiles = _source_tiles_needed(
        candidate_tile_ids,
        nx=nx,
        ny=ny,
        tile_size_m=tile_size,
        halo_m=halo,
    )
    add_stage_time(
        stage_seconds,
        "tile_generation",
        perf_counter() - tile_generation_started,
    )

    spool_dir, cache_identity = _persistent_spool_dir(
        source,
        baseline_cfg,
        output_debug_dir=output_debug_dir,
        nx=nx,
        ny=ny,
        tile_size_m=tile_size,
        x0=x0,
        y0=y0,
    )
    manifest = _load_spool_manifest(spool_dir)
    completed = {int(value) for value in manifest.get("complete_tiles", [])}
    cached_tiles = {
        tile_id
        for tile_id in needed_tiles
        if tile_id in completed and _valid_cached_tile(spool_dir, tile_id)
    }
    missing_tiles = set(needed_tiles) - cached_tiles

    # Any non-complete tile can contain bytes from an interrupted previous
    # scan. Delete it before rebuilding to avoid duplicate/partial Ground data.
    for tile_id in missing_tiles:
        stale = spool_dir / f"tile_{int(tile_id):08d}.f64"
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                pass

    tile_records: list[dict] = []
    fragments: dict[int, dict[str, list[dict]]] = {
        int(candidate.face_id): {"CREST": [], "TOE": []}
        for candidate in candidates
    }

    try:
        spool_started = perf_counter()
        if missing_tiles:
            spool_stats = _spool_tiles(
                source,
                baseline_cfg,
                needed_tiles=missing_tiles,
                spool_dir=spool_dir,
                nx=nx,
                ny=ny,
                tile_size_m=tile_size,
                x0=x0,
                y0=y0,
                progress=progress,
                cancel_check=cancel_check,
            )
            completed.update(missing_tiles)
            _save_spool_manifest(
                spool_dir,
                {
                    **cache_identity,
                    "complete_tiles": sorted(completed),
                },
            )
        else:
            spool_stats = {
                "raw_seen": 0,
                "selected_seen": 0,
                "spooled_points": 0,
                "tiles_written": 0,
            }
            if progress is not None:
                progress(
                    62.0,
                    (
                        f"AUTO V2 TILED · cache Ground HIT · "
                        f"{len(cached_tiles)}/{len(needed_tiles)} tiles"
                    ),
                )
        add_stage_time(
            stage_seconds,
            "ground_read_or_spool",
            perf_counter() - spool_started,
        )

        cached_points = _cached_point_count(spool_dir, set(needed_tiles))
        spool_stats.update(
            {
                "cache_enabled": True,
                "cache_key": spool_dir.name,
                "cache_hit_tiles": int(len(cached_tiles)),
                "cache_miss_tiles": int(len(missing_tiles)),
                "cached_points_available": int(cached_points),
                "cache_dir": str(spool_dir),
            }
        )

        total_jobs = max(1, len(jobs))
        done = 0
        fragment_cache_hits = 0
        fragment_cache_misses = 0

        # Load one tile+halo once, then run all face fragments that touch it.
        for tile_index, tile_id in enumerate(sorted(by_tile), start=1):
            _check_cancel(cancel_check)
            tile_started = perf_counter()
            read_started = perf_counter()
            tile_points = _load_tile_halo(
                tile_id,
                spool_dir=spool_dir,
                nx=nx,
                ny=ny,
                x0=x0,
                y0=y0,
                tile_size_m=tile_size,
                halo_m=halo,
            )
            read_ms = float((perf_counter() - read_started) * 1000.0)
            add_stage_time(stage_seconds, "tile_ground_query", read_ms / 1000.0)
            xmin, ymin, xmax, ymax = _tile_bounds(
                tile_id,
                nx=nx,
                x0=x0,
                y0=y0,
                tile_size_m=tile_size,
            )
            tile_profile = {
                "tile_id": int(tile_id),
                "bbox": [float(xmin), float(ymin), float(xmax), float(ymax)],
                "ground_points": int(len(tile_points)),
                "read_ms": read_ms,
                "tin_ms": 0.0,
                "refine_ms": 0.0,
                "stitch_ms": 0.0,
                "cache_hits": 0,
                "cache_misses": 0,
                "faces": int(len({int(job.face_id) for job in by_tile[tile_id]})),
                "jobs": int(len(by_tile[tile_id])),
            }

            for job in by_tile[tile_id]:
                _check_cancel(cancel_check)
                candidate = candidates[job.candidate_index]
                job_started = perf_counter()
                cache_path = _fragment_cache_path(
                    spool_dir,
                    candidate,
                    v2_cfg,
                    tile_id,
                )
                cache_started = perf_counter()
                cached = _load_fragment_cache(cache_path)
                cache_lookup_ms = float((perf_counter() - cache_started) * 1000.0)
                refine_ms = 0.0

                if cached is not None:
                    cached_fragments = []
                    for item in cached.get("fragments", []):
                        fragment = dict(item)
                        fragment["xyz"] = np.asarray(
                            fragment.get("xyz", []),
                            dtype=np.float64,
                        )
                        if len(fragment["xyz"]) < 2:
                            continue
                        cached_fragments.append(fragment)
                        fragments[int(candidate.face_id)][fragment["type"]].append(fragment)

                    record = dict(cached.get("record") or {})
                    record.update(
                        {
                            "tile_id": int(tile_id),
                            "ix": int(job.ix),
                            "iy": int(job.iy),
                            "face_id": int(candidate.face_id),
                            "seed": list(job.seed_xyz),
                            "status": "CACHE_HIT",
                            "reason": V2Reason.SUCCESS.value,
                            "kept_fragments": int(len(cached_fragments)),
                            "fragment_cache": True,
                        }
                    )
                    fragment_cache_hits += 1
                    tile_profile["cache_hits"] += 1
                else:
                    fragment_cache_misses += 1
                    tile_profile["cache_misses"] += 1
                    refine_started = perf_counter()
                    local_points = _bounded_candidate_points(
                        tile_points,
                        candidate,
                        max_points=max(int(v2_cfg.max_tin_points * 1.35), v2_cfg.max_tin_points),
                        seed=v2_cfg.random_seed + job.tile_id * 31 + candidate.face_id * 7919,
                    )
                    record = {
                        "tile_id": int(tile_id),
                        "ix": int(job.ix),
                        "iy": int(job.iy),
                        "face_id": int(candidate.face_id),
                        "seed": list(job.seed_xyz),
                        "tile_points": int(len(tile_points)),
                        "roi_points": int(len(local_points)),
                        "fragment_cache": False,
                    }

                    produced_fragments: list[dict] = []
                    try:
                        if len(local_points) < 100:
                            raise V2DetectionError(
                                V2Reason.LOW_GROUND_SUPPORT,
                                f"Tile {tile_id}: poucos pontos Ground na ROI.",
                            )

                        # Lazy import avoids a module cycle: global_auto imports this
                        # orchestration only at execution time.
                        from .global_auto import refine_face_candidate_v2

                        local_candidate = replace(
                            candidate,
                            seed_xyz=np.asarray(job.seed_xyz, dtype=np.float64),
                        )
                        lines, face_record = refine_face_candidate_v2(
                            local_candidate,
                            local_points,
                            v2_config=v2_cfg,
                            min_line_length_m=max(
                                float(v2_cfg.tile_min_fragment_m),
                                min(float(min_line_length_m), 2.0),
                            ),
                        )

                        margin = min(max(0.35, halo * 0.20), 2.0)
                        kept = 0
                        for line in lines:
                            clipped = _clip_line_to_core(
                                np.asarray(line["xyz"], dtype=np.float64),
                                tile_id,
                                nx=nx,
                                x0=x0,
                                y0=y0,
                                tile_size_m=tile_size,
                                margin_m=margin,
                            )
                            if len(clipped) < 2:
                                continue

                            length = float(
                                np.linalg.norm(np.diff(clipped[:, :2], axis=0), axis=1).sum()
                            )
                            if length < float(v2_cfg.tile_min_fragment_m):
                                continue

                            fragment = dict(line)
                            fragment["xyz"] = clipped
                            fragment["length_m"] = length
                            fragment["length_2d_m"] = length
                            fragment["source"] = "V2_RAW_TIN_TILE"
                            fragment["tile_id"] = int(tile_id)
                            fragments[int(candidate.face_id)][line["type"]].append(fragment)
                            produced_fragments.append(fragment)
                            kept += 1

                        record.update(
                            {
                                "status": "SUCCESS" if kept else "NO_CORE_FRAGMENT",
                                "reason": (
                                    V2Reason.SUCCESS.value
                                    if kept
                                    else V2Reason.MERGE_FAILED.value
                                ),
                                "kept_fragments": int(kept),
                                "v2_metrics": face_record.get("v2_metrics", {}),
                            }
                        )
                        if kept:
                            _save_fragment_cache(
                                cache_path,
                                produced_fragments,
                                record,
                            )
                        v2_timing = dict(face_record.get("v2_timing_s", {}) or {})
                        merge_stage_times(stage_seconds, v2_timing)
                        tile_profile["tin_ms"] += float(
                            v2_timing.get("tin_build", 0.0) or 0.0
                        ) * 1000.0
                    except Exception as exc:
                        record.update(
                            {
                                "status": "FAILED",
                                "reason": (
                                    exc.reason.value
                                    if isinstance(exc, V2DetectionError)
                                    else V2Reason.INTERNAL_ERROR.value
                                ),
                                "message": str(exc),
                            }
                        )
                    refine_ms = float((perf_counter() - refine_started) * 1000.0)

                job_elapsed_ms = float((perf_counter() - job_started) * 1000.0)
                record["cache_lookup_ms"] = cache_lookup_ms
                record["refine_ms"] = refine_ms
                record["elapsed_ms"] = job_elapsed_ms
                tile_profile["refine_ms"] += refine_ms

                face_row = face_profile.setdefault(
                    int(candidate.face_id),
                    {
                        "face_id": int(candidate.face_id),
                        "source": "V2_TILED",
                        "v2_attempted": True,
                        "v2_success": False,
                        "fallback_reason": None,
                        "tile_jobs": 0,
                        "points": 0,
                        "tin_points": 0,
                        "refine_ms": 0.0,
                        "stitch_ms": 0.0,
                        "cache_hits": 0,
                        "cache_misses": 0,
                    },
                )
                face_row["tile_jobs"] += 1
                face_row["points"] += int(record.get("roi_points", 0) or 0)
                metrics = dict(record.get("v2_metrics", {}) or {})
                face_row["tin_points"] += int(metrics.get("tin_points", 0) or 0)
                face_row["refine_ms"] += refine_ms
                face_row["cache_hits"] += 1 if cached is not None else 0
                face_row["cache_misses"] += 0 if cached is not None else 1

                tile_records.append(record)
                done += 1
                if progress is not None:
                    progress(
                        62.0 + 26.0 * done / total_jobs,
                        (
                            f"AUTO V2 TILED · fragmentos {done}/{total_jobs} · "
                            f"tile {tile_index}/{len(by_tile)} · "
                            f"cache {fragment_cache_hits} hit"
                        ),
                    )

            tile_profile["cache_hit"] = bool(
                tile_profile["cache_hits"] == tile_profile["jobs"]
                and tile_profile["jobs"] > 0
            )
            tile_profile["elapsed_ms"] = float((perf_counter() - tile_started) * 1000.0)
            tile_profile_records.append(tile_profile)

        results: dict[int, dict] = {}
        for candidate in candidates:
            _check_cancel(cancel_check)
            face_id = int(candidate.face_id)
            pair_lines: list[dict] = []
            stitch_meta: dict[str, dict] = {}
            failure: Exception | None = None

            stitch_started = perf_counter()
            for kind, reference in (
                ("CREST", candidate.crest),
                ("TOE", candidate.toe),
            ):
                try:
                    xyz, meta = stitch_fragments(
                        fragments[face_id][kind],
                        np.asarray(reference, dtype=np.float64),
                        v2_cfg,
                    )
                    length_2d = float(
                        np.linalg.norm(np.diff(xyz[:, :2], axis=0), axis=1).sum()
                    )
                    length_3d = float(
                        np.linalg.norm(np.diff(xyz, axis=0), axis=1).sum()
                    )
                    qualities = [
                        float(item.get("quality_score", 0.0))
                        for item in fragments[face_id][kind]
                    ]
                    quality = float(np.median(qualities)) if qualities else 0.0
                    pair_lines.append(
                        {
                            "face_id": face_id,
                            "type": kind,
                            "xyz": xyz,
                            "length_m": length_2d,
                            "length_2d_m": length_2d,
                            "length_3d_m": length_3d,
                            "confidence": quality,
                            "quality_score": quality,
                            "median_rmse": None,
                            "source": "V2_TILED_STITCHED",
                            "status": "AUTO",
                            "tile_fragment_count": int(meta["fragment_count"]),
                            "tile_coverage_ratio": float(meta["coverage_ratio"]),
                            "max_stitch_gap_m": float(meta["max_stitch_gap_m"]),
                        }
                    )
                    stitch_meta[kind] = meta
                except Exception as exc:
                    failure = exc
                    break

            stitch_ms = float((perf_counter() - stitch_started) * 1000.0)
            add_stage_time(stage_seconds, "stitching", stitch_ms / 1000.0)
            face_row = face_profile.setdefault(
                face_id,
                {
                    "face_id": face_id,
                    "source": "V2_TILED",
                    "v2_attempted": True,
                    "v2_success": False,
                    "fallback_reason": None,
                    "tile_jobs": 0,
                    "points": 0,
                    "tin_points": 0,
                    "refine_ms": 0.0,
                    "stitch_ms": 0.0,
                    "cache_hits": 0,
                    "cache_misses": 0,
                },
            )
            face_row["stitch_ms"] = stitch_ms
            face_row["elapsed_ms"] = float(face_row["refine_ms"] + stitch_ms)
            face_row["status"] = (
                "SUCCESS"
                if failure is None and len(pair_lines) == 2
                else "FAILED"
            )
            face_row["reason"] = (
                V2Reason.SUCCESS.value
                if failure is None and len(pair_lines) == 2
                else (
                    failure.reason.value
                    if isinstance(failure, V2DetectionError)
                    else V2Reason.MERGE_FAILED.value
                )
            )
            face_row["v2_success"] = bool(
                failure is None and len(pair_lines) == 2
            )
            face_row["fallback_reason"] = (
                None if face_row["v2_success"] else face_row["reason"]
            )

            if failure is None and len(pair_lines) == 2:
                results[face_id] = {
                    "status": "SUCCESS",
                    "reason": V2Reason.SUCCESS.value,
                    "lines": pair_lines,
                    "record": {
                        "face_id": face_id,
                        "status": "SUCCESS",
                        "reason": V2Reason.SUCCESS.value,
                        "mode": "TILED",
                        "tiles": int(
                            len(
                                {
                                    item["tile_id"]
                                    for kind in ("CREST", "TOE")
                                    for item in fragments[face_id][kind]
                                }
                            )
                        ),
                        "stitch": stitch_meta,
                    },
                }
            else:
                results[face_id] = {
                    "status": "FAILED",
                    "reason": (
                        failure.reason.value
                        if isinstance(failure, V2DetectionError)
                        else V2Reason.MERGE_FAILED.value
                    ),
                    "message": str(failure) if failure is not None else "Par tiled incompleto.",
                    "lines": [],
                    "record": {
                        "face_id": face_id,
                        "status": "FAILED",
                        "reason": (
                            failure.reason.value
                            if isinstance(failure, V2DetectionError)
                            else V2Reason.MERGE_FAILED.value
                        ),
                        "mode": "TILED",
                        "message": str(failure) if failure is not None else "Par tiled incompleto.",
                    },
                }

        output_debug_dir.mkdir(parents=True, exist_ok=True)
        with (output_debug_dir / "v2_tiles.jsonl").open("w", encoding="utf-8") as fh:
            for record in tile_records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        success_faces = sum(1 for item in results.values() if item["status"] == "SUCCESS")
        performance_profile = build_performance_profile(
            stage_seconds=stage_seconds,
            tile_records=tile_profile_records,
            face_records=face_profile.values(),
            cache={
                "ground_tile_hits": int(len(cached_tiles)),
                "ground_tile_misses": int(len(missing_tiles)),
                "ground_tile_hit_ratio": float(
                    len(cached_tiles) / max(1, len(cached_tiles) + len(missing_tiles))
                ),
                "fragment_hits": int(fragment_cache_hits),
                "fragment_misses": int(fragment_cache_misses),
                "fragment_hit_ratio": float(
                    fragment_cache_hits
                    / max(1, fragment_cache_hits + fragment_cache_misses)
                ),
            },
            counters={
                "candidate_tiles": int(len(candidate_tile_ids)),
                "tile_jobs": int(len(jobs)),
                "faces": int(len(candidates)),
                "successful_faces": int(success_faces),
                "failed_faces": int(len(results) - success_faces),
            },
            total_s=float(perf_counter() - started),
        )
        return results, {
            "mode": "TILED_HALO_STITCH",
            "tile_size_m": tile_size,
            "halo_m": halo,
            "nx": int(nx),
            "ny": int(ny),
            "candidate_tiles": int(len(candidate_tile_ids)),
            "source_tiles_needed": int(len(needed_tiles)),
            "tile_jobs": int(len(jobs)),
            "successful_faces": int(success_faces),
            "failed_faces": int(len(results) - success_faces),
            "spool": spool_stats,
            "fragment_cache_hits": int(fragment_cache_hits),
            "fragment_cache_misses": int(fragment_cache_misses),
            "fragment_cache_hit_ratio": float(
                fragment_cache_hits / max(1, fragment_cache_hits + fragment_cache_misses)
            ),
            "profile": performance_profile,
            "elapsed_s": float(perf_counter() - started),
        }
    finally:
        # Ground core tiles are a persistent cache keyed by source/config.
        # Only transient manifest files are removed; completed tile data is
        # intentionally kept so a second AUTO run avoids a full LAS scan.
        tmp_manifest = spool_dir / "manifest.json.tmp"
        if tmp_manifest.exists():
            try:
                tmp_manifest.unlink()
            except OSError:
                pass
