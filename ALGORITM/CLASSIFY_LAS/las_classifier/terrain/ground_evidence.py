from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np


GROUND_CLASS = np.uint8(2)
NON_GROUND_CLASS = np.uint8(1)


class GroundDecision(IntEnum):
    UNKNOWN = 0
    L3_GROUND_ORIGINAL_VALIDATED = 1
    L3_GROUND_RECOVERED_HIGH = 2
    L3_GROUND_RECOVERED_MEDIUM = 3
    NON_GROUND_VEGETATION = 4
    NON_GROUND_OBJECT = 5
    NOISE = 6
    L3_GROUND_MANTLE_RECOVERED = 7
    L3_GROUND_CONTINUITY_RECOVERED = 8


PROV_PTD_SURFACE = np.uint16(1 << 0)
PROV_RETURN = np.uint16(1 << 1)
PROV_NEIGHBOUR = np.uint16(1 << 2)
PROV_NORMAL = np.uint16(1 << 3)
PROV_CLASS2_PRIOR = np.uint16(1 << 4)
PROV_SMRF_AUX = np.uint16(1 << 5)
PROV_DETRENDED = np.uint16(1 << 6)
PROV_INTENSITY = np.uint16(1 << 7)
PROV_CLOTH_AUX = np.uint16(1 << 8)
PROV_INVERTED_MANTLE = np.uint16(1 << 9)
PROV_MANTLE_VETO = np.uint16(1 << 10)
PROV_GROUND_CONTINUITY = np.uint16(1 << 11)


@dataclass(frozen=True, slots=True)
class GroundEvidenceWeights:
    surface: float = 0.28
    neighbour: float = 0.20
    detrended_low: float = 0.16
    return_evidence: float = 0.14
    normal: float = 0.10
    slope: float = 0.07
    original_class_prior: float = 0.03
    intensity: float = 0.02

    def validate(self) -> None:
        total = (
            self.surface
            + self.neighbour
            + self.detrended_low
            + self.return_evidence
            + self.normal
            + self.slope
            + self.original_class_prior
            + self.intensity
        )
        if not np.isclose(total, 1.0, atol=1e-9):
            raise ValueError(
                f"GroundEvidence weights must sum to 1.0, got {total:.6f}"
            )


@dataclass(frozen=True, slots=True)
class GroundEvidenceConfig:
    surface_scale: float
    detrend_scale: float
    vertical_spread_limit: float
    roughness_scale: float
    high_threshold: float = 0.72
    medium_threshold: float = 0.64

    def validate(self) -> None:
        for name, value in (
            ("surface_scale", self.surface_scale),
            ("detrend_scale", self.detrend_scale),
            ("vertical_spread_limit", self.vertical_spread_limit),
            ("roughness_scale", self.roughness_scale),
        ):
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be > 0")
        if not 0 < self.medium_threshold < self.high_threshold < 1:
            raise ValueError("GroundEvidence thresholds must satisfy 0 < medium < high < 1")


