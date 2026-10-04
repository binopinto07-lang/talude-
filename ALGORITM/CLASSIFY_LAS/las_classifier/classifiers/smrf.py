from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from functools import lru_cache
from math import ceil, floor, sqrt
from time import perf_counter
from typing import Callable, Iterator

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import (
    binary_fill_holes,
    convolve,
    distance_transform_edt,
    grey_opening,
    map_coordinates,
)

from ..cloud.model import CloudModel
from .terrain3d import (
    Terrain3DParams,
    Terrain3DRefinement,
    build_terrain3d_refinement,
)


LOGGER = logging.getLogger("las_cafiisica.classifiers.smrf")

GROUND_CLASS = np.uint8(2)
NON_GROUND_CLASS = np.uint8(1)
ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True, slots=True)
class SMRFParams:
    cell: float = 1.0
    slope: float = 0.15
    window: float = 18.0
    threshold: float = 0.5
    scalar: float = 1.25
    fill_spacing: float = 0.25
    chunk_size: int = 2_000_000
    max_grid_cells: int = 8_000_000
    max_fill_points: int = 8_000_000
    inpaint_iterations: int = 300
    inpaint_tolerance: float = 0.001
    terrain3d_enabled: bool = True
    terrain3d_voxel: float = 0.50
    terrain3d_surface_thickness: float = 0.22
    terrain3d_coherence: float = 0.62
    terrain3d_max_normal_angle_deg: float = 88.0

    def validate(self) -> None:
        if self.cell <= 0:
            raise ValueError("SMRF cell must be greater than zero")
        if self.slope < 0:
            raise ValueError("SMRF slope cannot be negative")
        if self.window <= 0:
            raise ValueError("SMRF window must be greater than zero")
        if self.threshold < 0:
            raise ValueError("SMRF threshold cannot be negative")
        if self.scalar < 0:
            raise ValueError("SMRF scalar cannot be negative")
        if self.fill_spacing <= 0:
            raise ValueError("SMRF fill_spacing must be greater than zero")
        if self.chunk_size < 1:
            raise ValueError("SMRF chunk_size must be positive")
        if self.max_grid_cells < 1:
            raise ValueError("SMRF max_grid_cells must be positive")
        if self.max_fill_points < 1:
            raise ValueError("SMRF max_fill_points must be positive")
        if self.inpaint_iterations < 1:
            raise ValueError("SMRF inpaint_iterations must be positive")
        if self.inpaint_tolerance <= 0:
            raise ValueError("SMRF inpaint_tolerance must be positive")
        if self.terrain3d_voxel <= 0:
            raise ValueError("terrain3d_voxel must be positive")
        if self.terrain3d_surface_thickness <= 0:
            raise ValueError("terrain3d_surface_thickness must be positive")
        if not 0.0 < self.terrain3d_coherence <= 1.0:
            raise ValueError("terrain3d_coherence must be in (0, 1]")
        if not 0.0 < self.terrain3d_max_normal_angle_deg < 90.0:
            raise ValueError(
                "terrain3d_max_normal_angle_deg must be in (0, 90)"
            )


