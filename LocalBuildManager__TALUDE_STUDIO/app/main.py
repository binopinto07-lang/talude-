from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def prepare_app_root() -> Path:
    """Return a writable application root.

    In source mode it is the repository root. In a PyInstaller build we copy
    bundled project profiles next to the EXE on first launch so users can edit
    or add profiles without rebuilding the application.
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[1]

    exe_root = Path(sys.executable).resolve().parent
    exe_projects = exe_root / "projects"
    bundle_root = Path(getattr(sys, "_MEIPASS", exe_root))
    bundled_projects = bundle_root / "projects"
    exe_projects.mkdir(parents=True, exist_ok=True)
    if bundled_projects.exists():
        for src in bundled_projects.glob("*.json"):
            dst = exe_projects / src.name
            should_copy = not dst.exists()
            if dst.exists():
                try:
                    bundled = json.loads(src.read_text(encoding="utf-8"))
                    installed = json.loads(dst.read_text(encoding="utf-8"))
                    bundled_rev = int(bundled.get("config_revision", 0) or 0)
                    installed_rev = int(installed.get("config_revision", 0) or 0)
                    should_copy = bundled_rev > installed_rev
                except Exception:
                    # A broken installed profile must never block a healthy
                    # profile bundled with a newer manager.
                    should_copy = True
            if should_copy:
                shutil.copy2(src, dst)
    return exe_root


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Local Build Manager")
    app.setOrganizationName("LocalBuildManager")
    window = MainWindow(prepare_app_root())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
