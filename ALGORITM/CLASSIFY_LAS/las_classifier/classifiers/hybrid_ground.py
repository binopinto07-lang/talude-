from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Callable

import numpy as np

from ..cloud.model import CloudModel
from ..ground.types import GroundEngineParams
from ..terrain.l3_recovery import (
    L3RecoveryModel,
    L3RecoveryParams,
    build_l3_recovery,
)
from ..terrain.schema import SourceInspection, SourceType
from ..terrain.source_inspector import inspect_source
from .adaptive_ptd import (
    AdaptivePTDModel,
    GROUND_CLASS,
    NON_GROUND_CLASS,
    run_adaptive_ptd,
)
from .csf_engine import CSFModel, run_csf
from .smrf import SMRFModel, SMRFParams, run_smrf
from .terrain3d import (
    Terrain3DParams,
    Terrain3DRefinement,
    build_terrain3d_refinement,
)


LOGGER = logging.getLogger(
    "las_cafiisica.classifiers.hybrid_ground"
)
ProgressCallback = Callable[[int, str], None]


def _emit(
    callback: ProgressCallback | None,
    percent: int,
    message: str,
) -> None:
    if callback is not None:
        callback(
            max(0, min(100, int(percent))),
            message,
        )


def _l3_params(
    params: GroundEngineParams,
) -> L3RecoveryParams:
    quality = params.quality
    if quality == "extreme":
        return L3RecoveryParams(
            sample_target=8_000_000,
            voxel_size=0.35,
            max_plane_distance=0.25,
            min_points_per_voxel=3,
            min_geometry_score=0.26,
            strong_geometry_score=0.60,
            min_strong_fraction=0.16,
        )
    if quality == "high":
        return L3RecoveryParams(
            sample_target=5_000_000,
            voxel_size=0.40,
            max_plane_distance=0.26,
            min_points_per_voxel=3,
            min_geometry_score=0.27,
            strong_geometry_score=0.61,
            min_strong_fraction=0.18,
        )
    if quality == "fast":
        return L3RecoveryParams(
            sample_target=1_000_000,
            voxel_size=0.75,
            max_plane_distance=0.30,
            min_points_per_voxel=4,
            min_geometry_score=0.34,
            strong_geometry_score=0.67,
            min_strong_fraction=0.25,
        )
    return L3RecoveryParams()


def _smrf_params(
    params: GroundEngineParams,
    inspection: SourceInspection,
) -> SMRFParams:
    # SMRF is used as an independent vote only. Terrain3D is explicitly
    # disabled here so a coherent canopy cannot become ground merely because
    # its normals form a surface.
    if inspection.source_type is SourceType.P1_PHOTOGRAMMETRY:
        return SMRFParams(
            cell=0.60,
            slope=0.18,
            window=14.0,
            threshold=0.28,
            scalar=1.15,
            fill_spacing=max(
                params.synthetic_spacing or 0.25,
                0.20,
            ),
            chunk_size=params.chunk_size,
            terrain3d_enabled=False,
        )
    if inspection.source_type is SourceType.L3_LIDAR:
        return SMRFParams(
            cell=0.50,
            slope=0.20,
            window=14.0,
            threshold=0.32,
            scalar=1.20,
            fill_spacing=max(
                params.synthetic_spacing or 0.25,
                0.20,
            ),
            chunk_size=params.chunk_size,
            terrain3d_enabled=False,
        )
    return SMRFParams(
        cell=0.60,
        slope=0.18,
        window=14.0,
        threshold=0.30,
        scalar=1.20,
        fill_spacing=max(
            params.synthetic_spacing or 0.25,
            0.20,
        ),
        chunk_size=params.chunk_size,
        terrain3d_enabled=False,
    )


