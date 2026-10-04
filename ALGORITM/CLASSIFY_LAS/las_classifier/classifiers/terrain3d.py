from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from math import ceil, cos, radians
from typing import Callable

import numpy as np
from numpy.typing import NDArray

from ..cloud.model import CloudModel


LOGGER = logging.getLogger("las_cafiisica.classifiers.terrain3d")
ProgressCallback = Callable[[int, str], None]
NORMAL_NAMES = ("normal x", "normal y", "normal z")


@dataclass(frozen=True, slots=True)
class Terrain3DParams:
    voxel: float = 0.50
    surface_thickness: float = 0.22
    min_points: int = 5
    coherence: float = 0.62
    seed_ground_fraction: float = 0.60
    max_normal_angle_deg: float = 88.0
    target_sample_points: int = 30_000_000


@dataclass(frozen=True, slots=True)
class Terrain3DRefinement:
    params: Terrain3DParams
    min_x: float
    min_y: float
    min_z: float
    nx: int
    ny: int
    nz: int
    keys: NDArray[np.int64]
    centroids: NDArray[np.float32]
    normals: NDArray[np.float32]
    sampled_points: int
    candidate_voxels: int
    seed_voxels: int

    @property
    def terrain_voxel_count(self) -> int:
        return int(self.keys.size)

    def _keys_for_xyz(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        z: NDArray[np.float64],
    ) -> NDArray[np.int64]:
        ix = np.floor(
            (x - self.min_x) / self.params.voxel
        ).astype(np.int64, copy=False)
        iy = np.floor(
            (y - self.min_y) / self.params.voxel
        ).astype(np.int64, copy=False)
        iz = np.floor(
            (z - self.min_z) / self.params.voxel
        ).astype(np.int64, copy=False)

        np.clip(ix, 0, self.nx - 1, out=ix)
        np.clip(iy, 0, self.ny - 1, out=iy)
        np.clip(iz, 0, self.nz - 1, out=iz)

        return ix + self.nx * (
            iy + self.ny * iz
        )

    def ground_mask(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        z: NDArray[np.float64],
    ) -> NDArray[np.bool_]:
        if self.keys.size == 0:
            return np.zeros(
                z.shape[0],
                dtype=np.bool_,
            )

        point_keys = self._keys_for_xyz(
            x,
            y,
            z,
        )
        positions = np.searchsorted(
            self.keys,
            point_keys,
        )
        safe = np.minimum(
            positions,
            self.keys.size - 1,
        )
        matched = (
            positions < self.keys.size
        ) & (
            self.keys[safe] == point_keys
        )

        result = np.zeros(
            point_keys.shape[0],
            dtype=np.bool_,
        )
        if not np.any(matched):
            return result

        indices = safe[matched]
        dx = (
            x[matched]
            - self.centroids[indices, 0]
        )
        dy = (
            y[matched]
            - self.centroids[indices, 1]
        )
        dz = (
            z[matched]
            - self.centroids[indices, 2]
        )
        normals = self.normals[indices]
        plane_distance = np.abs(
            dx * normals[:, 0]
            + dy * normals[:, 1]
            + dz * normals[:, 2]
        )
        result[matched] = (
            plane_distance
            <= self.params.surface_thickness
        )
        return result


def _emit(
    callback: ProgressCallback | None,
    percent: int,
    message: str,
) -> None:
    if callback is not None:
        callback(
            max(0, min(100, int(percent))),
            message,
        )


def _decode_component(
    values,
) -> NDArray[np.float32]:
    array = np.asarray(values)
    kind = array.dtype.kind
    decoded = array.astype(
        np.float32,
        copy=False,
    )

    if kind == "u":
        maximum = float(
            np.iinfo(array.dtype).max
        )
        if maximum > 0:
            decoded = (
                decoded / maximum
            ) * 2.0 - 1.0
    elif kind == "i":
        info = np.iinfo(array.dtype)
        scale = float(
            max(
                abs(info.min),
                abs(info.max),
            )
        )
        if scale > 0:
            decoded = decoded / scale

    return np.asarray(
        decoded,
        dtype=np.float32,
    )


def _read_normals(
    cloud: CloudModel,
    indices: NDArray[np.int64],
) -> NDArray[np.float32]:
    components = [
        _decode_component(
            cloud.las[name][indices]
        )
        for name in NORMAL_NAMES
    ]
    normals = np.column_stack(
        components
    ).astype(
        np.float32,
        copy=False,
    )

    # Some producers store floating normals in 0..1 instead of -1..1.
    if (
        normals.size
        and np.nanmin(normals) >= 0.0
        and np.nanmax(normals) <= 1.0
    ):
        normals = (
            normals * 2.0 - 1.0
        )

    lengths = np.linalg.norm(
        normals,
        axis=1,
    )
    valid = (
        np.isfinite(lengths)
        & (lengths > 0.15)
    )
    normals[valid] /= (
        lengths[valid, None]
    )
    normals[~valid] = np.nan
    return normals