@dataclass(frozen=True, slots=True)
class SMRFModel:
    params: SMRFParams
    min_x: float
    min_y: float
    rows: int
    cols: int
    ground_surface: NDArray[np.float32]
    slope_surface: NDArray[np.float32]
    object_cell_mask: NDArray[np.bool_]
    inpaint_cell_mask: NDArray[np.bool_]
    fill_cell_mask: NDArray[np.bool_]
    terrain3d: Terrain3DRefinement | None = None

    def _grid_coordinates(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        cols = (x - self.min_x) / self.params.cell
        rows = (y - self.min_y) / self.params.cell
        np.clip(cols, 0.0, self.cols - 1.0, out=cols)
        np.clip(rows, 0.0, self.rows - 1.0, out=rows)
        return rows, cols

    def _cell_indices(
        self,
        rows: NDArray[np.float64],
        cols: NDArray[np.float64],
    ) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
        ri = np.floor(rows).astype(np.int64, copy=False)
        ci = np.floor(cols).astype(np.int64, copy=False)
        np.clip(ri, 0, self.rows - 1, out=ri)
        np.clip(ci, 0, self.cols - 1, out=ci)
        return ri, ci

    @staticmethod
    def _sample(
        surface: NDArray[np.float32],
        rows: NDArray[np.float64],
        cols: NDArray[np.float64],
    ) -> NDArray[np.float64]:
        return map_coordinates(
            surface,
            [rows, cols],
            order=1,
            mode="nearest",
            prefilter=False,
        ).astype(np.float64, copy=False)

    def classify_xyz(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        z: NDArray[np.float64],
    ) -> NDArray[np.uint8]:
        """Classify against the filled terrain surface.

        A steep face must not automatically receive a huge vertical tolerance.
        We therefore use point-to-local-plane distance and cap the slope bonus
        by the user SMRF slope parameter. Cells that SMRF itself removed and
        inpainted use the stricter vertical threshold, which rejects tree /
        shrub points while still accepting true returns close to the rebuilt
        terrain.
        """

        rows, cols = self._grid_coordinates(x, y)
        terrain_z = self._sample(
            self.ground_surface,
            rows,
            cols,
        )
        local_slope = self._sample(
            self.slope_surface,
            rows,
            cols,
        )

        vertical_residual = np.abs(
            z - terrain_z
        )
        normal_distance = vertical_residual / np.sqrt(
            1.0 + local_slope * local_slope
        )

        capped_slope = np.minimum(
            local_slope,
            self.params.slope,
        )
        tolerance = (
            self.params.threshold
            + self.params.scalar
            * capped_slope
            * self.params.cell
        )
        ground = normal_distance <= tolerance

        ri, ci = self._cell_indices(rows, cols)
        rebuilt = self.inpaint_cell_mask[ri, ci]
        if np.any(rebuilt):
            ground[rebuilt] &= (
                vertical_residual[rebuilt]
                <= self.params.threshold
            )

        if self.terrain3d is not None:
            terrain3d_ground = self.terrain3d.ground_mask(
                x,
                y,
                z,
            )

            # Keep only extremely reliable 2.5D ground outside the 3D grown
            # surface. Steep talude faces are decided by the 3D surface model,
            # while flat measured terrain remains safe even where normals are
            # sparse or missing.
            stable_base = (
                ground
                & (~rebuilt)
                & (
                    local_slope
                    <= max(
                        self.params.slope * 2.0,
                        0.30,
                    )
                )
                & (
                    vertical_residual
                    <= min(
                        self.params.threshold * 0.65,
                        0.35,
                    )
                )
            )
            ground = (
                terrain3d_ground
                | stable_base
            )

        classes = np.full(
            z.shape[0],
            NON_GROUND_CLASS,
            dtype=np.uint8,
        )
        classes[ground] = GROUND_CLASS
        return classes

    @property
    def fill_cell_count(self) -> int:
        return int(np.count_nonzero(self.fill_cell_mask))

    @property
    def fill_divisions(self) -> int:
        cells = self.fill_cell_count
        if cells == 0:
            return 0

        requested = max(
            1,
            int(ceil(
                self.params.cell
                / self.params.fill_spacing
            )),
        )
        permitted = max(
            1,
            int(floor(sqrt(
                self.params.max_fill_points
                / cells
            ))),
        )
        return min(requested, permitted)

    @property
    def effective_fill_spacing(self) -> float:
        divisions = self.fill_divisions
        if divisions == 0:
            return self.params.fill_spacing
        return self.params.cell / divisions

    @property
    def synthetic_fill_point_count(self) -> int:
        divisions = self.fill_divisions
        return (
            self.fill_cell_count
            * divisions
            * divisions
        )

    def iter_synthetic_fill_xyz(
        self,
        chunk_points: int = 500_000,
    ) -> Iterator[
        tuple[
            NDArray[np.float64],
            NDArray[np.float64],
            NDArray[np.float64],
        ]
    ]:
        """Generate interpolated class-2 points inside rebuilt terrain cells."""

        divisions = self.fill_divisions
        if divisions == 0:
            return

        rows, cols = np.nonzero(
            self.fill_cell_mask
        )
        per_cell = divisions * divisions
        cells_per_chunk = max(
            1,
            chunk_points // per_cell,
        )

        offsets = (
            np.arange(divisions, dtype=np.float64)
            + 0.5
        ) / divisions
        off_x, off_y = np.meshgrid(
            offsets,
            offsets,
        )
        off_x = off_x.ravel()
        off_y = off_y.ravel()

        for start in range(
            0,
            rows.size,
            cells_per_chunk,
        ):
            stop = min(
                start + cells_per_chunk,
                rows.size,
            )
            rr = rows[start:stop].astype(
                np.float64,
                copy=False,
            )
            cc = cols[start:stop].astype(
                np.float64,
                copy=False,
            )

            gx = (
                cc[:, None]
                + off_x[None, :]
            ).ravel()
            gy = (
                rr[:, None]
                + off_y[None, :]
            ).ravel()

            x = (
                self.min_x
                + gx * self.params.cell
            )
            y = (
                self.min_y
                + gy * self.params.cell
            )
            z = self._sample(
                self.ground_surface,
                gy,
                gx,
            )
            yield x, y, z


@dataclass(frozen=True, slots=True)
class SMRFResult:
    model: SMRFModel
    ground_count: int
    non_ground_count: int
    elapsed_seconds: float
    empty_cell_count: int
    interior_empty_cell_count: int
    low_outlier_cell_count: int
    object_cell_count: int
    inpainted_cell_count: int
    synthetic_fill_point_count: int
    terrain3d_voxel_count: int = 0
    terrain3d_seed_voxel_count: int = 0

    @property
    def point_count(self) -> int:
        return self.ground_count + self.non_ground_count


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


def _scaled_chunk(
    raw: NDArray,
    scale: float,
    offset: float,
) -> NDArray[np.float64]:
    values = np.asarray(
        raw,
        dtype=np.float64,
    )
    values *= float(scale)
    values += float(offset)
    return values


def _iter_scaled_xyz(
    cloud: CloudModel,
    chunk_size: int,
):
    total = cloud.point_count
    scales = cloud.las.header.scales
    offsets = cloud.las.header.offsets
    raw_x = cloud.las.X
    raw_y = cloud.las.Y
    raw_z = cloud.las.Z

    for start in range(
        0,
        total,
        chunk_size,
    ):
        stop = min(
            start + chunk_size,
            total,
        )
        yield (
            start,
            stop,
            _scaled_chunk(
                raw_x[start:stop],
                scales[0],
                offsets[0],
            ),
            _scaled_chunk(
                raw_y[start:stop],
                scales[1],
                offsets[1],
            ),
            _scaled_chunk(
                raw_z[start:stop],
                scales[2],
                offsets[2],
            ),
        )


@lru_cache(maxsize=64)
def _disk(
    radius: int,
) -> NDArray[np.bool_]:
    yy, xx = np.ogrid[
        -radius : radius + 1,
        -radius : radius + 1,
    ]
    return (
        xx * xx + yy * yy
    ) <= radius * radius


def _nearest_fill(
    surface: NDArray[np.float32],
    missing: NDArray[np.bool_],
) -> NDArray[np.float32]:
    if not np.any(missing):
        return surface.astype(
            np.float32,
            copy=True,
        )
    if np.all(missing):
        raise ValueError(
            "SMRF grid contains no valid terrain cells"
        )

    indices = distance_transform_edt(
        missing,
        return_distances=False,
        return_indices=True,
    )
    nearest = surface[tuple(indices)]
    filled = surface.astype(
        np.float32,
        copy=True,
    )
    filled[missing] = nearest[missing]
    return filled


def _inpaint_surface(
    surface: NDArray[np.float32],
    missing: NDArray[np.bool_],
    params: SMRFParams,
) -> NDArray[np.float32]:
    """Harmonic/spring-like interpolation across removed terrain cells."""

    missing = (
        missing
        | ~np.isfinite(surface)
    )
    values = _nearest_fill(
        surface,
        missing,
    )
    if not np.any(missing):
        return values

    kernel = np.array(
        [
            [0.0, 0.25, 0.0],
            [0.25, 0.0, 0.25],
            [0.0, 0.25, 0.0],
        ],
        dtype=np.float32,
    )

    for _ in range(
        params.inpaint_iterations
    ):
        relaxed = convolve(
            values,
            kernel,
            mode="nearest",
        )
        old = values[missing].copy()
        values[missing] = relaxed[missing]
        delta = float(
            np.max(
                np.abs(
                    values[missing] - old
                )
            )
        )
        if (
            delta
            <= params.inpaint_tolerance
        ):
            break

    return values.astype(
        np.float32,
        copy=False,
    )


def _linear_extrapolate_pad(
    surface: NDArray[np.float32],
    pad: int,
) -> NDArray[np.float32]:
    """Extend edge gradients so steep terrain stays continuous at borders."""

    if pad <= 0:
        return surface

    rows, cols = surface.shape
    padded = np.pad(
        surface,
        ((pad, pad), (pad, pad)),
        mode="edge",
    ).astype(np.float32, copy=False)

    if cols > 1:
        left_step = surface[:, 1] - surface[:, 0]
        right_step = surface[:, -1] - surface[:, -2]
        for distance in range(1, pad + 1):
            padded[pad : pad + rows, pad - distance] = (
                surface[:, 0] - left_step * distance
            )
            padded[
                pad : pad + rows,
                pad + cols - 1 + distance,
            ] = (
                surface[:, -1] + right_step * distance
            )

    if rows > 1:
        top_step = padded[pad + 1, :] - padded[pad, :]
        bottom_step = (
            padded[pad + rows - 1, :]
            - padded[pad + rows - 2, :]
        )
        for distance in range(1, pad + 1):
            padded[pad - distance, :] = (
                padded[pad, :] - top_step * distance
            )
            padded[
                pad + rows - 1 + distance,
                :,
            ] = (
                padded[pad + rows - 1, :]
                + bottom_step * distance
            )

    return padded


def _opening_with_extrapolated_border(
    surface: NDArray[np.float32],
    radius: int,
) -> NDArray[np.float32]:
    padded = _linear_extrapolate_pad(surface, radius)
    opened = grey_opening(
        padded,
        footprint=_disk(radius),
        mode="nearest",
    ).astype(np.float32, copy=False)

    return opened[
        radius : -radius,
        radius : -radius,
    ]


def _progressive_object_mask(
    surface: NDArray[np.float32],
    *,
    cell: float,
    slope: float,
    window: float,
    progress: Callable[
        [int, int],
        None,
    ]
    | None = None,
) -> NDArray[np.bool_]:
    maximum = max(
        1,
        int(ceil(window / cell)),
    )
    last_surface = surface.astype(
        np.float32,
        copy=True,
    )
    object_mask = np.zeros(
        surface.shape,
        dtype=np.bool_,
    )

    for radius in range(
        1,
        maximum + 1,
    ):
        opened = _opening_with_extrapolated_border(
            last_surface,
            radius,
        )

        elevation_limit = (
            slope
            * radius
            * cell
        )
        object_mask |= (
            last_surface - opened
        ) > elevation_limit
        last_surface = opened

        if progress is not None:
            progress(
                radius,
                maximum,
            )

    return object_mask


def _build_minimum_surface(
    cloud: CloudModel,
    params: SMRFParams,
    min_x: float,
    min_y: float,
    rows: int,
    cols: int,
    progress: ProgressCallback | None,
) -> tuple[
    NDArray[np.float32],
    NDArray[np.bool_],
]:
    minimum_surface = np.full(
        (rows, cols),
        np.inf,
        dtype=np.float32,
    )
    flat_surface = minimum_surface.ravel()
    total = cloud.point_count

    for _, stop, x, y, z in _iter_scaled_xyz(
        cloud,
        params.chunk_size,
    ):
        col = np.floor(
            (x - min_x)
            / params.cell
        ).astype(
            np.int64,
            copy=False,
        )
        row = np.floor(
            (y - min_y)
            / params.cell
        ).astype(
            np.int64,
            copy=False,
        )
        np.clip(
            col,
            0,
            cols - 1,
            out=col,
        )
        np.clip(
            row,
            0,
            rows - 1,
            out=row,
        )
        flat = row * cols + col
        np.minimum.at(
            flat_surface,
            flat,
            z.astype(
                np.float32,
                copy=False,
            ),
        )
        _emit(
            progress,
            int(
                25
                * stop
                / total
            ),
            (
                "SMRF minimum surface: "
                f"{stop:,}/{total:,}"
            ),
        )

    empty = ~np.isfinite(
        minimum_surface
    )
    minimum_surface[empty] = np.nan
    return (
        minimum_surface,
        empty,
    )


def _surface_slope(
    ground_surface: NDArray[np.float32],
    cell: float,
) -> NDArray[np.float32]:
    gy, gx = np.gradient(
        ground_surface.astype(
            np.float64
        ),
        cell,
        cell,
    )
    slope = np.sqrt(
        gx * gx + gy * gy
    )
    return slope.astype(
        np.float32,
        copy=False,
    )


def run_smrf(
    cloud: CloudModel,
    params: SMRFParams | None = None,
    progress: ProgressCallback | None = None,
    *,
    count_full: bool = True,
) -> SMRFResult:
    params = params or SMRFParams()
    params.validate()
    if cloud.point_count == 0:
        raise ValueError(
            "Cannot classify an empty cloud"
        )

    started = perf_counter()
    header = cloud.las.header
    min_x = float(
        header.mins[0]
    )
    min_y = float(
        header.mins[1]
    )
    max_x = float(
        header.maxs[0]
    )
    max_y = float(
        header.maxs[1]
    )

    # Grid dimensions must match the cells that can actually contain input
    # points. ceil(extent / cell) + 1 creates an artificial empty strip when
    # the maximum coordinate falls inside a partial final cell (for example
    # 20.5 m with 1 m cells). That strip deforms the inpainted terrain and
    # causes false non-ground classifications along steep raster borders.
    cols = max(
        1,
        int(floor(
            (max_x - min_x)
            / params.cell
        ))
        + 1,
    )
    rows = max(
        1,
        int(floor(
            (max_y - min_y)
            / params.cell
        ))
        + 1,
    )
    grid_cells = rows * cols
    if (
        grid_cells
        > params.max_grid_cells
    ):
        raise ValueError(
            "SMRF raster would contain "
            f"{grid_cells:,} cells "
            f"({rows} x {cols}). "
            "Increase Cell."
        )

    LOGGER.info(
        "SMRF_START points=%d cell=%s slope=%s window=%s "
        "threshold=%s scalar=%s fill_spacing=%s grid=%dx%d "
        "algorithm=SMRF_PLUS_TERRAIN3D_R1",
        cloud.point_count,
        params.cell,
        params.slope,
        params.window,
        params.threshold,
        params.scalar,
        params.fill_spacing,
        rows,
        cols,
    )

    (
        minimum_surface,
        empty_cells,
    ) = _build_minimum_surface(
        cloud,
        params,
        min_x,
        min_y,
        rows,
        cols,
        progress,
    )

    occupied_cells = ~empty_cells
    interior_empty_cells = (
        binary_fill_holes(
            occupied_cells
        )
        & empty_cells
    )

    _emit(
        progress,
        26,
        "SMRF preparing minimum surface",
    )
    minimum_filled = _inpaint_surface(
        minimum_surface,
        empty_cells,
        params,
    )

    _emit(
        progress,
        28,
        "SMRF detecting low outliers",
    )
    low_outlier_cells = (
        _progressive_object_mask(
            -minimum_filled,
            cell=params.cell,
            slope=5.0,
            window=params.cell,
        )
    )

    def morphology_progress(
        radius: int,
        maximum: int,
    ) -> None:
        _emit(
            progress,
            (
                30
                + int(
                    25
                    * radius
                    / maximum
                )
            ),
            (
                "SMRF progressive morphology: "
                f"{radius}/{maximum}"
            ),
        )

    object_cells = (
        _progressive_object_mask(
            minimum_filled,
            cell=params.cell,
            slope=params.slope,
            window=params.window,
            progress=morphology_progress,
        )
    )

    inpaint_cells = (
        interior_empty_cells
        | low_outlier_cells
        | object_cells
    )
    provisional_surface = (
        minimum_filled.copy()
    )
    provisional_surface[
        inpaint_cells
    ] = np.nan

    _emit(
        progress,
        57,
        (
            "SMRF rebuilding terrain: "
            f"{int(np.count_nonzero(inpaint_cells)):,} cells"
        ),
    )
    ground_surface = _inpaint_surface(
        provisional_surface,
        inpaint_cells,
        params,
    )
    slope_surface = _surface_slope(
        ground_surface,
        params.cell,
    )

    fill_cells = (
        interior_empty_cells
        | low_outlier_cells
        | object_cells
    )

    base_model = SMRFModel(
        params=params,
        min_x=min_x,
        min_y=min_y,
        rows=rows,
        cols=cols,
        ground_surface=ground_surface,
        slope_surface=slope_surface,
        object_cell_mask=object_cells,
        inpaint_cell_mask=inpaint_cells,
        fill_cell_mask=fill_cells,
    )

    terrain3d = None
    if params.terrain3d_enabled:
        terrain3d = build_terrain3d_refinement(
            cloud,
            base_model,
            progress,
            Terrain3DParams(
                voxel=params.terrain3d_voxel,
                surface_thickness=(
                    params.terrain3d_surface_thickness
                ),
                coherence=params.terrain3d_coherence,
                max_normal_angle_deg=(
                    params.terrain3d_max_normal_angle_deg
                ),
            ),
        )

    model = replace(
        base_model,
        terrain3d=terrain3d,
    )

    LOGGER.info(
        "SMRF_FILL cells=%d points=%d spacing=%.4f",
        model.fill_cell_count,
        model.synthetic_fill_point_count,
        model.effective_fill_spacing,
    )
    _emit(
        progress,
        86 if terrain3d is not None else 64,
        (
            "SMRF + 3D terrain ready"
            if terrain3d is not None
            else "SMRF rebuilt terrain ready"
        ),
    )

    ground_count = 0
    total = cloud.point_count
    if count_full:
        for _, stop, x, y, z in _iter_scaled_xyz(
            cloud,
            params.chunk_size,
        ):
            classes = (
                model.classify_xyz(
                    x,
                    y,
                    z,
                )
            )
            ground_count += int(
                np.count_nonzero(
                    classes
                    == GROUND_CLASS
                )
            )
            _emit(
                progress,
                (
                    (
                        86
                        + int(
                            14
                            * stop
                            / total
                        )
                        if terrain3d is not None
                        else 64
                        + int(
                            36
                            * stop
                            / total
                        )
                    )
                ),
                (
                    "SMRF classify: "
                    f"{stop:,}/{total:,}"
                ),
            )

    non_ground_count = (
        total - ground_count
        if count_full
        else 0
    )
    elapsed = (
        perf_counter()
        - started
    )

    result = SMRFResult(
        model=model,
        ground_count=ground_count,
        non_ground_count=non_ground_count,
        elapsed_seconds=elapsed,
        empty_cell_count=int(
            np.count_nonzero(
                empty_cells
            )
        ),
        interior_empty_cell_count=int(
            np.count_nonzero(
                interior_empty_cells
            )
        ),
        low_outlier_cell_count=int(
            np.count_nonzero(
                low_outlier_cells
            )
        ),
        object_cell_count=int(
            np.count_nonzero(
                object_cells
            )
        ),
        inpainted_cell_count=int(
            np.count_nonzero(
                inpaint_cells
            )
        ),
        synthetic_fill_point_count=(
            model.synthetic_fill_point_count
        ),
        terrain3d_voxel_count=(
            terrain3d.terrain_voxel_count
            if terrain3d is not None
            else 0
        ),
        terrain3d_seed_voxel_count=(
            terrain3d.seed_voxels
            if terrain3d is not None
            else 0
        ),
    )

    LOGGER.info(
        "SMRF_RASTER empty=%d interior_empty=%d low_outlier=%d "
        "object=%d inpainted=%d synthetic_fill=%d terrain3d=%d",
        result.empty_cell_count,
        result.interior_empty_cell_count,
        result.low_outlier_cell_count,
        result.object_cell_count,
        result.inpainted_cell_count,
        result.synthetic_fill_point_count,
        result.terrain3d_voxel_count,
    )
    LOGGER.info(
        "SMRF_DONE ground=%d non_ground=%d elapsed=%.3fs",
        result.ground_count,
        result.non_ground_count,
        result.elapsed_seconds,
    )
    _emit(
        progress,
        100,
        "SMRF classification complete",
    )
    return result
