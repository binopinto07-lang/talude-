from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Step:
    id: str
    name: str
    command: str
    required: bool = True
    timeout_minutes: int = 30
    working_dir: str = "."
    category: str = "build"
    venv: str = "default"


@dataclass
class Check:
    id: str
    name: str
    command: str
    required: bool = True
    hint: str = ""


@dataclass
class ProjectConfig:
    id: str
    name: str
    repo_url: str
    branch: str = "main"
    python_version: str = "3.12"
    checks: list[Check] = field(default_factory=list)
    pipelines: dict[str, list[Step]] = field(default_factory=dict)
    release: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectConfig":
        checks = [Check(**item) for item in data.get("checks", [])]
        pipelines: dict[str, list[Step]] = {}
        for key, values in data.get("pipelines", {}).items():
            pipelines[key] = [Step(**item) for item in values]
        return cls(
            id=data["id"],
            name=data["name"],
            repo_url=data.get("repo_url", ""),
            branch=data.get("branch", "main"),
            python_version=data.get("python_version", "3.12"),
            checks=checks,
            pipelines=pipelines,
            release=data.get("release", {}),
            raw=data,
        )

    def resolve(self, repo_path: Path, venv_python: Path | None = None) -> dict[str, str]:
        repo = repo_path.resolve()
        if venv_python is None:
            venv_python = repo / ".venv" / "Scripts" / "python.exe"
        builds = repo / "builds"
        work = builds / "local_work"
        dist_name = str(self.raw.get("dist_dir_name") or ("Cloud_to_lines_V2" if self.id == "cloud_to_lines" else self.id))
        dist = work / "dist" / dist_name
        latest = builds / "latest"
        archive = builds / "archive"
        return {
            "%REPO%": str(repo),
            "%PYTHON%": str(venv_python),
            "%BUILDS%": str(builds),
            "%WORK%": str(work),
            "%DIST%": str(dist),
            "%LATEST%": str(latest),
            "%ARCHIVE%": str(archive),
        }
