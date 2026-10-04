from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from .ground_evidence import GroundDecision, GroundEvidence, GroundEvidenceConfig


class GroundRejectReason(IntEnum):
    ACCEPTED = 0
    NO_SPATIAL_EVIDENCE = 1
    SURFACE_GATE_FAIL = 2
    SPATIAL_GATE_FAIL = 3
    VEGETATION_GATE = 4
    OBJECT_ROUGHNESS = 5
    NORMAL_MISMATCH = 6
    INVALID = 7
    SCORE_BELOW_MEDIUM = 8
    SCORE_BELOW_HIGH = 9
    OTHER_REJECTED = 10
    MANTLE_HEIGHT_VETO = 11
    ROOF_CANDIDATE_VETO = 12
    CANOPY_CANDIDATE_VETO = 13


@dataclass(frozen=True, slots=True)
class GroundGateDiagnostics:
    no_spatial_evidence: int
    surface_gate_fail: int
    spatial_gate_fail: int
    vegetation_gate: int
    object_roughness: int
    normal_mismatch: int
    invalid: int
    score_below_high: int
    score_below_medium: int
    rejected_total: int

    def __add__(self, other: "GroundGateDiagnostics") -> "GroundGateDiagnostics":
        return GroundGateDiagnostics(
            **{
                name: getattr(self, name) + getattr(other, name)
                for name in self.__dataclass_fields__
            }
        )

    @classmethod
    def zero(cls) -> "GroundGateDiagnostics":
        return cls(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


def diagnose_ground_gates(
    evidence: GroundEvidence,
    config: GroundEvidenceConfig,
    *,
    spatial_presence: np.ndarray | None = None,
) -> tuple[GroundGateDiagnostics, np.ndarray]:
    count = evidence.score.shape[0]
    score = np.asarray(evidence.score, dtype=np.float64)
    ptd = np.asarray(evidence.ptd_score, dtype=np.float64)
    tin = np.asarray(evidence.tin_residual, dtype=np.float64)
    neighbour = np.asarray(evidence.neighbour_support, dtype=np.float64)
    roughness = np.asarray(evidence.roughness, dtype=np.float64)
    decision = np.asarray(evidence.decision, dtype=np.uint8)

    if spatial_presence is None:
        presence = np.ones(count, dtype=np.bool_)
    else:
        presence = np.asarray(spatial_presence, dtype=np.bool_)
        if presence.shape[0] != count:
            raise ValueError("Ground diagnostics spatial presence length mismatch")

    accepted = (
        (decision == int(GroundDecision.L3_GROUND_ORIGINAL_VALIDATED))
        | (decision == int(GroundDecision.L3_GROUND_RECOVERED_HIGH))
        | (decision == int(GroundDecision.L3_GROUND_RECOVERED_MEDIUM))
        | (decision == int(GroundDecision.L3_GROUND_MANTLE_RECOVERED))
        | (decision == int(GroundDecision.L3_GROUND_CONTINUITY_RECOVERED))
    )
    rejected = ~accepted
    invalid = decision == int(GroundDecision.NOISE)
    vegetation = decision == int(GroundDecision.NON_GROUND_VEGETATION)
    no_spatial = ~presence
    surface_fail = (
        (~np.isfinite(tin))
        | ((tin > config.surface_scale * 1.70) & (ptd < 0.88))
    )
    spatial_fail = (neighbour < 0.18) & (ptd < 0.84)
    object_roughness = (
        (roughness > config.roughness_scale * 1.80)
        & (ptd < 0.82)
    )

    alignment = evidence.normal_alignment
    if alignment is None:
        normal_mismatch = np.zeros(count, dtype=np.bool_)
    else:
        alignment = np.asarray(alignment, dtype=np.float64)
        normal_mismatch = np.isfinite(alignment) & (alignment < 0.45)

    veto = getattr(evidence, "mantle_veto_code", None)
    if veto is None:
        veto = np.zeros(count, dtype=np.uint8)
    else:
        veto = np.asarray(veto, dtype=np.uint8)
        if veto.shape[0] != count:
            raise ValueError("R20.1 veto length mismatch")

    below_high = score < config.high_threshold
    below_medium = score < config.medium_threshold

    diagnostics = GroundGateDiagnostics(
        no_spatial_evidence=int(np.count_nonzero(no_spatial)),
        surface_gate_fail=int(np.count_nonzero(surface_fail)),
        spatial_gate_fail=int(np.count_nonzero(spatial_fail)),
        vegetation_gate=int(np.count_nonzero(vegetation)),
        object_roughness=int(np.count_nonzero(object_roughness)),
        normal_mismatch=int(np.count_nonzero(normal_mismatch)),
        invalid=int(np.count_nonzero(invalid)),
        score_below_high=int(np.count_nonzero(below_high)),
        score_below_medium=int(np.count_nonzero(below_medium)),
        rejected_total=int(np.count_nonzero(rejected)),
    )

    reason = np.full(
        count,
        int(GroundRejectReason.ACCEPTED),
        dtype=np.uint8,
    )
    unresolved = rejected.copy()
    for mask, code in (
        (invalid, GroundRejectReason.INVALID),
        (veto == 2, GroundRejectReason.ROOF_CANDIDATE_VETO),
        (veto == 3, GroundRejectReason.CANOPY_CANDIDATE_VETO),
        (veto == 1, GroundRejectReason.MANTLE_HEIGHT_VETO),
        (vegetation, GroundRejectReason.VEGETATION_GATE),
        (no_spatial, GroundRejectReason.NO_SPATIAL_EVIDENCE),
        (surface_fail, GroundRejectReason.SURFACE_GATE_FAIL),
        (spatial_fail, GroundRejectReason.SPATIAL_GATE_FAIL),
        (object_roughness, GroundRejectReason.OBJECT_ROUGHNESS),
        (normal_mismatch, GroundRejectReason.NORMAL_MISMATCH),
        (below_medium, GroundRejectReason.SCORE_BELOW_MEDIUM),
        (below_high, GroundRejectReason.SCORE_BELOW_HIGH),
    ):
        take = unresolved & mask
        reason[take] = int(code)
        unresolved[take] = False

    reason[unresolved] = int(GroundRejectReason.OTHER_REJECTED)
    return diagnostics, reason
