import json
from pathlib import Path


def _step(cfg: dict, pipeline: str, step_id: str) -> dict:
    return next(step for step in cfg["pipelines"][pipeline] if step["id"] == step_id)


def test_localbuild_packages_studio_with_classify_las_r20_4():
    cfg = json.loads(Path("localbuild/talude_v1.json").read_text(encoding="utf-8"))

    assert cfg["name"] == "Talude Studio V1.2 — CLASSIFY LAS R20.4 + Crista/Pé"
    assert cfg["branch"] == "classify-las-r20-4-module"
    assert int(cfg["config_revision"]) >= 15

    build = cfg["pipelines"]["build"]
    commands = "\n".join(step["command"] for step in build)

    assert "studio/scripts/bootstrap_vendor.ps1" in commands
    assert "PySide6.QtWebEngineWidgets" in commands
    assert "--collect-all rasterio" in commands
    assert "--add-data '%REPO%/studio/viewer;studio/viewer'" in commands
    assert "--add-data '%REPO%/studio/vendor;studio/vendor'" in commands
    assert "talude_studio.py" in commands
    assert "--hidden-import ezdxf" in commands
    assert "--collect-all ezdxf" not in commands

    compile_command = _step(cfg, "test", "compile")["command"]
    assert "ALGORITM" in compile_command
    assert "studio" in compile_command
    assert "talude_studio.py" in compile_command

    verify_command = _step(cfg, "test", "verify-source")["command"]
    assert "TALUDE_STUDIO_CLASSIFY_LAS_R20_4_2026_10" in verify_command

    full_ids = [step["id"] for step in cfg["pipelines"]["full"]]
    assert "verify-source" in full_ids
    assert "deps-build" in full_ids
    assert "vendor" in full_ids
    assert "pyinstaller" in full_ids
    assert "self-test" in full_ids


def test_localbuild_embedded_manager_is_required():
    cfg = json.loads(Path("localbuild/talude_v1.json").read_text(encoding="utf-8"))
    required = set(cfg.get("required_paths", []))

    expected = {
        "START_BUILD_MANAGER.bat",
        "tools/LocalBuildManager_TALUDE/START_LOCAL_BUILD_MANAGER.bat",
        "tools/LocalBuildManager_TALUDE/ENSURE_PYTHON_312.bat",
        "tools/LocalBuildManager_TALUDE/requirements.txt",
        "tools/LocalBuildManager_TALUDE/run.py",
        "tools/LocalBuildManager_TALUDE/app/main.py",
        "tools/LocalBuildManager_TALUDE/app/ui/main_window.py",
    }
    assert expected <= required
    for path in expected:
        assert Path(path).is_file(), path
