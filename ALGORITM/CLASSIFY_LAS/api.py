"""Stable adapter for the embedded LAS-CAFIISICA R20.4 engine."""
from __future__ import annotations
from pathlib import Path
from typing import Callable

from .las_classifier.classifiers.universal_ground import run_universal_ground
from .las_classifier.cloud.exporter import export_classified
from .las_classifier.cloud.loader import load_cloud
from .las_classifier.ground.ground_export import export_ground_only
from .las_classifier.terrain.mdt_export import export_ground_mdt

ProgressCallback = Callable[[int, str], None]
MODULE_VERSION = "R20.4"
SOURCE_BRANCH = "r20-4-universal-mdt"


def classify_and_create_mdt(
    source_path: str | Path,
    output_dir: str | Path,
    progress: ProgressCallback | None = None,
    *,
    mdt_resolution_m: float = 0.25,
    mdt_max_gap_m: float = 0.75,
) -> dict:
    """LAS/LAZ -> Universal Ground -> classified cloud -> Ground -> MDT."""
    source = Path(source_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    def emit(percent: int, message: str) -> None:
        if progress is not None:
            progress(max(0, min(100, int(percent))), message)

    emit(1, "CLASSIFY LAS R20.4: carregar nuvem")
    cloud = load_cloud(source)
    result = run_universal_ground(
        cloud,
        progress=lambda p, m: emit(int(p * 0.62), m),
    )

    classified = output / f"{source.stem}_CLASSIFIED_R20_4.laz"
    emit(63, "CLASSIFY LAS R20.4: exportar nuvem classificada")
    export_classified(
        source, classified, result.model,
        lambda p, m: emit(63 + int(p * 0.14), m),
    )

    ground_only = output / f"{source.stem}_GROUND_R20_4.laz"
    emit(78, "CLASSIFY LAS R20.4: exportar Ground medido")
    export_ground_only(
        source, ground_only, result.model,
        lambda p, m: emit(78 + int(p * 0.09), m),
        include_synthetic=False,
    )

    mdt = output / f"{source.stem}_MDT_R20_4.tif"
    emit(88, "CLASSIFY LAS R20.4: criar MDT")
    terrain = export_ground_mdt(
        source, mdt, result.model,
        lambda p, m: emit(88 + int(p * 0.12), m),
        resolution_m=mdt_resolution_m,
        max_gap_m=mdt_max_gap_m,
    )
    emit(100, "CLASSIFY LAS R20.4 concluído")
    return {
        "module": "CLASSIFY_LAS",
        "version": MODULE_VERSION,
        "source": str(source),
        "source_type": getattr(result, "source_type", "UNKNOWN"),
        "classified_cloud": str(classified),
        "ground_cloud": str(ground_only),
        "mdt": terrain["mdt"],
        "mdt_observation_state": terrain["observation_state"],
        "ground_count": int(getattr(result, "ground_count", 0)),
        "rejected_count": int(getattr(result, "non_ground_count", 0)),
        "mdt_info": terrain,
    }
