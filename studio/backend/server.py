from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auto_extract import start_auto_extract
from .converter import jobs, start_import
from .paths import potree_root, viewer_root
from .project_store import ProjectStore


APP_VERSION = "1.1.1-streaming"
app = FastAPI(title="Talude Studio Local API", version=APP_VERSION)
store = ProjectStore()


class CreateProjectRequest(BaseModel):
    parent_folder: str
    name: str


class OpenProjectRequest(BaseModel):
    path: str


class ImportCloudRequest(BaseModel):
    project_id: str
    source_path: str


class SaveStateRequest(BaseModel):
    state: dict[str, Any]


class DebugEventRequest(BaseModel):
    event: str
    level: str = "INFO"
    source: str = "viewer"
    details: dict[str, Any] = {}


class AutoExtractRequest(BaseModel):
    project_id: str
    cloud_id: str
    selected_classes: list[int] | None = None
    cell_size: float = 0.0
    slope_low_deg: float = 0.0
    slope_high_deg: float = 0.0
    min_face_area_m2: float = Field(default=4.0, gt=0)
    min_line_length_m: float = Field(default=2.0, gt=0)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "app": "Talude Studio",
        "version": APP_VERSION,
        "potree_ready": (potree_root() / "build" / "potree" / "potree.js").exists(),
    }


@app.get("/api/projects")
def list_projects() -> dict[str, list[dict[str, Any]]]:
    # Mantém o contrato do viewer Cloud_to_lines reaproveitado.
    return {"projects": store.list_projects()}


@app.post("/api/projects")
def create_project(req: CreateProjectRequest) -> dict[str, Any]:
    try:
        return store.create_project(req.parent_folder, req.name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/open")
def open_project(req: OpenProjectRequest) -> dict[str, Any]:
    try:
        return store.open_project(req.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    try:
        manifest = store.manifest(project_id)
        ref = store.get(project_id)
        return {**manifest, "path": str(ref.path)}
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/state")
def get_state(project_id: str) -> dict[str, Any]:
    try:
        return store.state(project_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/projects/{project_id}/state")
def save_state(project_id: str, req: SaveStateRequest) -> dict[str, bool]:
    try:
        store.save_state(project_id, req.state)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/debug-log")
def project_debug_log(project_id: str, req: DebugEventRequest) -> dict[str, Any]:
    try:
        record = store.debug_event(
            project_id,
            req.event,
            req.details,
            source=req.source,
            level=req.level,
        )
        return {"ok": True, "record": record}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/clouds/import")
def import_cloud(req: ImportCloudRequest) -> dict[str, str]:
    try:
        return {"job_id": start_import(store, req.project_id, req.source_path)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/talude/auto")
def talude_auto(req: AutoExtractRequest) -> dict[str, str]:
    try:
        job_id = start_auto_extract(
            store,
            req.project_id,
            req.cloud_id,
            selected_classes=req.selected_classes,
            cell_size=req.cell_size,
            slope_low_deg=req.slope_low_deg,
            slope_high_deg=req.slope_high_deg,
            min_face_area_m2=req.min_face_area_m2,
            min_line_length_m=req.min_line_length_m,
        )
        return {"job_id": job_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    try:
        return jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job não encontrado") from exc


@app.get("/api/cloud-data/{project_id}/{cloud_id}/{asset_path:path}")
def cloud_data(project_id: str, cloud_id: str, asset_path: str):
    try:
        manifest = store.manifest(project_id)
        cloud = next(
            (item for item in manifest.get("clouds", []) if item.get("id") == cloud_id),
            None,
        )
        if not cloud:
            raise FileNotFoundError("Nuvem não encontrada.")

        root = Path(cloud["potree_path"]).resolve()
        requested = (root / asset_path).resolve()
        if root != requested and root not in requested.parents:
            raise PermissionError("Caminho inválido.")
        if not requested.exists() or not requested.is_file():
            raise FileNotFoundError(requested)
        return FileResponse(requested)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


viewer = viewer_root()
potree = potree_root()

if viewer.exists():
    app.mount("/app", StaticFiles(directory=viewer, html=True), name="viewer")
if potree.exists():
    app.mount("/potree", StaticFiles(directory=potree), name="potree")


@app.get("/")
def root():
    index = viewer / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        {
            "ok": False,
            "detail": "Viewer Talude Studio não encontrado.",
        },
        status_code=503,
    )
