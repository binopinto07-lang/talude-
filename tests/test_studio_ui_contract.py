from pathlib import Path


def test_studio_uses_professional_cloud_viewer():
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    css = Path("studio/viewer/styles.css").read_text(encoding="utf-8")
    app = Path("studio/viewer/app.js").read_text(encoding="utf-8")
    auto = Path("studio/viewer/talude_auto.js").read_text(encoding="utf-8")
    desktop = Path("studio/desktop/main.py").read_text(encoding="utf-8")
    server = Path("studio/backend/server.py").read_text(encoding="utf-8")

    assert "TALUDE STUDIO" in html
    assert 'id="potree_render_area"' in html
    assert 'id="detectTalude"' in html
    assert "CRISTA + PÉ" in html
    assert "./talude_auto.js" in html

    assert ".sidebar" in css
    assert ".auto-talude-card" in css
    assert "talude-crest" in css
    assert "talude-toe" in css

    assert "new Potree.Viewer" in app
    assert "render.pass.perspective_overlay" in app
    assert "window.TaludeShell" in app
    assert "createLineObject" in app

    assert "/api/v2/talude/auto" in auto
    assert "0xe34b4b" in auto
    assert "0x2f80ed" in auto

    assert "QWebEngineView" in desktop
    assert "Talude Studio" in desktop
    assert '@app.post("/api/talude/auto")' in server


def test_primary_entrypoint_is_no_longer_tkinter():
    entry = Path("talude_gui.py").read_text(encoding="utf-8")
    assert "studio.desktop.main" in entry
    assert "tkinter" not in entry


def test_v22_qgis_agisoft_cad_navigation_contract():
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    css = Path("studio/viewer/styles.css").read_text(encoding="utf-8")
    app = Path("studio/viewer/app.js").read_text(encoding="utf-8")
    editor = Path("studio/viewer/vector_editor.js").read_text(encoding="utf-8")

    # QGIS-style dock/layer tree.
    assert 'id="sidebarResizeHandle"' in html
    assert 'id="vectorLayers"' in html
    assert 'id="layersShowAll"' in html
    assert 'id="layersHideAll"' in html
    assert 'id="layersSoloActive"' in html
    assert "--sidebar-width" in css
    assert ".sidebar-resize-handle" in css
    assert ".qgis-layer-tree" in css
    assert "NUVEM DE PONTOS" in editor
    assert "BREAKLINES" in editor
    assert "ANÁLISE / SUPORTE" in editor

    # Agisoft-like orbit/pan/pivot.
    assert "viewer.setControls(viewer.orbitControls)" in app
    assert "onViewerDoubleClick" in app
    assert "pointCloudIntersectionFromEvent" in app
    assert "navigationProfile" in app
    assert 'id="navProfileButton"' in html
    assert "Shift+esquerdo" in html

    # CAD/progeCAD-style standard views and WCS.
    for view in ("top", "front", "back", "left", "right", "iso"):
        assert f'data-standard-view="{view}"' in html
    for view in ("top", "bottom", "front", "back", "left", "right"):
        assert f'data-cube-view="{view}"' in html
    assert 'id="viewCubeWidget"' in html
    assert 'id="viewAxisWidget"' in html
    assert 'id="orthoModeButton"' in html
    assert "setCameraProjectionMode" in app
    assert "Potree.CameraMode.ORTHOGRAPHIC" in app
    assert ".view-cube-widget" in css
    assert ".view-axis-widget" in css


def test_precision_workspace_contract():
    html = Path("studio/viewer/index.html").read_text(encoding="utf-8")
    css = Path("studio/viewer/precision_workspace.css").read_text(encoding="utf-8")
    js = Path("studio/viewer/precision_workspace.js").read_text(encoding="utf-8")
    auto = Path("studio/viewer/talude_auto.js").read_text(encoding="utf-8")
    editor = Path("studio/viewer/vector_editor.js").read_text(encoding="utf-8")

    assert "./precision_workspace.css" in html
    assert "./precision_workspace.js" in html
    assert "precisionApplicationBar" in js
    assert "precisionCommandBar" in js
    assert "precisionInspector" in js
    assert "precisionEditRail" in js
    assert "precisionDigitizing" in js
    assert "Point Cloud" in js
    assert "SNAPPING" in js

    assert "grid-template-columns: minmax(250px, 17%) minmax(0, 1fr) minmax(260px, 17%)" in css
    assert "--precision-viewport: #171b1f" in css
    assert "--precision-active: #eaf4f8" in css
    assert ".precision-edit-rail" in css
    assert ".precision-inspector" in css

    # Geometry colours are distinct from the blue UI accent.
    assert "0xe34b4b" in auto
    assert "0x2f80ed" in auto
    assert "0xe34b4b" in editor
    assert "0x2f80ed" in editor
