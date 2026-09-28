from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auto_extract import start_auto_extract
from .auto_extract_v2 import start_auto_extract_v2
from .converter import jobs, start_import
from .paths import potree_root, viewer_root
from .project_store import ProjectStore
from .terrain_face_engine import extract_terrain_face_from_points
from .vector_documents import (
    active_document_info,
    autosave_active_document,
    list_revisions,
    read_active_document,
    recover_autosave,
    recover_revision,
    save_active_document,
)
from .vector_export import export_vector_document
from talude_v2 import V2Config, V2DetectionError, V2Reason, extract_face_raw_tin


APP_VERSION = "2.4.0-profile-edge-review"
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


class SaveVectorDocumentRequest(BaseModel):
    document: dict[str, Any]
    expected_revision: int | None = None
    reason: str = "edit"


class RecoverVectorDocumentRequest(BaseModel):
    revision: int | None = None
    autosave: bool = False


class ExportVectorDocumentRequest(BaseModel):
    formats: list[str] = ["dxf", "shp", "gpkg"]
    layer_ids: list[str] | None = None
    feature_ids: list[str] | None = None
    visible_only: bool = False
    selected_only: bool = False


class TerrainFaceRequest(BaseModel):
    project_id: str
    cloud_id: str
    profile: str = "face"
    seed: list[float]
    points: list[list[float]]
    classifications: list[int] | None = None
    selected_classes: list[int] | None = None
    grid_resolution: float = 0.0
    slope_low_deg: float = 0.0
    slope_high_deg: float = 0.0
    min_face_area_m2: float = Field(default=4.0, gt=0)
    min_line_length_m: float = Field(default=2.0, gt=0)
    line_smooth_window: int = Field(default=11, ge=3, le=51)


class V2TerrainFaceRequest(TerrainFaceRequest):
    tin_spacing_m: float = Field(default=0.25, ge=0.08, le=2.0)
    max_tin_points: int = Field(default=45000, ge=2000, le=120000)
    max_triangle_edge_m: float = Field(default=2.25, ge=0.25, le=10.0)
    graph_gap_m: float = Field(default=1.50, ge=0.0, le=10.0)
    station_spacing_m: float = Field(default=1.00, ge=0.20, le=5.0)
    patch_along_m: float = Field(default=2.50, ge=0.50, le=10.0)
    patch_cross_m: float = Field(default=1.80, ge=0.40, le=10.0)


class AutoExtractRequest(BaseModel):
    project_id: str
    cloud_id: str
    selected_classes: list[int] | None = None
    cell_size: float = 0.0
    slope_low_deg: float = 0.0
    slope_high_deg: float = 0.0
    min_face_area_m2: float = Field(default=4.0, gt=0)
    min_line_length_m: float = Field(default=2.0, gt=0)
    line_smooth_window: int = Field(default=11, ge=3, le=51)


class V2AutoExtractRequest(AutoExtractRequest):
    performance_mode: str = "balanced"
    tin_spacing_m: float = Field(default=0.25, ge=0.08, le=2.0)
    max_tin_points: int = Field(default=45000, ge=2000, le=120000)
    max_triangle_edge_m: float = Field(default=2.25, ge=0.25, le=10.0)
    graph_gap_m: float = Field(default=1.50, ge=0.0, le=10.0)
    station_spacing_m: float = Field(default=1.00, ge=0.20, le=5.0)
    patch_along_m: float = Field(default=2.50, ge=0.50, le=10.0)
    patch_cross_m: float = Field(default=1.80, ge=0.40, le=10.0)


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


