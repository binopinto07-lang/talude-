from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from talude_v1.config import ExtractConfig
from talude_v2 import V2Config, V2DetectionError, V2Reason, run_auto_global_v2

from .converter import jobs
from .project_store import ProjectStore
from .vector_documents import write_documents_from_geojson


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def start_auto_extract_v2(
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
    performance_mode: str = "balanced",
    tin_spacing_m: float = 0.25,
    max_tin_points: int = 45_000,
    max_triangle_edge_m: float = 2.25,
    graph_gap_m: float = 1.50,
    station_spacing_m: float = 1.00,
    patch_along_m: float = 2.50,
    patch_cross_m: float = 1.80,
) -> str:
    job_id = jobs.create("AUTO GLOBAL V2 — CRISTA + PÉ")
    thread = threading.Thread(
        target=_worker_v2,
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
            performance_mode,
            tin_spacing_m,
            max_tin_points,
            max_triangle_edge_m,
            graph_gap_m,
            station_spacing_m,
            patch_along_m,
            patch_cross_m,
        ),
        daemon=True,
        name=f"talude-auto-v2-{job_id[:8]}",
    )
    thread.start()
    return job_id


def _worker_v2(
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
    performance_mode: str,
    tin_spacing_m: float,
    max_tin_points: int,
    max_triangle_edge_m: float,
    graph_gap_m: float,
    station_spacing_m: float,
    patch_along_m: float,
    patch_cross_m: float,
) -> None:
    output: Path | None = None
    try:
        project = store.get(project_id)
        manifest = store.manifest(project_id)

        cloud = next(
            (item for item in manifest.get("clouds", []) if item.get("id") == cloud_id),
            None,
        )
        if not cloud:
            raise KeyError(f"Nuvem não encontrada: {cloud_id}")

        source = Path(cloud["source_path"]).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(source)

        output = project.path / "exports" / f"talude_auto_v2_{_stamp()}"
        output.mkdir(parents=True, exist_ok=True)

        classes = None
        if selected_classes is not None:
            normalized = tuple(sorted({int(v) for v in selected_classes}))
            classes = normalized if normalized else None

        mode = str(performance_mode or "balanced").strip().lower()
        if mode not in {"fast", "balanced", "precise"}:
            mode = "balanced"

        baseline_cfg = ExtractConfig(
            cell_size=float(cell_size),
            slope_low_deg=float(slope_low_deg),
            slope_high_deg=float(slope_high_deg),
            min_face_area_m2=float(min_face_area_m2),
            min_line_length_m=float(min_line_length_m),
            line_smooth_window=max(3, int(line_smooth_window)),
            use_ground_class=classes is None,
            classification_filter=classes,
        )
        v2_cfg = V2Config(
            target_tin_spacing_m=float(tin_spacing_m),
            max_tin_points=int(max_tin_points),
            max_triangle_edge_m=float(max_triangle_edge_m),
            graph_gap_m=float(graph_gap_m),
            station_spacing_m=float(station_spacing_m),
            patch_along_m=float(patch_along_m),
            patch_cross_m=float(patch_cross_m),
        )

        store.debug_event(
            project_id,
            "talude.v2_auto_started",
            {
                "job_id": job_id,
                "cloud_id": cloud_id,
                "source": str(source),
                "selected_classes": list(classes) if classes is not None else None,
                "performance_mode": mode,
                "baseline_config": baseline_cfg.to_dict(),
                "v2_config": {
                    key: getattr(v2_cfg, key)
                    for key in v2_cfg.__dataclass_fields__
                },
                "output": str(output),
            },
            source="v2-auto-global",
        )

        jobs.update(
            job_id,
            status="running",
            progress=2,
            message="AUTO GLOBAL V2 · a iniciar descoberta de faces…",
        )

        def cancelled() -> bool:
            return jobs.is_cancel_requested(job_id)

        def progress(value: float, message: str) -> None:
            if cancelled():
                raise V2DetectionError(
                    V2Reason.CANCELLED,
                    "AUTO GLOBAL V2 cancelado pelo utilizador.",
                )
            jobs.update(
                job_id,
                status="running",
                progress=max(1, min(99, int(round(value)))),
                message=str(message),
            )

        job_started = perf_counter()
        report = run_auto_global_v2(
            source,
            output,
            baseline_cfg,
            v2_cfg,
            progress=progress,
            cancel_check=cancelled,
            performance_mode=mode,
            profile_run_id=job_id,
        )

        geojson_path = output / "talude_breaklines.geojson"
        payload = json.loads(geojson_path.read_text(encoding="utf-8"))
        review_path = output / "talude_review.geojson"
        review_payload = (
            json.loads(review_path.read_text(encoding="utf-8"))
            if review_path.exists()
            else {"type": "FeatureCollection", "features": []}
        )
        document_payload = dict(payload)
        document_payload["features"] = [
            *list(payload.get("features", [])),
            *list(review_payload.get("features", [])),
        ]

        vector_started = perf_counter()
        vector_bundle = write_documents_from_geojson(
            project.path,
            output,
            document_payload,
            source={
                "engine": report.get("engine"),
                "mode": "AUTO_GLOBAL_V2",
                "performance_mode": mode,
                "cloud_id": cloud_id,
                "job_id": job_id,
                "output_dir": str(output),
            },
        )
        vector_elapsed = float(perf_counter() - vector_started)
        performance_profile = dict(report.get("performance_profile") or {})
        profile_stages = dict(performance_profile.get("stages_s") or {})
        profile_stages["vector_document"] = float(
            profile_stages.get("vector_document", 0.0) + vector_elapsed
        )
        performance_profile["stages_s"] = profile_stages
        performance_profile["total_s"] = float(perf_counter() - job_started)
        report["performance_profile"] = performance_profile
        report["elapsed_s"] = float(performance_profile["total_s"])

        profile_path = Path(
            report.get("performance_profile_path")
            or (output / "debug" / f"performance_{job_id}.json")
        )
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(
            json.dumps(performance_profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output / "talude_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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
                    "quality_score": props.get("quality_score"),
                    "length_m": props.get("length_m"),
                    "length_2d_m": props.get("length_2d_m"),
                    "length_3d_m": props.get("length_3d_m"),
                    "median_rmse": props.get("median_rmse"),
                    "source": props.get("source"),
                    "status": props.get("status"),
                    "vertices": coords,
                }
            )


        review_lines: list[dict[str, Any]] = []
        for feature in review_payload.get("features", []):
            props = feature.get("properties") or {}
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            if len(coords) < 2:
                continue
            review_lines.append(
                {
                    "line_id": props.get("line_id"),
                    "face_id": props.get("face_id"),
                    "type": props.get("type"),
                    "confidence": props.get("confidence"),
                    "quality_score": props.get("quality_score"),
                    "length_m": props.get("length_m"),
                    "source": props.get("source"),
                    "status": props.get("status"),
                    "review_state": props.get("review_state"),
                    "review_reason": props.get("review_reason"),
                    "vertices": coords,
                }
            )

        state = store.state(project_id)
        state.setdefault("talude_runs", [])
        state["talude_runs"].append(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "cloud_id": cloud_id,
                "engine": "v2-global",
                "performance_mode": mode,
                "output_dir": str(output),
                "report": {
                    key: value
                    for key, value in report.items()
                    if key not in {"lines", "review_lines"}
                },
                "line_count": len(lines),
                "vector_document": vector_bundle["run_path"],
            }
        )
        state["active_vector_document"] = vector_bundle["active_path"]
        state["active_vector_document_summary"] = vector_bundle["summary"]
        store.save_state(project_id, state)

        result = {
            "project_id": project_id,
            "cloud_id": cloud_id,
            "engine": "v2-global",
            "performance_mode": mode,
            "output_dir": str(output),
            "report": report,
            "lines": lines,
            "review_lines": review_lines,
            "vector_document": vector_bundle["active_path"],
            "vector_document_summary": vector_bundle["summary"],
        }

        store.debug_event(
            project_id,
            "talude.v2_auto_completed",
            {
                "job_id": job_id,
                "cloud_id": cloud_id,
                "output": str(output),
                "faces_detected": report.get("faces_detected"),
                "approved_faces": report.get("approved_faces"),
                "review_faces": report.get("review_faces"),
                "review_line_count": report.get("review_line_count"),
                "candidate_faces": report.get("candidate_faces"),
                "performance_mode": mode,
                "v2_attempted_faces": report.get("v2_attempted_faces"),
                "performance_skipped_faces": report.get("performance_skipped_faces"),
                "v2_success_faces": report.get("v2_success_faces"),
                "baseline_fallback_faces": report.get("baseline_fallback_faces"),
                "crest_lines": report.get("crest_lines"),
                "toe_lines": report.get("toe_lines"),
                "reason_counts": report.get("reason_counts"),
                "vector_document": vector_bundle["active_path"],
                "vector_document_summary": vector_bundle["summary"],
                "elapsed_s": report.get("elapsed_s"),
            },
            source="v2-auto-global",
        )

        jobs.update(
            job_id,
            status="completed",
            progress=100,
            message=(
                f'{report.get("approved_faces", 0)} aprovadas · '
                f'{report.get("review_faces", 0)} revisão · '
                f'V2 {report.get("v2_success_faces", 0)}'
            ),
            result=result,
        )

    except Exception as exc:
        cancelled = (
            isinstance(exc, V2DetectionError)
            and exc.reason == V2Reason.CANCELLED
        ) or jobs.is_cancel_requested(job_id)

        if cancelled:
            try:
                store.debug_event(
                    project_id,
                    "talude.v2_auto_cancelled",
                    {
                        "job_id": job_id,
                        "cloud_id": cloud_id,
                        "output": str(output) if output is not None else None,
                    },
                    source="v2-auto-global",
                    level="WARNING",
                )
            except Exception:
                pass

            jobs.update(
                job_id,
                status="cancelled",
                progress=100,
                message="AUTO GLOBAL V2 cancelado.",
                error=None,
            )
            return

        try:
            store.debug_event(
                project_id,
                "talude.v2_auto_failed",
                {
                    "job_id": job_id,
                    "cloud_id": cloud_id,
                    "output": str(output) if output is not None else None,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "reason": (
                        exc.reason.value
                        if isinstance(exc, V2DetectionError)
                        else V2Reason.INTERNAL_ERROR.value
                    ),
                },
                source="v2-auto-global",
                level="ERROR",
            )
        except Exception:
            pass

        jobs.update(
            job_id,
            status="failed",
            progress=100,
            message="Falha no AUTO GLOBAL V2",
            error=f"{type(exc).__name__}: {exc}",
        )
