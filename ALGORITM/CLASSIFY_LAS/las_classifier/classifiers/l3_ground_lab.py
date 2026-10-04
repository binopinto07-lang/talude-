from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Callable

import numpy as np

from ..cloud.model import CloudModel
from ..ground.local_geometry import normal_alignment, point_normals
from ..ground.types import GroundEngineParams
from ..terrain.ground_debug import (
    GroundGateDiagnostics,
    GroundRejectReason,
    diagnose_ground_gates,
)
from ..terrain.ground_evidence import (
    GroundDecision,
    GroundEvidence,
    GroundEvidenceConfig,
    GroundEvidenceScorer,
    PROV_INVERTED_MANTLE,
)
from ..terrain.mantle_veto import (
    VETO_HEIGHT, VETO_ROOF_CANDIDATE, VETO_CANOPY_CANDIDATE,
    apply_mantle_veto,
)
from ..terrain.ground_continuity import apply_continuity_recovery
from ..terrain.l3_context import (
    CoarseDetrendModel,
    L3SpatialContext,
    build_l3_spatial_context,
)
from ..terrain.schema import SourceInspection, SourceType
from ..terrain.source_inspector import inspect_source
from .adaptive_ptd import (
    GROUND_CLASS,
    AdaptivePTDModel,
    run_adaptive_ptd,
)
from .smrf import SMRFModel, SMRFParams, run_smrf


