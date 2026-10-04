from __future__ import annotations

import logging
from dataclasses import dataclass
from math import degrees
from time import perf_counter
from typing import Callable

import numpy as np

from ..cloud.model import CloudModel
from ..ground.breaklines import triangle_discontinuity
from ..ground.density import analyze_cloud
from ..ground.gap_detection import GapAnalysis, detect_gaps
from ..ground.gap_reconstruction import (
    estimate_fill_count,
    iter_triangle_fill,
)
from ..ground.ground_confidence import ground_confidence
from ..ground.height_above_ground import hag_confidence
from ..ground.ground_seeds import (
    lowest_candidates,
    select_multiscale_seeds,
)
from ..ground.local_geometry import (
    normal_alignment,
    point_normals,
)
from ..ground.noise_filter import robust_noise_mask
from ..ground.terrain_tin import TerrainTIN
from ..ground.types import GroundAnalysis, GroundEngineParams


LOGGER = logging.getLogger("las_cafiisica.classifiers.adaptive_ptd")
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


def _dedupe_xy_lowest(xyz: np.ndarray, resolution: float = 1e-5) -> np.ndarray:
    if xyz.shape[0] <= 1:
        return xyz

    x0 = float(np.min(xyz[:, 0]))
    y0 = float(np.min(xyz[:, 1]))
    ix = np.rint((xyz[:, 0] - x0) / resolution).astype(np.int64)
    iy = np.rint((xyz[:, 1] - y0) / resolution).astype(np.int64)
    width = int(ix.max() - ix.min() + 1)
    keys = (ix - ix.min()) + width * (iy - iy.min())

    order = np.lexsort((xyz[:, 2], keys))
    sorted_keys = keys[order]
    first = np.r_[True, sorted_keys[1:] != sorted_keys[:-1]]
    return xyz[order[first]]


def _axelsson_acceptance(
    tin: TerrainTIN,
    xyz: np.ndarray,
    *,
    max_distance: float,
    max_angle_deg: float,
    min_triangle_edge: float,
    candidate_normals: np.ndarray | None = None,
    min_normal_alignment: float = 0.58,
) -> np.ndarray:
    metrics = tin.metrics(xyz[:, 0], xyz[:, 1], xyz[:, 2])
    simplex = metrics["simplex"]
    valid = metrics["valid"]
    if not np.any(valid):
        return np.zeros(xyz.shape[0], dtype=np.bool_)

    safe = np.maximum(simplex, 0)
    tri = tin.vertices[tin.simplices[safe]]
    signed = metrics["signed_distance"]
    normals = metrics["normal"]
    projected = xyz - signed[:, None] * normals

    h0 = np.linalg.norm(projected - tri[:, 0], axis=1)
    h1 = np.linalg.norm(projected - tri[:, 1], axis=1)
    h2 = np.linalg.norm(projected - tri[:, 2], axis=1)
    nearest_vertex = np.minimum(h0, np.minimum(h1, h2))
    angle = np.degrees(
        np.arctan2(
            metrics["plane_distance"],
            np.maximum(nearest_vertex, 1e-6),
        )
    )

    accepted = (
        valid
        & (metrics["plane_distance"] <= max_distance)
        & (angle <= max_angle_deg)
        & (metrics["max_edge"] >= min_triangle_edge)
    )

    if candidate_normals is not None:
        candidate_normals = np.asarray(
            candidate_normals,
            dtype=np.float64,
        )
        known = np.all(np.isfinite(candidate_normals), axis=1)
        if np.any(known):
            alignment = np.zeros(xyz.shape[0], dtype=np.float64)
            alignment[known] = np.abs(
                np.einsum(
                    "ij,ij->i",
                    candidate_normals[known],
                    normals[known],
                )
            )
            accepted[
                known
                & (alignment < float(min_normal_alignment))
            ] = False

    return accepted


