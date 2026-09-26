from __future__ import annotations

import os
import sys
from pathlib import Path


def repo_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def studio_root() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base / "studio"
    return Path(__file__).resolve().parents[1]


def appdata_root() -> Path:
    base = os.environ.get("APPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / "Talude_V1"
    path.mkdir(parents=True, exist_ok=True)
    return path


def viewer_root() -> Path:
    return studio_root() / "viewer"


def vendor_root() -> Path:
    return studio_root() / "vendor"


def potree_root() -> Path:
    return vendor_root() / "potree"


def converter_executable() -> Path:
    explicit = os.environ.get("TALUDE_POTREE_CONVERTER")
    if explicit:
        return Path(explicit).expanduser().resolve()

    candidates = [
        vendor_root() / "potreeconverter" / "PotreeConverter.exe",
        vendor_root() / "potreeconverter" / "PotreeConverter",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