@dataclass(slots=True)
class GroundEvidence:
    original_class: np.ndarray | None
    return_number: np.ndarray | None
    number_of_returns: np.ndarray | None
    intensity: np.ndarray | None
    scan_angle: np.ndarray | None
    gps_time: np.ndarray | None
    point_source_id: np.ndarray | None
    ptd_score: np.ndarray
    tin_residual: np.ndarray
    vertical_residual: np.ndarray
    detrended_residual: np.ndarray
    normal_alignment: np.ndarray | None
    slope_difference: np.ndarray | None
    neighbour_support: np.ndarray
    vertical_spread: np.ndarray
    roughness: np.ndarray
    return_evidence: np.ndarray
    smrf_evidence: np.ndarray | None
    cloth_evidence: np.ndarray | None
    score: np.ndarray
    decision: np.ndarray
    provenance: np.ndarray
    spatial_presence: np.ndarray | None = None
    rejection_reason: np.ndarray | None = None
    mantle_veto_code: np.ndarray | None = None
    continuity_recovered: np.ndarray | None = None

    def classifications(self) -> np.ndarray:
        classes = np.full(
            self.score.shape[0],
            NON_GROUND_CLASS,
            dtype=np.uint8,
        )
        ground = (
            (self.decision == GroundDecision.L3_GROUND_ORIGINAL_VALIDATED)
            | (self.decision == GroundDecision.L3_GROUND_RECOVERED_HIGH)
            | (self.decision == GroundDecision.L3_GROUND_RECOVERED_MEDIUM)
            | (self.decision == GroundDecision.L3_GROUND_MANTLE_RECOVERED)
            | (self.decision == GroundDecision.L3_GROUND_CONTINUITY_RECOVERED)
        )
        classes[ground] = GROUND_CLASS
        return classes

    def ground_source_codes(self) -> np.ndarray:
        """Return per-point measured-ground provenance for Ground Only export.

        1 = source class 2 validated by R18 evidence
        3 = measured non-class2 point recovered with high evidence
        4 = measured non-class2 point recovered with medium evidence
        0 = non-ground / unknown
        2 remains reserved for reconstructed synthetic ground.
        """
        codes = np.zeros(self.score.shape[0], dtype=np.uint8)
        codes[
            self.decision == GroundDecision.L3_GROUND_ORIGINAL_VALIDATED
        ] = 1
        codes[
            self.decision == GroundDecision.L3_GROUND_RECOVERED_HIGH
        ] = 3
        codes[
            self.decision == GroundDecision.L3_GROUND_RECOVERED_MEDIUM
        ] = 4
        codes[
            self.decision == GroundDecision.L3_GROUND_MANTLE_RECOVERED
        ] = 5  # R20: physically measured return recovered by mantle geometry
        codes[
            self.decision == GroundDecision.L3_GROUND_CONTINUITY_RECOVERED
        ] = 6  # R20.2 measured-only 3D continuity; never synthetic
        return codes


