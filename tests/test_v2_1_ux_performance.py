from __future__ import annotations

import numpy as np
from pathlib import Path

from talude_v2.global_auto import (
    GlobalFaceCandidate,
    _partition_candidates_for_performance,
)


def _candidate(face_id: int, length: float = 40.0, width: float = 5.0) -> GlobalFaceCandidate:
    y = np.linspace(0.0, length, max(3, int(length) + 1))
    crest = np.column_stack((np.zeros_like(y), y, np.full_like(y, 10.0)))
    toe = np.column_stack((np.full_like(y, width), y, np.full_like(y, 5.0)))
    center = 0.5 * (crest + toe)
    return GlobalFaceCandidate(
        face_id=face_id,
        crest=crest,
        toe=toe,
        centerline=center,
        seed_xyz=center[len(center) // 2],
        corridor_radius_m=max(3.0, width),
        baseline_width_median=width,
        baseline_width_p90=width * 1.1,
    )


def test_qgis_layer_tree_and_cad_view_cube_contract():
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    css = Path("studio/viewer/styles.css").read_text(encoding="utf-8")
    js = Path("studio/viewer/vector_editor.js").read_text(encoding="utf-8")
    app = Path("studio/viewer/app.js").read_text(encoding="utf-8")

    for control in (
        'id="layersShowAll"',
        'id="layersHideAll"',
        'id="layersSoloActive"',
        'id="vectorLayers"',
        'id="viewCubeWidget"',
        'id="viewCube"',
        'id="navProfileButton"',
    ):
        assert control in html

    for view in ("top", "bottom", "front", "back", "left", "right", "iso"):
        assert f'data-cube-view="{view}"' in html

    assert ".qgis-layer-tree" in css
    assert ".qgis-group-header" in css
    assert ".view-cube-widget" in css
    assert "NUVEM DE PONTOS" in js
    assert "BREAKLINES" in js
    assert "ANÁLISE / SUPORTE" in js
    assert "setAllVectorLayersVisible" in js
    assert "soloActiveLayer" in js

    assert 'navigationProfile: "agisoft"' in app
    assert "onViewerDoubleClick" in app
    assert "event.button === 2" in app
    assert "event.shiftKey" in app
    assert "updateViewCubeOrientation" in app
    assert "setNavigationProfile" in app


def test_v2_performance_profile_is_wired_end_to_end():
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    auto = Path("studio/viewer/talude_auto.js").read_text(encoding="utf-8")
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")
    worker = Path("studio/backend/auto_extract_v2.py").read_text(encoding="utf-8")
    engine = Path("src/talude_v2/global_auto.py").read_text(encoding="utf-8")
    tiled = Path("src/talude_v2/tiled_auto.py").read_text(encoding="utf-8")

    assert 'id="v2PerformanceMode"' in html
    assert 'value="fast"' in html
    assert 'value="balanced"' in html
    assert 'value="precise"' in html
    assert 'id="lineVertexSpacing"' in html

    assert "performance_mode: performanceMode" in auto
    assert 'numberValue("lineVertexSpacing", 1.0)' in auto
    assert 'performance_mode: str = "balanced"' in server
    assert "performance_mode=req.performance_mode" in server
    assert 'mode not in {"fast", "balanced", "precise"}' in worker
    assert "performance_mode=mode" in worker
    assert "_partition_candidates_for_performance" in engine
    assert "FAST_BASELINE_ONLY" in engine
    assert "PERFORMANCE_BASELINE" in engine
    assert '"v2_attempted_faces"' in engine
    assert '"performance_skipped_faces"' in engine
    assert "_baseline_cache_dir" in engine
    assert "baseline_cache_hit" in engine
    assert "_persistent_spool_dir" in tiled
    assert "cache_hit_tiles" in tiled
    assert "manifest.json" in tiled


def test_fast_profile_never_sends_faces_to_raw_tin():
    candidates = [_candidate(i) for i in range(1, 11)]
    attempted, skipped, stats = _partition_candidates_for_performance(
        candidates,
        mode="fast",
        point_count=241_149_524,
    )
    assert attempted == []
    assert len(skipped) == 10
    assert stats["attempted_faces"] == 0
    assert stats["selection"] == "BASELINE_ONLY"


def test_balanced_large_cloud_caps_expensive_refinement_but_keeps_every_face_accounted():
    candidates = [_candidate(i, length=30.0 + (i % 10)) for i in range(1, 564)]
    attempted, skipped, stats = _partition_candidates_for_performance(
        candidates,
        mode="balanced",
        point_count=241_149_524,
    )
    assert len(attempted) == 60
    assert len(skipped) == 503
    assert len(attempted) + len(skipped) == 563
    assert stats["attempt_limit"] == 60
    assert stats["selection"] == "COHERENCE_PRIORITY"


def test_precise_profile_keeps_current_all_face_behaviour():
    candidates = [_candidate(i) for i in range(1, 21)]
    attempted, skipped, stats = _partition_candidates_for_performance(
        candidates,
        mode="precise",
        point_count=241_149_524,
    )
    assert len(attempted) == 20
    assert skipped == []
    assert stats["selection"] == "ALL_FACES"


def test_clicked_v2_has_protected_baseline_fallback():
    app = Path("studio/viewer/app.js").read_text(encoding="utf-8")
    assert "terrain_face.v2_fallback_started" in app
    assert 'api("/api/feature-lines/terrain-face"' in app
    assert "result.v2_fallback = true" in app


def test_v2_final_lines_are_resampled_to_operator_spacing():
    engine = Path("src/talude_v2/global_auto.py").read_text(encoding="utf-8")
    assert "_resample_polyline(xyz, output_spacing)" in engine
    assert '"vertex_spacing_m"' in engine
    assert '"output_vertex_spacing_m"' in engine