def _reduce_records(
    keys: NDArray[np.int64],
    values: NDArray[np.float32],
) -> tuple[
    NDArray[np.int64],
    NDArray[np.float32],
]:
    if keys.size == 0:
        return keys, values

    order = np.argsort(
        keys,
        kind="mergesort",
    )
    keys = keys[order]
    values = values[order]

    starts = np.r_[
        0,
        np.flatnonzero(
            keys[1:] != keys[:-1]
        ) + 1,
    ]
    unique = keys[starts]
    reduced = np.add.reduceat(
        values,
        starts,
        axis=0,
    ).astype(
        np.float32,
        copy=False,
    )
    return unique, reduced


def _aggregate_chunk(
    keys: NDArray[np.int64],
    xyz_relative: NDArray[np.float32],
    normals: NDArray[np.float32],
    base_ground: NDArray[np.bool_],
) -> tuple[
    NDArray[np.int64],
    NDArray[np.float32],
]:
    valid = np.all(
        np.isfinite(normals),
        axis=1,
    )
    if not np.any(valid):
        return (
            np.empty(0, dtype=np.int64),
            np.empty((0, 11), dtype=np.float32),
        )

    keys = keys[valid]
    xyz = xyz_relative[valid]
    normals = normals[valid]
    ground = base_ground[valid]

    unique, inverse = np.unique(
        keys,
        return_inverse=True,
    )
    count = np.bincount(
        inverse
    ).astype(np.float32)
    ground_count = np.bincount(
        inverse,
        weights=ground.astype(
            np.float32
        ),
    ).astype(np.float32)

    columns = [
        count,
        ground_count,
    ]
    for axis in range(3):
        columns.append(
            np.bincount(
                inverse,
                weights=xyz[:, axis],
            ).astype(np.float32)
        )

    nx = normals[:, 0]
    ny = normals[:, 1]
    nz = normals[:, 2]
    for values in (
        nx * nx,
        ny * ny,
        nz * nz,
        nx * ny,
        nx * nz,
        ny * nz,
    ):
        columns.append(
            np.bincount(
                inverse,
                weights=values,
            ).astype(np.float32)
        )

    return (
        unique.astype(
            np.int64,
            copy=False,
        ),
        np.column_stack(
            columns
        ).astype(
            np.float32,
            copy=False,
        ),
    )


def _grow_surface(
    keys: NDArray[np.int64],
    centroids: NDArray[np.float32],
    normals: NDArray[np.float32],
    candidate: NDArray[np.bool_],
    seed: NDArray[np.bool_],
    nx: int,
    ny: int,
    nz: int,
    max_angle_deg: float,
) -> NDArray[np.bool_]:
    accepted = seed.copy()
    candidate_indices = np.flatnonzero(
        candidate
    )
    key_to_index = {
        int(keys[index]): int(index)
        for index in candidate_indices
    }

    queue = deque(
        np.flatnonzero(seed).tolist()
    )
    minimum_dot = cos(
        radians(max_angle_deg)
    )
    layer = nx * ny

    offsets = [
        (dx, dy, dz)
        for dz in (-1, 0, 1)
        for dy in (-1, 0, 1)
        for dx in (-1, 0, 1)
        if not (
            dx == 0
            and dy == 0
            and dz == 0
        )
    ]

    while queue:
        index = queue.popleft()
        key = int(keys[index])
        iz = key // layer
        remainder = key - iz * layer
        iy = remainder // nx
        ix = remainder - iy * nx
        normal = normals[index]

        for dx, dy, dz in offsets:
            tx = ix + dx
            ty = iy + dy
            tz = iz + dz
            if (
                tx < 0
                or tx >= nx
                or ty < 0
                or ty >= ny
                or tz < 0
                or tz >= nz
            ):
                continue

            neighbour_key = (
                tx
                + nx
                * (
                    ty
                    + ny * tz
                )
            )
            neighbour = key_to_index.get(
                int(neighbour_key)
            )
            if (
                neighbour is None
                or accepted[neighbour]
            ):
                continue

            alignment = abs(
                float(
                    np.dot(
                        normal,
                        normals[neighbour],
                    )
                )
            )
            if alignment < minimum_dot:
                continue

            # Reject jumps between unrelated parallel surfaces that merely
            # occupy adjacent voxels. A genuine surface neighbour must also
            # lie reasonably close to the current local tangent plane.
            delta = (
                centroids[neighbour]
                - centroids[index]
            )
            tangent_distance = abs(
                float(
                    np.dot(
                        delta,
                        normal,
                    )
                )
            )
            if tangent_distance > 0.45:
                continue

            accepted[neighbour] = True
            queue.append(neighbour)

    return accepted