class GroundEvidenceScorer:
    def __init__(
        self,
        config: GroundEvidenceConfig,
        weights: GroundEvidenceWeights | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.weights = weights or GroundEvidenceWeights()
        self.weights.validate()

    @staticmethod
    def _optional_float(
        values: np.ndarray | None,
        count: int,
        default: float,
    ) -> np.ndarray:
        if values is None:
            return np.full(count, default, dtype=np.float64)
        result = np.asarray(values, dtype=np.float64)
        if result.shape[0] != count:
            raise ValueError("GroundEvidence input length mismatch")
        return result

    @staticmethod
    def _return_score(
        return_number: np.ndarray | None,
        number_of_returns: np.ndarray | None,
        count: int,
        *,
        enabled: bool = True,
    ) -> np.ndarray:
        # Return structure is optional evidence, never a requirement.  In
        # photogrammetric/single-return clouds 1/1 is acquisition metadata, not
        # proof that a pulse penetrated vegetation to terrain, so keep it neutral.
        if not enabled or return_number is None or number_of_returns is None:
            return np.full(count, 0.50, dtype=np.float64)

        rn = np.asarray(return_number, dtype=np.int16)
        nr = np.asarray(number_of_returns, dtype=np.int16)
        result = np.zeros(count, dtype=np.float64)
        valid = (rn > 0) & (nr > 0) & (rn <= nr)
        last_or_only = valid & (rn == nr)
        first_multi = valid & (rn == 1) & (nr > 1)
        intermediate = valid & (rn > 1) & (rn < nr)
        result[last_or_only] = 1.0
        result[intermediate] = 0.65
        result[first_multi] = 0.35
        return result

    def evaluate(
        self,
        *,
        ptd_score: np.ndarray,
        tin_residual: np.ndarray,
        vertical_residual: np.ndarray,
        detrended_residual: np.ndarray,
        neighbour_support: np.ndarray,
        vertical_spread: np.ndarray,
        roughness: np.ndarray,
        original_class: np.ndarray | None = None,
        return_number: np.ndarray | None = None,
        number_of_returns: np.ndarray | None = None,
        intensity: np.ndarray | None = None,
        intensity_score: np.ndarray | None = None,
        normal_alignment: np.ndarray | None = None,
        slope_difference: np.ndarray | None = None,
        smrf_evidence: np.ndarray | None = None,
        cloth_evidence: np.ndarray | None = None,
        scan_angle: np.ndarray | None = None,
        gps_time: np.ndarray | None = None,
        point_source_id: np.ndarray | None = None,
        invalid_mask: np.ndarray | None = None,
        source_noise_mask: np.ndarray | None = None,
        spatial_presence: np.ndarray | None = None,
        use_return_evidence: bool = True,
    ) -> GroundEvidence:
        ptd = np.asarray(ptd_score, dtype=np.float64)
        count = ptd.shape[0]
        tin = np.asarray(tin_residual, dtype=np.float64)
        vertical = np.asarray(vertical_residual, dtype=np.float64)
        detrended = np.asarray(detrended_residual, dtype=np.float64)
        neighbour = np.clip(
            np.asarray(neighbour_support, dtype=np.float64),
            0.0,
            1.0,
        )
        spread = np.asarray(vertical_spread, dtype=np.float64)
        local_roughness = np.asarray(roughness, dtype=np.float64)
        for values in (
            tin,
            vertical,
            detrended,
            neighbour,
            spread,
            local_roughness,
        ):
            if values.shape[0] != count:
                raise ValueError("GroundEvidence input length mismatch")

        cfg = self.config
        w = self.weights

        finite_tin = np.isfinite(tin)
        safe_tin = np.where(
            finite_tin,
            np.maximum(tin, 0.0),
            np.inf,
        )
        surface_score = np.exp(
            -0.5 * np.square(safe_tin / cfg.surface_scale)
        )
        surface_score[~finite_tin] = 0.0

        finite_detrend = np.isfinite(detrended)
        detrended_score = np.zeros(count, dtype=np.float64)
        detrended_score[finite_detrend] = np.exp(
            -np.abs(detrended[finite_detrend]) / cfg.detrend_scale
        )

        return_score = self._return_score(
            return_number,
            number_of_returns,
            count,
            enabled=use_return_evidence,
        )

        alignment = self._optional_float(
            normal_alignment,
            count,
            np.nan,
        )
        normal_known = np.isfinite(alignment)
        normal_score = np.full(count, 0.50, dtype=np.float64)
        normal_score[normal_known] = np.clip(
            alignment[normal_known],
            0.0,
            1.0,
        )

        slope_diff = self._optional_float(
            slope_difference,
            count,
            np.nan,
        )
        slope_known = np.isfinite(slope_diff)
        slope_score = np.full(count, 0.50, dtype=np.float64)
        slope_score[slope_known] = 1.0 - np.clip(
            np.abs(slope_diff[slope_known]) / 50.0,
            0.0,
            1.0,
        )

        class2 = np.zeros(count, dtype=np.bool_)
        if original_class is not None:
            classes = np.asarray(original_class)
            if classes.shape[0] != count:
                raise ValueError(
                    "GroundEvidence classification length mismatch"
                )
            class2 = classes == 2
        class_score = class2.astype(np.float64)

        intensity_component = self._optional_float(
            intensity_score,
            count,
            0.50,
        )
        intensity_component = np.clip(
            intensity_component,
            0.0,
            1.0,
        )

        score = (
            w.surface * surface_score
            + w.neighbour * neighbour
            + w.detrended_low * detrended_score
            + w.return_evidence * return_score
            + w.normal * normal_score
            + w.slope * slope_score
            + w.original_class_prior * class_score
            + w.intensity * intensity_component
        )

        finite_vertical = np.isfinite(vertical)
        vegetation = (
            finite_vertical
            & (
                vertical
                > max(
                    0.18,
                    cfg.surface_scale * 0.70,
                )
            )
            & (spread > cfg.vertical_spread_limit)
            & (
                (neighbour < 0.65)
                | (
                    local_roughness
                    > cfg.roughness_scale
                )
            )
        )
        isolated = (
            (neighbour < 0.08)
            & (ptd < 0.88)
        )
        normal_mismatch = (
            normal_known
            & (alignment < 0.45)
        )
        excess_residual = (
            safe_tin
            > (cfg.surface_scale * 1.90)
        )
        rough_object = (
            (
                local_roughness
                > cfg.roughness_scale * 1.80
            )
            & (ptd < 0.82)
        )

        score[vegetation] -= 0.20
        score[isolated] -= 0.20
        score[normal_mismatch] -= 0.15
        score[excess_residual] -= 0.25
        score[rough_object] -= 0.10
        score = np.clip(score, 0.0, 1.0)

        hard_invalid = np.zeros(
            count,
            dtype=np.bool_,
        )
        if invalid_mask is not None:
            hard_invalid |= np.asarray(
                invalid_mask,
                dtype=np.bool_,
            )
        if source_noise_mask is not None:
            hard_invalid |= np.asarray(
                source_noise_mask,
                dtype=np.bool_,
            )

        surface_gate = (
            (
                safe_tin
                <= cfg.surface_scale * 1.70
            )
            | (ptd >= 0.88)
        )
        spatial_gate = (
            (neighbour >= 0.10)
            | (ptd >= 0.90)
        )
        high_ground = (
            (score >= cfg.high_threshold)
            & surface_gate
            & spatial_gate
            & ~vegetation
            & ~excess_residual
            & ~hard_invalid
        )
        medium_ground = (
            ~high_ground
            & (score >= cfg.medium_threshold)
            & surface_gate
            & (
                (neighbour >= 0.18)
                | (ptd >= 0.84)
            )
            & ~vegetation
            & ~excess_residual
            & ~hard_invalid
        )
        accepted = high_ground | medium_ground

        decision = np.full(
            count,
            int(GroundDecision.UNKNOWN),
            dtype=np.uint8,
        )
        decision[
            vegetation & ~hard_invalid
        ] = int(
            GroundDecision.NON_GROUND_VEGETATION
        )
        object_mask = (
            (excess_residual | rough_object)
            & ~vegetation
            & ~hard_invalid
        )
        decision[object_mask] = int(
            GroundDecision.NON_GROUND_OBJECT
        )
        decision[hard_invalid] = int(
            GroundDecision.NOISE
        )

        original_validated = accepted & class2
        recovered = accepted & ~class2
        decision[original_validated] = int(
            GroundDecision.L3_GROUND_ORIGINAL_VALIDATED
        )
        decision[
            recovered & high_ground
        ] = int(
            GroundDecision.L3_GROUND_RECOVERED_HIGH
        )
        decision[
            recovered & medium_ground
        ] = int(
            GroundDecision.L3_GROUND_RECOVERED_MEDIUM
        )

        provenance = np.zeros(
            count,
            dtype=np.uint16,
        )
        provenance[
            ptd >= 0.62
        ] |= PROV_PTD_SURFACE
        provenance[
            return_score >= 0.99
        ] |= PROV_RETURN
        provenance[
            neighbour >= 0.50
        ] |= PROV_NEIGHBOUR
        provenance[
            normal_known
            & (alignment >= 0.70)
        ] |= PROV_NORMAL
        provenance[
            class2
        ] |= PROV_CLASS2_PRIOR
        provenance[
            finite_detrend
            & (
                np.abs(detrended)
                <= cfg.detrend_scale
            )
        ] |= PROV_DETRENDED
        provenance[
            intensity_component >= 0.60
        ] |= PROV_INTENSITY
        if smrf_evidence is not None:
            smrf = np.asarray(
                smrf_evidence,
                dtype=np.bool_,
            )
            provenance[smrf] |= PROV_SMRF_AUX
        if cloth_evidence is not None:
            cloth = np.asarray(
                cloth_evidence,
                dtype=np.float64,
            )
            provenance[
                np.isfinite(cloth)
                & (cloth >= 0.60)
            ] |= PROV_CLOTH_AUX

        return GroundEvidence(
            original_class=original_class,
            return_number=return_number,
            number_of_returns=number_of_returns,
            intensity=intensity,
            scan_angle=scan_angle,
            gps_time=gps_time,
            point_source_id=point_source_id,
            ptd_score=ptd.astype(
                np.float32,
                copy=False,
            ),
            tin_residual=tin.astype(
                np.float32,
                copy=False,
            ),
            vertical_residual=vertical.astype(
                np.float32,
                copy=False,
            ),
            detrended_residual=detrended.astype(
                np.float32,
                copy=False,
            ),
            normal_alignment=(
                alignment.astype(
                    np.float32,
                    copy=False,
                )
                if normal_alignment is not None
                else None
            ),
            slope_difference=(
                slope_diff.astype(
                    np.float32,
                    copy=False,
                )
                if slope_difference is not None
                else None
            ),
            neighbour_support=neighbour.astype(
                np.float32,
                copy=False,
            ),
            vertical_spread=spread.astype(
                np.float32,
                copy=False,
            ),
            roughness=local_roughness.astype(
                np.float32,
                copy=False,
            ),
            return_evidence=return_score.astype(
                np.float32,
                copy=False,
            ),
            smrf_evidence=smrf_evidence,
            cloth_evidence=cloth_evidence,
            score=score.astype(
                np.float32,
                copy=False,
            ),
            decision=decision,
            provenance=provenance,
            spatial_presence=(
                np.asarray(
                    spatial_presence,
                    dtype=np.bool_,
                )
                if spatial_presence is not None
                else None
            ),
        )
