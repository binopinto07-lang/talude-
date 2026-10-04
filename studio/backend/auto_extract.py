from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from talude_v1.config import ExtractConfig
from talude_v1.engine import extract

from .converter import jobs
from .project_store import ProjectStore


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def start_auto_extract(
    store: ProjectStore,
    project_id: str,
    cloud_id: str,
    *,
    selected_classes: list[int] | None = None,
    cell_size: float = 0.0,
    slope_low_deg: float = 0.0,
    slope_high_deg: float = 0.0,
    min_face_area_m2: float = 4.0,
    min_line_length_m: float = 2.0,
    line_smooth_window: int = 11,
    vertex_spacing_m: float = 1.0,
    ground_gap_fill_m: float = 0.0,
) -> str:
    job_id = jobs.create("Extrair CRISTA + PÉ automaticamente")
    thread = threading.Thread(
        target=_worker,
        args=(
            store,
            project_id,
            cloud_id,
            job_id,
            selected_classes,
            cell_size,
            slope_low_deg,
            slope_high_deg,
            min_face_area_m2,
            min_line_length_m,
            line_smooth_window,
            vertex_spacing_m,
            ground_gap_fill_m,
        ),
        daemon=True,
        name=f"talude-auto-{job_id[:8]}",
    )
    thread.start()
    return job_id


def _worker(
    store: ProjectStore,
    project_id: str,
    cloud_id: str,
    job_id: str,
    selected_classes: list[int] | None,
    cell_size: float,
    slope_low_deg: float,
    slope_high_deg: float,
    min_face_area_m2: float,
    min_line_length_m: float,
    line_smooth_window: int,
    vertex_spacing_m: float,
    ground_gap_fill_m: float,
) -> None:
    try:
        project = store.get(project_id)
        manifest = store.manifest(project_id)

        cloud = next(
            (item for item in manifest.get("clouds", []) if item.get("id") == cloud_id),
            None,
        )
        if not cloud:
            raise KeyError(f"Nuvem não encontrada: {cloud_id}")

        classify_info = cloud.get("classify_las") or {}
        classified_path = classify_info.get("classified_cloud")
        if not classified_path:
            raise RuntimeError(
                "Execute primeiro CLASSIFICAR GROUND + CRIAR MDT (CLASSIFY LAS R20.4)."
            )
        source = Path(classified_path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(source)

        output = project.path / "exports" / f"talude_auto_{_stamp()}"
        output.mkdir(parents=True, exist_ok=True)

        # The Talude detector consumes the Ground produced by CLASSIFY LAS.
        # UI visibility filters do not redefine terrain authority.
        classes = (2,)

        cfg = ExtractConfig(
            cell_size=float(cell_size),
            slope_low_deg=float(slope_low_deg),
            slope_high_deg=float(slope_high_deg),
            min_face_area_m2=float(min_face_area_m2),
            min_line_length_m=float(min_line_length_m),
            line_smooth_window=max(3, int(line_smooth_window)),
            vertex_spacing_m=max(0.20, float(vertex_spacing_m)),
            max_ground_gap_m=0.0,
            use_ground_class=classes is None,
            classification_filter=classes,
        )

        store.debug_event(
            project_id,
            "talude.auto_started",
            {
                "job_id": job_id,
                "cloud_id": cloud_id,
                "source": str(source),
                "selected_classes": list(classes) if classes is not None else None,
                "config": cfg.to_dict(),
                "output": str(output),
            },
            source="talude-engine",
        )
        jobs.update(job_id, status="running", progress=8, message="A preparar nuvem e classes…")

        jobs.update(job_id, progress=18, message="A calcular grelha, declive e persistência multiescala…")

        def _progress(value: float, message: str) -> None:
            jobs.update(
                job_id,
                status="running",
                progress=max(1, min(95, int(round(value)))),
                message=message,
            )

        report = extract(source, output, cfg, progress=_progress)

        jobs.update(job_id, progress=88, message="A carregar CRISTA + PÉ para o viewer…")
        geojson_path = output / "talude_breaklines.geojson"
        payload = json.loads(geojson_path.read_text(encoding="utf-8"))

        lines: list[dict[str, Any]] = []
        for feature in payload.get("features", []):
            props = feature.get("properties") or {}
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            if len(coords) < 2:
                continue
            lines.append(
                {
                    "line_id": props.get("line_id"),
                    "face_id": props.get("face_id"),
                    "type": props.get("type"),
                    "confidence": props.get("confidence"),
                    "length_m": props.get("length_m"),
                    "slope_mean_deg": props.get("slope_mean_deg"),
                    "median_rmse": props.get("median_rmse"),
                    "vertices": coords,
                }
            )

        state = store.state(project_id)
        state.setdefault("talude_runs", [])
        state["talude_runs"].append(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "cloud_id": cloud_id,
                "output_dir": str(output),
                "report": {
                    k: v
                    for k, v in report.items()
                    if k not in {"lines"}
                },
                "line_count": len(lines),
            }
        )
        store.save_state(project_id, state)

        result = {
            "project_id": project_id,
            "cloud_id": cloud_id,
            "output_dir": str(output),
            "report": report,
            "lines": lines,
        }

        store.debug_event(
            project_id,
            "talude.auto_completed",
            {
                "job_id": job_id,
                "cloud_id": cloud_id,
                "output": str(output),
                "faces_detected": report.get("faces_detected"),
                "crest_lines": report.get("crest_lines"),
                "toe_lines": report.get("toe_lines"),
                "elapsed_s": report.get("elapsed_s"),
            },
            source="talude-engine",
        )

        jobs.update(
            job_id,
            status="completed",
            progress=100,
            message=(
                f'{report.get("faces_detected", 0)} faces · '
                f'{report.get("crest_lines", 0)} cristas · '
                f'{report.get("toe_lines", 0)} pés'
            ),
            result=result,
        )

    except Exception as exc:
        try:
            store.debug_event(
                project_id,
                "talude.auto_failed",
                {
                    "job_id": job_id,
                    "cloud_id": cloud_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
                source="talude-engine",
                level="ERROR",
            )
        except Exception:
            pass

        jobs.update(
            job_id,
            status="failed",
            progress=100,
            message="Falha na extração automática",
            error=f"{type(exc).__name__}: {exc}",
        )
