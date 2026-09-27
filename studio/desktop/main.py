from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

import uvicorn
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView

from studio.backend.paths import converter_executable, potree_root, viewer_root
from studio.backend.server import APP_VERSION, app


_RUNTIME_STREAMS: list[object] = []


def _ensure_runtime_streams() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return

    log_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Talude_V1" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stream = open(log_dir / "TALUDE_STUDIO_RUNTIME.log", "a", encoding="utf-8", buffering=1)
    _RUNTIME_STREAMS.append(stream)

    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _serve(port: int, errors: list[str]) -> None:
    try:
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            log_config=None,
            use_colors=False,
            loop="asyncio",
            http="h11",
            ws="none",
            lifespan="off",
        )
        uvicorn.Server(config).run()
    except BaseException:
        errors.append(traceback.format_exc())


def _wait_for_server(url: str, errors: list[str], timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=0.8) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last = exc
            time.sleep(0.15)
    detail = errors[-1] if errors else ""
    raise RuntimeError(f"Servidor Talude Studio não arrancou: {last}\n{detail}")


class DesktopBridge(QtCore.QObject):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._window: QtWidgets.QWidget | None = None

    def bind_window(self, window: QtWidgets.QWidget) -> None:
        self._window = window

    @QtCore.Slot(result=str)
    def choose_project_parent(self) -> str:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self._window,
            "Escolher pasta para o projeto Talude",
        )
        return path or ""

    @QtCore.Slot(result=str)
    def choose_project_folder(self) -> str:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self._window,
            "Abrir projeto Talude",
        )
        return path or ""

    @QtCore.Slot(result=str)
    def choose_cloud(self) -> str:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._window,
            "Adicionar nuvem",
            "",
            "Nuvens de pontos (*.las *.laz);;LAS (*.las);;LAZ / COPC (*.laz)",
        )
        return path or ""

    @QtCore.Slot(str, result=bool)
    def open_folder(self, path: str) -> bool:
        folder = Path(path).expanduser()
        if not folder.exists():
            return False
        return bool(QtGui.QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve()))))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.setWindowTitle(f"Talude Studio — Crista + Pé Automático — {APP_VERSION}")
        self.resize(1600, 960)
        self.setMinimumSize(1180, 720)

        self.web = QWebEngineView(self)
        self.setCentralWidget(self.web)

        self.bridge = DesktopBridge(self)
        self.bridge.bind_window(self)

        self.channel = QWebChannel(self.web.page())
        self.channel.registerObject("desktopBridge", self.bridge)
        self.web.page().setWebChannel(self.channel)

        settings = self.web.settings()
        settings.setAttribute(settings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(settings.WebAttribute.JavascriptEnabled, True)

        self.web.load(QUrl(base_url + "/app/"))


def self_test() -> int:
    _ensure_runtime_streams()

    checks: list[tuple[str, bool, str]] = []
    try:
        from PySide6 import QtWebChannel, QtWebEngineCore, QtWebEngineWidgets  # noqa: F401
        checks.append(("PySide6 QtWebEngine", True, "ok"))
    except Exception as exc:
        checks.append(("PySide6 QtWebEngine", False, repr(exc)))

    try:
        from talude_v1.engine import extract  # noqa: F401
        checks.append(("BREAKLINE_ENGINE_V1", True, "ok"))
    except Exception as exc:
        checks.append(("BREAKLINE_ENGINE_V1", False, repr(exc)))

    try:
        from talude_v2 import extract_face_raw_tin, run_auto_global_v2  # noqa: F401
        checks.append(("BREAKLINE_ENGINE_V2_RAW_TIN_MST", True, "experimental"))
        checks.append(("AUTO_GLOBAL_V2", True, "second-delivery"))
    except Exception as exc:
        checks.append(("BREAKLINE_ENGINE_V2_RAW_TIN_MST", False, repr(exc)))
        checks.append(("AUTO_GLOBAL_V2", False, repr(exc)))

    try:
        import pyproj  # noqa: F401
        checks.append(("pyproj", True, pyproj.__version__))
    except Exception as exc:
        checks.append(("pyproj", False, repr(exc)))

    try:
        from studio.backend.terrain_face_engine import extract_terrain_face_from_points  # noqa: F401
        from talude_v1.engine import detect_faces  # noqa: F401
        checks.append(("Clicked Face AUTO engine", True, "ok"))
    except Exception as exc:
        checks.append(("Clicked Face AUTO engine", False, repr(exc)))

    assets = [
        ("Potree", potree_root() / "build" / "potree" / "potree.js"),
        ("PotreeConverter", converter_executable()),
        ("Viewer HTML", viewer_root() / "index.html"),
        ("Viewer JS", viewer_root() / "app.js"),
    ]
    for name, path in assets:
        checks.append((name, path.exists(), str(path)))

    ok = all(item[1] for item in checks)
    lines = ["TALUDE_V1_SELF_TEST=" + ("OK" if ok else "FAILED")]
    lines.extend(f"{name}={'OK' if passed else 'FAIL'} | {detail}" for name, passed, detail in checks)

    Path("TALUDE_V1_SELF_TEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    _ensure_runtime_streams()
    errors: list[str] = []
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    thread = threading.Thread(
        target=_serve,
        args=(port, errors),
        daemon=True,
        name="talude-local-api",
    )
    thread.start()
    _wait_for_server(base_url, errors)

    qt_app = QtWidgets.QApplication(sys.argv)
    qt_app.setApplicationName("Talude Studio")
    qt_app.setOrganizationName("Talude")
    window = MainWindow(base_url)
    window.show()
    return int(qt_app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
