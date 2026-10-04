from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import laspy
import numpy as np


LOGGER = logging.getLogger("las_cafiisica.ground.ground_export")
GROUND_CLASS = np.uint8(2)
ProgressCallback = Callable[[int, str], None]

GROUND_CONFIDENCE = "GroundConfidence"
GROUND_SOURCE = "GroundSource"
INTERPOLATION_DISTANCE = "InterpolationDistance"
GROUND_METHOD = "GroundMethod"
GROUND_DECISION = "GroundDecision"
GROUND_PROVENANCE = "GroundProvenance"


def _scaled(raw, scale: float, offset: float) -> np.ndarray:
    return (
        np.asarray(raw, dtype=np.float64) * float(scale)
        + float(offset)
    )


def _classify_points(model, points, x, y, z) -> np.ndarray:
    method = getattr(model, "classify_points", None)
    if method is not None:
        return method(points, x, y, z)
    return model.classify_xyz(x, y, z)


def _method_code(model) -> int:
    name = str(getattr(model, "engine_name", "")).lower()
    if "universal ground r20.4" in name:
        return 10
    if "ground continuity r20.3" in name:
        return 9
    if "ground continuity r20.2" in name:
        return 8
    if "inverted ground r20.1" in name:
        return 7
    if "inverted ground r20" in name:
        return 6
    if "l3 dense ground r19" in name or "l3 ground lab" in name or "ground evidence" in name:
        return 5
    if "hybrid" in name:
        return 2
    if "adaptive" in name or "ptd" in name:
        return 1
    if "csf" in name:
        return 3
    if "smrf" in name:
        return 4
    return 0


def _with_ground_metadata(header: laspy.LasHeader) -> laspy.LasHeader:
    result = header.copy()
    names = set(result.point_format.dimension_names)
    extras = []
    if GROUND_CONFIDENCE not in names:
        extras.append(
            laspy.ExtraBytesParams(
                name=GROUND_CONFIDENCE,
                type=np.float32,
            )
        )
    if GROUND_SOURCE not in names:
        extras.append(
            laspy.ExtraBytesParams(
                name=GROUND_SOURCE,
                type=np.uint8,
            )
        )
    if INTERPOLATION_DISTANCE not in names:
        extras.append(
            laspy.ExtraBytesParams(
                name=INTERPOLATION_DISTANCE,
                type=np.float32,
            )
        )
    if GROUND_METHOD not in names:
        extras.append(
            laspy.ExtraBytesParams(
                name=GROUND_METHOD,
                type=np.uint8,
            )
        )
    if GROUND_DECISION not in names:
        extras.append(
            laspy.ExtraBytesParams(
                name=GROUND_DECISION,
                type=np.uint8,
            )
        )
    if GROUND_PROVENANCE not in names:
        extras.append(
            laspy.ExtraBytesParams(
                name=GROUND_PROVENANCE,
                type=np.uint16,
            )
        )
    if extras:
        result.add_extra_dims(extras)
    return result


def _copy_to_output_format(
    source_points,
    output_header: laspy.LasHeader,
) -> laspy.ScaleAwarePointRecord:
    target = laspy.ScaleAwarePointRecord.zeros(
        len(source_points),
        header=output_header,
    )
    target_names = set(target.point_format.dimension_names)
    for name in source_points.point_format.dimension_names:
        if name not in target_names:
            continue
        try:
            target[name] = source_points[name]
        except Exception:
            LOGGER.debug(
                "GROUND_EXPORT_COPY_SKIPPED dimension=%s",
                name,
            )
    return target


def _point_confidence(
    model,
    points,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    classes: np.ndarray,
) -> np.ndarray:
    method = getattr(model, "confidence_points", None)
    if method is not None:
        return np.asarray(
            method(points, x, y, z),
            dtype=np.float32,
        )
    method = getattr(model, "confidence_xyz", None)
    if method is not None:
        return np.asarray(
            method(x, y, z),
            dtype=np.float32,
        )
    return np.where(
        classes == GROUND_CLASS,
        1.0,
        0.0,
    ).astype(np.float32)


