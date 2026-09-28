from pathlib import Path


def test_v2_api_is_isolated_from_baseline_and_has_global_auto():
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")
    assert '@app.post("/api/feature-lines/terrain-face")' in server
    assert '@app.post("/api/v2/feature-lines/terrain-face")' in server
    assert '@app.post("/api/talude/auto")' in server
    assert '@app.post("/api/v2/talude/auto")' in server
    assert "start_auto_extract" in server
    assert "start_auto_extract_v2" in server
    assert "extract_terrain_face_from_points" in server
    assert "extract_face_raw_tin" in server
    assert 'APP_VERSION = "2.4.1-profile-edge-global"' in server


def test_v2_viewer_can_switch_global_and_clicked_engines():
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    app = Path("studio/viewer/app.js").read_text(encoding="utf-8")
    auto = Path("studio/viewer/talude_auto.js").read_text(encoding="utf-8")

    assert 'id="geometryEngine"' in html
    assert 'data-engine="baseline"' in html
    assert 'data-engine="v2"' in html
    assert 'geometryEngine: "v2"' in app
    assert '"/api/v2/feature-lines/terrain-face"' in app
    assert '"/api/feature-lines/terrain-face"' in app
    assert '"/api/v2/talude/auto"' in auto
    assert 'const useV2 = true' in auto


def test_v2_global_auto_has_progress_cancel_and_regression_fallback():
    engine = Path("src/talude_v2/global_auto.py").read_text(encoding="utf-8")
    worker = Path("studio/backend/auto_extract_v2.py").read_text(encoding="utf-8")
    converter = Path("studio/backend/converter.py").read_text(encoding="utf-8")
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")
    auto = Path("studio/viewer/talude_auto.js").read_text(encoding="utf-8")
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")

    assert "run_auto_global_v2" in engine
    assert "_collect_roi_points_stream" in engine
    assert "STREAM_FACE_GLOBAL" in engine
    assert '"review_geometry": False' in engine
    assert "_PriorityReservoir" in engine
    assert "extract_profile_edge_pair" in engine
    assert "talude_review.geojson" in engine
    assert "REVIEW_REQUIRED" in engine
    assert "reason_counts" in engine
    assert "v2_success_faces" in engine
    assert "baseline_fallback_faces" in engine

    assert "jobs.is_cancel_requested" in worker
    assert "V2Reason.CANCELLED" in worker
    assert "request_cancel" in converter
    assert "is_cancel_requested" in converter
    assert '@app.post("/api/jobs/{job_id}/cancel")' in server
    assert 'id="cancelJob"' in html
    assert '"/cancel"' in auto


def test_v2_build_is_pinned_to_experimental_branch():
    build = Path("localbuild/talude_v1.json").read_text(encoding="utf-8")
    assert '"branch": "v2-experimental-raw-tin-mst"' in build


def test_v2_phases_3_to_5_are_wired_end_to_end():
    global_engine = Path("src/talude_v2/global_auto.py").read_text(encoding="utf-8")
    tiled = Path("src/talude_v2/tiled_auto.py").read_text(encoding="utf-8")
    engine = Path("src/talude_v2/engine.py").read_text(encoding="utf-8")
    vector = Path("src/talude_v2/vector_document.py").read_text(encoding="utf-8")
    vector_backend = Path("studio/backend/vector_documents.py").read_text(encoding="utf-8")
    auto_v2 = Path("studio/backend/auto_extract_v2.py").read_text(encoding="utf-8")
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")

    # Phase 3 — active production path no longer stitches per-tile line fragments.
    assert "_collect_roi_points_stream" in global_engine
    assert "STREAM_FACE_GLOBAL" in global_engine
    assert "process_candidates_tiled(" not in global_engine
    assert "_spool_tiles" in tiled  # legacy/diagnostic implementation remains isolated.
    assert "stitch_fragments" in tiled
    assert "tile_size_m" in engine
    assert "tile_halo_m" in engine

    # Phase 4
    assert "_support_aware_smooth" in engine
    assert "_bridge_short_refine_gaps" in engine
    assert "support_ratio" in engine
    assert "bridged_stations" in engine
    assert "refine_support_target" in engine

    # Phase 5
    assert 'SCHEMA = "talude-vector-document/v1"' in vector
    assert "create_vector_document" in vector
    assert "write_documents_from_geojson" in vector_backend
    assert "active_vector_document" in auto_v2
    assert '@app.get("/api/projects/{project_id}/vector-document")' in server
    assert '@app.get("/api/projects/{project_id}/vector-document/summary")' in server


def test_v2_phases_6_to_9_are_wired_end_to_end():
    vector = Path("src/talude_v2/vector_document.py").read_text(encoding="utf-8")
    backend = Path("studio/backend/vector_documents.py").read_text(encoding="utf-8")
    export = Path("studio/backend/vector_export.py").read_text(encoding="utf-8")
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    js = Path("studio/viewer/vector_editor.js").read_text(encoding="utf-8")
    build = Path("scripts/build_windows.py").read_text(encoding="utf-8")

    assert '"FACES"' in vector and '"DEBUG"' in vector
    assert '"CRISTA_REVIEW"' in vector
    assert '"PE_TALUDE_REVIEW"' in vector
    assert '"FACES_REJEITADAS"' in vector
    assert "save_active_document" in backend
    assert "recover_revision" in backend
    assert "export_shapefiles" in export
    assert "export_gpkg" in export
    assert "export_dxf" in export
    assert "vector-document/export" in server
    assert "vector-document/recover" in server
    assert 'id="vectorLayers"' in html
    assert "vectorMoveVertex" in js
    assert "vectorInsertVertex" in js
    assert "vectorDeleteVertex" in js
    assert "vectorDeleteLine" in js
    assert "approveSelectedFace" in js
    assert "rejectSelectedFace" in js
    assert "TaludeStudioBuild" in build


def test_v2_local_click_has_safe_baseline_fallback():
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")

    assert "soft_rejections" in server
    assert "V2Reason.FACE_TOO_SMALL" in server
    assert "V2Reason.LOW_GROUND_SUPPORT" in server
    assert "V2_LOCAL_FALLBACK_1_1_7" in server
    assert "v2_raw_tin.local_fallback" in server
    assert "extract_terrain_face_from_points" in server
