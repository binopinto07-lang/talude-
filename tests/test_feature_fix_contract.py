from pathlib import Path


def test_individual_crest_toe_backend_is_wired():
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")
    bridge = Path("studio/backend/terrain_face_engine.py").read_text(encoding="utf-8")
    core = Path("core/terrain_face.py").read_text(encoding="utf-8")
    viewer = Path("studio/viewer/app.js").read_text(encoding="utf-8")

    assert '@app.post("/api/feature-lines/terrain-face")' in server
    assert "extract_terrain_face_from_points" in server
    assert "extract_terrain_face_edge" in bridge
    assert "def extract_terrain_face_edge" in core
    assert 'api("/api/feature-lines/terrain-face"' in viewer


def test_auto_engine_has_crs_dependency_and_safe_fallback():
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    io = Path("src/talude_v1/io.py").read_text(encoding="utf-8")
    build = Path("localbuild/talude_v1.json").read_text(encoding="utf-8")

    assert "pyproj" in requirements
    assert "crs_wkt = None" in io
    assert "--hidden-import pyproj" in build


def test_auto_detector_is_restored_to_112_without_face_gate():
    config = Path("src/talude_v1/config.py").read_text(encoding="utf-8")
    engine = Path("src/talude_v1/engine.py").read_text(encoding="utf-8")
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")
    auto = Path("studio/viewer/talude_auto.js").read_text(encoding="utf-8")
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")

    assert "min_face_width_m" not in config
    assert "min_face_height_m" not in config
    assert "rejected_narrow_face" not in engine
    assert "rejected_low_relief" not in engine
    assert '"face_filter"' not in engine
    assert "min_face_width_m" not in server
    assert "min_face_height_m" not in server
    assert 'numberValue("minFaceWidth"' not in auto
    assert 'numberValue("minFaceHeight"' not in auto
    assert 'id="minFaceWidth"' not in html
    assert 'id="minFaceHeight"' not in html


def test_output_lines_are_smoothed_without_changing_detection_stage():
    config = Path("src/talude_v1/config.py").read_text(encoding="utf-8")
    engine = Path("src/talude_v1/engine.py").read_text(encoding="utf-8")

    assert "line_smooth_window: int = 11" in config
    assert "Reamostragem uniforme" in engine
    assert "savgol_filter" in engine
    assert "smoothed[0] = pts[0]" in engine
    assert "smoothed[-1] = pts[-1]" in engine


def test_orbit_navigation_click_vs_drag_and_cad_views_are_wired():
    viewer = Path("studio/viewer/app.js").read_text(encoding="utf-8")
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    css = Path("studio/viewer/styles.css").read_text(encoding="utf-8")

    assert "viewer.setControls(viewer.orbitControls)" in viewer
    assert "onViewerNavMouseDown" in viewer
    assert "onViewerNavMouseUp" in viewer
    assert "if (moved <= 5)" in viewer
    assert "setStandardView" in viewer
    assert "setIsoView" in viewer

    for view in ("top", "front", "back", "left", "right", "iso"):
        assert f'data-standard-view="{view}"' in html

    assert "Rodar: arrastar esquerdo" in html
    assert "Picar: clique curto" in html
    assert ".standard-views" in css


def test_local_terrain_face_uses_adaptive_lod_grid():
    core = Path("core/terrain_face.py").read_text(encoding="utf-8")

    assert "density_resolution" in core
    assert "retry_resolution" in core
    assert "Poucos pontos locais para formar a superfície do talude" in core
