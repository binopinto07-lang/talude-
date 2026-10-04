from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import laspy
import numpy as np
from pyproj import CRS

from ..classifiers.smrf import (
    GROUND_CLASS,
    SMRFModel,
)
from .crs import WORKING_EPSG


LOGGER = logging.getLogger("las_cafiisica.cloud.exporter")
ProgressCallback = Callable[[int, str], None]


def _scaled(
    raw,
    scale: float,
    offset: float,
) -> np.ndarray:
    values = np.asarray(
        raw,
        dtype=np.float64,
    )
    values *= float(scale)
    values += float(offset)
    return values


def _synthetic_record(
    header: laspy.LasHeader,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
) -> laspy.ScaleAwarePointRecord:
    count = int(x.size)
    points = (
        laspy.ScaleAwarePointRecord.zeros(
            count,
            header=header,
        )
    )
    points.x = x
    points.y = y
    points.z = z
    points.classification = np.full(
        count,
        GROUND_CLASS,
        dtype=np.uint8,
    )

    # Synthetic points are explicitly marked so they can never be confused
    # with measured returns.
    try:
        points.synthetic = np.ones(
            count,
            dtype=np.uint8,
        )
    except Exception:
        LOGGER.warning(
            "SYNTHETIC_FLAG_UNAVAILABLE"
        )

    names = set(
        header.point_format.dimension_names
    )
    if "return_number" in names:
        points.return_number = np.ones(
            count,
            dtype=np.uint8,
        )
    if "number_of_returns" in names:
        points.number_of_returns = np.ones(
            count,
            dtype=np.uint8,
        )
    if {
        "red",
        "green",
        "blue",
    }.issubset(names):
        points.red = np.full(
            count,
            12000,
            dtype=np.uint16,
        )
        points.green = np.full(
            count,
            50000,
            dtype=np.uint16,
        )
        points.blue = np.full(
            count,
            12000,
            dtype=np.uint16,
        )

    return points


def export_classified(
    source_path: str | Path,
    output_path: str | Path,
    model: SMRFModel,
    progress: ProgressCallback | None = None,
) -> Path:
    source = Path(
        source_path
    ).expanduser().resolve()
    output = Path(
        output_path
    ).expanduser().resolve()

    if source == output:
        raise ValueError(
            "Output must be different from the source file"
        )
    if (
        output.suffix.lower()
        not in {".las", ".laz"}
    ):
        raise ValueError(
            "Output must use .las or .laz"
        )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    temporary = output.with_name(
        output.name + ".partial"
    )
    if temporary.exists():
        temporary.unlink()

    try:
        with laspy.open(
            source
        ) as reader:
            header = reader.header.copy()
            header.add_crs(
                CRS.from_epsg(
                    WORKING_EPSG
                ),
                keep_compatibility=True,
            )
            total = int(
                reader.header.point_count
            )
            scales = header.scales
            offsets = header.offsets
            do_compress = (
                output.suffix.lower()
                == ".laz"
            )
            fill_total = (
                model.synthetic_fill_point_count
            )

            with laspy.open(
                temporary,
                mode="w",
                header=header,
                do_compress=do_compress,
            ) as writer:
                processed = 0
                for points in (
                    reader.chunk_iterator(
                        model.params.chunk_size
                    )
                ):
                    x = _scaled(
                        points.X,
                        scales[0],
                        offsets[0],
                    )
                    y = _scaled(
                        points.Y,
                        scales[1],
                        offsets[1],
                    )
                    z = _scaled(
                        points.Z,
                        scales[2],
                        offsets[2],
                    )
                    classify_points = getattr(model, "classify_points", None)
                    if classify_points is not None:
                        classes = classify_points(points, x, y, z)
                    else:
                        classes = model.classify_xyz(x, y, z)
                    points.classification = classes
                    writer.write_points(
                        points
                    )
                    processed += len(points)

                    if (
                        progress is not None
                        and total
                    ):
                        progress(
                            int(
                                82
                                * processed
                                / total
                            ),
                            (
                                "Export classified: "
                                f"{processed:,}/{total:,}"
                            ),
                        )

                fill_written = 0
                for x, y, z in (
                    model.iter_synthetic_fill_xyz()
                ):
                    synthetic = (
                        _synthetic_record(
                            header,
                            x,
                            y,
                            z,
                        )
                    )
                    writer.write_points(
                        synthetic
                    )
                    fill_written += len(
                        synthetic
                    )

                    if (
                        progress is not None
                        and fill_total
                    ):
                        progress(
                            (
                                82
                                + int(
                                    18
                                    * fill_written
                                    / fill_total
                                )
                            ),
                            (
                                "Fill terrain: "
                                f"{fill_written:,}/"
                                f"{fill_total:,}"
                            ),
                        )

        temporary.replace(
            output
        )
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise

    LOGGER.info(
        "CLASSIFIED_EXPORT=%s WORKING_CRS=EPSG:%d "
        "SYNTHETIC_FILL=%d FILL_SPACING=%.4f",
        output,
        WORKING_EPSG,
        model.synthetic_fill_point_count,
        model.effective_fill_spacing,
    )
    if progress is not None:
        progress(
            100,
            "Classified export complete",
        )
    return output
