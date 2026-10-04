from __future__ import annotations

import logging
from pathlib import Path

import laspy
import numpy as np

from .crs import WORKING_CRS
from .model import CloudModel


LOGGER = logging.getLogger("las_cafiisica.cloud.loader")
IGNORE_INPUT_CLASSIFICATION = True
SUPPORTED_EXTENSIONS = {".las", ".laz"}


def load_cloud(path: str | Path) -> CloudModel:
    """Read LAS/LAZ using EPSG:3763 as the fixed working CRS."""

    source = Path(path).expanduser().resolve()
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported point-cloud format: {source.suffix or '<none>'}"
        )
    if not source.is_file():
        raise FileNotFoundError(source)

    LOGGER.info("INPUT_FILE=%s", source)
    LOGGER.info("WORKING_CRS=%s", WORKING_CRS)
    las = laspy.read(source)

    if "classification" in las.point_format.dimension_names:
        original_class = np.asarray(
            las.classification, dtype=np.uint8
        ).copy()
    else:
        original_class = np.zeros(len(las.points), dtype=np.uint8)

    cloud = CloudModel(
        path=source,
        las=las,
        original_class=original_class,
    )

    LOGGER.info("POINT_COUNT=%d", cloud.point_count)
    LOGGER.info(
        "IGNORE_INPUT_CLASSIFICATION=%s",
        IGNORE_INPUT_CLASSIFICATION,
    )
    LOGGER.info("XYZ_MATERIALIZED=%s", cloud._xyz_cache is not None)
    LOGGER.info(
        "WORKING_CLASS_MATERIALIZED=%s",
        cloud._working_class is not None,
    )
    return cloud
