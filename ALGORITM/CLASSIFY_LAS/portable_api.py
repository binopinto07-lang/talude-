"""Portable entry point for the complete LAS-CAFIISICA R20.4 engine.

This module is part of ALGORITM and is deliberately outside the Talude Studio
application package. Replacing the CLASSIFY_LAS folder upgrades the classifier
without rewriting the feature-detection UI.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

import laspy
import numpy as np

from las_classifier.cloud.model import CloudModel
from las_classifier.classifiers.universal_ground import (
    ENGINE_NAME,
    REVISION,
    run_universal_ground,
)
from las_classifier.ground.types import GroundEngineParams

API_VERSION = "2.0"
ALGORITHM_ID = "LAS_CAFIISICA_UNIVERSAL_GROUND_R20_4"
CRS_EPSG = 3763
ProgressCallback = Callable[[str, float], None]


@dataclass(frozen=True, slots=True)
class Settings:
    quality: str = "balanced"
    chunk_points: int = 750_000
    mdt_resolution_m: float = 0.25
    mdt_max_gap_m: float = 0.75

    def validate(self) -> None:
        if self.quality.lower() not in {"fast", "balanced", "high", "extreme"}:
            raise ValueError("quality must be fast/balanced/high/extreme")
        if self.chunk_points < 1:
            raise ValueError("chunk_points must be positive")
        if self.mdt_resolution_m <= 0 or self.mdt_max_gap_m < 0:
            raise ValueError("Invalid MDT settings")


def _emit(callback: ProgressCallback | None, label: str, fraction: float) -> None:
    if callback is not None:
        callback(label, max(0.0, min(1.0, float(fraction))))


def _validate_source(source: Path) -> None:
    with laspy.open(source) as reader:
        crs = reader.header.parse_crs()
        if crs is None or crs.to_epsg() != CRS_EPSG:
            raise ValueError("CLASSIFY LAS R20.4 requires declared EPSG:3763")


def _scaled(points, header):
    scales = header.scales
    offsets = header.offsets
    x = np.asarray(points.X, dtype=np.float64) * scales[0] + offsets[0]
    y = np.asarray(points.Y, dtype=np.float64) * scales[1] + offsets[1]
    z = np.asarray(points.Z, dtype=np.float64) * scales[2] + offsets[2]
    return x, y, z


def inspect_source(source: str | Path) -> dict:
    source = Path(source).resolve()
    _validate_source(source)
    with laspy.open(source) as reader:
        names = set(reader.header.point_format.dimension_names)
        return {
            "path": str(source),
            "point_count": int(reader.header.point_count),
            "point_format": int(reader.header.point_format.id),
            "crs": "EPSG:3763",
            "has_rgb": {"red", "green", "blue"}.issubset(names),
            "has_intensity": "intensity" in names,
            "has_returns": {"return_number", "number_of_returns"}.issubset(names),
            "processing_mode": "LAS_CAFIISICA_R20_4_UNIVERSAL",
            "sensor_selection_required": False,
        }


def classify_file(
    source: str | Path,
    destination: str | Path,
    settings: Settings = Settings(),
    overwrite: bool = False,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> dict:
    """Run the complete vendored R20.4 engine and write a full classified cloud."""
    settings.validate()
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source == destination:
        raise ValueError("Input and output must differ")
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    if destination.suffix.lower() not in {".las", ".laz"}:
        raise ValueError("Output must be LAS/LAZ")
    _validate_source(source)

    _emit(progress_callback, "LOAD CLASSIFY LAS R20.4", 0.02)
    las = laspy.read(source)
    original_class = np.asarray(las.classification, dtype=np.uint8).copy()
    cloud = CloudModel(path=source, las=las, original_class=original_class)
    params = replace(
        GroundEngineParams.preset(settings.quality),
        chunk_size=min(int(settings.chunk_points), 750_000),
        synthetic_spacing=0.0,
    )

    def engine_progress(percent: int, message: str) -> None:
        if cancel_callback is not None and cancel_callback():
            raise InterruptedError("CLASSIFY LAS cancelled")
        _emit(progress_callback, message, 0.03 + 0.72 * (percent / 100.0))

    result = run_universal_ground(cloud, params, engine_progress)
    if cancel_callback is not None and cancel_callback():
        raise InterruptedError("CLASSIFY LAS cancelled")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    temporary.unlink(missing_ok=True)
    do_compress = destination.suffix.lower() == ".laz"

    try:
        with laspy.open(source) as reader:
            header = reader.header.copy()
            total = int(reader.header.point_count)
            with laspy.open(
                temporary, mode="w", header=header, do_compress=do_compress
            ) as writer:
                processed = 0
                for points in reader.chunk_iterator(params.chunk_size):
                    if cancel_callback is not None and cancel_callback():
                        raise InterruptedError("CLASSIFY LAS cancelled")
                    x, y, z = _scaled(points, header)
                    points.classification = result.model.classify_points(
                        points, x, y, z
                    )
                    writer.write_points(points)
                    processed += len(points)
                    _emit(
                        progress_callback,
                        "WRITE CLASSIFIED LAS/LAZ",
                        0.75 + 0.24 * processed / max(1, total),
                    )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    stats = {
        "algorithm": ALGORITHM_ID,
        "engine": ENGINE_NAME,
        "revision": REVISION,
        "api_version": API_VERSION,
        "source_type_diagnostic": result.source_type,
        "source_confidence_diagnostic": result.source_confidence,
        "sensor_selection_required": False,
        "ground_points": int(result.ground_count),
        "rejected_points": int(result.non_ground_count),
        "mantle_recovered_points": int(result.mantle_recovered_count),
        "continuity_recovered_points": int(result.continuity_recovered_count),
        "roof_veto_points": int(result.mantle_roof_veto_count),
        "canopy_veto_points": int(result.mantle_canopy_veto_count),
        "input": str(source),
        "output": str(destination),
        "settings": asdict(settings),
    }
    _emit(progress_callback, "CLASSIFY LAS R20.4 COMPLETE", 1.0)
    return stats


def create_mdt_from_classified(
    classified: str | Path,
    output_tif: str | Path,
    settings: Settings = Settings(),
) -> dict:
    """Create MDT from class 2 with measured/interpolated provenance."""
    settings.validate()
    from scipy.ndimage import distance_transform_edt
    import rasterio
    from rasterio.transform import from_origin

    source = Path(classified).resolve()
    output = Path(output_tif).resolve()
    _validate_source(source)
    with laspy.open(source) as reader:
        xmin, ymin = map(float, reader.header.mins[:2])
        xmax, ymax = map(float, reader.header.maxs[:2])
        res = float(settings.mdt_resolution_m)
        width = max(1, int(np.floor((xmax - xmin) / res)) + 1)
        height = max(1, int(np.floor((ymax - ymin) / res)) + 1)
        cells = width * height
        if cells > 20_000_000:
            raise MemoryError("MDT exceeds 20M cells; increase resolution")
        sums = np.zeros(cells, dtype=np.float64)
        counts = np.zeros(cells, dtype=np.uint32)
        used = 0
        for points in reader.chunk_iterator(settings.chunk_points):
            mask = np.asarray(points.classification) == 2
            if not np.any(mask):
                continue
            x = np.asarray(points.x[mask], dtype=np.float64)
            y = np.asarray(points.y[mask], dtype=np.float64)
            z = np.asarray(points.z[mask], dtype=np.float64)
            good = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
            x, y, z = x[good], y[good], z[good]
            col = np.floor((x - xmin) / res).astype(np.int64)
            row = np.floor((ymax - y) / res).astype(np.int64)
            np.clip(col, 0, width - 1, out=col)
            np.clip(row, 0, height - 1, out=row)
            flat = row * width + col
            sums += np.bincount(flat, weights=z, minlength=cells)
            counts += np.bincount(flat, minlength=cells).astype(np.uint32)
            used += len(z)

    if used == 0:
        raise ValueError("No class 2 Ground points")
    count2 = counts.reshape(height, width)
    observed = count2 > 0
    elevation = np.full((height, width), np.nan, dtype=np.float32)
    sum2 = sums.reshape(height, width)
    elevation[observed] = (sum2[observed] / count2[observed]).astype(np.float32)

    state = np.zeros((height, width), dtype=np.uint8)
    state[observed] = 1
    if settings.mdt_max_gap_m > 0:
        distance, nearest = distance_transform_edt(~observed, return_indices=True)
        fill = (~observed) & (distance * res <= settings.mdt_max_gap_m)
        elevation[fill] = elevation[tuple(nearest[:, fill])]
        state[fill] = 2

    output.parent.mkdir(parents=True, exist_ok=True)
    state_path = output.with_name(output.stem + "_OBSERVATION_STATE.tif")
    nodata = -9999.0
    transform = from_origin(xmin, ymax + res / 2.0, res, res)
    profile = dict(
        driver="GTiff", width=width, height=height, count=1, dtype="float32",
        crs="EPSG:3763", transform=transform, nodata=nodata,
        compress="deflate", tiled=True,
    )
    with rasterio.open(output, "w", **profile) as dst:
        dst.write(np.where(np.isfinite(elevation), elevation, nodata).astype(np.float32), 1)
    state_profile = dict(profile)
    state_profile.update(dtype="uint8", nodata=255)
    with rasterio.open(state_path, "w", **state_profile) as dst:
        dst.write(state, 1)

    info = {
        "algorithm": "LAS_CAFIISICA_MDT_R20_4",
        "path": str(output),
        "observation_state": str(state_path),
        "epsg": 3763,
        "resolution_m": res,
        "ground_points_used": int(used),
        "observed_cells": int(np.count_nonzero(state == 1)),
        "interpolated_cells": int(np.count_nonzero(state == 2)),
        "no_ground_observation_cells": int(np.count_nonzero(state == 0)),
        "interpolated_is_measured_ground": False,
    }
    output.with_suffix(".json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return info
