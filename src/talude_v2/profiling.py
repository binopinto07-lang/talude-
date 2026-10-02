from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


DEFAULT_STAGE_NAMES = (
    "global_candidate_detection",
    "ground_read_or_spool",
    "tile_generation",
    "tile_ground_query",
    "tin_build",
    "face_detection",
    "crest_toe_classification",
    "boundary_extraction",
    "refinement",
    "stitching",
    "fallback",
    "vector_document",
    "export",
)


def add_stage_time(stages: dict[str, float], name: str, seconds: float) -> None:
    """Accumulate a measured duration without inventing missing stages."""
    value = max(0.0, float(seconds))
    stages[str(name)] = float(stages.get(str(name), 0.0) + value)


def merge_stage_times(
    target: dict[str, float],
    source: Mapping[str, Any] | None,
) -> None:
    if not source:
        return
    for name, seconds in source.items():
        try:
            add_stage_time(target, str(name), float(seconds))
        except (TypeError, ValueError):
            continue


def _record_elapsed_ms(record: Mapping[str, Any]) -> float:
    direct = record.get("elapsed_ms")
    if direct is not None:
        try:
            return max(0.0, float(direct))
        except (TypeError, ValueError):
            pass

    total = 0.0
    for key in (
        "read_ms",
        "cache_lookup_ms",
        "tin_ms",
        "refine_ms",
        "stitch_ms",
        "fallback_ms",
    ):
        try:
            total += max(0.0, float(record.get(key, 0.0) or 0.0))
        except (TypeError, ValueError):
            continue
    return total


def _slowest(
    records: Iterable[Mapping[str, Any]],
    *,
    top_n: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in records:
        row = dict(item)
        row["elapsed_ms"] = _record_elapsed_ms(item)
        normalized.append(row)
    normalized.sort(
        key=lambda row: (
            -float(row.get("elapsed_ms", 0.0)),
            int(row.get("tile_id", row.get("face_id", 0)) or 0),
        )
    )
    return normalized[: max(0, int(top_n))]


def build_performance_profile(
    *,
    stage_seconds: Mapping[str, Any] | None = None,
    tile_records: Iterable[Mapping[str, Any]] = (),
    face_records: Iterable[Mapping[str, Any]] = (),
    cache: Mapping[str, Any] | None = None,
    counters: Mapping[str, Any] | None = None,
    total_s: float | None = None,
    top_n: int = 20,
) -> dict[str, Any]:
    """Build the serializable AUTO V2 performance payload from measured data."""
    stages: dict[str, float] = {}
    merge_stage_times(stages, stage_seconds)
    ordered_stages = {
        name: float(stages[name])
        for name in DEFAULT_STAGE_NAMES
        if name in stages
    }
    for name in sorted(stages):
        if name not in ordered_stages:
            ordered_stages[name] = float(stages[name])

    payload: dict[str, Any] = {
        "schema": "talude-v2-performance/v1",
        "stages_s": ordered_stages,
        "slowest_tiles": _slowest(tile_records, top_n=top_n),
        "slowest_faces": _slowest(face_records, top_n=top_n),
        "cache": dict(cache or {}),
        "counters": dict(counters or {}),
    }
    if total_s is not None:
        payload["total_s"] = max(0.0, float(total_s))
    return payload
