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
    assert "traceTerrainFace(hit).catch" in viewer
    assert "? traceRasterTerrain(hit)" not in viewer


def test_auto_engine_has_crs_dependency_and_safe_fallback():
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    io = Path("src/talude_v1/io.py").read_text(encoding="utf-8")
    build = Path("localbuild/talude_v1.json").read_text(encoding="utf-8")

    assert "pyproj" in requirements
    assert "crs_wkt = None" in io
    assert "--hidden-import pyproj" in build


def test_auto_face_gate_is_wired_end_to_end():
    config = Path("src/talude_v1/config.py").read_text(encoding="utf-8")
    engine = Path("src/talude_v1/engine.py").read_text(encoding="utf-8")
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")
    auto = Path("studio/viewer/talude_auto.js").read_text(encoding="utf-8")
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")

    assert "min_face_width_m" in config
    assert "min_face_height_m" in config
    assert "rejected_narrow_face" in engine
    assert "rejected_low_relief" in engine
    assert "face_filter" in engine
    assert "min_face_width_m" in server
    assert "min_face_height_m" in server
    assert 'numberValue("minFaceWidth", 0)' in auto
    assert 'numberValue("minFaceHeight", 0)' in auto
    assert 'id="minFaceWidth"' in html
    assert 'id="minFaceHeight"' in html