LOGGER = logging.getLogger(
    "las_cafiisica.classifiers.l3_ground_lab"
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


def _dimension(
    points,
    *names: str,
    dtype=None,
) -> np.ndarray | None:
    if points is None:
        return None
    available = set(
        points.point_format.dimension_names
    )
    for name in names:
        if name in available:
            values = np.asarray(points[name])
            if dtype is not None:
                values = values.astype(
                    dtype,
                    copy=False,
                )
            return values
    return None


def _l3_smrf_params(
    params: GroundEngineParams,
) -> SMRFParams:
    # SMRF remains auxiliary evidence/debug in R18. It is NOT a vote.
    return SMRFParams(
        cell=0.50,
        slope=0.20,
        window=14.0,
        threshold=0.32,
        scalar=1.20,
        fill_spacing=0.25,
        chunk_size=params.chunk_size,
        terrain3d_enabled=False,
    )


def _slope_difference(
    point_normal: np.ndarray | None,
    terrain_normal: np.ndarray,
) -> np.ndarray | None:
    if point_normal is None:
        return None
    point_normal = np.asarray(
        point_normal,
        dtype=np.float64,
    )
    terrain_normal = np.asarray(
        terrain_normal,
        dtype=np.float64,
    )
    valid = (
        np.all(
            np.isfinite(point_normal),
            axis=1,
        )
        & np.all(
            np.isfinite(terrain_normal),
            axis=1,
        )
    )
    result = np.full(
        point_normal.shape[0],
        np.nan,
        dtype=np.float64,
    )
    if not np.any(valid):
        return result

    pn = point_normal[valid]
    tn = terrain_normal[valid]
    point_slope = np.degrees(
        np.arctan2(
            np.linalg.norm(
                pn[:, :2],
                axis=1,
            ),
            np.maximum(
                np.abs(pn[:, 2]),
                1e-9,
            ),
        )
    )
    terrain_slope = np.degrees(
        np.arctan2(
            np.linalg.norm(
                tn[:, :2],
                axis=1,
            ),
            np.maximum(
                np.abs(tn[:, 2]),
                1e-9,
            ),
        )
    )
    result[valid] = np.abs(
        point_slope - terrain_slope
    )
    return result


@dataclass(slots=True)
class L3GroundLabModel:
    params: GroundEngineParams
    ptd: AdaptivePTDModel
    smrf: SMRFModel
    context: object
    coarse_detrend: CoarseDetrendModel | None
    scorer: GroundEvidenceScorer
    source_inspection: SourceInspection
    engine_name: str = "L3 Ground Lab"
    return_evidence_enabled: bool = True
    mantle: object | None = None
    continuity: object | None = None

    @property
    def synthetic_fill_point_count(
        self,
    ) -> int:
        return 0

    @property
    def effective_fill_spacing(
        self,
    ) -> float:
        return 0.0

    def _evaluate(
        self,
        points,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> GroundEvidence:
        ptd_score, metrics = (
            self.ptd._confidence(
                x,
                y,
                z,
                points=points,
            )
        )

        if self.coarse_detrend is not None:
            detrended = (
                self.coarse_detrend.residual_xyz(
                    x,
                    y,
                    z,
                )
            )
        else:
            detrended = np.asarray(
                metrics["vertical_residual"],
                dtype=np.float64,
            )

        if hasattr(
            self.context,
            "query_with_presence",
        ):
            (
                neighbour,
                spread,
                roughness,
                spatial_presence,
            ) = self.context.query_with_presence(
                x,
                y,
            )
        else:
            (
                neighbour,
                spread,
                roughness,
            ) = self.context.query(
                x,
                y,
            )
            spatial_presence = None

        normals = (
            point_normals(points)
            if points is not None
            else None
        )
        alignment = normal_alignment(
            normals,
            metrics["normal"],
        )
        slope_difference = (
            _slope_difference(
                normals,
                metrics["normal"],
            )
        )

        smrf_ground = (
            self.smrf.classify_xyz(
                x,
                y,
                z,
            )
            == GROUND_CLASS
        )

        original_class = _dimension(
            points,
            "classification",
            dtype=np.uint8,
        )
        return_number = _dimension(
            points,
            "return_number",
            dtype=np.uint8,
        )
        number_of_returns = _dimension(
            points,
            "number_of_returns",
            dtype=np.uint8,
        )
        intensity = _dimension(
            points,
            "intensity",
        )
        scan_angle = _dimension(
            points,
            "scan_angle",
            "scan_angle_rank",
        )
        gps_time = _dimension(
            points,
            "gps_time",
        )
        point_source_id = _dimension(
            points,
            "point_source_id",
        )
        withheld = _dimension(
            points,
            "withheld",
        )
        synthetic = _dimension(
            points,
            "synthetic",
        )

        invalid = (
            ~np.isfinite(x)
            | ~np.isfinite(y)
            | ~np.isfinite(z)
        )
        if withheld is not None:
            invalid |= np.asarray(
                withheld,
                dtype=np.bool_,
            )
        # R18 L3 is measured-ground only. Existing source synthetic points
        # cannot bootstrap measured Ground.
        if synthetic is not None:
            invalid |= np.asarray(
                synthetic,
                dtype=np.bool_,
            )

        if (
            self.context.intensity_profile
            is not None
        ):
            intensity_score = (
                self.context.intensity_profile.score(
                    intensity,
                    x.shape[0],
                )
            )
        else:
            intensity_score = np.full(
                x.shape[0],
                0.50,
                dtype=np.float64,
            )

        evidence = self.scorer.evaluate(
            ptd_score=ptd_score,
            tin_residual=(
                metrics["plane_distance"]
            ),
            vertical_residual=(
                metrics["vertical_residual"]
            ),
            detrended_residual=detrended,
            neighbour_support=neighbour,
            vertical_spread=spread,
            roughness=roughness,
            original_class=original_class,
            return_number=return_number,
            number_of_returns=number_of_returns,
            intensity=intensity,
            intensity_score=intensity_score,
            normal_alignment=alignment,
            slope_difference=slope_difference,
            smrf_evidence=smrf_ground,
            cloth_evidence=None,
            scan_angle=scan_angle,
            gps_time=gps_time,
            point_source_id=point_source_id,
            invalid_mask=invalid,
            source_noise_mask=None,
            spatial_presence=spatial_presence,
            use_return_evidence=self.return_evidence_enabled,
        )
        if self.mantle is not None:
            # This is measured-return recovery ONLY. Never relabel a withheld,
            # source-synthetic, or missing point as physically observed Ground.
            candidate = self.mantle.recovery_mask(x, y, z)
            rejected = (
                (evidence.decision != int(GroundDecision.L3_GROUND_ORIGINAL_VALIDATED))
                & (evidence.decision != int(GroundDecision.L3_GROUND_RECOVERED_HIGH))
                & (evidence.decision != int(GroundDecision.L3_GROUND_RECOVERED_MEDIUM))
            )
            recovered = candidate & rejected & ~invalid
            evidence.decision[recovered] = int(
                GroundDecision.L3_GROUND_MANTLE_RECOVERED
            )
            evidence.provenance[recovered] |= PROV_INVERTED_MANTLE

            guard = getattr(self.mantle, "veto_guard", None)
            if guard is not None:
                apply_mantle_veto(evidence, guard, x, y, z, invalid)
                if self.continuity is not None:
                    apply_continuity_recovery(
                        evidence, self.continuity, guard, x, y, z, invalid
                    )
        return evidence

    def rejection_reason_points(
        self,
        points,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        evidence = self._evaluate(
            points,
            x,
            y,
            z,
        )
        _, reason = diagnose_ground_gates(
            evidence,
            self.scorer.config,
            spatial_presence=(
                evidence.spatial_presence
            ),
        )
        return reason

    def evaluate_points(
        self,
        points,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> GroundEvidence:
        return self._evaluate(
            points,
            x,
            y,
            z,
        )

    def classify_points(
        self,
        points,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        return self._evaluate(
            points,
            x,
            y,
            z,
        ).classifications()

    def classify_xyz(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        return self._evaluate(
            None,
            x,
            y,
            z,
        ).classifications()

    def confidence_points(
        self,
        points,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        return self._evaluate(
            points,
            x,
            y,
            z,
        ).score

    def confidence_xyz(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> np.ndarray:
        return self._evaluate(
            None,
            x,
            y,
            z,
        ).score

    def iter_synthetic_fill_xyz(self):
        if False:
            yield None

    def iter_viewer_synthetic_fill_xyz(self):
        if False:
            yield None


@dataclass(frozen=True, slots=True)
class L3GroundLabResult:
    model: L3GroundLabModel
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
    original_validated_count: int
    recovered_high_count: int
    recovered_medium_count: int
    rejected_class2_count: int
    non_ground_vegetation_count: int
    non_ground_object_count: int
    unknown_count: int
    noise_count: int
    return_only_count: int
    return_last_multi_count: int
    return_first_multi_count: int
    return_intermediate_count: int
    return_invalid_count: int
    context_cell_count: int
    coarse_representative_count: int
    source_type: str
    source_confidence: float
    engine_name: str = "L3 Ground Lab"
    ground_only: bool = True
    no_spatial_evidence_count: int = 0
    surface_gate_fail_count: int = 0
    spatial_gate_fail_count: int = 0
    vegetation_gate_count: int = 0
    object_roughness_count: int = 0
    normal_mismatch_count: int = 0
    invalid_gate_count: int = 0
    score_below_high_count: int = 0
    score_below_medium_count: int = 0
    rejection_reason_counts: tuple[tuple[str, int], ...] = ()
    mantle_recovered_count: int = 0
    mantle_recovered_class2_count: int = 0
    mantle_observed_cells: int = 0
    mantle_reliable_cells: int = 0
    mantle_inferred_cells: int = 0
    mantle_ambiguous_cells: int = 0
    mantle_possible_unobserved_cells: int = 0
    mantle_height_veto_count: int = 0
    mantle_roof_veto_count: int = 0
    mantle_canopy_veto_count: int = 0
    mantle_roof_candidate_cells: int = 0
    mantle_canopy_candidate_cells: int = 0
    continuity_recovered_count: int = 0
    continuity_recovered_class2_count: int = 0
    continuity_anchor_cells: int = 0
    continuity_connected_cells: int = 0
    continuity_expanded_cells: int = 0
    continuity_blocked_cells: int = 0

    @property
    def point_count(self) -> int:
        return (
            self.ground_count
            + self.non_ground_count
        )

    @property
    def l3_recovered_count(
        self,
    ) -> int:
        return (
            self.recovered_high_count
            + self.recovered_medium_count
        )

    @property
    def l3_recovery_voxel_count(
        self,
    ) -> int:
        return self.context_cell_count


def _return_counts(
    points,
) -> tuple[
    int,
    int,
    int,
    int,
    int,
]:
    rn = _dimension(
        points,
        "return_number",
        dtype=np.int16,
    )
    nr = _dimension(
        points,
        "number_of_returns",
        dtype=np.int16,
    )
    if rn is None or nr is None:
        return (
            0,
            0,
            0,
            0,
            len(points),
        )
    valid = (
        (rn > 0)
        & (nr > 0)
        & (rn <= nr)
    )
    only = (
        valid
        & (rn == 1)
        & (nr == 1)
    )
    last_multi = (
        valid
        & (nr > 1)
        & (rn == nr)
    )
    first_multi = (
        valid
        & (nr > 1)
        & (rn == 1)
    )
    intermediate = (
        valid
        & (rn > 1)
        & (rn < nr)
    )
    invalid = ~valid
    return tuple(
        int(
            np.count_nonzero(mask)
        )
        for mask in (
            only,
            last_multi,
            first_multi,
            intermediate,
            invalid,
        )
    )


def run_l3_ground_lab(
    cloud: CloudModel,
    params: GroundEngineParams | None = None,
    progress: ProgressCallback | None = None,
    source_override: SourceType | str | None = None,
    *,
    context_builder: Callable | None = None,
    engine_name: str = "L3 Ground Lab",
    revision_label: str = "R18",
    collect_gate_diagnostics: bool = False,
    mantle_builder: Callable | None = None,
    continuity_builder: Callable | None = None,
    require_lidar: bool = True,
) -> L3GroundLabResult:
    started = perf_counter()
    requested = (
        params
        or GroundEngineParams()
    )
    # Keep the second evidence pass bounded on 100M+/300M+ clouds.
    params = replace(
        requested,
        chunk_size=min(
            int(requested.chunk_size),
            750_000,
        ),
        synthetic_spacing=0.0,
    )

    LOGGER.info(
        "GROUND_ENGINE_START"
    )
    LOGGER.info(
        "GROUND_ENGINE=L3_GROUND_EVIDENCE_%s",
        revision_label,
    )
    LOGGER.info(
        "%s_SYNTHETIC_POLICY=DISABLED",
        revision_label,
    )

    inspection = inspect_source(
        cloud,
        override=source_override,
    )
    if require_lidar and inspection.source_type is not SourceType.L3_LIDAR:
        raise RuntimeError(
            f"{revision_label} L3 Ground Lab aceita apenas "
            "nuvens L3/LiDAR nesta fase. "
            "Source Inspector: "
            f"{inspection.source_type.value}."
        )

    # Source identification is diagnostic. It does not choose the R20.4
    # algorithm. Return evidence is decided later from the complete dense grid
    # so point ordering cannot change the universal classification policy.
    return_evidence_enabled = False
    LOGGER.info(
        "%s_SOURCE_POLICY type=%s require_lidar=%s",
        revision_label,
        inspection.source_type.value,
        require_lidar,
    )

    def ptd_progress(
        percent: int,
        message: str,
    ) -> None:
        _emit(
            progress,
            int(percent * 0.38),
            message,
        )

    # Stable R17 PTD remains the geometric nucleus. R18 changes the final
    # per-point decision, not the proven TIN/Qhull machinery.
    ptd_result = run_adaptive_ptd(
        cloud,
        params,
        ptd_progress,
        count_full=False,
    )

    _emit(
        progress,
        39,
        f"{revision_label} Ground: coarse detrending",
    )
    coarse = CoarseDetrendModel.build(
        ptd_result.model.tin.vertices,
        spacing=(
            ptd_result.analysis.median_spacing
        ),
    )

    def smrf_progress(
        percent: int,
        message: str,
    ) -> None:
        _emit(
            progress,
            40 + int(
                percent * 0.06
            ),
            (
                "SMRF evidence: "
                f"{message}"
            ),
        )

    smrf_result = run_smrf(
        cloud,
        _l3_smrf_params(params),
        smrf_progress,
        count_full=False,
    )

    if context_builder is None:
        context = build_l3_spatial_context(
            cloud,
            ptd_result.model,
            sample_target=min(
                1_500_000,
                max(
                    250_000,
                    params.sample_target,
                ),
            ),
            progress=progress,
        )
    else:
        context = context_builder(
            cloud,
            ptd_result.model,
            params,
            progress,
        )

    # Dense R19+ context records every measured return. Use that complete,
    # order-independent population to decide whether return structure is useful.
    if all(
        hasattr(context, name)
        for name in (
            "return_last_multi_count",
            "return_first_multi_count",
            "return_intermediate_count",
            "measured_point_count",
        )
    ):
        multi_count = int(np.sum(context.return_last_multi_count, dtype=np.int64))
        multi_count += int(np.sum(context.return_first_multi_count, dtype=np.int64))
        multi_count += int(np.sum(context.return_intermediate_count, dtype=np.int64))
        measured_count = int(context.measured_point_count)
        return_evidence_enabled = (
            measured_count > 0
            and (multi_count / measured_count) >= 0.01
        )
    elif require_lidar:
        # Legacy L3 modes keep their previous diagnostic source behavior.
        return_evidence_enabled = bool(
            inspection.has_returns
            and inspection.max_number_of_returns > 1
            and inspection.multi_return_fraction >= 0.01
        )
    LOGGER.info(
        "%s_RETURN_EVIDENCE enabled=%s",
        revision_label,
        return_evidence_enabled,
    )

    spacing = max(
        float(
            ptd_result.analysis.median_spacing
        ),
        0.005,
    )
    evidence_config = GroundEvidenceConfig(
        surface_scale=max(
            0.18,
            min(
                0.38,
                (
                    ptd_result.model.distance_limit
                    * 1.20
                ),
            ),
        ),
        detrend_scale=max(
            0.35,
            min(
                1.25,
                spacing * 12.0,
            ),
        ),
        vertical_spread_limit=max(
            0.65,
            min(
                1.60,
                spacing * 18.0,
            ),
        ),
        roughness_scale=max(
            0.12,
            min(
                0.35,
                spacing * 5.0,
            ),
        ),
    )
    mantle = (
        mantle_builder(context, progress)
        if mantle_builder is not None
        else None
    )
    continuity = (
        continuity_builder(context, mantle, progress)
        if continuity_builder is not None
        else None
    )
    model = L3GroundLabModel(
        params=params,
        ptd=ptd_result.model,
        smrf=smrf_result.model,
        context=context,
        coarse_detrend=coarse,
        scorer=GroundEvidenceScorer(
            evidence_config
        ),
        source_inspection=inspection,
        engine_name=engine_name,
        return_evidence_enabled=return_evidence_enabled,
        mantle=mantle,
        continuity=continuity,
    )

    totals = {
        GroundDecision.L3_GROUND_ORIGINAL_VALIDATED: 0,
        GroundDecision.L3_GROUND_RECOVERED_HIGH: 0,
        GroundDecision.L3_GROUND_RECOVERED_MEDIUM: 0,
        GroundDecision.L3_GROUND_MANTLE_RECOVERED: 0,
        GroundDecision.L3_GROUND_CONTINUITY_RECOVERED: 0,
        GroundDecision.NON_GROUND_VEGETATION: 0,
        GroundDecision.NON_GROUND_OBJECT: 0,
        GroundDecision.NOISE: 0,
        GroundDecision.UNKNOWN: 0,
    }
    class2_input = 0
    mantle_recovered_class2 = 0
    continuity_recovered_class2 = 0
    confidence_sum = 0.0
    confidence_n = 0
    return_only = 0
    return_last_multi = 0
    return_first_multi = 0
    return_intermediate = 0
    return_invalid = 0
    gate_diagnostics = (
        GroundGateDiagnostics.zero()
    )
    reason_histogram = np.zeros(
        max(int(item) for item in GroundRejectReason) + 1,
        dtype=np.int64,
    )
    mantle_veto_totals = np.zeros(4, dtype=np.int64)

    scales = cloud.las.header.scales
    offsets = cloud.las.header.offsets
    total = cloud.point_count
    _emit(
        progress,
        58,
        (
            f"{revision_label} second pass: scoring "
            "every measured point"
        ),
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
        points = cloud.las.points[
            start:stop
        ]
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

        evidence = model.evaluate_points(
            points,
            x,
            y,
            z,
        )
        if collect_gate_diagnostics:
            (
                chunk_diagnostics,
                rejection_reason,
            ) = diagnose_ground_gates(
                evidence,
                evidence_config,
                spatial_presence=(
                    evidence.spatial_presence
                ),
            )
            evidence.rejection_reason = (
                rejection_reason
            )
            gate_diagnostics = (
                gate_diagnostics
                + chunk_diagnostics
            )
            reason_histogram += np.bincount(
                rejection_reason,
                minlength=reason_histogram.size,
            ).astype(np.int64, copy=False)

        if evidence.mantle_veto_code is not None:
            mantle_veto_totals += np.bincount(
                evidence.mantle_veto_code, minlength=4
            ).astype(np.int64, copy=False)
        for decision in totals:
            totals[decision] += int(
                np.count_nonzero(
                    evidence.decision
                    == int(decision)
                )
            )

        original_class = (
            evidence.original_class
        )
        if original_class is not None:
            class2_input += int(
                np.count_nonzero(
                    original_class == 2
                )
            )
            mantle_recovered_class2 += int(
                np.count_nonzero(
                    (original_class == 2)
                    & (evidence.decision == int(GroundDecision.L3_GROUND_MANTLE_RECOVERED))
                )
            )
            continuity_recovered_class2 += int(
                np.count_nonzero(
                    (original_class == 2)
                    & (evidence.decision == int(GroundDecision.L3_GROUND_CONTINUITY_RECOVERED))
                )
            )

        confidence_sum += float(
            np.sum(
                evidence.score,
                dtype=np.float64,
            )
        )
        confidence_n += int(
            evidence.score.size
        )

        (
            only,
            last_multi,
            first_multi,
            intermediate,
            invalid,
        ) = _return_counts(points)
        return_only += only
        return_last_multi += last_multi
        return_first_multi += first_multi
        return_intermediate += intermediate
        return_invalid += invalid

        _emit(
            progress,
            58
            + int(
                41
                * stop
                / max(1, total)
            ),
            (
                f"{revision_label} GroundScore "
                f"{stop:,}/{total:,}"
            ),
        )

    original_validated = totals[
        GroundDecision.L3_GROUND_ORIGINAL_VALIDATED
    ]
    recovered_high = totals[
        GroundDecision.L3_GROUND_RECOVERED_HIGH
    ]
    recovered_medium = totals[
        GroundDecision.L3_GROUND_RECOVERED_MEDIUM
    ]
    mantle_recovered = totals[
        GroundDecision.L3_GROUND_MANTLE_RECOVERED
    ]
    continuity_recovered = totals[
        GroundDecision.L3_GROUND_CONTINUITY_RECOVERED
    ]
    ground_count = (
        original_validated
        + recovered_high
        + recovered_medium
        + mantle_recovered
        + continuity_recovered
    )
    non_ground_count = (
        total - ground_count
    )
    mean_confidence = (
        confidence_sum
        / confidence_n
        if confidence_n
        else 0.0
    )
    elapsed = (
        perf_counter() - started
    )

    LOGGER.info(
        "L3_GROUND_EVIDENCE "
        "original_validated=%d "
        "recovered_high=%d "
        "recovered_medium=%d "
        "rejected_class2=%d",
        original_validated,
        recovered_high,
        recovered_medium,
        max(
            0,
            class2_input
            - original_validated
            - mantle_recovered_class2
            - continuity_recovered_class2,
        ),
    )
    LOGGER.info(
        "L3_REJECTED vegetation=%d "
        "object=%d unknown=%d noise=%d",
        totals[
            GroundDecision.NON_GROUND_VEGETATION
        ],
        totals[
            GroundDecision.NON_GROUND_OBJECT
        ],
        totals[
            GroundDecision.UNKNOWN
        ],
        totals[
            GroundDecision.NOISE
        ],
    )
    LOGGER.info(
        "L3_RETURNS only=%d "
        "last_multi=%d first_multi=%d "
        "intermediate=%d invalid=%d",
        return_only,
        return_last_multi,
        return_first_multi,
        return_intermediate,
        return_invalid,
    )
    context_cells = int(
        getattr(
            context,
            "occupied_cell_count",
            0,
        )
    )
    if (
        context_cells <= 0
        and hasattr(context, "keys")
    ):
        context_cells = int(
            context.keys.size
        )
    LOGGER.info(
        "L3_CONTEXT cells=%d "
        "coarse_representatives=%d",
        context_cells,
        (
            coarse.representative_count
            if coarse is not None
            else 0
        ),
    )
    if collect_gate_diagnostics:
        diagnostic_revision = revision_label if mantle is not None else "R19"
        exclusive_rejected = int(np.sum(reason_histogram[1:], dtype=np.int64))
        if exclusive_rejected != non_ground_count:
            raise RuntimeError(
                f"{diagnostic_revision} rejection histogram mismatch: "
                f"{exclusive_rejected} != {non_ground_count}"
            )
        LOGGER.info(
            "%s_REJECT_REASONS %s",
            diagnostic_revision,
            " ".join(
                f"{item.name.lower()}={reason_histogram[int(item)]:,}"
                for item in GroundRejectReason
                if item is not GroundRejectReason.ACCEPTED
            ),
        )
        LOGGER.info(
            "%s_GATES no_spatial=%d "
            "surface_fail=%d spatial_fail=%d "
            "vegetation=%d object_roughness=%d "
            "normal_mismatch=%d invalid=%d "
            "below_high=%d below_medium=%d",
            diagnostic_revision,
            gate_diagnostics.no_spatial_evidence,
            gate_diagnostics.surface_gate_fail,
            gate_diagnostics.spatial_gate_fail,
            gate_diagnostics.vegetation_gate,
            gate_diagnostics.object_roughness,
            gate_diagnostics.normal_mismatch,
            gate_diagnostics.invalid,
            gate_diagnostics.score_below_high,
            gate_diagnostics.score_below_medium,
        )
    if getattr(mantle, "veto_guard", None) is not None:
        LOGGER.info(
            "R20_1_VETO height=%d roof_candidate=%d canopy_candidate=%d "
            "roof_cells=%d canopy_cells=%d",
            int(mantle_veto_totals[int(VETO_HEIGHT)]),
            int(mantle_veto_totals[int(VETO_ROOF_CANDIDATE)]),
            int(mantle_veto_totals[int(VETO_CANOPY_CANDIDATE)]),
            mantle.veto_guard.roof_candidate_cell_count,
            mantle.veto_guard.canopy_candidate_cell_count,
        )
    if continuity is not None:
        LOGGER.info(
            "R20_2_RECOVERY measured_points=%d source_class2=%d "
            "anchors=%d connected_cells=%d expanded_cells=%d "
            "veto_blocked_cells=%d synthetic=0",
            continuity_recovered,
            continuity_recovered_class2,
            continuity.anchor_cell_count,
            continuity.connected_cell_count,
            continuity.expanded_cell_count,
            continuity.blocked_cell_count,
        )
    LOGGER.info(
        "SYNTHETIC_POINTS=0"
    )
    LOGGER.info(
        "GROUND_CONFIDENCE_MEAN=%.4f",
        mean_confidence,
    )
    LOGGER.info(
        "PROCESSING_TIME=%.3f",
        elapsed,
    )

    _emit(
        progress,
        100,
        f"{revision_label} Ground Evidence complete",
    )
    return L3GroundLabResult(
        model=model,
        ground_count=ground_count,
        non_ground_count=(
            non_ground_count
        ),
        elapsed_seconds=elapsed,
        analysis=ptd_result.analysis,
        seed_count=(
            ptd_result.seed_count
        ),
        candidate_count=(
            ptd_result.candidate_count
        ),
        ptd_iterations=(
            ptd_result.ptd_iterations
        ),
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
        synthetic_fill_point_count=0,
        mean_confidence=(
            mean_confidence
        ),
        original_validated_count=(
            original_validated
        ),
        recovered_high_count=(
            recovered_high
        ),
        recovered_medium_count=(
            recovered_medium
        ),
        rejected_class2_count=max(
            0,
            class2_input
            - original_validated
            - mantle_recovered_class2
            - continuity_recovered_class2,
        ),
        non_ground_vegetation_count=(
            totals[
                GroundDecision.NON_GROUND_VEGETATION
            ]
        ),
        non_ground_object_count=(
            totals[
                GroundDecision.NON_GROUND_OBJECT
            ]
        ),
        unknown_count=(
            totals[
                GroundDecision.UNKNOWN
            ]
        ),
        noise_count=(
            totals[
                GroundDecision.NOISE
            ]
        ),
        return_only_count=(
            return_only
        ),
        return_last_multi_count=(
            return_last_multi
        ),
        return_first_multi_count=(
            return_first_multi
        ),
        return_intermediate_count=(
            return_intermediate
        ),
        return_invalid_count=(
            return_invalid
        ),
        context_cell_count=(
            context_cells
        ),
        coarse_representative_count=(
            coarse.representative_count
            if coarse is not None
            else 0
        ),
        source_type=(
            inspection.source_type.value
        ),
        source_confidence=(
            inspection.confidence
        ),
        engine_name=engine_name,
        no_spatial_evidence_count=(
            gate_diagnostics.no_spatial_evidence
        ),
        surface_gate_fail_count=(
            gate_diagnostics.surface_gate_fail
        ),
        spatial_gate_fail_count=(
            gate_diagnostics.spatial_gate_fail
        ),
        vegetation_gate_count=(
            gate_diagnostics.vegetation_gate
        ),
        object_roughness_count=(
            gate_diagnostics.object_roughness
        ),
        normal_mismatch_count=(
            gate_diagnostics.normal_mismatch
        ),
        invalid_gate_count=(
            gate_diagnostics.invalid
        ),
        score_below_high_count=(
            gate_diagnostics.score_below_high
        ),
        score_below_medium_count=(
            gate_diagnostics.score_below_medium
        ),
        mantle_recovered_count=mantle_recovered,
        mantle_recovered_class2_count=mantle_recovered_class2,
        mantle_observed_cells=mantle.observed_cell_count if mantle is not None else 0,
        mantle_reliable_cells=mantle.reliable_cell_count if mantle is not None else 0,
        mantle_inferred_cells=mantle.inferred_cell_count if mantle is not None else 0,
        mantle_ambiguous_cells=mantle.ambiguous_cell_count if mantle is not None else 0,
        mantle_possible_unobserved_cells=(
            mantle.possible_no_ground_observation_count if mantle is not None else 0
        ),
        mantle_height_veto_count=int(mantle_veto_totals[int(VETO_HEIGHT)]),
        mantle_roof_veto_count=int(mantle_veto_totals[int(VETO_ROOF_CANDIDATE)]),
        mantle_canopy_veto_count=int(mantle_veto_totals[int(VETO_CANOPY_CANDIDATE)]),
        mantle_roof_candidate_cells=(
            mantle.veto_guard.roof_candidate_cell_count
            if getattr(mantle, "veto_guard", None) is not None else 0
        ),
        mantle_canopy_candidate_cells=(
            mantle.veto_guard.canopy_candidate_cell_count
            if getattr(mantle, "veto_guard", None) is not None else 0
        ),
        continuity_recovered_count=continuity_recovered,
        continuity_recovered_class2_count=continuity_recovered_class2,
        continuity_anchor_cells=continuity.anchor_cell_count if continuity is not None else 0,
        continuity_connected_cells=continuity.connected_cell_count if continuity is not None else 0,
        continuity_expanded_cells=continuity.expanded_cell_count if continuity is not None else 0,
        continuity_blocked_cells=continuity.blocked_cell_count if continuity is not None else 0,
        rejection_reason_counts=(
            tuple(
                (item.name.lower(), int(reason_histogram[int(item)]))
                for item in GroundRejectReason
                if item is not GroundRejectReason.ACCEPTED
            )
            if collect_gate_diagnostics
            else ()
        ),
    )
