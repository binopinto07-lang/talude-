"""Talude Studio V2 experimental geometry engine.

This package is intentionally isolated from talude_v1. The 1.1.7 detector stays
untouched and serves as the baseline while V2 experiments with RAW point-cloud
TIN, graph continuity and local surface intersections.
"""

from .engine import V2Config, extract_face_raw_tin

__all__ = ["V2Config", "extract_face_raw_tin"]
