from __future__ import annotations

import numpy as np

from core.terrain_face import extract_terrain_face_edge


def _filter_by_classification(
    points: np.ndarray,
    classifications,
    selected_classes,
    *,
    minimum_points: int = 80,
) -> tuple[np.ndarray, np.ndarray | None]:
    if classifications is None:
        return points, None

    classes = np.asarray(classifications, dtype=np.int16).reshape(-1)
    if len(classes) != len(points):
        raise ValueError("classification não corresponde ao número de pontos.")

    if len(classes) and np.all(classes < 0):
        return points, None

    if selected_classes is None:
        return points, classes

    selected = {int(v) for v in selected_classes}
    if not selected:
        raise ValueError("Nenhuma classificação está ativa para o motor.")

    mask = np.isin(classes, list(selected))
    filtered = points[mask]
    filtered_classes = classes[mask]

    if len(filtered) < int(minimum_points):
        names = ", ".join(str(v) for v in sorted(selected))
        raise ValueError(
            "Poucos pontos nas classificações selecionadas "
            f"({names}) junto ao clique."
        )

    return filtered, filtered_classes


def _bounded_density(points: np.ndarray, classes: np.ndarray | None, limit: int = 180_000):
    if len(points) <= limit:
        return points, classes
    stride = int(np.ceil(len(points) / limit))
    idx = np.arange(0, len(points), stride, dtype=np.int64)
    bounded_points = points[idx]
    bounded_classes = classes[idx] if classes is not None else None
    return bounded_points, bounded_classes


def extract_terrain_face_from_points(
    points_xyz,
    seed_xyz,
    *,
    profile: str,
    classifications=None,
    selected_classes=None,
    grid_resolution: float = 0.20,
) -> dict:
    points = np.asarray(points_xyz, dtype=np.float64)
    seed = np.asarray(seed_xyz, dtype=np.float64)
    key = str(profile).strip().lower()

    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("Pontos locais inválidos; esperado N x 3.")
    if seed.shape != (3,):
        raise ValueError("Seed XYZ inválido.")
    if key not in {"ridge", "toe"}:
        raise ValueError("Extrator automático disponível para Crista e Pé.")

    classes = None
    auto_ground_used = False

    if classifications is not None:
        classes = np.asarray(classifications, dtype=np.int16).reshape(-1)
        if len(classes) != len(points):
            raise ValueError("classification não corresponde ao número de pontos.")
        if len(classes) and np.all(classes < 0):
            classes = None

    # Terrain breaklines must be solved from terrain. If LAS class 2 exists,
    # prefer it automatically. An explicit engine filter can still constrain
    # the source first, but class 2 remains the preferred subset for ridge/toe.
    if selected_classes is not None:
        points, classes = _filter_by_classification(
            points,
            classes,
            selected_classes,
            minimum_points=200,
        )

    if classes is not None:
        ground = classes == 2
        ground_count = int(np.count_nonzero(ground))
        if ground_count >= max(500, int(len(points) * 0.05)):
            points = points[ground]
            classes = classes[ground]
            auto_ground_used = True

    source_points_before_bound = int(len(points))
    points, classes = _bounded_density(points, classes, limit=70_000)

    result = extract_terrain_face_edge(
        points,
        seed,
        profile=key,
        grid_resolution=float(grid_resolution),
    )

    vertices = np.asarray(result.vertices, dtype=np.float64)
    if len(vertices) < 2:
        raise ValueError("A face do talude não produziu uma aresta utilizável.")

    return {
        "vertices": vertices.tolist(),
        "profile": key,
        "detector": f"terrain-face-{key}",
        "confidence": float(result.confidence),
        "mean_break_angle_deg": float(result.face_slope_deg),
        "face_slope_deg": float(result.face_slope_deg),
        "low_slope_threshold_deg": float(result.low_slope_threshold_deg),
        "high_slope_threshold_deg": float(result.high_slope_threshold_deg),
        "grid_resolution": float(result.grid_resolution),
        "face_cells": int(result.face_cells),
        "raw_vertices": int(result.raw_vertices),
        "rough_vertices": int(result.rough_vertices),
        "refined_vertices": int(result.refined_vertices),
        "refinement_ratio": float(result.refinement_ratio),
        "snapped_vertices": int(result.snapped_vertices),
        "snap_ratio": float(result.snap_ratio),
        "median_snap_offset_m": float(result.median_snap_offset_m),
        "simplified_vertices": int(len(vertices)),
        "source_points": int(result.source_points),
        "source_points_before_bound": source_points_before_bound,
        "selected_classes": (
            sorted({int(v) for v in selected_classes})
            if selected_classes is not None
            else None
        ),
        "auto_ground_used": auto_ground_used,
        "query_source": "potree-terrain-tile",
        "signature": {
            "profile": key,
            "method": "slope-face-boundary-plane-intersection",
            "grid_resolution": float(result.grid_resolution),
            "face_slope_deg": float(result.face_slope_deg),
            "low_slope_threshold_deg": float(result.low_slope_threshold_deg),
            "high_slope_threshold_deg": float(result.high_slope_threshold_deg),
            "snap_ratio": float(result.snap_ratio),
            "median_snap_offset_m": float(result.median_snap_offset_m),
            "auto_ground_used": auto_ground_used,
        },
    }

