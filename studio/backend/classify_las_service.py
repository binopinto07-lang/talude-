from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any

from ALGORITM.CLASSIFY_LAS.api import classify_and_create_mdt

from .converter import jobs
from .project_store import ProjectStore


def _cloud_from_manifest(store: ProjectStore, project_id: str, cloud_id: str) -> dict[str, Any]:
    manifest = store.manifest(project_id)
    cloud = next((item for item in manifest.get("clouds", []) if item.get("id") == cloud_id), None)
    if cloud is None:
        raise ValueError("Nuvem não pertence ao projeto ativo.")
    return cloud


def start_classify_las(store: ProjectStore, project_id: str, cloud_id: str) -> str:
    cloud = _cloud_from_manifest(store, project_id, cloud_id)
    job_id = jobs.create(f"CLASSIFY LAS R20.4 · {cloud.get('name', cloud_id)}")
    thread = threading.Thread(
        target=_worker,
        args=(store, project_id, cloud_id, job_id),
        daemon=True,
        name=f"classify-las-{job_id[:8]}",
    )
    thread.start()
    return job_id


def _worker(store: ProjectStore, project_id: str, cloud_id: str, job_id: str) -> None:
    try:
        cloud = _cloud_from_manifest(store, project_id, cloud_id)
        source = Path(cloud["source_path"]).expanduser().resolve()
        project = store.get(project_id)
        output_dir = project.path / "exports" / "CLASSIFY_LAS" / cloud_id
        output_dir.mkdir(parents=True, exist_ok=True)

        jobs.update(job_id, status="running", progress=1, message="CLASSIFY LAS R20.4 a iniciar")
        store.debug_event(
            project_id,
            "classify_las.started",
            {"job_id": job_id, "cloud_id": cloud_id, "source_path": str(source)},
            source="classify_las",
        )

        result = classify_and_create_mdt(
            source,
            output_dir,
            progress=lambda p, m: jobs.update(
                job_id, status="running", progress=int(p), message=str(m)
            ),
        )

        manifest = store.manifest(project_id)
        for item in manifest.get("clouds", []):
            if item.get("id") == cloud_id:
                item["classify_las"] = {
                    "version": result["version"],
                    "classified_cloud": result["classified_cloud"],
                    "ground_cloud": result["ground_cloud"],
                    "source_type": result["source_type"],
                    "ground_count": result["ground_count"],
                    "rejected_count": result["rejected_count"],
                }
                break

        terrain = manifest.setdefault("terrain", {})
        mdt_info = result["mdt_info"]
        terrain["mdt"] = {
            "name": Path(result["mdt"]).name,
            "path": result["mdt"],
            "crs": mdt_info["crs"],
            "resolution": [mdt_info["resolution_m"], mdt_info["resolution_m"]],
            "observation_state_path": result["mdt_observation_state"],
            "source": "CLASSIFY_LAS_R20.4",
        }
        store.save_manifest(project_id, manifest)
        store.debug_event(
            project_id,
            "classify_las.completed",
            {"job_id": job_id, "cloud_id": cloud_id, **result},
            source="classify_las",
        )
        jobs.update(
            job_id,
            status="completed",
            progress=100,
            message="CLASSIFY LAS R20.4 + MDT concluído",
            result={"kind": "classify_las", "project_id": project_id, "cloud_id": cloud_id, **result},
        )
    except Exception as exc:
        try:
            store.debug_event(
                project_id,
                "classify_las.failed",
                {
                    "job_id": job_id,
                    "cloud_id": cloud_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
                source="classify_las",
                level="ERROR",
            )
        except Exception:
            pass
        jobs.update(
            job_id,
            status="failed",
            progress=100,
            message="Falha no CLASSIFY LAS",
            error=f"{type(exc).__name__}: {exc}",
        )
