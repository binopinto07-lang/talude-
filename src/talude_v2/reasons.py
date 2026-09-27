from __future__ import annotations

from enum import StrEnum


class V2Reason(StrEnum):
    """Stable reason codes for Talude Studio V2 geometry processing."""

    SUCCESS = "SUCCESS"
    NO_GROUND = "NO_GROUND"
    LOW_GROUND_SUPPORT = "LOW_GROUND_SUPPORT"
    INVALID_TIN = "INVALID_TIN"
    NO_FACE = "NO_FACE"
    FACE_TOO_SMALL = "FACE_TOO_SMALL"
    FACE_TOO_SHORT = "FACE_TOO_SHORT"
    LOW_SLOPE = "LOW_SLOPE"
    LOW_CONTINUITY = "LOW_CONTINUITY"
    BOUNDARY_NOT_FOUND = "BOUNDARY_NOT_FOUND"
    CREST_NOT_FOUND = "CREST_NOT_FOUND"
    TOE_NOT_FOUND = "TOE_NOT_FOUND"
    LINE_TOO_SHORT = "LINE_TOO_SHORT"
    REFINEMENT_FAILED = "REFINEMENT_FAILED"
    MERGE_FAILED = "MERGE_FAILED"
    CANCELLED = "CANCELLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class V2DetectionError(ValueError):
    """Expected V2 rejection with a machine-readable reason code."""

    def __init__(self, reason: V2Reason, message: str):
        self.reason = reason
        self.message = message
        super().__init__(f"[{reason.value}] {message}")
