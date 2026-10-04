from __future__ import annotations

import logging

import numpy as np
from scipy.spatial import Delaunay, QhullError


LOGGER = logging.getLogger("las_cafiisica.ground.tin_delaunay")


def prepare_xy(vertices: np.ndarray):
    data = np.asarray(vertices, dtype=np.float64)
    data = data[:, :3]
    data = data[np.all(np.isfinite(data), axis=1)]
    if data.shape[0] < 3:
        raise ValueError(
            "Terrain TIN requires at least three finite vertices"
        )

    xy = data[:, :2]
    center = (xy.min(axis=0) + xy.max(axis=0)) * 0.5
    scale = float(np.max(np.ptp(xy, axis=0)))
    if scale <= 1e-12:
        raise ValueError(
            "Terrain TIN requires non-zero XY extent"
        )

    norm = (xy - center) / scale

    # Collapse repeated/nearly repeated XY locations while keeping the lowest
    # Z value. Qhull becomes unstable on dense photogrammetric clouds when
    # thousands of vertices share effectively the same planimetric location.
    key = np.rint(norm / 1e-10).astype(np.int64)
    order = np.lexsort(
        (
            data[:, 2],
            key[:, 1],
            key[:, 0],
        )
    )
    key = key[order]
    keep = np.r_[
        True,
        np.any(key[1:] != key[:-1], axis=1),
    ]
    data = data[order[keep]]

    if data.shape[0] < 3:
        raise ValueError(
            "Terrain TIN requires three unique XY vertices"
        )

    xy = data[:, :2]
    center = (xy.min(axis=0) + xy.max(axis=0)) * 0.5
    scale = float(np.max(np.ptp(xy, axis=0)))
    normalized = (xy - center) / scale

    extent = np.ptp(normalized, axis=0)
    LOGGER.info(
        "TIN_PREPARED input=%d unique=%d xy_scale=%.6f "
        "norm_extent=(%.9f,%.9f)",
        int(np.asarray(vertices).shape[0]),
        int(data.shape[0]),
        scale,
        float(extent[0]),
        float(extent[1]),
    )
    return data, normalized, center, scale


def robust_delaunay(xy: np.ndarray) -> Delaunay:
    """Build a Delaunay triangulation with bounded, deterministic recovery.

    Coordinates passed here are already centered/scaled to approximately
    [-0.5, 0.5]. Explicit joggle amplitudes prevent Qhull from escalating to a
    huge automatic perturbation on dense/near-degenerate terrain point sets.
    """

    xy = np.asarray(xy, dtype=np.float64)
    if xy.ndim != 2 or xy.shape[1] != 2:
        raise ValueError(
            "Terrain Delaunay requires an Nx2 coordinate array"
        )
    if xy.shape[0] < 3:
        raise ValueError(
            "Terrain Delaunay requires at least three points"
        )

    extent = np.ptp(xy, axis=0)
    LOGGER.info(
        "TIN_QHULL_INPUT points=%d extent_x=%.12g extent_y=%.12g",
        int(xy.shape[0]),
        float(extent[0]),
        float(extent[1]),
    )

    failures: list[str] = []
    attempts = (
        "Qbb Qc Qz Q12",
        "Qbb Qc Qz Q12 QJ1e-10",
        "Qbb Qc Q12 QJ1e-8",
        "Qbb Qc Q12 QJ1e-6",
    )
    for options in attempts:
        try:
            mesh = Delaunay(
                xy,
                qhull_options=options,
            )
            LOGGER.info(
                "TIN_QHULL_OK options=%s points=%d triangles=%d",
                options,
                int(xy.shape[0]),
                int(mesh.simplices.shape[0]),
            )
            return mesh
        except QhullError as exc:
            message = str(exc).splitlines()[0]
            failures.append(
                f"{options}: {message}"
            )
            LOGGER.warning(
                "TIN_QHULL_RETRY options=%s error=%s",
                options,
                message,
            )

    # Last recovery is independent of Qhull's random joggle. It adds a tiny,
    # deterministic 2-D perturbation in normalized coordinates. The amplitude
    # is many orders of magnitude smaller than terrain geometry, but separates
    # pathological coincident/coplanar configurations enough for triangulation.
    centered = xy - np.mean(xy, axis=0)
    singular = np.linalg.svd(
        centered,
        full_matrices=False,
        compute_uv=False,
    )
    dimensional = (
        singular.size >= 2
        and singular[0] > 0.0
        and singular[1]
        > max(
            1e-14,
            singular[0] * 1e-14,
        )
    )

    if dimensional:
        index = (
            np.arange(
                xy.shape[0],
                dtype=np.float64,
            )
            + 1.0
        )
        base = np.column_stack(
            (
                np.sin(index * 1.61803398875),
                np.cos(index * 2.41421356237),
            )
        )
        for amplitude in (1e-10, 1e-8, 1e-6):
            perturbed = xy + base * amplitude
            try:
                mesh = Delaunay(
                    perturbed,
                    qhull_options="Qbb Qc Q12",
                )
                LOGGER.warning(
                    "TIN_QHULL_RECOVERED amplitude=%.1e "
                    "points=%d triangles=%d",
                    amplitude,
                    int(xy.shape[0]),
                    int(mesh.simplices.shape[0]),
                )
                return mesh
            except QhullError as exc:
                message = str(exc).splitlines()[0]
                failures.append(
                    f"deterministic-{amplitude:.1e}: {message}"
                )

    raise RuntimeError(
        "Unable to build normalized terrain TIN after robust "
        "Qhull recovery: "
        + " | ".join(failures)
    )
