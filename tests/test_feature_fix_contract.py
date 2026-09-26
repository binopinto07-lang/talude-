from pathlib import Path

import numpy as np

from studio.backend.terrain_face_engine import extract_terrain_face_from_points


def test_clicked_face_backend_uses_same_auto_detector():
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")
    bridge = Path("studio/backend/terrain_face_engine.py").read_text(encoding="utf-8")
    engine = Path("src/talude_v1/engine.py").read_text(encoding="utf-8")
    viewer = Path("studio/viewer/app.js").read_text(encoding="utf-8")

    assert '@app.post("/api/feature-lines/terrain-face")' in server
    assert "AUTO_FACE_V1_1_2_LOCAL" in bridge
    assert "detect_faces" in bridge
    assert "_component_lines" in bridge
    assert "seed_xy" in engine
    assert 'api("/api/feature-lines/terrain-face"' in viewer
    assert "drawFacePairCandidate" in viewer


def test_clicked_face_returns_crest_and_toe_from_synthetic_slope():
    rng = np.random.default_rng(123)
    xs = np.arange(0.0, 30.0, 0.20)
    ys = np.arange(0.0, 20.0, 0.20)
    xx, yy = np.meshgrid(xs, ys)
    zz = np.where(
        xx <= 10.0,
        10.0,
        np.where(xx >= 15.0, 5.0, 10.0 - (xx - 10.0)),
    )
    zz += rng.normal(0.0, 0.01, size=zz.shape)

    points = np.column_stack((xx.ravel(), yy.ravel(), zz.ravel()))
    classes = np.full(len(points), 2, dtype=np.int16)

    result = extract_terrain_face_from_points(
        points,
        [12.5, 10.0, 7.5],
        profile="face",
        classifications=classes,
        selected_classes=[2],
        grid_resolution=0.20,
    )

    assert result["detector"] == "AUTO_FACE_V1_1_2_LOCAL"
    assert len(result["lines"]) == 2
    assert result["crest"]["type"] == "CREST"
    assert result["toe"]["type"] == "TOE"
    assert len(result["crest"]["vertices"]) >= 2
    assert len(result["toe"]["vertices"]) >= 2


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


def test_orbit_pan_click_vs_drag_and_cad_views_are_wired():
    viewer = Path("studio/viewer/app.js").read_text(encoding="utf-8")
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    css = Path("studio/viewer/styles.css").read_text(encoding="utf-8")

    assert "viewer.setControls(viewer.orbitControls)" in viewer
    assert "onViewerNavMouseDown" in viewer
    assert "onViewerNavMouseMove" in viewer
    assert "onViewerNavMouseUp" in viewer
    assert "setPanMode" in viewer
    assert "controls.panDelta.x" in viewer
    assert "if (moved <= 5)" in viewer
    assert 'id="panModeButton"' in html
    assert "PAN: direito / meio / botão PAN" in html
    assert ".pan-button.active" in css
    assert "#potree_render_area.pan-mode" in css

    for view in ("top", "front", "back", "left", "right", "iso"):
        assert f'data-standard-view="{view}"' in html


def test_auto_unchecked_filter_falls_back_to_ground():
    auto = Path("studio/viewer/talude_auto.js").read_text(encoding="utf-8")
    backend = Path("studio/backend/auto_extract.py").read_text(encoding="utf-8")

    assert "if (!control || !control.checked) return null;" in auto
    assert "classes = normalized if normalized else None" in backend


def test_clicked_face_waits_for_density_and_caps_sparse_lod_cell():
    viewer = Path("studio/viewer/app.js").read_text(encoding="utf-8")
    bridge = Path("studio/backend/terrain_face_engine.py").read_text(encoding="utf-8")

    assert "minimumForSoftFinish" in viewer
    assert "hardTimeoutMs: 9000" in viewer
    assert "if cfg.cell_size <= 0.0 and cell > 0.35" in bridge
    assert "cell = 0.35" in bridge
    assert "auto_cell_capped" in bridge


def test_tin_edge_geometry_and_vertex_spacing_contract():
    config = Path("src/talude_v1/config.py").read_text(encoding="utf-8")
    engine = Path("src/talude_v1/engine.py").read_text(encoding="utf-8")
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    auto = Path("studio/viewer/talude_auto.js").read_text(encoding="utf-8")
    viewer = Path("studio/viewer/app.js").read_text(encoding="utf-8")

    assert "vertex_spacing_m: float = 1.0" in config
    assert "tin_snap_search_m: float = 1.25" in config
    assert "def _tin_break_score" in engine
    assert "def _face_boundary_pair" in engine
    assert "face-boundary-local-gradient" in engine
    assert "def _snap_line_to_tin_break" in engine
    assert "def _resample_xy_spacing" in engine
    assert 'id="vertexSpacing"' in html
    assert 'vertex_spacing_m: numberValue("vertexSpacing", 1.0)' in auto
    assert 'byId("vertexSpacing") ? byId("vertexSpacing").value : 1.0' in viewer


def test_vertex_spacing_resamples_a_straight_line_at_about_one_metre():
    from talude_v1.engine import _resample_xy_spacing

    xy = np.array(
        [[0.0, 0.0], [0.2, 0.0], [0.7, 0.0], [1.4, 0.0], [2.2, 0.0], [3.1, 0.0]],
        dtype=float,
    )
    out = _resample_xy_spacing(xy, 1.0)
    assert np.allclose(out[0], [0.0, 0.0])
    assert np.allclose(out[-1], [3.1, 0.0])
    distances = np.linalg.norm(np.diff(out, axis=0), axis=1)
    assert np.all(distances[:-1] >= 0.95)
    assert np.all(distances[:-1] <= 1.05)


def test_tin_break_score_peaks_at_synthetic_crest_and_toe():
    from talude_v1.engine import Grid, _tin_break_score

    cell = 0.25
    xs = np.arange(0.0, 20.0, cell)
    ys = np.arange(0.0, 8.0, cell)
    xx, yy = np.meshgrid(xs, ys)
    zz = np.where(
        xx < 7.0,
        10.0,
        np.where(xx > 10.0, 5.0, 10.0 - (xx - 7.0) * (5.0 / 3.0)),
    )
    grid = Grid(
        z=zz.astype(np.float32),
        valid=np.ones_like(zz, dtype=bool),
        x0=0.0,
        y0=0.0,
        cell=cell,
    )
    score = _tin_break_score(grid).mean(axis=0)
    top = np.argsort(score)[-6:]
    x_breaks = {(int(i) + 1) * cell for i in top}
    assert any(abs(x - 7.0) <= 0.5 for x in x_breaks)
    assert any(abs(x - 10.0) <= 0.5 for x in x_breaks)
