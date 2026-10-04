"""Separate R20.3 engine retaining the R20.1 veto and unchanged mantle."""
from __future__ import annotations

from typing import Callable

from ..cloud.model import CloudModel
from ..ground.types import GroundEngineParams
from ..terrain.ground_continuity import build_ground_continuity
from ..terrain.schema import SourceType
from .l3_ground_lab import L3GroundLabResult, run_l3_ground_lab
from .l3_inverted_ground import _dense_context
from .l3_mantle_veto import _mantle_with_guard

ProgressCallback = Callable[[int, str], None]


def _continuity_builder(context, mantle, progress):
    if mantle is None or mantle.veto_guard is None:
        raise RuntimeError("R20.3 requires the R20.1 guarded mantle")
    if progress is not None:
        progress(56, "R20.3: breakline-safe measured Ground recovery")
    return build_ground_continuity(context, mantle, mantle.veto_guard)


def run_l3_ground_continuity(
    cloud: CloudModel,
    params: GroundEngineParams | None = None,
    progress: ProgressCallback | None = None,
    source_override: SourceType | str | None = None,
) -> L3GroundLabResult:
    """Recover measured terrain missed by PTD; synthetic Ground stays disabled."""
    return run_l3_ground_lab(
        cloud,
        params,
        progress,
        source_override=source_override,
        context_builder=_dense_context,
        mantle_builder=_mantle_with_guard,
        continuity_builder=_continuity_builder,
        engine_name="L3 Ground Continuity R20.3",
        revision_label="R20.3",
        collect_gate_diagnostics=True,
    )
