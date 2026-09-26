from __future__ import annotations

import math

import numpy as np

from talude_v1.config import ExtractConfig
from talude_v1.engine import (
    _component_lines,
    choose_cell_size,
    detect_faces,
    estimate_spacing,
    rasterize_mean,
    refine_lines,
)


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
        return points, classes

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


def _bounded_density(
    points: np.ndarray,
    classes: np.ndarray | None,
    limit: int = 180_000,
) -> tuple[np.ndarray, np.ndarray | None]:
    if len(points) <= limit:
        return points, classes

    stride = int(np.ceil(len(points) / limit))
    idx = np.arange(0, len(points), stride, dtype=np.int64)
    bounded_points = points[idx]
    bounded_classes = classes[idx] if classes is not None else None
    return bounded_points, bounded_classes


def _line_payload(line: dict) -> dict:
    vertices = np.asarray(line["xyz"], dtype=np.float64)
    return {
        "type": str(line["type"]),
        "profile": "ridge" if line["type"] == "CREST" else "toe",
        "vertices": vertices.tolist(),
        "length_m": float(line.get("length_m", 0.0)),
        "confidence": float(line.get("confidence", 0.0)),
        "slope_mean_deg": float(line.get("slope_mean_deg", 0.0)),
        "gradient_coherence": float(line.get("gradient_coherence", 0.0)),
        "scale_persistence": float(line.get("scale_persistence", 0.0)),
        "median_rmse": (
            float(line["median_rmse"])
            if np.isfinite(line.get("median_rmse", np.nan))
            else None
        ),
    }


def extract_terrain_face_from_points(
    points_xyz,
    seed_xyz,
    *,
    profile: str = "face",
    classifications=None,
    selected_classes=None,
    grid_resolution: float = 0.0,
) -> dict:
    """Executa o MESMO detector AUTO da 1.1.2, limitado à face clicada.

    O clique não segue uma aresta especial nem usa um segundo algoritmo.
    Os pontos locais entram em:
        spacing -> grelha -> slope multiescala -> persistence/hysteresis
        -> FACE_DETECTOR -> componente mais próximo da seed
        -> CRISTA + PÉ -> refinamento XYZ.
    """

    points = np.asarray(points_xyz, dtype=np.float64)
    seed = np.asarray(seed_xyz, dtype=np.float64)

    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("Pontos locais inválidos; esperado N x 3.")
    if seed.shape != (3,):
        raise ValueError("Seed XYZ inválido.")
    if len(points) < 120:
        raise ValueError("Poucos pontos locais para detetar a face clicada.")

    classes = None
    if classifications is not None:
        classes = np.asarray(classifications, dtype=np.int16).reshape(-1)
        if len(classes) != len(points):
            raise ValueError("classification não corresponde ao número de pontos.")
        if len(classes) and np.all(classes < 0):
            classes = None

    points, classes = _filter_by_classification(
        points,
        classes,
        selected_classes,
        minimum_points=120,
    )

    # Se a classe Solo existir na amostra, mantém a mesma filosofia do AUTO:
    # terreno primeiro. Isto evita vegetação/objetos na face clicada.
    auto_ground_used = False
    if classes is not None:
        ground = classes == 2
        ground_count = int(np.count_nonzero(ground))
        if ground_count >= max(250, int(len(points) * 0.05)):
            points = points[ground]
            classes = classes[ground]
            auto_ground_used = True

    source_points_before_bound = int(len(points))
    points, classes = _bounded_density(points, classes, limit=120_000)

    cfg = ExtractConfig(
        cell_size=0.0,
        slope_low_deg=0.0,
        slope_high_deg=0.0,
        min_face_area_m2=4.0,
        min_line_length_m=2.0,
        line_smooth_window=11,
        use_ground_class=False,
        classification_filter=None,
    )

    spacing = estimate_spacing(points, cfg)
    requested = float(grid_resolution) if float(grid_resolution) > 0 else 0.0
    cell = choose_cell_size(spacing, requested)

    grid = rasterize_mean(points, cell)
    det = detect_faces(grid, cfg)
    approx = _component_lines(
        grid,
        det,
        cfg,
        seed_xy=(float(seed[0]), float(seed[1])),
    )
    lines = refine_lines(approx, points, grid.cell, cfg)

    by_type = {str(line["type"]): line for line in lines}
    if "CREST" not in by_type or "TOE" not in by_type:
        raise ValueError(
            "A face clicada não produziu o par CRISTA + PÉ. "
            "Clique mais no centro da face inclinada."
        )

    crest = _line_payload(by_type["CREST"])
    toe = _line_payload(by_type["TOE"])
    confidence = float(
        np.clip(
            0.5 * crest["confidence"] + 0.5 * toe["confidence"],
            0.0,
            1.0,
        )
    )

    return {
        "detector": "AUTO_FACE_V1_1_2_LOCAL",
        "profile": "face",
        "seed": [float(v) for v in seed],
        "lines": [crest, toe],
        "crest": crest,
        "toe": toe,
        "confidence": confidence,
        "grid_resolution": float(grid.cell),
        "estimated_spacing_m": float(spacing),
        "slope_low_deg": float(det["slope_low"]),
        "slope_high_deg": float(det["slope_high"]),
        "selected_face_label": int(det.get("selected_face_label", 0)),
        "seed_to_face_distance_m": float(det.get("selected_face_seed_distance_m", 0.0)),
        "source_points": int(len(points)),
        "source_points_before_bound": source_points_before_bound,
        "selected_classes": (
            sorted({int(v) for v in selected_classes})
            if selected_classes is not None
            else None
        ),
        "auto_ground_used": auto_ground_used,
        "query_source": "potree-local-auto-face",
        "signature": {
            "method": "same-auto-1.1.2-clicked-face",
            "cell_size_m": float(grid.cell),
            "slope_low_deg": float(det["slope_low"]),
            "slope_high_deg": float(det["slope_high"]),
            "seed_to_face_distance_m": float(
                det.get("selected_face_seed_distance_m", 0.0)
            ),
        },
    }
