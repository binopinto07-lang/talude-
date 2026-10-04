"""Isolated R20 engine: R19 dense evidence plus an inverted measured mantle."""
from __future__ import annotations

from typing import Callable

from ..cloud.model import CloudModel
from ..ground.types import GroundEngineParams
from ..terrain.dense_spatial_evidence import build_dense_spatial_evidence_grid
from ..terrain.inverted_mantle import build_inverted_ground_mantle
from ..terrain.schema import SourceType
from .l3_ground_lab import L3GroundLabResult, run_l3_ground_lab

ProgressCallback = Callable[[int, str], None]


def _dense_context(cloud, ptd_model, params, progress):
    return build_dense_spatial_evidence_grid(
        cloud, ptd_model,
        chunk_size=params.chunk_size,
        max_cells=3_000_000,
        neighbourhood_radius=1,
        progress=progress,
    )


def _inverted_mantle(context, progress):
    return build_inverted_ground_mantle(context, progress=progress)


def run_l3_inverted_ground(
    cloud: CloudModel,
    params: GroundEngineParams | None = None,
    progress: ProgressCallback | None = None,
    source_override: SourceType | str | None = None,
) -> L3GroundLabResult:
    """R20 is experimental and measured-only; R19.1 remains selectable."""
    return run_l3_ground_lab(
        cloud,
        params,
        progress,
        source_override=source_override,
        context_builder=_dense_context,
        mantle_builder=_inverted_mantle,
        engine_name="L3 Inverted Ground R20",
        revision_label="R20",
        collect_gate_diagnostics=True,
    )
