"""Export R20 mantle as a separate, explicitly inferred diagnostic LAS/LAZ.

This file is NOT Ground Only. Every exported vertex is synthetic diagnostic
geometry (LAS class 0), and state 2/4 is never represented as observed Ground.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import laspy
import numpy as np
from pyproj import CRS

from .inverted_mantle import InvertedGroundMantle

MANTLE_STATE = "MantleState"
MANTLE_ANCHOR_DISTANCE = "MantleAnchorM"
# 1 observed/reliable lower envelope, 2 unobserved interpolated small gap,
# 3 observed/ambiguous, 4 possible Ground not observed beneath upper returns.
STATE_OBSERVED_RELIABLE = np.uint8(1)
STATE_INFERRED_EMPTY = np.uint8(2)
STATE_OBSERVED_AMBIGUOUS = np.uint8(3)
STATE_POSSIBLE_UNOBSERVED = np.uint8(4)
STATE_ROOF_CANDIDATE = np.uint8(5)
STATE_CANOPY_CANDIDATE = np.uint8(6)
ProgressCallback = Callable[[int, str], None]


def mantle_state_grid(mantle: InvertedGroundMantle) -> np.ndarray:
    state = np.zeros(mantle.observed.shape, dtype=np.uint8)
    state[mantle.observed & mantle.reliable] = STATE_OBSERVED_RELIABLE
    state[mantle.inferred] = STATE_INFERRED_EMPTY
    state[mantle.ambiguous] = STATE_OBSERVED_AMBIGUOUS
    state[mantle.possible_no_ground_observation] = STATE_POSSIBLE_UNOBSERVED
    guard = getattr(mantle, "veto_guard", None)
    if guard is not None:
        # R20.1 warning classes, not observed Ground; preserve R20 geometry.
        state[guard.canopy_candidate] = STATE_CANOPY_CANDIDATE
        state[guard.roof_candidate] = STATE_ROOF_CANDIDATE
    return state


def export_mantle_diagnostic(
    mantle: InvertedGroundMantle,
    output_path: str | Path,
    progress: ProgressCallback | None = None,
    *,
    chunk_size: int = 100_000,
) -> Path:
    """Stream a color-coded mantle with EPSG:3763 into an independent file."""
    output = Path(output_path).expanduser().resolve()
    if output.suffix.lower() not in (".las", ".laz"):
        raise ValueError("Mantle diagnostic output must be .las or .laz")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".partial")
    if temporary.exists():
        temporary.unlink()

    state = mantle_state_grid(mantle).ravel()
    indices = np.flatnonzero(state != 0)
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = np.array([0.001, 0.001, 0.001], dtype=np.float64)
    header.offsets = np.array(
        [float(mantle.origin[0]), float(mantle.origin[1]), float(np.floor(mantle.reference_z))],
        dtype=np.float64,
    )
    header.add_crs(CRS.from_epsg(3763))
    header.add_extra_dims(
        [
            laspy.ExtraBytesParams(name=MANTLE_STATE, type=np.uint8),
            laspy.ExtraBytesParams(name=MANTLE_ANCHOR_DISTANCE, type=np.float32),
        ]
    )
    # Diagnostics are deliberately NOT LAS class 2. The points describe a
    # mathematical surface, not returns from the lidar sensor.
    palette = np.array(
        [
            [0, 0, 0],
            [13000, 51000, 17000],   # 1 observed evidence: green
            [9000, 19000, 59000],    # 2 interpolated uncertainty: blue
            [30000, 30000, 30000],  # 3 ambiguous measured: grey
            [49000, 20000, 49000],  # 4 suspected unobserved ground: purple
            [65535, 11000, 9000],   # 5 R20.1 elevated roof-like island: red
            [65535, 40000, 5000],   # 6 R20.1 elevated canopy-like island: orange
        ],
        dtype=np.uint16,
    )
    step = max(1, int(chunk_size))
    try:
        with laspy.open(
            temporary, mode="w", header=header,
            do_compress=output.suffix.lower() == ".laz",
        ) as writer:
            for start in range(0, int(indices.size), step):
                stop = min(start + step, int(indices.size))
                flat = indices[start:stop]
                iy = flat // mantle.nx
                ix = flat % mantle.nx
                cloud = laspy.ScaleAwarePointRecord.zeros(len(flat), header=header)
                cloud.x = mantle.origin[0] + (ix.astype(np.float64) + 0.5) * mantle.cell_size
                cloud.y = mantle.origin[1] + (iy.astype(np.float64) + 0.5) * mantle.cell_size
                cloud.z = mantle.surface[iy, ix].astype(np.float64)
                cloud.classification = np.zeros(len(flat), dtype=np.uint8)
                cloud.synthetic = np.ones(len(flat), dtype=np.uint8)
                cloud.return_number = np.ones(len(flat), dtype=np.uint8)
                cloud.number_of_returns = np.ones(len(flat), dtype=np.uint8)
                codes = state[flat]
                cloud[MANTLE_STATE] = codes
                dist = mantle.anchor_distance[iy, ix].astype(np.float32)
                cloud[MANTLE_ANCHOR_DISTANCE] = np.where(
                    np.isfinite(dist), dist, np.float32(-1.0)
                )
                rgb = palette[codes]
                cloud.red = rgb[:, 0]
                cloud.green = rgb[:, 1]
                cloud.blue = rgb[:, 2]
                writer.write_points(cloud)
                if progress is not None:
                    progress(
                        int(100 * stop / max(1, int(indices.size))),
                        f"R20 separate mantle diagnostic: {stop:,}/{indices.size:,} cells",
                    )
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output