def _terrain3d_params(
    params: GroundEngineParams,
) -> Terrain3DParams:
    # For photogrammetry Terrain3D is a SUPPORT GATE, never a positive rescue.
    # Deliberately stricter than the previous R15 settings.
    quality = params.quality
    if quality == "extreme":
        return Terrain3DParams(
            voxel=0.30,
            surface_thickness=0.09,
            min_points=6,
            coherence=0.80,
            seed_ground_fraction=0.68,
            max_normal_angle_deg=80.0,
            target_sample_points=30_000_000,
        )
    if quality == "high":
        return Terrain3DParams(
            voxel=0.35,
            surface_thickness=0.10,
            min_points=6,
            coherence=0.79,
            seed_ground_fraction=0.68,
            max_normal_angle_deg=80.0,
            target_sample_points=20_000_000,
        )
    if quality == "fast":
        return Terrain3DParams(
            voxel=0.75,
            surface_thickness=0.14,
            min_points=6,
            coherence=0.82,
            seed_ground_fraction=0.72,
            max_normal_angle_deg=78.0,
            target_sample_points=6_000_000,
        )
    return Terrain3DParams(
        voxel=0.45,
        surface_thickness=0.11,
        min_points=6,
        coherence=0.80,
        seed_ground_fraction=0.70,
        max_normal_angle_deg=80.0,
        target_sample_points=12_000_000,
    )


