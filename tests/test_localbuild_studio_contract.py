import json
from pathlib import Path


def test_localbuild_packages_studio_not_legacy_tkinter():
    cfg = json.loads(Path("localbuild/talude_v1.json").read_text(encoding="utf-8"))

    assert cfg["name"] == "Talude Studio V1 — Crista + Pé"

    build = cfg["pipelines"]["build"]
    commands = "\n".join(step["command"] for step in build)

    assert "studio/scripts/bootstrap_vendor.ps1" in commands
    assert "PySide6.QtWebEngineWidgets" in commands
    assert "--add-data 'studio/viewer;studio/viewer'" in commands
    assert "--add-data 'studio/vendor;studio/vendor'" in commands
    assert "talude_studio.py" in commands

    compile_command = cfg["pipelines"]["test"][1]["command"]
    assert "studio" in compile_command
    assert "talude_studio.py" in compile_command

    full_ids = [step["id"] for step in cfg["pipelines"]["full"]]
    assert "deps-build" in full_ids
    assert "vendor" in full_ids
    assert "pyinstaller" in full_ids
    assert "self-test" in full_ids
