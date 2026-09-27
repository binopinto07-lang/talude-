from __future__ import annotations

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


def main() -> int:
    repo = _repo_root()
    root = _short_root()
    dist = root / "dist"
    work = root / "work"
    spec = root / "spec"
    log = root / "BUILD_MANIFEST.json"

    _require(repo / "talude_studio.py", "Entry point")
    _require(repo / "studio" / "viewer" / "index.html", "Viewer")
    _require(repo / "studio" / "vendor", "Vendor Potree/PotreeConverter")

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

    print("=" * 72)
    print("TALUDE STUDIO WINDOWS BUILDER")
    print(f"Repo      : {repo}")
    print(f"Short root: {root}")
    print(f"Python    : {sys.executable}")
    print("=" * 72)

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