def build_terrain3d_refinement(
    cloud: CloudModel,
    base_model,
    progress: ProgressCallback | None = None,
    params: Terrain3DParams | None = None,
) -> Terrain3DRefinement | None:
    params = params or Terrain3DParams()

    names = set(
        cloud.las.point_format.dimension_names
    )
    if not set(NORMAL_NAMES).issubset(names):
        LOGGER.warning(
            "TERRAIN3D_DISABLED normals_missing=%s",
            ",".join(
                name
                for name in NORMAL_NAMES
                if name not in names
            ),
        )
        return None

    header = cloud.las.header
    min_x = float(header.mins[0])
    min_y = float(header.mins[1])
    min_z = float(header.mins[2])
    max_x = float(header.maxs[0])
    max_y = float(header.maxs[1])
    max_z = float(header.maxs[2])

    nx = max(
        1,
        int(
            np.floor(
                (max_x - min_x)
                / params.voxel
            )
        ) + 1,
    )
    ny = max(
        1,
        int(
            np.floor(
                (max_y - min_y)
                / params.voxel
            )
        ) + 1,
    )
    nz = max(
        1,
        int(
            np.floor(
                (max_z - min_z)
                / params.voxel
            )
        ) + 1,
    )

    sample_stride = max(
        1,
        int(
            ceil(
                cloud.point_count
                / params.target_sample_points
            )
        ),
    )
    LOGGER.info(
        "TERRAIN3D_START voxel=%.3f thickness=%.3f stride=%d "
        "dims=%dx%dx%d normals=%s",
        params.voxel,
        params.surface_thickness,
        sample_stride,
        nx,
        ny,
        nz,
        ",".join(NORMAL_NAMES),
    )

    scales = cloud.las.header.scales
    offsets = cloud.las.header.offsets
    raw_x = cloud.las.X
    raw_y = cloud.las.Y
    raw_z = cloud.las.Z
    total = cloud.point_count
    chunk_size = base_model.params.chunk_size

    key_blocks: list[NDArray[np.int64]] = []
    value_blocks: list[NDArray[np.float32]] = []
    buffered_records = 0
    sampled_points = 0

    def compact_blocks() -> None:
        nonlocal key_blocks
        nonlocal value_blocks
        nonlocal buffered_records

        if not key_blocks:
            return
        keys = np.concatenate(key_blocks)
        values = np.concatenate(
            value_blocks,
            axis=0,
        )
        keys, values = _reduce_records(
            keys,
            values,
        )
        key_blocks = [keys]
        value_blocks = [values]
        buffered_records = int(
            keys.size
        )

    for start in range(
        0,
        total,
        chunk_size,
    ):
        stop = min(
            start + chunk_size,
            total,
        )
        first = (
            -start
        ) % sample_stride
        local = np.arange(
            first,
            stop - start,
            sample_stride,
            dtype=np.int64,
        )
        if local.size == 0:
            continue
        indices = (
            local + start
        )

        x = (
            np.asarray(
                raw_x[indices],
                dtype=np.float64,
            )
            * float(scales[0])
            + float(offsets[0])
        )
        y = (
            np.asarray(
                raw_y[indices],
                dtype=np.float64,
            )
            * float(scales[1])
            + float(offsets[1])
        )
        z = (
            np.asarray(
                raw_z[indices],
                dtype=np.float64,
            )
            * float(scales[2])
            + float(offsets[2])
        )

        ix = np.floor(
            (x - min_x)
            / params.voxel
        ).astype(
            np.int64,
            copy=False,
        )
        iy = np.floor(
            (y - min_y)
            / params.voxel
        ).astype(
            np.int64,
            copy=False,
        )
        iz = np.floor(
            (z - min_z)
            / params.voxel
        ).astype(
            np.int64,
            copy=False,
        )
        np.clip(ix, 0, nx - 1, out=ix)
        np.clip(iy, 0, ny - 1, out=iy)
        np.clip(iz, 0, nz - 1, out=iz)
        keys = ix + nx * (
            iy + ny * iz
        )

        normals = _read_normals(
            cloud,
            indices,
        )
        base_classes = (
            base_model.classify_xyz(
                x,
                y,
                z,
            )
        )
        base_ground = (
            base_classes == 2
        )
        relative = np.column_stack(
            (
                x - min_x,
                y - min_y,
                z - min_z,
            )
        ).astype(
            np.float32,
            copy=False,
        )

        block_keys, block_values = (
            _aggregate_chunk(
                keys,
                relative,
                normals,
                base_ground,
            )
        )
        if block_keys.size:
            key_blocks.append(
                block_keys
            )
            value_blocks.append(
                block_values
            )
            buffered_records += int(
                block_keys.size
            )

        sampled_points += int(
            local.size
        )
        if buffered_records >= 1_000_000:
            compact_blocks()

        _emit(
            progress,
            66
            + int(
                14
                * stop
                / total
            ),
            (
                "3D terrain normals: "
                f"{stop:,}/{total:,}"
            ),
        )

    compact_blocks()
    if not key_blocks:
        LOGGER.warning(
            "TERRAIN3D_DISABLED no_valid_normals"
        )
        return None

    keys = key_blocks[0]
    values = value_blocks[0]
    count = values[:, 0].astype(
        np.float64,
        copy=False,
    )
    ground_fraction = (
        values[:, 1]
        / np.maximum(
            count,
            1.0,
        )
    )

    centroids = (
        values[:, 2:5]
        / count[:, None]
    )
    centroids[:, 0] += min_x
    centroids[:, 1] += min_y
    centroids[:, 2] += min_z

    matrices = np.empty(
        (keys.size, 3, 3),
        dtype=np.float32,
    )
    matrices[:, 0, 0] = (
        values[:, 5] / count
    )
    matrices[:, 1, 1] = (
        values[:, 6] / count
    )
    matrices[:, 2, 2] = (
        values[:, 7] / count
    )
    matrices[:, 0, 1] = (
        values[:, 8] / count
    )
    matrices[:, 1, 0] = (
        matrices[:, 0, 1]
    )
    matrices[:, 0, 2] = (
        values[:, 9] / count
    )
    matrices[:, 2, 0] = (
        matrices[:, 0, 2]
    )
    matrices[:, 1, 2] = (
        values[:, 10] / count
    )
    matrices[:, 2, 1] = (
        matrices[:, 1, 2]
    )

    eigenvalues, eigenvectors = (
        np.linalg.eigh(matrices)
    )
    normals = eigenvectors[
        :,
        :,
        -1,
    ].astype(
        np.float32,
        copy=False,
    )
    trace = np.maximum(
        np.sum(
            eigenvalues,
            axis=1,
        ),
        1e-6,
    )
    coherence = (
        eigenvalues[:, -1]
        / trace
    )

    candidate = (
        count >= params.min_points
    ) & (
        coherence >= params.coherence
    )
    seed = candidate & (
        ground_fraction
        >= params.seed_ground_fraction
    )

    _emit(
        progress,
        82,
        (
            "3D terrain grow: "
            f"{int(np.count_nonzero(seed)):,} seeds"
        ),
    )
    accepted = _grow_surface(
        keys,
        centroids.astype(
            np.float32,
            copy=False,
        ),
        normals,
        candidate,
        seed,
        nx,
        ny,
        nz,
        params.max_normal_angle_deg,
    )

    terrain_keys = keys[
        accepted
    ].astype(
        np.int64,
        copy=False,
    )
    terrain_centroids = centroids[
        accepted
    ].astype(
        np.float32,
        copy=False,
    )
    terrain_normals = normals[
        accepted
    ].astype(
        np.float32,
        copy=False,
    )

    LOGGER.info(
        "TERRAIN3D_DONE sampled=%d occupied=%d candidates=%d "
        "seeds=%d terrain_voxels=%d coherence=%.3f angle=%.1f",
        sampled_points,
        keys.size,
        int(np.count_nonzero(candidate)),
        int(np.count_nonzero(seed)),
        terrain_keys.size,
        params.coherence,
        params.max_normal_angle_deg,
    )
    _emit(
        progress,
        86,
        (
            "3D terrain ready: "
            f"{terrain_keys.size:,} surface voxels"
        ),
    )

    return Terrain3DRefinement(
        params=params,
        min_x=min_x,
        min_y=min_y,
        min_z=min_z,
        nx=nx,
        ny=ny,
        nz=nz,
        keys=terrain_keys,
        centroids=terrain_centroids,
        normals=terrain_normals,
        sampled_points=sampled_points,
        candidate_voxels=int(
            np.count_nonzero(candidate)
        ),
        seed_voxels=int(
            np.count_nonzero(seed)
        ),
    )