def _synthetic_record(
    header: laspy.LasHeader,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    *,
    mark_las_synthetic: bool = True,
) -> laspy.ScaleAwarePointRecord:
    count = int(x.size)
    points = laspy.ScaleAwarePointRecord.zeros(
        count,
        header=header,
    )
    points.x = x
    points.y = y
    points.z = z
    points.classification = np.full(
        count,
        GROUND_CLASS,
        dtype=np.uint8,
    )
    names = set(header.point_format.dimension_names)
    if GROUND_CONFIDENCE in names:
        points[GROUND_CONFIDENCE] = np.full(
            count,
            0.75,
            dtype=np.float32,
        )
    if GROUND_SOURCE in names:
        points[GROUND_SOURCE] = np.full(
            count,
            2,
            dtype=np.uint8,
        )
    if INTERPOLATION_DISTANCE in names:
        points[INTERPOLATION_DISTANCE] = np.zeros(
            count,
            dtype=np.float32,
        )

    # The Potree preview may retain the LAS synthetic bit because our viewer
    # renders it explicitly. The final exported Ground Only cloud deliberately
    # leaves the LAS synthetic bit clear: several third-party LAS viewers hide
    # synthetic returns by default, which made reconstructed gaps look open
    # even though the points were physically present. Provenance is preserved
    # losslessly in GroundSource=2 and GroundMethod.
    try:
        points.synthetic = np.full(
            count,
            1 if mark_las_synthetic else 0,
            dtype=np.uint8,
        )
    except Exception:
        LOGGER.warning("SYNTHETIC_FLAG_UNAVAILABLE")

    if "return_number" in names:
        points.return_number = np.ones(count, dtype=np.uint8)
    if "number_of_returns" in names:
        points.number_of_returns = np.ones(count, dtype=np.uint8)

    # Reconstructed ground must remain visible in generic LAS viewers that
    # default to RGB or intensity instead of classification. Zero-valued
    # synthetic RGB/intensity rendered as black and looked like open holes.
    if "intensity" in names:
        points.intensity = np.full(
            count,
            32768,
            dtype=np.uint16,
        )
    if {"red", "green", "blue"}.issubset(names):
        neutral = np.full(
            count,
            32768,
            dtype=np.uint16,
        )
        points.red = neutral
        points.green = neutral
        points.blue = neutral

    return points