def _adaptive_settings(
    analysis: GroundAnalysis,
    params: GroundEngineParams,
) -> tuple[float, float, float, float, float]:
    spacing = max(analysis.median_spacing, 0.01)
    seed_resolution = (
        params.seed_resolution
        if params.seed_resolution > 0
        else max(4.0, min(12.0, spacing * 35.0))
    )
    if params.candidate_spacing > 0:
        candidate_spacing = params.candidate_spacing
    else:
        # Keep the global Delaunay tractable on 100M+ point projects. The
        # spacing cap is derived from XY area, not point count, so high-density
        # photogrammetry does not create millions of almost redundant TIN
        # vertices. Higher quality presets raise the target vertex budget.
        area = (
            analysis.point_count / analysis.xy_density
            if analysis.xy_density > 0
            else 1.0
        )
        target_candidates = {
            "fast": 180_000,
            "balanced": 350_000,
            "high": 650_000,
            "extreme": 1_000_000,
        }.get(params.quality, 350_000)
        area_spacing = np.sqrt(
            max(area, 1.0) / target_candidates
        )
        candidate_spacing = max(
            0.35,
            min(
                1.75,
                max(
                    spacing * 7.0,
                    float(area_spacing),
                ),
            ),
        )
    max_edge = (
        params.max_triangle_edge
        if params.max_triangle_edge > 0
        else max(seed_resolution * 2.0, candidate_spacing * 12.0)
    )
    gap_max = max(
        params.gap_max_size,
        candidate_spacing * 10.0,
    )
    if params.synthetic_spacing > 0:
        synthetic_spacing = params.synthetic_spacing
    else:
        # Reconstruction density follows the *native* XY density instead of
        # the thinned PTD sample spacing. This makes the exported Ground Only
        # cloud physically fill supported gaps at a density comparable with
        # the source cloud rather than only looking closed in Potree.
        density_spacing = (
            float(np.sqrt(1.0 / analysis.xy_density))
            if analysis.xy_density > 0
            else spacing
        )
        native_spacing = max(
            0.02,
            min(spacing, density_spacing),
        )
        spacing_factor = {
            "fast": 2.5,
            "balanced": 2.0,
            "high": 1.5,
            "extreme": 1.0,
        }.get(params.quality, 2.0)
        synthetic_spacing = max(
            0.025,
            min(0.30, native_spacing * spacing_factor),
        )
    return (
        seed_resolution,
        candidate_spacing,
        max_edge,
        gap_max,
        synthetic_spacing,
    )


