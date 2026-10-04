"""Independent bounded-memory, two-pass adapter for Standalone Ground V1.

Does not import QGIS, LAS-CAFIISICA or its ground/mantle. The supplied
classify_las_algorithm.py remains unchanged and can be replaced independently.
This extension uses the measured LAS header bounds and the original V1
cell-support/height formula. It NEVER synthesises missing points or terrain.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import laspy
import numpy as np
from scipy.ndimage import distance_transform_edt

STREAMING_API_VERSION = "1.0"
STREAMING_IMPLEMENTATION = "TALUDE_STANDALONE_GROUND_V1_TWO_PASS_R1"

Progress = Callable[[str, float], None]
Cancel = Callable[[], bool]


def _notify(progress_callback: Progress | None, label: str, fraction: float) -> None:
    if progress_callback is not None:
        progress_callback(label, float(min(1.0, max(0.0, fraction))))


def _cancel(cancel_callback: Cancel | None) -> None:
    if cancel_callback is not None and cancel_callback():
        raise InterruptedError("Classificação cancelada; o ficheiro parcial não será publicado.")


def _valid_indices(chunk, *, original, xmin: float, ymin: float,
                   cell: float, nx: int, ny: int):
    x = np.asarray(chunk.x, dtype=np.float64)
    y = np.asarray(chunk.y, dtype=np.float64)
    z = np.asarray(chunk.z, dtype=np.float64)
    valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    # All classified points are physically measured and finite.
    index = original._index(x[valid], y[valid], xmin, ymin, cell, nx, ny)
    return valid, index, z


def _support_mask(low, high, count, nx: int, ny: int, s):
    observed = count.reshape(ny, nx) > 0
    low2 = low.reshape(ny, nx)
    high2 = high.reshape(ny, nx)
    seed = observed & ((high2 - low2) <= s.max_local_relief_m)
    if not seed.any():
        return None, None, None, int(observed.sum()), 0, "no_supported_seeds"

    seed_neighbors = np.zeros((ny, nx), dtype=np.int16)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            y0, y1 = max(0, -dy), min(ny, ny - dy)
            x0, x1 = max(0, -dx), min(nx, nx - dx)
            seed_neighbors[y0:y1, x0:x1] += seed[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
    support = seed & (seed_neighbors >= 2)
    if not support.any():
        return None, None, None, int(observed.sum()), 0, "no_connected_support"

    distance, nearest = distance_transform_edt(~support, return_indices=True)
    reference = low2[tuple(nearest)]
    cell_ground = (observed
                   & (distance * s.cell_m <= s.max_unsupported_distance_m)
                   & ((high2 - low2) <= s.max_local_relief_m))
    return (cell_ground.ravel(), reference.ravel(), distance.ravel(),
            int(observed.sum()), int(support.sum()), None)


def classify_file_streamed(source, destination, *, original, settings=None,
                           overwrite: bool = False,
                           progress_callback: Progress | None = None,
                           cancel_callback: Cancel | None = None):
    """Classify LAS/LAZ in two sequential point-cloud passes, bounded by grid+chunk.

    Input/output order, all original LAS dimensions and XYZ values are preserved.
    Class 7 is protected, others receive class 2 (Ground) or 1 (Unclassified)
    consistently with the supplied Standalone V1. No full-cloud XYZ arrays.

    Returns stats only after writer has completed; failures remove partial output.
    Caller is responsible for atomic publishing of the validated file.
    """
    source = Path(source)
    destination = Path(destination)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.resolve() == destination.resolve():
        raise ValueError("Input and output must differ")
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    if getattr(original, 'API_VERSION', None) != STREAMING_API_VERSION:
        raise RuntimeError("Streaming extension requires standalone classifier API_VERSION=1.0")
    s = settings if settings is not None else original.Settings()
    if (s.cell_m <= 0 or s.chunk_points < 1 or s.max_cells < 1 or
            s.max_local_relief_m <= 0 or s.max_surface_offset_m < 0 or
            s.max_unsupported_distance_m < 0):
        raise ValueError("Invalid streaming ground settings")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if overwrite and destination.exists():
        # Never remove a previous usable result until the replacement is complete.
        # V3 normally calls this with overwrite=False and a unique .partial.las.
        raise ValueError("Overwrite of existing output is not supported by the safe streaming adapter")

    try:
        with laspy.open(source) as reader:
            hdr = reader.header
            crs = hdr.parse_crs()
            if crs is None or crs.to_epsg() != original.CRS_EPSG:
                raise ValueError("Expected explicitly declared EPSG:3763 input CRS")
            total = int(hdr.point_count)
            if total < 1:
                raise ValueError("No source points")
            xmin, ymin = float(hdr.mins[0]), float(hdr.mins[1])
            xmax, ymax = float(hdr.maxs[0]), float(hdr.maxs[1])
            if not np.isfinite([xmin, ymin, xmax, ymax]).all() or xmax < xmin or ymax < ymin:
                raise ValueError("Invalid declared LAS header bounds")
            nx = int(np.floor((xmax - xmin) / s.cell_m)) + 1
            ny = int(np.floor((ymax - ymin) / s.cell_m)) + 1
            cells = nx * ny
            if cells > s.max_cells:
                raise MemoryError(
                    f"Grid requires {cells:,} cells, limit {s.max_cells:,}. "
                    "Raise cell_m explicitly or process independent spatial tiles."
                )
            low = np.full(cells, np.inf, dtype=np.float64)
            high = np.full(cells, -np.inf, dtype=np.float64)
            count = np.zeros(cells, dtype=np.int64)
            scanned = 0
            _notify(progress_callback, "PASSAGEM 1/2: modelo de suporte por células", 0.0)
            for chunk in reader.chunk_iterator(s.chunk_points):
                _cancel(cancel_callback)
                valid, flat, z = _valid_indices(
                    chunk, original=original, xmin=xmin, ymin=ymin,
                    cell=s.cell_m, nx=nx, ny=ny
                )
                np.minimum.at(low, flat, z[valid])
                np.maximum.at(high, flat, z[valid])
                np.add.at(count, flat, 1)
                scanned += len(chunk)
                if scanned > total:
                    raise ValueError("LAS reader produced more points than declared in header")
                _notify(progress_callback, "PASSAGEM 1/2: modelo de suporte por células", 0.46 * scanned / total)
            if scanned != total or not int(count.sum()):
                raise ValueError("Header point count inconsistent or LAS has no valid XYZ")
        _cancel(cancel_callback)
        (cell_ok, reference, dist, observed_cells,
         supported_cells, reason) = _support_mask(low, high, count, nx, ny, s)
        _notify(progress_callback, "Suporte do terreno calculado", 0.50)

        ground_count = 0
        preserved_noise = 0
        written = 0
        with laspy.open(source) as reader:
            with laspy.open(destination, mode='w', header=reader.header) as writer:
                for chunk in reader.chunk_iterator(s.chunk_points):
                    _cancel(cancel_callback)
                    valid, flat, z = _valid_indices(
                        chunk, original=original, xmin=xmin, ymin=ymin,
                        cell=s.cell_m, nx=nx, ny=ny
                    )
                    # Empty support: never claim ground from guessed/unobserved cells.
                    ground = np.zeros(len(chunk), dtype=bool)
                    if reason is None and flat.size:
                        local_low = low[flat]
                        ground[valid] = (
                            cell_ok[flat]
                            & (z[valid] - local_low <= s.max_surface_offset_m)
                            & ((np.abs(local_low - reference[flat]) <= s.max_local_relief_m)
                               | (dist[flat] == 0))
                        )
                    classes = np.asarray(chunk.classification).copy()
                    protected = classes == 7
                    ground_count += int(np.count_nonzero(ground & ~protected))
                    preserved_noise += int(np.count_nonzero(protected))
                    classes[~protected] = np.where(ground[~protected], 2, 1)
                    chunk.classification = classes
                    writer.write_points(chunk)
                    written += len(chunk)
                    if written > total:
                        raise ValueError("LAS second pass produced excess points")
                    _notify(progress_callback, "PASSAGEM 2/2: escrever nuvem classificada", 0.50 + 0.50 * written / total)
        if written != total:
            raise ValueError("LAS second pass incomplete")
        result = {
            "input": str(source), "output": str(destination),
            "algorithm": STREAMING_IMPLEMENTATION,
            "base_algorithm": original.ALGORITHM_ID,
            "api_version": STREAMING_API_VERSION,
            "processing": "bounded_memory_two_pass",
            "grid_bbox": "LAS_HEADER",
            "cells": int(cells), "observed_cells": observed_cells,
            "supported_cells": supported_cells,
            "ground_points": ground_count,
            "preserved_class_7": preserved_noise,
            "total_points": written,
            "cell_m": float(s.cell_m),
            "chunk_points": int(s.chunk_points),
            "note": ("Experimental independent measured-ground classifier; "
                     "no infill of unobserved terrain. Validate against surveyed ground truth."),
        }
        if reason:
            result['reason'] = reason
        return result
    except BaseException:
        # Destination is exclusively the private partial path from V3 caller.
        destination.unlink(missing_ok=True)
        raise