def export_ground_only(
    source_path: str | Path,
    output_path: str | Path,
    model,
    progress: ProgressCallback | None = None,
    *,
    include_synthetic: bool = True,
) -> Path:
    """Export only measured ground plus optional reconstructed ground.

    The source LAS header is copied as-is so valid source CRS, scale, offset,
    LAS version and point format are preserved. The input file is never
    modified.
    """

    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()

    if source == output:
        raise ValueError("Output must be different from the source file")
    if output.suffix.lower() not in {".las", ".laz"}:
        raise ValueError("Output must use .las or .laz")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".partial")
    if temporary.exists():
        temporary.unlink()

    with laspy.open(source) as reader:
        source_header = reader.header.copy()
        header = _with_ground_metadata(source_header)
        scales = source_header.scales
        offsets = header.offsets
        total = int(reader.header.point_count)
        chunk_size = int(getattr(model.params, "chunk_size", 2_000_000))
        do_compress = output.suffix.lower() == ".laz"

        real_ground = 0
        synthetic_written = 0

        try:
            with laspy.open(
                temporary,
                mode="w",
                header=header,
                do_compress=do_compress,
            ) as writer:
                processed = 0
                for points in reader.chunk_iterator(chunk_size):
                    x = _scaled(points.X, scales[0], offsets[0])
                    y = _scaled(points.Y, scales[1], offsets[1])
                    z = _scaled(points.Z, scales[2], offsets[2])
                    evaluate = getattr(
                        model,
                        "evaluate_points",
                        None,
                    )
                    evidence = None
                    ground_sources = None
                    decision_codes = None
                    provenance_codes = None
                    confidence = None
                    if evaluate is not None:
                        evidence = evaluate(
                            points,
                            x,
                            y,
                            z,
                        )
                        classes = (
                            evidence.classifications()
                        )
                        confidence = np.asarray(
                            evidence.score,
                            dtype=np.float32,
                        )
                        ground_sources = (
                            evidence.ground_source_codes()
                        )
                        decision_codes = np.asarray(
                            evidence.decision,
                            dtype=np.uint8,
                        )
                        provenance_codes = np.asarray(
                            evidence.provenance,
                            dtype=np.uint16,
                        )
                    else:
                        classes = _classify_points(
                            model,
                            points,
                            x,
                            y,
                            z,
                        )

                    keep = classes == GROUND_CLASS
                    if np.any(keep):
                        if confidence is None:
                            confidence = _point_confidence(
                                model,
                                points,
                                x,
                                y,
                                z,
                                classes,
                            )
                        selected = points[keep]
                        ground_points = _copy_to_output_format(
                            selected,
                            header,
                        )
                        kept_count = int(np.count_nonzero(keep))
                        ground_points.classification = np.full(
                            kept_count,
                            GROUND_CLASS,
                            dtype=np.uint8,
                        )
                        names = set(
                            header.point_format.dimension_names
                        )
                        if GROUND_CONFIDENCE in names:
                            ground_points[GROUND_CONFIDENCE] = (
                                confidence[keep]
                            )
                        if GROUND_SOURCE in names:
                            if ground_sources is not None:
                                ground_points[GROUND_SOURCE] = (
                                    ground_sources[keep]
                                )
                            else:
                                ground_points[GROUND_SOURCE] = np.full(
                                    kept_count,
                                    1,
                                    dtype=np.uint8,
                                )
                        if INTERPOLATION_DISTANCE in names:
                            ground_points[
                                INTERPOLATION_DISTANCE
                            ] = np.zeros(
                                kept_count,
                                dtype=np.float32,
                            )
                        if GROUND_METHOD in names:
                            ground_points[GROUND_METHOD] = np.full(
                                kept_count,
                                _method_code(model),
                                dtype=np.uint8,
                            )
                        if (
                            GROUND_DECISION in names
                            and decision_codes is not None
                        ):
                            ground_points[GROUND_DECISION] = (
                                decision_codes[keep]
                            )
                        if (
                            GROUND_PROVENANCE in names
                            and provenance_codes is not None
                        ):
                            ground_points[GROUND_PROVENANCE] = (
                                provenance_codes[keep]
                            )
                        writer.write_points(ground_points)
                        real_ground += len(ground_points)

                    processed += len(points)
                    if progress is not None and total:
                        progress(
                            int(82 * processed / total),
                            (
                                "Export ground real: "
                                f"{processed:,}/{total:,}"
                            ),
                        )

                if include_synthetic:
                    fill_total = int(
                        getattr(
                            model,
                            "synthetic_fill_point_count",
                            0,
                        )
                    )
                    iterator = getattr(
                        model,
                        "iter_synthetic_fill_xyz",
                        None,
                    )
                    if iterator is not None:
                        for x, y, z in iterator():
                            synthetic = _synthetic_record(
                                header,
                                np.asarray(x, dtype=np.float64),
                                np.asarray(y, dtype=np.float64),
                                np.asarray(z, dtype=np.float64),
                                mark_las_synthetic=False,
                            )
                            names = set(
                                header.point_format.dimension_names
                            )
                            if GROUND_METHOD in names:
                                synthetic[GROUND_METHOD] = np.full(
                                    len(synthetic),
                                    _method_code(model),
                                    dtype=np.uint8,
                                )
                            if INTERPOLATION_DISTANCE in names:
                                synthetic[
                                    INTERPOLATION_DISTANCE
                                ] = np.full(
                                    len(synthetic),
                                    float(
                                        getattr(
                                            model,
                                            "effective_fill_spacing",
                                            0.0,
                                        )
                                    ),
                                    dtype=np.float32,
                                )
                            writer.write_points(synthetic)
                            synthetic_written += len(synthetic)
                            if progress is not None and fill_total:
                                progress(
                                    82
                                    + int(
                                        18
                                        * synthetic_written
                                        / max(1, fill_total)
                                    ),
                                    (
                                        "Reconstruct ground: "
                                        f"{synthetic_written:,}/"
                                        f"{fill_total:,}"
                                    ),
                                )

            temporary.replace(output)
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise

    LOGGER.info("GROUND_ONLY_EXPORT=%s", output)
    LOGGER.info("GROUND_REAL=%d", real_ground)
    LOGGER.info("SYNTHETIC_POINTS=%d", synthetic_written)
    LOGGER.info("GROUND_FINAL=%d", real_ground + synthetic_written)
    LOGGER.info(
        "GROUND_EXPORT_RECONSTRUCTED_VISIBLE=%s",
        bool(include_synthetic and synthetic_written),
    )
    if progress is not None:
        progress(100, "Ground-only export complete")
    return output