@app.get("/api/projects/{project_id}/vector-document")
def get_vector_document(project_id: str) -> dict[str, Any]:
    try:
        ref = store.get(project_id)
        return read_active_document(ref.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/vector-document/summary")
def get_vector_document_summary(project_id: str) -> dict[str, Any]:
    try:
        ref = store.get(project_id)
        return active_document_info(ref.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/projects/{project_id}/vector-document")
def put_vector_document(
    project_id: str,
    req: SaveVectorDocumentRequest,
) -> dict[str, Any]:
    try:
        ref = store.get(project_id)
        result = save_active_document(
            ref.path,
            req.document,
            expected_revision=req.expected_revision,
            reason=req.reason,
        )
        store.debug_event(
            project_id,
            "vector_document.saved",
            {
                "revision": result["summary"]["revision"],
                "feature_count": result["summary"]["feature_count"],
                "reason": req.reason,
            },
            source="vector_editor",
        )
        return result
    except RuntimeError as exc:
        if str(exc).startswith("REVISION_CONFLICT:"):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/projects/{project_id}/vector-document/autosave")
def autosave_vector_document(
    project_id: str,
    req: SaveVectorDocumentRequest,
) -> dict[str, Any]:
    try:
        ref = store.get(project_id)
        result = autosave_active_document(
            ref.path,
            req.document,
            expected_revision=req.expected_revision,
            reason=req.reason or "autosave",
        )
        return result
    except RuntimeError as exc:
        if str(exc).startswith("REVISION_CONFLICT:"):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/vector-document/revisions")
def get_vector_document_revisions(project_id: str) -> dict[str, Any]:
    try:
        ref = store.get(project_id)
        return {"revisions": list_revisions(ref.path)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/vector-document/recover")
def recover_vector_document(
    project_id: str,
    req: RecoverVectorDocumentRequest,
) -> dict[str, Any]:
    try:
        ref = store.get(project_id)
        if req.autosave:
            result = recover_autosave(ref.path)
        else:
            result = recover_revision(ref.path, req.revision)
        store.debug_event(
            project_id,
            "vector_document.recovered",
            {
                "requested_revision": req.revision,
                "autosave": req.autosave,
                "new_revision": result["summary"]["revision"],
            },
            source="vector_editor",
            level="WARNING",
        )
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/vector-document/export")
def export_project_vector_document(
    project_id: str,
    req: ExportVectorDocumentRequest,
) -> dict[str, Any]:
    try:
        ref = store.get(project_id)
        document = read_active_document(ref.path)
        result = export_vector_document(
            ref.path,
            document,
            formats=req.formats,
            layer_ids=req.layer_ids,
            feature_ids=req.feature_ids,
            visible_only=req.visible_only,
            selected_only=req.selected_only,
        )
        store.debug_event(
            project_id,
            "vector_document.exported",
            {
                "formats": req.formats,
                "feature_count": result["feature_count"],
                "output_dir": result["output_dir"],
                "layer_ids": req.layer_ids,
                "visible_only": req.visible_only,
                "selected_only": req.selected_only,
            },
            source="vector_export",
        )
        return result
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



@app.post("/api/feature-lines/terrain-face")
def terrain_face(req: TerrainFaceRequest) -> dict[str, Any]:
    try:
        if len(req.seed) != 3:
            raise ValueError("Seed XYZ inválido.")
        if req.profile not in {"face", "ridge", "toe"}:
            raise ValueError("Modo de face inválido.")

        manifest = store.manifest(req.project_id)
        if not any(c.get("id") == req.cloud_id for c in manifest.get("clouds", [])):
            raise ValueError("Nuvem não pertence ao projeto ativo.")

        store.debug_event(
            req.project_id,
            "terrain_face.backend_started",
            {
                "cloud_id": req.cloud_id,
                "profile": req.profile,
                "seed": req.seed,
                "input_points": len(req.points),
                "selected_classes": req.selected_classes,
                "grid_resolution": req.grid_resolution,
            },
            source="feature_engine",
        )

        result = extract_terrain_face_from_points(
            req.points,
            req.seed,
            profile=req.profile,
            classifications=req.classifications,
            selected_classes=req.selected_classes,
            grid_resolution=req.grid_resolution,
            slope_low_deg=req.slope_low_deg,
            slope_high_deg=req.slope_high_deg,
            min_face_area_m2=req.min_face_area_m2,
            min_line_length_m=req.min_line_length_m,
            line_smooth_window=req.line_smooth_window,
        )

        store.debug_event(
            req.project_id,
            "terrain_face.backend_completed",
            {
                "cloud_id": req.cloud_id,
                "profile": req.profile,
                "seed": req.seed,
                "line_count": len(result.get("lines", [])),
                "crest_vertices": len((result.get("crest") or {}).get("vertices", [])),
                "toe_vertices": len((result.get("toe") or {}).get("vertices", [])),
                "confidence": result.get("confidence"),
                "grid_resolution": result.get("grid_resolution"),
                "seed_to_face_distance_m": result.get("seed_to_face_distance_m"),
            },
            source="feature_engine",
        )
        return result
    except Exception as exc:
        try:
            store.debug_event(
                req.project_id,
                "terrain_face.backend_failed",
                {
                    "cloud_id": req.cloud_id,
                    "profile": req.profile,
                    "seed": req.seed,
                    "input_points": len(req.points),
                    "selected_classes": req.selected_classes,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                source="feature_engine",
                level="ERROR",
            )
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v2/feature-lines/terrain-face")
def terrain_face_v2(req: V2TerrainFaceRequest) -> dict[str, Any]:
    try:
        import numpy as np

        if len(req.seed) != 3:
            raise ValueError("Seed XYZ inválido.")

        manifest = store.manifest(req.project_id)
        if not any(c.get("id") == req.cloud_id for c in manifest.get("clouds", [])):
            raise ValueError("Nuvem não pertence ao projeto ativo.")

        pts = np.asarray(req.points, dtype=np.float64)
        if pts.ndim != 2 or pts.shape[1] != 3:
            raise ValueError("Pontos locais V2 inválidos.")

        selected = req.selected_classes
        if req.classifications is not None:
            classes = np.asarray(req.classifications, dtype=np.int16).reshape(-1)
            if len(classes) != len(pts):
                raise ValueError("classification não corresponde aos pontos V2.")

            if selected:
                mask = np.isin(classes, np.asarray(selected, dtype=np.int16))
                if int(mask.sum()) >= 100:
                    pts = pts[mask]
            elif np.any(classes == 2):
                mask = classes == 2
                if int(mask.sum()) >= 100:
                    pts = pts[mask]

        store.debug_event(
            req.project_id,
            "v2_raw_tin.backend_started",
            {
                "cloud_id": req.cloud_id,
                "seed": req.seed,
                "input_points": len(req.points),
                "filtered_points": int(len(pts)),
                "selected_classes": selected,
                "tin_spacing_m": req.tin_spacing_m,
                "max_tin_points": req.max_tin_points,
                "station_spacing_m": req.station_spacing_m,
            },
            source="v2_raw_tin",
        )

        cfg = V2Config(
            target_tin_spacing_m=req.tin_spacing_m,
            max_tin_points=req.max_tin_points,
            max_triangle_edge_m=req.max_triangle_edge_m,
            graph_gap_m=req.graph_gap_m,
            station_spacing_m=req.station_spacing_m,
            patch_along_m=req.patch_along_m,
            patch_cross_m=req.patch_cross_m,
        )
        result = extract_face_raw_tin(
            pts,
            req.seed,
            config=cfg,
        )

        store.debug_event(
            req.project_id,
            "v2_raw_tin.backend_completed",
            {
                "cloud_id": req.cloud_id,
                "detector": result.get("detector"),
                "raw_points": result.get("raw_points"),
                "tin_points": result.get("tin_points"),
                "tin_triangles": result.get("tin_triangles"),
                "face_triangles": result.get("face_triangles"),
                "crest_candidate_edges": result.get("crest_candidate_edges"),
                "toe_candidate_edges": result.get("toe_candidate_edges"),
                "crest_vertices": len((result.get("crest") or {}).get("vertices", [])),
                "toe_vertices": len((result.get("toe") or {}).get("vertices", [])),
                "refine_ratio": result.get("refine_ratio"),
                "face_slope_median_deg": result.get("face_slope_median_deg"),
                "local_normal_coherence": result.get("local_normal_coherence"),
                "local_direction_p95_deg": result.get("local_direction_p95_deg"),
                "quality_score": result.get("quality_score"),
                "status": result.get("status"),
                "reason": result.get("reason"),
                "metrics": result.get("metrics"),
            },
            source="v2_raw_tin",
        )
        return result
    except Exception as exc:
        reason = (
            exc.reason.value
            if isinstance(exc, V2DetectionError)
            else V2Reason.INTERNAL_ERROR.value
        )

        # A local click must remain useful even when the experimental RAW-TIN
        # rejects a sparse/small face. For expected geometry rejections, reuse
        # the exact same local points through the proven baseline detector.
        # CANCELLED/INTERNAL errors are not hidden by this fallback.
        soft_rejections = {
            V2Reason.NO_GROUND,
            V2Reason.LOW_GROUND_SUPPORT,
            V2Reason.INVALID_TIN,
            V2Reason.NO_FACE,
            V2Reason.FACE_TOO_SMALL,
            V2Reason.FACE_TOO_SHORT,
            V2Reason.LOW_SLOPE,
            V2Reason.LOW_CONTINUITY,
            V2Reason.BOUNDARY_NOT_FOUND,
            V2Reason.CREST_NOT_FOUND,
            V2Reason.TOE_NOT_FOUND,
            V2Reason.LINE_TOO_SHORT,
            V2Reason.REFINEMENT_FAILED,
            V2Reason.MERGE_FAILED,
        }

        if isinstance(exc, V2DetectionError) and exc.reason in soft_rejections:
            try:
                fallback = extract_terrain_face_from_points(
                    req.points,
                    req.seed,
                    profile=req.profile,
                    classifications=req.classifications,
                    selected_classes=req.selected_classes,
                    grid_resolution=req.grid_resolution,
                    slope_low_deg=req.slope_low_deg,
                    slope_high_deg=req.slope_high_deg,
                    min_face_area_m2=req.min_face_area_m2,
                    min_line_length_m=req.min_line_length_m,
                    line_smooth_window=req.line_smooth_window,
                )
                fallback = dict(fallback)
                fallback["detector"] = "V2_LOCAL_FALLBACK_1_1_7"
                fallback["v2_status"] = "FALLBACK"
                fallback["v2_reason"] = reason
                fallback["v2_error"] = str(exc)

                store.debug_event(
                    req.project_id,
                    "v2_raw_tin.local_fallback",
                    {
                        "cloud_id": req.cloud_id,
                        "seed": req.seed,
                        "input_points": len(req.points),
                        "reason": reason,
                        "fallback_detector": fallback.get("detector"),
                        "crest_vertices": len((fallback.get("crest") or {}).get("vertices", [])),
                        "toe_vertices": len((fallback.get("toe") or {}).get("vertices", [])),
                    },
                    source="v2_raw_tin",
                    level="WARNING",
                )
                return fallback
            except Exception as fallback_exc:
                try:
                    store.debug_event(
                        req.project_id,
                        "v2_raw_tin.local_fallback_failed",
                        {
                            "cloud_id": req.cloud_id,
                            "seed": req.seed,
                            "v2_reason": reason,
                            "v2_error": str(exc),
                            "fallback_error": str(fallback_exc),
                        },
                        source="v2_raw_tin",
                        level="ERROR",
                    )
                except Exception:
                    pass

        try:
            store.debug_event(
                req.project_id,
                "v2_raw_tin.backend_failed",
                {
                    "cloud_id": req.cloud_id,
                    "seed": req.seed,
                    "input_points": len(req.points),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "status": "FAILED",
                    "reason": reason,
                },
                source="v2_raw_tin",
                level="ERROR",
            )
        except Exception:
            pass
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
            line_smooth_window=req.line_smooth_window,
        )
        return {"job_id": job_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v2/talude/auto")
def talude_auto_v2(req: V2AutoExtractRequest) -> dict[str, str]:
    try:
        job_id = start_auto_extract_v2(
            store,
            req.project_id,
            req.cloud_id,
            selected_classes=req.selected_classes,
            cell_size=req.cell_size,
            slope_low_deg=req.slope_low_deg,
            slope_high_deg=req.slope_high_deg,
            min_face_area_m2=req.min_face_area_m2,
            min_line_length_m=req.min_line_length_m,
            line_smooth_window=req.line_smooth_window,
            performance_mode=req.performance_mode,
            tin_spacing_m=req.tin_spacing_m,
            max_tin_points=req.max_tin_points,
            max_triangle_edge_m=req.max_triangle_edge_m,
            graph_gap_m=req.graph_gap_m,
            station_spacing_m=req.station_spacing_m,
            patch_along_m=req.patch_along_m,
            patch_cross_m=req.patch_cross_m,
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


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    try:
        return jobs.request_cancel(job_id)
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
