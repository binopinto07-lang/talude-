from __future__ import annotations

from datetime import datetime
from pathlib import Path


def new_log_file(repo: Path, prefix: str = "build") -> Path:
    folder = repo / "logs" / "local_build_manager"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return folder / f"{prefix}_{stamp}.log"


def append_log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