@dataclass(slots=True)
class HybridGroundModel:
    params: GroundEngineParams
    ptd: AdaptivePTDModel
    csf: CSFModel
    smrf: SMRFModel
    terrain3d: Terrain3DRefinement | None = None
    l3_recovery: L3RecoveryModel | None = None
    source_inspection: SourceInspection | None = None
    engine_name: str = "Hybrid"

    @property
    def _source_type(self) -> SourceType:
        if self.source_inspection is None:
            return SourceType.UNKNOWN
        return self.source_inspection.source_type

    @property
    def synthetic_fill_point_count(self) -> int:
        # A P1 canopy can be the only observed surface. Until an observability
        # model proves that a missing patch is safely reconstructable, do not
        # manufacture a hidden terrain surface from photogrammetry.
        if self._source_type is SourceType.P1_PHOTOGRAMMETRY:
            return 0
        return self.ptd.synthetic_fill_point_count

    @property
    def effective_fill_spacing(self) -> float:
        return self.ptd.effective_fill_spacing

    def _evidence(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
        points=None,
    ) -> dict[str, np.ndarray]:
        ptd_score, _ = self.ptd._confidence(
            x,
            y,
            z,
            points=points,
        )
        csf_score = self.csf.confidence_xyz(
            x,
            y,
            z,
        )
        smrf_ground = (
            self.smrf.classify_xyz(
                x,
                y,
                z,
            )
            == GROUND_CLASS
        )

        ptd_ground = (
            ptd_score
            >= self.params.confidence_threshold
        )
        ptd_strong = ptd_score >= max(
            0.84,
            self.params.confidence_threshold + 0.16,
        )
        csf_ground = csf_score >= 0.50

        votes = (
            ptd_ground.astype(np.uint8)
            + csf_ground.astype(np.uint8)
            + smrf_ground.astype(np.uint8)
        )

        terrain_support = np.zeros(
            x.shape[0],
            dtype=np.bool_,
        )
        if self.terrain3d is not None:
            terrain_support = self.terrain3d.ground_mask(
                x,
                y,
                z,
            )

        l3_recovered = np.zeros(
            x.shape[0],
            dtype=np.bool_,
        )
        if (
            self.l3_recovery is not None
            and points is not None
        ):
            l3_recovered = (
                self.l3_recovery.recovered_mask(
                    points,
                    x,
                    y,
                    z,
                )
            )

        return {
            "ptd_score": ptd_score,
            "csf_score": csf_score,
            "smrf_ground": smrf_ground,
            "ptd_ground": ptd_ground,
            "ptd_strong": ptd_strong,
            "csf_ground": csf_ground,
            "votes": votes,
            "terrain_support": terrain_support,
            "l3_recovered": l3_recovered,
        }

    def _ground_mask_from_evidence(
        self,
        e: dict[str, np.ndarray],
    ) -> np.ndarray:
        ptd_score = e["ptd_score"]
        ptd_strong = e["ptd_strong"]
        csf_ground = e["csf_ground"]
        smrf_ground = e["smrf_ground"]
        votes = e["votes"]
        source_type = self._source_type

        if source_type is SourceType.P1_PHOTOGRAMMETRY:
            # P1: never interpret a coherent visible canopy as hidden ground.
            # Require PTD geometric support plus at least one independent
            # filter. When trustworthy normals exist, the 3D surface acts only
            # as an additional support gate, never as a rescue by itself.
            base = (
                (votes >= 2)
                & (ptd_score >= 0.40)
            )
            if self.terrain3d is not None:
                base &= (
                    e["terrain_support"]
                    | ptd_strong
                )
            return (
                base
                | (
                    ptd_strong
                    & csf_ground
                    & smrf_ground
                )
            )

        if source_type is SourceType.L3_LIDAR:
            # L3: two independent geometric votes are enough. A measured
            # last/only return can additionally recover a point, but only when
            # at least one independent surface filter or moderate PTD support
            # agrees. Return position alone is never ground.
            consensus = votes >= 2
            recovered = (
                e["l3_recovered"]
                & (
                    csf_ground
                    | smrf_ground
                    | (ptd_score >= 0.45)
                )
            )
            return (
                consensus
                | recovered
                | ptd_strong
            )

        return (
            (votes >= 2)
            | ptd_strong
        )

    def _score(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
        points=None,
    ) -> np.ndarray:
        e = self._evidence(
            x,
            y,
            z,
            points=points,
        )
        score = (
            0.50 * e["ptd_score"]
            + 0.25 * e["csf_score"]
            + 0.25 * e["smrf_ground"].astype(
                np.float64
            )
        )

        if self._source_type is SourceType.L3_LIDAR:
            score[e["l3_recovered"]] = np.maximum(
                score[e["l3_recovered"]],
                0.82,
            )
        elif (
            self._source_type
            is SourceType.P1_PHOTOGRAMMETRY
            and self.terrain3d is not None
        ):
            unsupported = (
                ~e["terrain_support"]
                & ~e["ptd_strong"]
            )
            score[unsupported] *= 0.55

        ground = self._ground_mask_from_evidence(e)
        score[ground] = np.maximum(
            score[ground],
            0.80,
        )
        score[~ground] = np.minimum(
            score[~ground],
            0.49,
        )
        return np.clip(
            score,
            0.0,
            1.0,
        )

    def classify_xyz(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        e = self._evidence(
            x,
            y,
            z,
        )
        ground = self._ground_mask_from_evidence(e)
        classes = np.full(
            x.shape[0],
            NON_GROUND_CLASS,
            dtype=np.uint8,
        )
        classes[ground] = GROUND_CLASS
        return classes

    def classify_points(
        self,
        points,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        e = self._evidence(
            x,
            y,
            z,
            points=points,
        )
        ground = self._ground_mask_from_evidence(e)
        classes = np.full(
            x.shape[0],
            NON_GROUND_CLASS,
            dtype=np.uint8,
        )
        classes[ground] = GROUND_CLASS
        return classes

    def confidence_xyz(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        return self._score(
            x,
            y,
            z,
        )

    def confidence_points(
        self,
        points,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        return self._score(
            x,
            y,
            z,
            points=points,
        )

    def iter_synthetic_fill_xyz(self):
        if self._source_type is SourceType.P1_PHOTOGRAMMETRY:
            return
        yield from self.ptd.iter_synthetic_fill_xyz()

    def iter_viewer_synthetic_fill_xyz(self):
        if self._source_type is SourceType.P1_PHOTOGRAMMETRY:
            return
        yield from self.ptd.iter_viewer_synthetic_fill_xyz()


@dataclass(frozen=True, slots=True)
class HybridGroundResult:
    model: HybridGroundModel
    ground_count: int
    non_ground_count: int
    elapsed_seconds: float
    analysis: object
    seed_count: int
    candidate_count: int
    ptd_iterations: int
    detected_gap_count: int
    supported_gap_count: int
    occluded_gap_count: int
    rejected_gap_count: int
    synthetic_fill_point_count: int
    mean_confidence: float
    terrain3d_voxel_count: int = 0
    terrain3d_seed_voxel_count: int = 0
    l3_recovered_count: int = 0
    l3_recovery_voxel_count: int = 0
    ptd_vote_count: int = 0
    smrf_vote_count: int = 0
    csf_vote_count: int = 0
    consensus_2of3_count: int = 0
    source_type: str = "UNKNOWN"
    source_confidence: float = 0.0
    engine_name: str = "Hybrid"
    ground_only: bool = True

    @property
    def point_count(self) -> int:
        return (
            self.ground_count
            + self.non_ground_count
        )


def run_hybrid_ground(
    cloud: CloudModel,
    params: GroundEngineParams | None = None,
    progress: ProgressCallback | None = None,
    source_override: SourceType | str | None = None,
) -> HybridGroundResult:
    params = params or GroundEngineParams()
    started = perf_counter()
    LOGGER.info("GROUND_ENGINE_START")
    LOGGER.info("GROUND_ENGINE=HYBRID_CONSENSUS_R1")

    inspection = inspect_source(
        cloud,
        override=source_override,
    )
    LOGGER.info(
        "GROUND_SOURCE type=%s confidence=%.3f evidence=%s",
        inspection.source_type.value,
        inspection.confidence,
        "; ".join(inspection.evidence),
    )

    def ptd_progress(
        percent: int,
        message: str,
    ) -> None:
        _emit(
            progress,
            int(percent * 0.35),
            message,
        )

    ptd_result = run_adaptive_ptd(
        cloud,
        params,
        ptd_progress,
        count_full=False,
    )

    def csf_progress(
        percent: int,
        message: str,
    ) -> None:
        _emit(
            progress,
            35 + int(percent * 0.10),
            message,
        )

    csf_result = run_csf(
        cloud,
        params,
        csf_progress,
        count_full=False,
    )

    def smrf_progress(
        percent: int,
        message: str,
    ) -> None:
        _emit(
            progress,
            45 + int(percent * 0.15),
            message,
        )

    smrf_result = run_smrf(
        cloud,
        _smrf_params(
            params,
            inspection,
        ),
        smrf_progress,
        count_full=False,
    )

    l3_recovery = build_l3_recovery(
        cloud,
        ptd_result.model,
        inspection,
        progress,
        _l3_params(params),
    )

    terrain3d = None
    if (
        inspection.source_type
        is SourceType.P1_PHOTOGRAMMETRY
    ):
        _emit(
            progress,
            69,
            "P1: validating coherent measured surfaces",
        )
        terrain3d = build_terrain3d_refinement(
            cloud,
            ptd_result.model,
            progress,
            _terrain3d_params(params),
        )

    model = HybridGroundModel(
        params=params,
        ptd=ptd_result.model,
        csf=csf_result.model,
        smrf=smrf_result.model,
        terrain3d=terrain3d,
        l3_recovery=l3_recovery,
        source_inspection=inspection,
    )

    total = cloud.point_count
    ground_count = 0
    confidence_sum = 0.0
    confidence_n = 0
    l3_recovered_count = 0
    ptd_vote_count = 0
    smrf_vote_count = 0
    csf_vote_count = 0
    consensus_2of3_count = 0

    scales = cloud.las.header.scales
    offsets = cloud.las.header.offsets

    _emit(
        progress,
        80,
        "Hybrid consensus: validating every measured point",
    )

    for start in range(
        0,
        total,
        params.chunk_size,
    ):
        stop = min(
            start + params.chunk_size,
            total,
        )
        points = cloud.las.points[start:stop]
        x = (
            np.asarray(
                points.X,
                dtype=np.float64,
            )
            * scales[0]
            + offsets[0]
        )
        y = (
            np.asarray(
                points.Y,
                dtype=np.float64,
            )
            * scales[1]
            + offsets[1]
        )
        z = (
            np.asarray(
                points.Z,
                dtype=np.float64,
            )
            * scales[2]
            + offsets[2]
        )

        evidence = model._evidence(
            x,
            y,
            z,
            points=points,
        )
        ground = model._ground_mask_from_evidence(
            evidence
        )
        ground_count += int(
            np.count_nonzero(ground)
        )

        ptd_vote_count += int(
            np.count_nonzero(
                evidence["ptd_ground"]
            )
        )
        smrf_vote_count += int(
            np.count_nonzero(
                evidence["smrf_ground"]
            )
        )
        csf_vote_count += int(
            np.count_nonzero(
                evidence["csf_ground"]
            )
        )
        consensus_2of3_count += int(
            np.count_nonzero(
                evidence["votes"] >= 2
            )
        )
        l3_recovered_count += int(
            np.count_nonzero(
                evidence["l3_recovered"]
            )
        )

        score = (
            0.50 * evidence["ptd_score"]
            + 0.25 * evidence["csf_score"]
            + 0.25
            * evidence["smrf_ground"].astype(
                np.float64
            )
        )
        confidence_sum += float(
            np.sum(score)
        )
        confidence_n += int(score.size)

        _emit(
            progress,
            80
            + int(
                19
                * stop
                / max(1, total)
            ),
            (
                "Hybrid consensus "
                f"{stop:,}/{total:,}"
            ),
        )

    non_ground = total - ground_count
    mean_confidence = (
        confidence_sum / confidence_n
        if confidence_n
        else 0.0
    )
    elapsed = perf_counter() - started

    terrain3d_voxels = (
        terrain3d.terrain_voxel_count
        if terrain3d is not None
        else 0
    )
    terrain3d_seeds = (
        terrain3d.seed_voxels
        if terrain3d is not None
        else 0
    )

    LOGGER.info(
        "CONSENSUS_VOTES PTD=%d SMRF=%d CSF=%d TWO_OF_THREE=%d",
        ptd_vote_count,
        smrf_vote_count,
        csf_vote_count,
        consensus_2of3_count,
    )
    LOGGER.info(
        "GROUND_REAL=%d NON_GROUND=%d",
        ground_count,
        non_ground,
    )
    LOGGER.info(
        "TERRAIN3D_SUPPORT_VOXELS=%d TERRAIN3D_SEEDS=%d",
        terrain3d_voxels,
        terrain3d_seeds,
    )
    LOGGER.info(
        "L3_RETURN_SUPPORTED_POINTS=%d L3_RECOVERY_VOXELS=%d",
        l3_recovered_count,
        (
            model.l3_recovery.approved_voxel_count
            if model.l3_recovery is not None
            else 0
        ),
    )
    LOGGER.info(
        "INPUT_CLASSIFICATION_USED=0"
    )
    LOGGER.info(
        "P1_SYNTHETIC_POLICY=%s",
        (
            "DISABLED_UNTIL_OBSERVABILITY"
            if inspection.source_type
            is SourceType.P1_PHOTOGRAMMETRY
            else "PTD_SUPPORTED_GAPS"
        ),
    )
    LOGGER.info(
        "PROCESSING_TIME=%.3f",
        elapsed,
    )

    _emit(
        progress,
        100,
        "Hybrid consensus complete",
    )
    return HybridGroundResult(
        model=model,
        ground_count=ground_count,
        non_ground_count=non_ground,
        elapsed_seconds=elapsed,
        analysis=ptd_result.analysis,
        seed_count=ptd_result.seed_count,
        candidate_count=ptd_result.candidate_count,
        ptd_iterations=ptd_result.ptd_iterations,
        detected_gap_count=(
            ptd_result.detected_gap_count
        ),
        supported_gap_count=(
            ptd_result.supported_gap_count
        ),
        occluded_gap_count=(
            ptd_result.occluded_gap_count
        ),
        rejected_gap_count=(
            ptd_result.rejected_gap_count
        ),
        synthetic_fill_point_count=(
            model.synthetic_fill_point_count
        ),
        mean_confidence=mean_confidence,
        terrain3d_voxel_count=terrain3d_voxels,
        terrain3d_seed_voxel_count=terrain3d_seeds,
        l3_recovered_count=l3_recovered_count,
        l3_recovery_voxel_count=(
            model.l3_recovery.approved_voxel_count
            if model.l3_recovery is not None
            else 0
        ),
        ptd_vote_count=ptd_vote_count,
        smrf_vote_count=smrf_vote_count,
        csf_vote_count=csf_vote_count,
        consensus_2of3_count=consensus_2of3_count,
        source_type=inspection.source_type.value,
        source_confidence=inspection.confidence,
    )
