"""Sensor-aware terrain evidence for LAS-CAFIISICA."""

from .schema import (
    RichPointChunk,
    SourceInspection,
    SourceType,
)
from .source_inspector import inspect_source

__all__ = [
    "RichPointChunk",
    "SourceInspection",
    "SourceType",
    "inspect_source",
]
