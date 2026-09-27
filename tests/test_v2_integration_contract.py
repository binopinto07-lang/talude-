from pathlib import Path


def test_v2_api_is_isolated_from_baseline():
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")
    assert '@app.post("/api/feature-lines/terrain-face")' in server
    assert '@app.post("/api/v2/feature-lines/terrain-face")' in server
    assert "extract_terrain_face_from_points" in server
    assert "extract_face_raw_tin" in server
    assert 'APP_VERSION = "2.0.0-exp1-raw-tin-mst"' in server


def test_v2_viewer_can_switch_engines_without_replacing_global_auto():
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    app = Path("studio/viewer/app.js").read_text(encoding="utf-8")
    auto = Path("studio/viewer/talude_auto.js").read_text(encoding="utf-8")

    assert 'id="geometryEngine"' in html
    assert 'data-engine="baseline"' in html
    assert 'data-engine="v2"' in html
    assert 'geometryEngine: "baseline"' in app
    assert '"/api/v2/feature-lines/terrain-face"' in app
    assert '"/api/feature-lines/terrain-face"' in app
    assert '"/api/talude/auto"' in auto


def test_v2_build_is_pinned_to_experimental_branch():
    build = Path("localbuild/talude_v1.json").read_text(encoding="utf-8")
    assert '"branch": "v2"' in build
