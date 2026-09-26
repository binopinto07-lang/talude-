from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .debug_log import append_project_debug
from .paths import appdata_root


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _slug(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    return text.strip("._-") or "project"


@dataclass(frozen=True)
class ProjectRef:
    id: str
    name: str
    path: Path


class ProjectStore:
    def __init__(self) -> None:
        self.index_path = appdata_root() / "projects.json"
        if not self.index_path.exists():
            _atomic_json(self.index_path, {"version": 1, "projects": []})

    def _read_index(self) -> dict[str, Any]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "projects": []}

    def _write_index(self, data: dict[str, Any]) -> None:
        _atomic_json(self.index_path, data)

    def list_projects(self) -> list[dict[str, Any]]:
        data = self._read_index()
        result = []
        changed = False
        for item in data.get("projects", []):
            path = Path(item["path"])
            exists = (path / "project.json").exists()
            enriched = dict(item)
            enriched["exists"] = exists
            result.append(enriched)
            if item.get("exists") != exists:
                changed = True
        if changed:
            data["projects"] = [{k: v for k, v in x.items() if k != "exists"} for x in result]
            self._write_index(data)
        return result

    def create_project(self, folder: str | Path, name: str) -> dict[str, Any]:
        parent = Path(folder).expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)

        project_dir = parent / _slug(name)
        if project_dir.exists() and any(project_dir.iterdir()):
            suffix = uuid.uuid4().hex[:6]
            project_dir = parent / f"{_slug(name)}_{suffix}"
        project_dir.mkdir(parents=True, exist_ok=True)

        for dirname in ("sources", "clouds", "vectors", "cache", "exports", "logs"):
            (project_dir / dirname).mkdir(exist_ok=True)

        project_id = uuid.uuid4().hex
        manifest = {
            "schema": "talude-project/v1",
            "id": project_id,
            "name": name.strip() or project_dir.name,
            "created_at": _now(),
            "updated_at": _now(),
            "crs": None,
            "vertical": None,
            "clouds": [],
            "terrain": {},
        }
        _atomic_json(project_dir / "project.json", manifest)
        _atomic_json(
            project_dir / "state.json",
            {
                "schema": "talude-state/v1",
                "updated_at": _now(),
                "camera": None,
                "display": {
                    "edl": True,
                    "point_budget": 7_500_000,
                    "background": "black",
                },
                "feature_lines": [],
            },
        )

        index = self._read_index()
        index.setdefault("projects", [])
        index["projects"] = [p for p in index["projects"] if p.get("id") != project_id]
        index["projects"].insert(
            0,
            {
                "id": project_id,
                "name": manifest["name"],
                "path": str(project_dir),
                "updated_at": manifest["updated_at"],
            },
        )
        self._write_index(index)
        append_project_debug(
            project_dir,
            "project.created",
            {
                "project_id": project_id,
                "project_name": manifest["name"],
                "project_path": str(project_dir),
                "schema": manifest["schema"],
            },
            source="project_store",
        )
        return {**manifest, "path": str(project_dir)}

    def open_project(self, path: str | Path) -> dict[str, Any]:
        project_dir = Path(path).expanduser().resolve()
        manifest_path = project_dir / "project.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"project.json não encontrado em {project_dir}")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        index = self._read_index()
        index.setdefault("projects", [])
        index["projects"] = [p for p in index["projects"] if p.get("id") != manifest["id"]]
        index["projects"].insert(
            0,
            {
                "id": manifest["id"],
                "name": manifest["name"],
                "path": str(project_dir),
                "updated_at": manifest.get("updated_at", _now()),
            },
        )
        self._write_index(index)
        append_project_debug(
            project_dir,
            "project.opened",
            {
                "project_id": manifest["id"],
                "project_name": manifest["name"],
                "project_path": str(project_dir),
                "cloud_count": len(manifest.get("clouds", [])),
            },
            source="project_store",
        )
        return {**manifest, "path": str(project_dir)}

    def get(self, project_id: str) -> ProjectRef:
        for item in self._read_index().get("projects", []):
            if item.get("id") == project_id:
                path = Path(item["path"]).resolve()
                manifest_path = path / "project.json"
                if not manifest_path.exists():
                    raise FileNotFoundError(f"Projeto indisponível: {path}")
                return ProjectRef(project_id, item["name"], path)
        raise KeyError(project_id)

    def manifest(self, project_id: str) -> dict[str, Any]:
        ref = self.get(project_id)
        return json.loads((ref.path / "project.json").read_text(encoding="utf-8"))

    def save_manifest(self, project_id: str, manifest: dict[str, Any]) -> None:
        ref = self.get(project_id)
        manifest["updated_at"] = _now()
        _atomic_json(ref.path / "project.json", manifest)
        append_project_debug(
            ref.path,
            "project.manifest_saved",
            {
                "project_id": project_id,
                "cloud_count": len(manifest.get("clouds", [])),
                "crs_present": bool(manifest.get("crs")),
                "updated_at": manifest["updated_at"],
            },
            source="project_store",
        )

    def state(self, project_id: str) -> dict[str, Any]:
        ref = self.get(project_id)
        return json.loads((ref.path / "state.json").read_text(encoding="utf-8"))

    def save_state(self, project_id: str, state: dict[str, Any]) -> None:
        ref = self.get(project_id)
        state["updated_at"] = _now()
        _atomic_json(ref.path / "state.json", state)
        append_project_debug(
            ref.path,
            "project.state_saved",
            {
                "project_id": project_id,
                "feature_line_count": len(state.get("feature_lines", [])),
                "camera_present": state.get("camera") is not None,
                "updated_at": state["updated_at"],
            },
            source="project_store",
        )


    def register_terrain_raster(
        self,
        project_id: str,
        kind: str,
        source_path: str | Path,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        key = str(kind).strip().lower()
        if key not in {"mdt", "slope"}:
            raise ValueError("Tipo raster inválido; use mdt ou slope.")

        source = Path(source_path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"Raster não encontrado: {source}")

        manifest = self.manifest(project_id)
        terrain = manifest.setdefault("terrain", {})
        terrain[key] = {
            **metadata,
            "path": str(source),
            "registered_at": _now(),
        }
        self.save_manifest(project_id, manifest)

        ref = self.get(project_id)
        append_project_debug(
            ref.path,
            "terrain.raster_registered",
            {
                "project_id": project_id,
                "kind": key,
                "path": str(source),
                "crs": metadata.get("crs"),
                "resolution": metadata.get("resolution"),
                "width": metadata.get("width"),
                "height": metadata.get("height"),
            },
            source="project_store",
        )
        return {**manifest, "path": str(ref.path)}

    def debug_event(
        self,
        project_id: str,
        event: str,
        details: dict[str, Any] | None = None,
        *,
        source: str = "backend",
        level: str = "INFO",
    ) -> dict[str, Any]:
        ref = self.get(project_id)
        return append_project_debug(
            ref.path,
            event,
            details,
            source=source,
            level=level,
        )
