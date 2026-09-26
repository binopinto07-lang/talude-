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

    assert "/api/talude/auto" in auto
    assert "0xffd54a" in auto
    assert "0x38d5ff" in auto

    assert "QWebEngineView" in desktop
    assert "Talude Studio" in desktop
    assert '@app.post("/api/talude/auto")' in server


def test_primary_entrypoint_is_no_longer_tkinter():
    entry = Path("talude_gui.py").read_text(encoding="utf-8")
    assert "studio.desktop.main" in entry
    assert "tkinter" not in entry
