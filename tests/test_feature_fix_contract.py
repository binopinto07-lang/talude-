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
