from __future__ import annotations

import json
from pathlib import Path

from talude_v2.profiling import (
    add_stage_time,
    build_performance_profile,
    merge_stage_times,
)


def test_performance_profile_accumulates_real_stage_times():
    stages: dict[str, float] = {}
    add_stage_time(stages, "refinement", 1.25)
    add_stage_time(stages, "refinement", 0.75)
    merge_stage_times(stages, {"stitching": 0.5, "invalid": "x"})

    profile = build_performance_profile(
        stage_seconds=stages,
        total_s=3.0,
    )

    assert profile["schema"] == "talude-v2-performance/v1"
    assert profile["stages_s"]["refinement"] == 2.0
    assert profile["stages_s"]["stitching"] == 0.5
    assert "invalid" not in profile["stages_s"]
    assert profile["total_s"] == 3.0


def test_performance_profile_returns_slowest_tiles_and_faces():
    profile = build_performance_profile(
        tile_records=[
            {"tile_id": 7, "read_ms": 10.0, "refine_ms": 20.0},
            {"tile_id": 3, "elapsed_ms": 80.0},
            {"tile_id": 9, "elapsed_ms": 40.0},
        ],
        face_records=[
            {"face_id": 4, "refine_ms": 90.0},
            {"face_id": 2, "refine_ms": 10.0, "stitch_ms": 15.0},
        ],
        top_n=2,
    )

    assert [row["tile_id"] for row in profile["slowest_tiles"]] == [3, 9]
    assert [row["face_id"] for row in profile["slowest_faces"]] == [4, 2]


def test_v2_profiling_is_wired_without_replacing_geometry_engine():
    engine = Path("src/talude_v2/engine.py").read_text(encoding="utf-8")
    global_auto = Path("src/talude_v2/global_auto.py").read_text(encoding="utf-8")
    tiled = Path("src/talude_v2/tiled_auto.py").read_text(encoding="utf-8")
    worker = Path("studio/backend/auto_extract_v2.py").read_text(encoding="utf-8")
    builder = Path("scripts/build_windows.py").read_text(encoding="utf-8")
    localbuild = json.loads(
        Path("localbuild/talude_v2.json").read_text(encoding="utf-8")
    )

    for key in (
        "tin_build",
        "face_detection",
        "crest_toe_classification",
        "boundary_extraction",
        "refinement",
    ):
        assert f'timing_s["{key}"]' in engine

    assert "BASELINE_SHA" in global_auto
    assert "extract_profile_edge_pair" in global_auto
    assert "src/talude_v2/edge_profile.py" in localbuild["required_paths"]
    assert "performance_profile_path" in global_auto
    assert 'f"performance_{safe_profile_id}.json"' in global_auto
    assert '"slowest_tiles"' in Path(
        "src/talude_v2/profiling.py"
    ).read_text(encoding="utf-8")

    assert "tile_profile_records" in tiled
    assert "face_profile" in tiled
    assert '"profile": performance_profile' in tiled
    assert "profile_run_id=job_id" in worker
    assert 'profile_stages["vector_document"]' in worker

    assert '"talude_v2.profiling"' in builder
    assert "src/talude_v2/profiling.py" in localbuild["required_paths"]
    assert localbuild["config_revision"] >= 11
