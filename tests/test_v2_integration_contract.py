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
    assert 'APP_VERSION = "2.0.0-exp2-auto-global"' in server


def test_v2_viewer_can_switch_global_and_clicked_engines():
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    app = Path("studio/viewer/app.js").read_text(encoding="utf-8")
    auto = Path("studio/viewer/talude_auto.js").read_text(encoding="utf-8")

    assert 'id="geometryEngine"' in html
    assert 'data-engine="baseline"' in html
    assert 'data-engine="v2"' in html
    assert 'geometryEngine: "baseline"' in app
    assert '"/api/v2/feature-lines/terrain-face"' in app
    assert '"/api/feature-lines/terrain-face"' in app
    assert '"/api/v2/talude/auto"' in auto
    assert '"/api/talude/auto"' in auto
    assert 's.state.geometryEngine === "v2"' in auto


def test_v2_global_auto_has_progress_cancel_and_regression_fallback():
    engine = Path("src/talude_v2/global_auto.py").read_text(encoding="utf-8")
    worker = Path("studio/backend/auto_extract_v2.py").read_text(encoding="utf-8")
    converter = Path("studio/backend/converter.py").read_text(encoding="utf-8")
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")
    auto = Path("studio/viewer/talude_auto.js").read_text(encoding="utf-8")
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")

    assert "run_auto_global_v2" in engine
    assert "BASELINE_1_1_7_FALLBACK" in engine
    assert "_collect_roi_points_stream" in engine
    assert "_PriorityReservoir" in engine
    assert "extract_face_raw_tin" in engine
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