@dataclass(slots=True)
class AdaptivePTDModel:
    params: GroundEngineParams
    analysis: GroundAnalysis
    tin: TerrainTIN
    discontinuity: np.ndarray
    gaps: GapAnalysis
    distance_limit: float
    max_triangle_edge: float
    effective_fill_spacing: float
    synthetic_fill_point_count: int
    engine_name: str = "Adaptive PTD"

    def _confidence(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
        points=None,
    ) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        metrics = self.tin.metrics(x, y, z)
        simplex = metrics["simplex"]
        valid = metrics["valid"]
        safe = np.maximum(simplex, 0)

        discontinuity = np.ones(x.shape[0], dtype=np.float64)
        discontinuity[valid] = self.discontinuity[safe[valid]]

        alignment = None
        if points is not None:
            normals = point_normals(points)
            alignment = normal_alignment(normals, metrics["normal"])

        confidence = ground_confidence(
            metrics["plane_distance"],
            metrics["max_edge"],
            distance_limit=self.distance_limit,
            edge_limit=self.max_triangle_edge,
            discontinuity=discontinuity,
            normal_alignment=alignment,
        )

        # Height-above-ground is evaluated against the final TIN rather than a
        # raster DEM. This gives a strong vegetation veto even on steep faces,
        # while point-to-plane distance still protects genuine talude points.
        hag_limit = max(
            0.22,
            min(
                0.55,
                self.analysis.median_spacing * 5.0,
            ),
        )
        hscore = hag_confidence(
            metrics["vertical_residual"],
            positive_limit=hag_limit,
            negative_limit=hag_limit * 1.8,
        )
        confidence = 0.76 * confidence + 0.24 * hscore
        high_above = (
            metrics["vertical_residual"]
            > hag_limit * 1.35
        )
        confidence[high_above] *= 0.15
        confidence[~valid] = 0.0

        # Large unsupported triangles are deliberately not trusted. This is
        # the key safety rule that prevents trees from becoming "terrain"
        # simply because a broad TIN facet happens to pass nearby.
        confidence[
            metrics["max_edge"] > self.max_triangle_edge
        ] *= 0.20

        if alignment is not None:
            known = np.isfinite(alignment)
            suspicious = known & (alignment < 0.60)
            confidence[suspicious] *= 0.35

        # Hard near-ground rule requested for the final terrain product:
        # a measured return that is within 10 cm of the reconstructed local
        # terrain is ground, even when photogrammetric normals are noisy.
        # We still require a valid, supported TIN facet so this rule cannot
        # bridge a large unknown gap or extrapolate outside the terrain.
        # Delaunay triangles across a regular cell can contain a diagonal
        # sqrt(2) times longer than the nominal supported edge. Treat that
        # diagonal as the same supported local neighbourhood; otherwise steep
        # but perfectly valid terrain faces are rejected only because of the
        # triangulation diagonal.
        near_ground_edge_limit = (
            self.max_triangle_edge * np.sqrt(2.0)
            + 1e-9
        )
        near_ground = (
            valid
            & np.isfinite(metrics["plane_distance"])
            & (metrics["plane_distance"] <= 0.10)
            & (
                metrics["max_edge"]
                <= near_ground_edge_limit
            )
            & (discontinuity <= 0.35)
        )
        # Use true 3-D point-to-plane distance, not vertical Z difference.
        # On steep taludes a genuine terrain point can be >10 cm vertically
        # from the local plane while still being <10 cm normal to the face.
        confidence[near_ground] = np.maximum(
            confidence[near_ground],
            0.98,
        )

        return confidence, metrics

    def classify_xyz(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        confidence, _ = self._confidence(x, y, z)
        classes = np.full(
            x.shape[0],
            NON_GROUND_CLASS,
            dtype=np.uint8,
        )
        classes[
            confidence >= self.params.confidence_threshold
        ] = GROUND_CLASS
        return classes

    def classify_points(
        self,
        points,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        confidence, metrics = self._confidence(
            x,
            y,
            z,
            points=points,
        )
        classes = np.full(
            x.shape[0],
            NON_GROUND_CLASS,
            dtype=np.uint8,
        )
        classes[
            confidence >= self.params.confidence_threshold
        ] = GROUND_CLASS
        return classes

    def confidence_xyz(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        confidence, _ = self._confidence(x, y, z)
        return confidence

    def confidence_points(
        self,
        points,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        confidence, _ = self._confidence(
            x,
            y,
            z,
            points=points,
        )
        return confidence

    def iter_synthetic_fill_xyz(self):
        yield from iter_triangle_fill(
            self.tin,
            self.gaps.supported_mask,
            self.effective_fill_spacing,
        )

    def iter_viewer_synthetic_fill_xyz(self):
        # Potree preview stays bounded. The Ground Only export uses the full
        # reconstruction density through iter_synthetic_fill_xyz().
        yield from iter_triangle_fill(
            self.tin,
            self.gaps.supported_mask,
            self.effective_fill_spacing,
            max_points=12_000_000,
        )


@dataclass(frozen=True, slots=True)
class AdaptivePTDResult:
    model: AdaptivePTDModel
    ground_count: int
    non_ground_count: int
    elapsed_seconds: float
    analysis: GroundAnalysis
    seed_count: int
    candidate_count: int
    ptd_iterations: int
    detected_gap_count: int
    supported_gap_count: int
    occluded_gap_count: int
    rejected_gap_count: int
    synthetic_fill_point_count: int
    mean_confidence: float
    engine_name: str = "Adaptive PTD"
    ground_only: bool = True

    @property
    def point_count(self) -> int:
        return self.ground_count + self.non_ground_count


def run_adaptive_ptd(
    cloud: CloudModel,
    params: GroundEngineParams | None = None,
    progress: ProgressCallback | None = None,
    *,
    count_full: bool = True,
) -> AdaptivePTDResult:
    params = params or GroundEngineParams()
    started = perf_counter()
    LOGGER.info("GROUND_ENGINE_START")
    LOGGER.info("GROUND_ENGINE=ADAPTIVE_PTD")
    LOGGER.info("POINTS_INPUT=%d", cloud.point_count)

    _emit(progress, 3, "Analyzing cloud")
    analysis, sample_xyz = analyze_cloud(
        cloud,
        sample_target=params.sample_target,
    )
    (
        seed_resolution,
        candidate_spacing,
        max_triangle_edge,
        gap_max_size,
        synthetic_spacing,
    ) = _adaptive_settings(analysis, params)

    _emit(progress, 10, "Removing noise / low outliers")
    valid = robust_noise_mask(
        sample_xyz,
        analysis.median_spacing,
    )

    _emit(progress, 18, "Detecting multiscale ground seeds")
    seeds = select_multiscale_seeds(
        sample_xyz,
        valid,
        analysis.median_spacing,
        seed_resolution,
    )
    seed_xyz = _dedupe_xy_lowest(
        sample_xyz[seeds.indices]
    )
    if seed_xyz.shape[0] < 3:
        raise RuntimeError("Adaptive PTD could not create enough ground seeds")

    candidate_ids = lowest_candidates(
        sample_xyz,
        valid,
        candidate_spacing,
    )
    candidate_xyz = sample_xyz[candidate_ids]
    candidate_normals = None
    # Legacy point-stride samples could map candidates back to source indices.
    # R20.4 spatial-low sampling is intentionally order-independent and has no
    # one-dimensional source-index mapping; normals therefore remain optional.
    if analysis.sample_stride > 0:
        sample_source_indices = np.arange(
            0,
            cloud.point_count,
            analysis.sample_stride,
            dtype=np.int64,
        )[: sample_xyz.shape[0]]
        if sample_source_indices.size == sample_xyz.shape[0]:
            try:
                candidate_points = cloud.las.points[
                    sample_source_indices[candidate_ids]
                ]
                candidate_normals = point_normals(candidate_points)
            except Exception:
                LOGGER.exception(
                    "PTD candidate normal extraction failed; "
                    "continuing without normal gate"
                )
                candidate_normals = None

    known_candidate_normals = (
        int(
            np.count_nonzero(
                np.all(np.isfinite(candidate_normals), axis=1)
            )
        )
        if candidate_normals is not None
        else 0
    )
    LOGGER.info(
        "CANDIDATES=%d PTD_NORMALS_AVAILABLE=%d",
        candidate_xyz.shape[0],
        known_candidate_normals,
    )

    vertices = seed_xyz
    accepted_total = 0
    iterations = 0

    for iteration in range(1, params.max_iterations + 1):
        _emit(
            progress,
            22 + int(40 * iteration / max(1, params.max_iterations)),
            f"PTD iteration {iteration}",
        )
        tin = TerrainTIN.build(vertices)
        accepted = _axelsson_acceptance(
            tin,
            candidate_xyz,
            max_distance=max(
                params.max_iteration_distance,
                analysis.median_spacing * 2.0,
            ),
            max_angle_deg=params.max_iteration_angle_deg,
            min_triangle_edge=candidate_spacing * 0.75,
            candidate_normals=candidate_normals,
        )

        if not np.any(accepted):
            break

        new_points = candidate_xyz[accepted]
        previous_count = vertices.shape[0]
        vertices = _dedupe_xy_lowest(
            np.vstack((vertices, new_points))
        )
        added = vertices.shape[0] - previous_count
        candidate_xyz = candidate_xyz[~accepted]
        if candidate_normals is not None:
            candidate_normals = candidate_normals[~accepted]
        iterations = iteration
        accepted_total += max(0, added)

        LOGGER.info(
            "PTD_ITERATION=%d ACCEPTED=%d REMAINING=%d TIN_VERTICES=%d",
            iteration,
            added,
            candidate_xyz.shape[0],
            vertices.shape[0],
        )
        if added < max(20, int(vertices.shape[0] * 0.0005)):
            break

    _emit(progress, 64, "Building final terrain TIN")
    tin = TerrainTIN.build(vertices)
    discontinuity = triangle_discontinuity(tin)

    dense_edge = max(
        candidate_spacing * 2.5,
        analysis.median_spacing * 6.0,
    )
    gaps = detect_gaps(
        tin,
        dense_edge=dense_edge,
        max_gap_edge=gap_max_size,
        evidence_xyz=sample_xyz,
        occlusion_hag=max(
            0.50,
            analysis.median_spacing * 6.0,
        ),
    )
    synthetic_count = estimate_fill_count(
        tin,
        gaps.supported_mask,
        synthetic_spacing,
    )

    distance_limit = max(
        0.10,
        min(
            0.32,
            max(
                params.max_iteration_distance,
                analysis.median_spacing * 2.2,
            ),
        ),
    )
    model = AdaptivePTDModel(
        params=params,
        analysis=analysis,
        tin=tin,
        discontinuity=discontinuity,
        gaps=gaps,
        distance_limit=distance_limit,
        max_triangle_edge=max_triangle_edge,
        effective_fill_spacing=synthetic_spacing,
        synthetic_fill_point_count=synthetic_count,
    )

    total = cloud.point_count
    ground_count = 0
    confidence_sum = 0.0
    confidence_n = 0

    if count_full:
        _emit(progress, 70, "Validating full cloud")
        scales = cloud.las.header.scales
        offsets = cloud.las.header.offsets

        for start in range(0, total, params.chunk_size):
            stop = min(start + params.chunk_size, total)
            points = cloud.las.points[start:stop]
            x = (
                np.asarray(points.X, dtype=np.float64)
                * float(scales[0])
                + float(offsets[0])
            )
            y = (
                np.asarray(points.Y, dtype=np.float64)
                * float(scales[1])
                + float(offsets[1])
            )
            z = (
                np.asarray(points.Z, dtype=np.float64)
                * float(scales[2])
                + float(offsets[2])
            )
            classes = model.classify_points(points, x, y, z)
            ground_count += int(np.count_nonzero(classes == GROUND_CLASS))

            confidence = model.confidence_xyz(x, y, z)
            confidence_sum += float(np.sum(confidence))
            confidence_n += int(confidence.size)

            _emit(
                progress,
                70 + int(26 * stop / max(1, total)),
                f"Validating ground {stop:,}/{total:,}",
            )

    non_ground = total - ground_count if count_full else 0
    elapsed = perf_counter() - started
    mean_confidence = (
        confidence_sum / confidence_n
        if confidence_n
        else 0.0
    )

    LOGGER.info("TIN_TRIANGLES=%d", tin.triangle_count)
    LOGGER.info("TIN_MAX_EDGE=%.3f", max_triangle_edge)
    LOGGER.info("GROUND_REAL=%d", ground_count)
    LOGGER.info("NON_GROUND=%d", non_ground)
    LOGGER.info("GAPS_TOTAL=%d", gaps.detected_count)
    LOGGER.info("GAPS_SUPPORTED=%d", gaps.supported_count)
    LOGGER.info("GAPS_OCCLUDED=%d", gaps.occluded_count)
    LOGGER.info("GAPS_REJECTED=%d", gaps.rejected_count)
    LOGGER.info("SYNTHETIC_POINTS=%d", synthetic_count)
    LOGGER.info("GROUND_CONFIDENCE_MEAN=%.4f", mean_confidence)
    LOGGER.info("PROCESSING_TIME=%.3f", elapsed)

    _emit(progress, 100, "Adaptive PTD complete")
    return AdaptivePTDResult(
        model=model,
        ground_count=ground_count,
        non_ground_count=non_ground,
        elapsed_seconds=elapsed,
        analysis=analysis,
        seed_count=int(seed_xyz.shape[0]),
        candidate_count=int(candidate_ids.size),
        ptd_iterations=iterations,
        detected_gap_count=gaps.detected_count,
        supported_gap_count=gaps.supported_count,
        occluded_gap_count=gaps.occluded_count,
        rejected_gap_count=gaps.rejected_count,
        synthetic_fill_point_count=synthetic_count,
        mean_confidence=mean_confidence,
    )
