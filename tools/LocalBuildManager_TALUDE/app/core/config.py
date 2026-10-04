from __future__ import annotations

import json
from pathlib import Path

from .models import ProjectConfig


def load_project_config(path: Path) -> ProjectConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ProjectConfig.from_dict(data)


def load_project_configs(projects_dir: Path) -> list[ProjectConfig]:
    configs: list[ProjectConfig] = []
    for path in sorted(projects_dir.glob("*.json")):
        configs.append(load_project_config(path))
    return configs


def find_repo_local_config(repo: Path, project_id: str) -> Path | None:
    """Return a repository-owned Local Build Manager profile when present."""
    candidates = [
        repo / "localbuild" / f"{project_id}.json",
        repo / "localbuild.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def discover_repo_local_configs(repo: Path) -> list[tuple[Path, ProjectConfig]]:
    """Discover every build profile committed inside a selected source folder.

    This works for both real Git clones and GitHub ZIP extractions.  The latter
    is important for Talude V2 because an extracted branch has no ``origin`` to
    auto-detect from, but it still contains ``localbuild/*.json``.
    """
    result: list[tuple[Path, ProjectConfig]] = []
    seen: set[str] = set()

    localbuild_dir = repo / "localbuild"
    candidates = sorted(localbuild_dir.glob("*.json")) if localbuild_dir.is_dir() else []
    root_profile = repo / "localbuild.json"
    if root_profile.is_file():
        candidates.append(root_profile)

    for path in candidates:
        try:
            cfg = load_project_config(path)
        except Exception:
            continue
        if cfg.id in seen:
            continue
        seen.add(cfg.id)
        result.append((path, cfg))
    return result


def config_revision(cfg: ProjectConfig) -> int:
    try:
        return int(cfg.raw.get("config_revision", 0) or 0)
    except Exception:
        return 0


def load_settings(path: Path) -> dict:
    if not path.exists():
        return {"project_paths": {}, "last_project": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"project_paths": {}, "last_project": None}


def save_settings(path: Path, settings: dict) -> None:
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
