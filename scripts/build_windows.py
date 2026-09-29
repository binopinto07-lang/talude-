from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

import PyInstaller.__main__


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _short_root() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return local / "LBM" / "TaludeStudioBuild"


def _require(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} não encontrado: {path}")


def _module_preflight(repo: Path) -> list[tuple[str, str]]:
    for path in (repo, repo / "src"):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)

    modules = [
        "numpy",
        "scipy",
        "laspy",
        "lazrs",
        "ezdxf",
        "pyproj",
        "fastapi",
        "uvicorn",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebChannel",
        "talude_v1.engine",
        "talude_v2.engine",
        "talude_v2.edge_profile",
        "talude_v2.section_edge_tracker",
        "talude_v2.plane_edge_snap",
        "core.terrain_face",
        "talude_v2.profiling",
        "talude_v2.tiled_auto",
        "talude_v2.vector_document",
        "studio.backend.server",
        "studio.backend.vector_documents",
        "studio.backend.vector_export",
        "studio.desktop.main",
    ]
    result: list[tuple[str, str]] = []
    for name in modules:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", "ok")
        result.append((name, str(version)))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Validar imports/assets sem executar PyInstaller.",
    )
    args_cli = parser.parse_args()

    repo = _repo_root()
    root = _short_root()
    dist = root / "dist"
    work = root / "work"
    spec = root / "spec"
    log = root / "BUILD_MANIFEST.json"

    _require(repo / "talude_studio.py", "Entry point")
    _require(repo / "studio" / "viewer" / "index.html", "Viewer")
    _require(repo / "studio" / "viewer" / "vector_editor.js", "Vector Editor")
    _require(repo / "studio" / "vendor", "Vendor Potree/PotreeConverter")

    print("=" * 72)
    print("TALUDE STUDIO BUILD PREFLIGHT")
    print(f"Repo      : {repo}")
    print(f"Short root: {root}")
    print(f"Python    : {sys.executable}")
    for name, version in _module_preflight(repo):
        print(f"[OK] {name} | {version}")
    print("=" * 72)

    if args_cli.preflight:
        return 0

    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    for path in (dist, work, spec):
        path.mkdir(parents=True, exist_ok=True)

    args = [
        "--noconfirm",
        "--clean",
        "--windowed",
        "--noupx",
        "--name", "Talude_V1",
        "--distpath", str(dist),
        "--workpath", str(work),
        "--specpath", str(spec),
        "--paths", str(repo),
        "--paths", str(repo / "src"),
        "--hidden-import", "PySide6.QtWebEngineWidgets",
        "--hidden-import", "PySide6.QtWebEngineCore",
        "--hidden-import", "PySide6.QtWebChannel",
        "--hidden-import", "uvicorn.loops.asyncio",
        "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "h11",
        "--hidden-import", "laspy",
        "--hidden-import", "laspy.lasreader",
        "--hidden-import", "laspy.laswriter",
        "--hidden-import", "lazrs",
        "--hidden-import", "ezdxf",
        "--hidden-import", "pyproj",
        "--hidden-import", "talude_v2.edge_profile",
        "--hidden-import", "talude_v2.section_edge_tracker",
        "--hidden-import", "talude_v2.plane_edge_snap",
        "--hidden-import", "core.terrain_face",
        "--hidden-import", "talude_v2.profiling",
        "--hidden-import", "talude_v2.tiled_auto",
        "--hidden-import", "talude_v2.vector_document",
        "--hidden-import", "studio.backend.vector_documents",
        "--hidden-import", "studio.backend.vector_export",
        "--collect-all", "uvicorn",
        "--collect-all", "h11",
        "--collect-all", "fastapi",
        "--collect-all", "starlette",
        "--collect-all", "anyio",
        "--collect-all", "scipy",
        "--collect-all", "pyproj",
        "--add-data", f"{repo / 'studio' / 'viewer'}{os.pathsep}studio/viewer",
        "--add-data", f"{repo / 'studio' / 'vendor'}{os.pathsep}studio/vendor",
        str(repo / "talude_studio.py"),
    ]

    manifest = {
        "python": sys.executable,
        "repo": str(repo),
        "short_root": str(root),
        "dist": str(dist),
        "work": str(work),
        "spec": str(spec),
        "args": args,
    }
    log.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("TALUDE STUDIO WINDOWS BUILDER · PyInstaller")
    try:
        PyInstaller.__main__.run(args)
    except SystemExit as exc:
        code = int(exc.code or 0)
        if code:
            print(f"[ERRO] PyInstaller terminou com código {code}.")
        return code
    except BaseException:
        traceback.print_exc()
        return 1

    exe = dist / "Talude_V1" / "Talude_V1.exe"
    if not exe.exists():
        print(f"[ERRO] EXE não criado: {exe}")
        return 2

    print(f"[OK] EXE: {exe}")
    print(f"[OK] Tamanho: {exe.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
