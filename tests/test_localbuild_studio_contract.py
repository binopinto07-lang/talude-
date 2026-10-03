import json
from pathlib import Path


def test_localbuild_packages_studio_not_legacy_tkinter():
    cfg = json.loads(Path("localbuild/talude_v1.json").read_text(encoding="utf-8"))

    # A V2 experimental continua a usar o mesmo empacotamento desktop do
    # Talude Studio, mas o nome apresentado pelo Local Build Manager identifica
    # explicitamente o marco V2 TILED + REFINEMENT + VECTOR DOCUMENT. Não prender este contrato ao nome V1.
    assert cfg["name"] == "Talude Studio V2 — LAYERS + EDITOR + PROJECT + EXPORT"

    build = cfg["pipelines"]["build"]
    commands = "\n".join(step["command"] for step in build)

    assert "studio/scripts/bootstrap_vendor.ps1" in commands
    assert "scripts/build_windows.py" in commands
    assert "TaludeStudioBuild" in commands
    assert "%LOCALAPPDATA%" not in commands  # PowerShell uses $env:LOCALAPPDATA safely.
    assert "talude_studio.py" not in commands  # Long PyInstaller CLI moved into Python builder.

    compile_command = cfg["pipelines"]["test"][1]["command"]
    assert "studio" in compile_command
    assert "talude_studio.py" in compile_command

    full_ids = [step["id"] for step in cfg["pipelines"]["full"]]
    assert "deps-build" in full_ids
    assert "vendor" in full_ids
    assert "builder-preflight" in full_ids
    assert "pyinstaller" in full_ids
    assert "self-test" in full_ids


def test_short_path_windows_builder_contains_required_desktop_assets():
    text = Path("scripts/build_windows.py").read_text(encoding="utf-8")
    assert "PySide6.QtWebEngineWidgets" in text
    assert "studio/backend/vector_export" not in text
    assert "studio.backend.vector_export" in text
    assert "talude_v2.tiled_auto" in text
    assert "--add-data" in text
    assert "studio/viewer" in text
    assert "studio/vendor" in text
    assert "TaludeStudioV3Build" in text
    assert "--preflight" in text
    assert "studio.backend.vector_export" in text
    assert "Talude_V3.exe" in text
