"""R20.1 keeps the R20 inverted cloth and adds post-classification veto."""
from __future__ import annotations

from typing import Callable

from ..cloud.model import CloudModel
from ..ground.types import GroundEngineParams
from ..terrain.inverted_mantle import build_inverted_ground_mantle
from ..terrain.mantle_veto import build_mantle_veto
from ..terrain.schema import SourceType
from .l3_ground_lab import L3GroundLabResult, run_l3_ground_lab
from .l3_inverted_ground import _dense_context

ProgressCallback = Callable[[int, str], None]


def _mantle_with_guard(context, progress):
    mantle = build_inverted_ground_mantle(context, progress=progress)
    mantle.veto_guard = build_mantle_veto(context, mantle)
    return mantle


def run_l3_mantle_veto(
    cloud: CloudModel,
    params: GroundEngineParams | None = None,
    progress: ProgressCallback | None = None,
    source_override: SourceType | str | None = None,
) -> L3GroundLabResult:
    """Measured Ground only. R20 cloth geometry/thresholds are unchanged."""
    return run_l3_ground_lab(
        cloud,
        params,
        progress,
        source_override=source_override,
        context_builder=_dense_context,
        mantle_builder=_mantle_with_guard,
        engine_name="L3 Inverted Ground R20.1",
        revision_label="R20.1",
        collect_gate_diagnostics=True,
    )
