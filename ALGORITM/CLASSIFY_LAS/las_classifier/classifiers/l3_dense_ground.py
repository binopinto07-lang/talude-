from __future__ import annotations

from typing import Callable

from ..cloud.model import CloudModel
from ..ground.types import GroundEngineParams
from ..terrain.dense_spatial_evidence import (
    build_dense_spatial_evidence_grid,
)
from ..terrain.schema import SourceType
from .l3_ground_lab import L3GroundLabResult, run_l3_ground_lab


ProgressCallback = Callable[[int, str], None]


def _dense_context_builder(
    cloud: CloudModel,
    ptd_model,
    params: GroundEngineParams,
    progress: ProgressCallback | None,
):
    return build_dense_spatial_evidence_grid(
        cloud,
        ptd_model,
        chunk_size=params.chunk_size,
        max_cells=3_000_000,
        neighbourhood_radius=1,
        progress=progress,
    )


def run_l3_dense_ground(
    cloud: CloudModel,
    params: GroundEngineParams | None = None,
    progress: ProgressCallback | None = None,
    source_override: SourceType | str | None = None,
) -> L3GroundLabResult:
    """R19-A: R18 evidence scorer with dense, order-invariant spatial context.

    Deliberately keeps R18 thresholds/weights unchanged so the first field test
    isolates the sparse-context defect instead of mixing it with retuning.
    """
    return run_l3_ground_lab(
        cloud,
        params,
        progress,
        source_override=source_override,
        context_builder=_dense_context_builder,
        engine_name="L3 Dense Ground R19",
        revision_label="R19.1",
        collect_gate_diagnostics=True,
    )
