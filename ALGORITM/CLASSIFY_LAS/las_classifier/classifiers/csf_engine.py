from __future__ import annotations

import logging
from dataclasses import dataclass
from math import ceil
from time import perf_counter
from typing import Callable

import numpy as np
from scipy.ndimage import convolve, distance_transform_edt, map_coordinates

from ..cloud.model import CloudModel
from ..ground.density import analyze_cloud
from ..ground.types import GroundAnalysis, GroundEngineParams


LOGGER = logging.getLogger("las_cafiisica.classifiers.csf_engine")
GROUND_CLASS = np.uint8(2)
NON_GROUND_CLASS = np.uint8(1)
ProgressCallback = Callable[[int, str], None]


def _emit(
    callback: ProgressCallback | None,
    percent: int,
    message: str,
) -> None:
    if callback is not None:
        callback(max(0, min(100, int(percent))), message)


@dataclass(slots=True)
class CSFModel:
    params: GroundEngineParams
    analysis: GroundAnalysis
    min_x: float
    min_y: float
    resolution: float
    rows: int
    cols: int
    surface: np.ndarray
    threshold: float
    engine_name: str = "CSF"

    @property
    def synthetic_fill_point_count(self) -> int:
        return 0

    @property
    def effective_fill_spacing(self) -> float:
        return max(self.analysis.median_spacing * 2.5, 0.10)

    def _surface_at(
        self,
        x: np.ndarray,
        y: np.ndarray,
    ) -> np.ndarray:
        cols = (x - self.min_x) / self.resolution
        rows = (y - self.min_y) / self.resolution
        np.clip(cols, 0.0, self.cols - 1.0, out=cols)
        np.clip(rows, 0.0, self.rows - 1.0, out=rows)
        return map_coordinates(
            self.surface,
            [rows, cols],
            order=1,
            mode="nearest",
            prefilter=False,
        ).astype(np.float64, copy=False)

    def confidence_xyz(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        terrain = self._surface_at(x, y)
        residual = np.abs(z - terrain)
        return np.exp(-np.square(residual / max(self.threshold, 1e-6)))

    def classify_xyz(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        confidence = self.confidence_xyz(x, y, z)
        classes = np.full(x.shape[0], NON_GROUND_CLASS, dtype=np.uint8)
        classes[confidence >= 0.50] = GROUND_CLASS
        return classes

    def classify_points(
        self,
        points,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        return self.classify_xyz(x, y, z)

    def iter_synthetic_fill_xyz(self):
        if False:
            yield np.empty(0), np.empty(0), np.empty(0)


@dataclass(frozen=True, slots=True)
class CSFResult:
    model: CSFModel
    ground_count: int
    non_ground_count: int
    elapsed_seconds: float
    analysis: GroundAnalysis
    engine_name: str = "CSF"
    ground_only: bool = True
    synthetic_fill_point_count: int = 0

    @property
    def point_count(self) -> int:
        return self.ground_count + self.non_ground_count


def _nearest_fill(surface: np.ndarray) -> np.ndarray:
    missing = ~np.isfinite(surface)
    if not np.any(missing):
        return surface
    if np.all(missing):
        raise RuntimeError("CSF surface has no valid cells")
    indices = distance_transform_edt(
        missing,
        return_distances=False,
        return_indices=True,
    )
    filled = surface.copy()
    filled[missing] = surface[tuple(indices)][missing]
    return filled


def run_csf(
    cloud: CloudModel,
    params: GroundEngineParams | None = None,
    progress: ProgressCallback | None = None,
    *,
    count_full: bool = True,
) -> CSFResult:
    params = params or GroundEngineParams()
    started = perf_counter()
    LOGGER.info("GROUND_ENGINE_START")
    LOGGER.info("GROUND_ENGINE=CSF")

    _emit(progress, 5, "CSF: analyzing cloud")
    analysis, sample = analyze_cloud(
        cloud,
        sample_target=min(params.sample_target, 2_000_000),
    )

    resolution = max(0.35, min(1.5, analysis.median_spacing * 8.0))
    min_x = float(cloud.las.header.mins[0])
    min_y = float(cloud.las.header.mins[1])
    max_x = float(cloud.las.header.maxs[0])
    max_y = float(cloud.las.header.maxs[1])
    cols = max(1, int(np.floor((max_x - min_x) / resolution)) + 1)
    rows = max(1, int(np.floor((max_y - min_y) / resolution)) + 1)

    _emit(progress, 20, "CSF: building lower envelope")
    surface = np.full((rows, cols), np.inf, dtype=np.float32)
    ix = np.floor((sample[:, 0] - min_x) / resolution).astype(np.int64)
    iy = np.floor((sample[:, 1] - min_y) / resolution).astype(np.int64)
    np.clip(ix, 0, cols - 1, out=ix)
    np.clip(iy, 0, rows - 1, out=iy)
    flat = iy * cols + ix
    np.minimum.at(surface.ravel(), flat, sample[:, 2].astype(np.float32))
    surface[~np.isfinite(surface)] = np.nan
    surface = _nearest_fill(surface)

    # Cloth-like relaxation in original Z coordinates. This is equivalent
    # to dropping a cloth onto the inverted cloud: here the cloth starts below
    # the terrain, moves upward, is smoothed by springs, and is never allowed
    # to pass above the observed lower envelope.
    kernel = np.array(
        [
            [0.0, 0.25, 0.0],
            [0.25, 0.0, 0.25],
            [0.0, 0.25, 0.0],
        ],
        dtype=np.float32,
    )
    iterations = 90 if params.quality in {"high", "extreme"} else 55
    z_min = float(np.min(surface))
    z_max = float(np.max(surface))
    cloth = np.full_like(
        surface,
        z_min - max(1.0, resolution * 2.0),
    )
    gravity_step = max(
        0.05,
        (z_max - z_min + 2.0) / max(1, iterations),
    )
    rigidity = 0.72
    for iteration in range(iterations):
        lifted = cloth + gravity_step
        smooth = convolve(lifted, kernel, mode="nearest")
        candidate = rigidity * lifted + (1.0 - rigidity) * smooth
        cloth = np.minimum(candidate, surface)
        if iteration % 10 == 0:
            _emit(
                progress,
                25 + int(35 * iteration / max(1, iterations)),
                f"CSF cloth iteration {iteration + 1}/{iterations}",
            )

    threshold = max(0.18, min(0.60, analysis.median_spacing * 5.0))
    model = CSFModel(
        params=params,
        analysis=analysis,
        min_x=min_x,
        min_y=min_y,
        resolution=resolution,
        rows=rows,
        cols=cols,
        surface=cloth,
        threshold=threshold,
    )

    ground_count = 0
    total = cloud.point_count
    if count_full:
        scales = cloud.las.header.scales
        offsets = cloud.las.header.offsets
        for start in range(0, total, params.chunk_size):
            stop = min(start + params.chunk_size, total)
            points = cloud.las.points[start:stop]
            x = np.asarray(points.X, dtype=np.float64) * scales[0] + offsets[0]
            y = np.asarray(points.Y, dtype=np.float64) * scales[1] + offsets[1]
            z = np.asarray(points.Z, dtype=np.float64) * scales[2] + offsets[2]
            classes = model.classify_xyz(x, y, z)
            ground_count += int(np.count_nonzero(classes == GROUND_CLASS))
            _emit(
                progress,
                62 + int(38 * stop / max(1, total)),
                f"CSF classify {stop:,}/{total:,}",
            )

    elapsed = perf_counter() - started
    non_ground = total - ground_count if count_full else 0
    LOGGER.info(
        "CSF_DONE resolution=%.3f ground=%d non_ground=%d elapsed=%.3f",
        resolution,
        ground_count,
        non_ground,
        elapsed,
    )
    return CSFResult(
        model=model,
        ground_count=ground_count,
        non_ground_count=non_ground,
        elapsed_seconds=elapsed,
        analysis=analysis,
    )
