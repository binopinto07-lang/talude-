from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from typing import Callable

import numpy as np

from ..cloud.model import CloudModel
from .l3_context import IntensityProfile


LOGGER = logging.getLogger("las_cafiisica.terrain.dense_spatial_evidence")
ProgressCallback = Callable[[int, str], None]


def _emit(callback: ProgressCallback | None, percent: int, message: str) -> None:
    if callback is not None:
        callback(max(0, min(100, int(percent))), message)


def _dimension(points, name: str, dtype=None) -> np.ndarray | None:
    names = set(points.point_format.dimension_names)
    if name not in names:
        return None
    values = np.asarray(points[name])
    return values.astype(dtype, copy=False) if dtype is not None else values


def _flag_dimension(points, name: str) -> np.ndarray | None:
    try:
        return np.asarray(points[name], dtype=np.bool_)
    except Exception:
        return None


def _histogram_median(histogram: np.ndarray) -> float | None:
    histogram = np.asarray(histogram, dtype=np.uint64)
    total = int(np.sum(histogram, dtype=np.uint64))
    if total <= 0:
        return None
    cumulative = np.cumsum(histogram, dtype=np.uint64)
    return float(np.searchsorted(cumulative, ((total - 1) // 2) + 1, side="left"))


def _intensity_profile(histogram: np.ndarray) -> IntensityProfile | None:
    histogram = np.asarray(histogram, dtype=np.uint64)
    total = int(np.sum(histogram, dtype=np.uint64))
    if total < 32:
        return None
    median = _histogram_median(histogram)
    if median is None:
        return None
    bins = np.arange(histogram.size, dtype=np.int64)
    distance = np.abs(bins - int(round(median)))
    mad_hist = np.bincount(
        distance,
        weights=histogram.astype(np.float64, copy=False),
        minlength=histogram.size,
    )
    mad = _histogram_median(mad_hist.astype(np.uint64, copy=False))
    return IntensityProfile(
        median=float(median),
        mad=max(float(mad if mad is not None else 1.0), 1.0),
        sample_count=total,
    )


def _grid_geometry(
    mins: np.ndarray,
    maxs: np.ndarray,
    *,
    spacing: float,
    max_cells: int,
) -> tuple[np.ndarray, float, int, int]:
    mins = np.asarray(mins, dtype=np.float64)
    maxs = np.asarray(maxs, dtype=np.float64)
    if mins.shape != (2,) or maxs.shape != (2,):
        raise ValueError("Dense grid bounds must be 2-D XY vectors")
    if not np.all(np.isfinite(mins)) or not np.all(np.isfinite(maxs)):
        raise ValueError("Dense grid bounds must be finite")
    if np.any(maxs < mins):
        raise ValueError("Dense grid max bounds must be >= min bounds")

    cell_size = max(0.35, min(0.90, max(float(spacing), 0.005) * 8.0))
    max_cells = max(10_000, int(max_cells))
    for _ in range(4):
        origin = np.floor(mins / cell_size) * cell_size
        nx = max(1, int(np.ceil((maxs[0] - origin[0]) / cell_size)) + 2)
        ny = max(1, int(np.ceil((maxs[1] - origin[1]) / cell_size)) + 2)
        cells = int(nx) * int(ny)
        if cells <= max_cells:
            return origin, float(cell_size), int(nx), int(ny)
        cell_size *= np.sqrt(cells / max_cells) * 1.001

    origin = np.floor(mins / cell_size) * cell_size
    nx = max(1, int(np.ceil((maxs[0] - origin[0]) / cell_size)) + 2)
    ny = max(1, int(np.ceil((maxs[1] - origin[1]) / cell_size)) + 2)
    return origin, float(cell_size), int(nx), int(ny)


@dataclass(slots=True)
class DenseSpatialEvidenceGrid:
    """R19-A spatial evidence built from every measured return.

    Exact-cell absence is not treated as physical absence: queries aggregate a
    local 3x3 neighbourhood by default (5x5 is available with radius=2).
    """

    origin: np.ndarray
    cell_size: float
    nx: int
    ny: int
    strong_threshold: float
    neighbourhood_radius: int
    point_count: np.ndarray
    strong_count: np.ndarray
    geometry_count: np.ndarray
    min_z: np.ndarray
    max_z: np.ndarray
    min_vertical_residual: np.ndarray
    max_vertical_residual: np.ndarray
    ptd_score_sum: np.ndarray
    plane_distance_sum: np.ndarray
    class2_count: np.ndarray
    return_only_count: np.ndarray
    return_last_multi_count: np.ndarray
    return_first_multi_count: np.ndarray
    return_intermediate_count: np.ndarray
    return_invalid_count: np.ndarray
    intensity_sum: np.ndarray
    intensity_count: np.ndarray
    invalid_count: np.ndarray
    intensity_histogram: np.ndarray
    intensity_profile: IntensityProfile | None = None
    processed_points: int = 0

    @classmethod
    def create(
        cls,
        mins: np.ndarray,
        maxs: np.ndarray,
        *,
        spacing: float,
        strong_threshold: float,
        max_cells: int = 3_000_000,
        neighbourhood_radius: int = 1,
    ) -> "DenseSpatialEvidenceGrid":
        origin, cell_size, nx, ny = _grid_geometry(
            mins,
            maxs,
            spacing=spacing,
            max_cells=max_cells,
        )
        cells = int(nx) * int(ny)
        radius = max(0, min(2, int(neighbourhood_radius)))
        return cls(
            origin=origin,
            cell_size=cell_size,
            nx=nx,
            ny=ny,
            strong_threshold=float(strong_threshold),
            neighbourhood_radius=radius,
            point_count=np.zeros(cells, dtype=np.int32),
            strong_count=np.zeros(cells, dtype=np.int32),
            geometry_count=np.zeros(cells, dtype=np.int32),
            min_z=np.full(cells, np.inf, dtype=np.float32),
            max_z=np.full(cells, -np.inf, dtype=np.float32),
            min_vertical_residual=np.full(cells, np.inf, dtype=np.float32),
            max_vertical_residual=np.full(cells, -np.inf, dtype=np.float32),
            ptd_score_sum=np.zeros(cells, dtype=np.float64),
            plane_distance_sum=np.zeros(cells, dtype=np.float64),
            class2_count=np.zeros(cells, dtype=np.int32),
            return_only_count=np.zeros(cells, dtype=np.int32),
            return_last_multi_count=np.zeros(cells, dtype=np.int32),
            return_first_multi_count=np.zeros(cells, dtype=np.int32),
            return_intermediate_count=np.zeros(cells, dtype=np.int32),
            return_invalid_count=np.zeros(cells, dtype=np.int32),
            intensity_sum=np.zeros(cells, dtype=np.float64),
            intensity_count=np.zeros(cells, dtype=np.int32),
            invalid_count=np.zeros(cells, dtype=np.int32),
            intensity_histogram=np.zeros(65_536, dtype=np.uint64),
        )

    @property
    def cell_count(self) -> int:
        return int(self.nx) * int(self.ny)

    @property
    def occupied_cell_count(self) -> int:
        return int(np.count_nonzero(self.point_count))

    @property
    def coverage_fraction(self) -> float:
        return self.occupied_cell_count / self.cell_count if self.cell_count else 0.0

    @property
    def measured_point_count(self) -> int:
        return int(np.sum(self.point_count, dtype=np.int64))

    @property
    def geometry_point_count(self) -> int:
        return int(np.sum(self.geometry_count, dtype=np.int64))

    @property
    def memory_bytes(self) -> int:
        return sum(
            int(getattr(self, item.name).nbytes)
            for item in fields(self)
            if isinstance(getattr(self, item.name), np.ndarray)
        )

    def _flat_keys(
        self,
        x: np.ndarray,
        y: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        finite = np.isfinite(x) & np.isfinite(y)
        ix = np.zeros(x.shape[0], dtype=np.int64)
        iy = np.zeros(y.shape[0], dtype=np.int64)
        ix[finite] = np.floor((x[finite] - self.origin[0]) / self.cell_size).astype(np.int64)
        iy[finite] = np.floor((y[finite] - self.origin[1]) / self.cell_size).astype(np.int64)
        inside = finite & (ix >= 0) & (iy >= 0) & (ix < self.nx) & (iy < self.ny)
        return ix + self.nx * iy, inside, ix, iy

    @staticmethod
    def _add_counts(
        target: np.ndarray,
        unique: np.ndarray,
        inverse: np.ndarray,
        weights: np.ndarray | None = None,
    ) -> None:
        counts = np.bincount(
            inverse,
            weights=None if weights is None else np.asarray(weights, dtype=np.float64),
            minlength=unique.size,
        )
        target[unique] += counts.astype(target.dtype, copy=False)

    def accumulate(
        self,
        *,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
        ptd_score: np.ndarray,
        vertical_residual: np.ndarray,
        plane_distance: np.ndarray,
        valid_mask: np.ndarray | None = None,
        geometry_mask: np.ndarray | None = None,
        original_class: np.ndarray | None = None,
        return_number: np.ndarray | None = None,
        number_of_returns: np.ndarray | None = None,
        intensity: np.ndarray | None = None,
        invalid_mask: np.ndarray | None = None,
    ) -> None:
        """Record measured presence independently of PTD/TIN validity.

        An unsupported TIN facet is missing *geometric evidence*, not missing
        physical points. Only supported PTD observations contribute to
        confidence/plane/residual aggregates. Original class and return data
        never promote unsupported observations into Ground by themselves.
        """
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        z = np.asarray(z, dtype=np.float64)
        ptd = np.asarray(ptd_score, dtype=np.float64)
        vertical = np.asarray(vertical_residual, dtype=np.float64)
        plane = np.asarray(plane_distance, dtype=np.float64)
        count = x.shape[0]
        if any(a.shape != x.shape for a in (y, z, ptd, vertical, plane)):
            raise ValueError("Dense spatial evidence input shape mismatch")

        keys, inside, _, _ = self._flat_keys(x, y)
        self.processed_points += int(count)
        hard_invalid = (
            np.zeros(count, dtype=np.bool_)
            if invalid_mask is None
            else np.asarray(invalid_mask, dtype=np.bool_).copy()
        )
        if hard_invalid.shape != x.shape:
            raise ValueError("Dense spatial evidence invalid mask shape mismatch")
        hard_invalid |= ~np.isfinite(z)

        invalid_ids = np.flatnonzero(inside & hard_invalid)
        if invalid_ids.size:
            unique, inverse = np.unique(keys[invalid_ids], return_inverse=True)
            self._add_counts(self.invalid_count, unique, inverse)

        measured = inside & ~hard_invalid
        if valid_mask is not None:
            supplied = np.asarray(valid_mask, dtype=np.bool_)
            if supplied.shape != x.shape:
                raise ValueError("Dense spatial evidence measured mask shape mismatch")
            measured &= supplied

        ids = np.flatnonzero(measured)
        if not ids.size:
            return

        cell_keys = keys[ids]
        unique, inverse = np.unique(cell_keys, return_inverse=True)
        self._add_counts(self.point_count, unique, inverse)

        local_min_z = np.full(unique.size, np.inf, dtype=np.float64)
        local_max_z = np.full(unique.size, -np.inf, dtype=np.float64)
        np.minimum.at(local_min_z, inverse, z[ids])
        np.maximum.at(local_max_z, inverse, z[ids])
        self.min_z[unique] = np.minimum(self.min_z[unique], local_min_z)
        self.max_z[unique] = np.maximum(self.max_z[unique], local_max_z)

        geometry = measured & np.isfinite(ptd) & np.isfinite(vertical) & np.isfinite(plane)
        if geometry_mask is not None:
            supplied = np.asarray(geometry_mask, dtype=np.bool_)
            if supplied.shape != x.shape:
                raise ValueError("Dense spatial evidence geometry mask shape mismatch")
            geometry &= supplied
        gids = np.flatnonzero(geometry)
        strong_gids = np.empty(0, dtype=np.int64)
        if gids.size:
            gkeys, ginverse = np.unique(keys[gids], return_inverse=True)
            self._add_counts(self.geometry_count, gkeys, ginverse)
            strong = ptd[gids] >= self.strong_threshold
            self._add_counts(self.strong_count, gkeys, ginverse, strong)
            strong_gids = gids[strong]

            local_min_v = np.full(gkeys.size, np.inf, dtype=np.float64)
            local_max_v = np.full(gkeys.size, -np.inf, dtype=np.float64)
            np.minimum.at(local_min_v, ginverse, vertical[gids])
            np.maximum.at(local_max_v, ginverse, vertical[gids])
            self.min_vertical_residual[gkeys] = np.minimum(
                self.min_vertical_residual[gkeys], local_min_v
            )
            self.max_vertical_residual[gkeys] = np.maximum(
                self.max_vertical_residual[gkeys], local_max_v
            )
            self.ptd_score_sum[gkeys] += np.bincount(
                ginverse, weights=ptd[gids], minlength=gkeys.size
            )
            self.plane_distance_sum[gkeys] += np.bincount(
                ginverse, weights=plane[gids], minlength=gkeys.size
            )

        if original_class is not None:
            classes = np.asarray(original_class)
            if classes.shape != x.shape:
                raise ValueError("Dense spatial evidence class shape mismatch")
            self._add_counts(self.class2_count, unique, inverse, classes[ids] == 2)

        if return_number is not None and number_of_returns is not None:
            rn = np.asarray(return_number, dtype=np.int16)
            nr = np.asarray(number_of_returns, dtype=np.int16)
            if rn.shape != x.shape or nr.shape != x.shape:
                raise ValueError("Dense spatial evidence return shape mismatch")
            vrn = rn[ids]
            vnr = nr[ids]
            return_valid = (vrn > 0) & (vnr > 0) & (vrn <= vnr)
            masks = (
                (return_valid & (vrn == 1) & (vnr == 1), self.return_only_count),
                (return_valid & (vnr > 1) & (vrn == vnr), self.return_last_multi_count),
                (return_valid & (vnr > 1) & (vrn == 1), self.return_first_multi_count),
                (return_valid & (vrn > 1) & (vrn < vnr), self.return_intermediate_count),
                (~return_valid, self.return_invalid_count),
            )
            for mask, target in masks:
                self._add_counts(target, unique, inverse, mask)

        if intensity is not None:
            values = np.asarray(intensity, dtype=np.float64)
            if values.shape != x.shape:
                raise ValueError("Dense spatial evidence intensity shape mismatch")
            finite_i = np.isfinite(values[ids])
            if np.any(finite_i):
                intensity_ids = ids[finite_i]
                i_unique, i_inverse = np.unique(keys[intensity_ids], return_inverse=True)
                self._add_counts(self.intensity_count, i_unique, i_inverse)
                self.intensity_sum[i_unique] += np.bincount(
                    i_inverse, weights=values[intensity_ids], minlength=i_unique.size
                )
            strong_values = values[strong_gids]
            finite_strong = np.isfinite(strong_values)
            if np.any(finite_strong):
                bins = np.clip(
                    np.rint(strong_values[finite_strong]), 0, 65_535
                ).astype(np.int64)
                self.intensity_histogram += np.bincount(
                    bins, minlength=65_536
                ).astype(np.uint64, copy=False)

    def finalize(self) -> None:
        self.intensity_profile = _intensity_profile(self.intensity_histogram)

    def query_with_presence(
        self,
        x: np.ndarray,
        y: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        count = x.shape[0]
        if y.shape[0] != count:
            raise ValueError("Dense spatial query length mismatch")

        local_count = np.zeros(count, dtype=np.int64)
        strong_count = np.zeros(count, dtype=np.int64)
        geometry_count = np.zeros(count, dtype=np.int64)
        minimum = np.full(count, np.inf, dtype=np.float64)
        maximum = np.full(count, -np.inf, dtype=np.float64)
        plane_sum = np.zeros(count, dtype=np.float64)
        _, inside, ix, iy = self._flat_keys(x, y)
        ids = np.flatnonzero(inside)

        radius = self.neighbourhood_radius
        for dy in range(-radius, radius + 1):
            jy = iy[ids] + dy
            for dx in range(-radius, radius + 1):
                jx = ix[ids] + dx
                valid = (jx >= 0) & (jx < self.nx) & (jy >= 0) & (jy < self.ny)
                if not np.any(valid):
                    continue
                target = ids[valid]
                keys = jx[valid] + self.nx * jy[valid]
                cells = self.point_count[keys].astype(np.int64, copy=False)
                local_count[target] += cells
                strong_count[target] += self.strong_count[keys]
                geometry = self.geometry_count[keys] > 0
                geometry_count[target] += self.geometry_count[keys]
                if np.any(geometry):
                    target_o = target[geometry]
                    keys_o = keys[geometry]
                    minimum[target_o] = np.minimum(
                        minimum[target_o], self.min_vertical_residual[keys_o]
                    )
                    maximum[target_o] = np.maximum(
                        maximum[target_o], self.max_vertical_residual[keys_o]
                    )
                    plane_sum[target_o] += self.plane_distance_sum[keys_o]

        present = local_count > 0
        support = np.zeros(count, dtype=np.float64)
        spread = np.zeros(count, dtype=np.float64)
        roughness = np.zeros(count, dtype=np.float64)
        support[present] = strong_count[present] / local_count[present]
        supported_geometry = geometry_count > 0
        spread[supported_geometry] = np.maximum(
            0.0, maximum[supported_geometry] - minimum[supported_geometry]
        )
        roughness[supported_geometry] = (
            plane_sum[supported_geometry] / geometry_count[supported_geometry]
        )
        return (
            support.astype(np.float32, copy=False),
            spread.astype(np.float32, copy=False),
            roughness.astype(np.float32, copy=False),
            present,
        )

    def query_low_z(
        self,
        x: np.ndarray,
        y: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Median of occupied-cell minima in the configured neighbourhood."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        count = x.shape[0]
        _, inside, ix, iy = self._flat_keys(x, y)
        samples: list[np.ndarray] = []
        radius = self.neighbourhood_radius

        for dy in range(-radius, radius + 1):
            jy = iy + dy
            for dx in range(-radius, radius + 1):
                jx = ix + dx
                valid = (
                    inside
                    & (jx >= 0)
                    & (jx < self.nx)
                    & (jy >= 0)
                    & (jy < self.ny)
                )
                sample = np.full(count, np.nan, dtype=np.float64)
                if np.any(valid):
                    target = np.flatnonzero(valid)
                    keys = jx[valid] + self.nx * jy[valid]
                    occupied = self.point_count[keys] > 0
                    if np.any(occupied):
                        sample[target[occupied]] = self.min_z[keys[occupied]]
                samples.append(sample)

        if not samples:
            return (
                np.full(count, np.nan, dtype=np.float32),
                np.zeros(count, dtype=np.bool_),
            )
        stack = np.vstack(samples)
        present = np.any(np.isfinite(stack), axis=0)
        with np.errstate(all="ignore"):
            low_z = np.nanmedian(stack, axis=0)
        low_z[~present] = np.nan
        return low_z.astype(np.float32, copy=False), present

    def query_z_spread(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        count = x.shape[0]
        minimum = np.full(count, np.inf, dtype=np.float64)
        maximum = np.full(count, -np.inf, dtype=np.float64)
        _, inside, ix, iy = self._flat_keys(x, y)
        radius = self.neighbourhood_radius

        for dy in range(-radius, radius + 1):
            jy = iy + dy
            for dx in range(-radius, radius + 1):
                jx = ix + dx
                valid = (
                    inside
                    & (jx >= 0)
                    & (jx < self.nx)
                    & (jy >= 0)
                    & (jy < self.ny)
                )
                if not np.any(valid):
                    continue
                target = np.flatnonzero(valid)
                keys = jx[valid] + self.nx * jy[valid]
                occupied = self.point_count[keys] > 0
                if not np.any(occupied):
                    continue
                target = target[occupied]
                keys = keys[occupied]
                minimum[target] = np.minimum(minimum[target], self.min_z[keys])
                maximum[target] = np.maximum(maximum[target], self.max_z[keys])

        present = np.isfinite(minimum) & np.isfinite(maximum)
        spread = np.zeros(count, dtype=np.float64)
        spread[present] = np.maximum(0.0, maximum[present] - minimum[present])
        return spread.astype(np.float32, copy=False)

    def query(
        self,
        x: np.ndarray,
        y: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        support, spread, roughness, _ = self.query_with_presence(x, y)
        return support, spread, roughness


def build_dense_spatial_evidence_grid(
    cloud: CloudModel,
    ptd_model,
    *,
    chunk_size: int = 750_000,
    max_cells: int = 3_000_000,
    neighbourhood_radius: int = 1,
    progress: ProgressCallback | None = None,
) -> DenseSpatialEvidenceGrid:
    spacing = max(float(ptd_model.analysis.median_spacing), 0.005)
    strong_threshold = max(0.68, float(ptd_model.params.confidence_threshold))
    grid = DenseSpatialEvidenceGrid.create(
        np.asarray(cloud.las.header.mins[:2], dtype=np.float64),
        np.asarray(cloud.las.header.maxs[:2], dtype=np.float64),
        spacing=spacing,
        strong_threshold=strong_threshold,
        max_cells=max_cells,
        neighbourhood_radius=neighbourhood_radius,
    )

    total = int(cloud.point_count)
    scales = cloud.las.header.scales
    offsets = cloud.las.header.offsets
    chunk_size = max(50_000, int(chunk_size))
    LOGGER.info(
        "R19_DENSE_GRID_START points=%d cell=%.4f nx=%d ny=%d cells=%d "
        "radius=%d ram_mb=%.1f",
        total,
        grid.cell_size,
        grid.nx,
        grid.ny,
        grid.cell_count,
        grid.neighbourhood_radius,
        grid.memory_bytes / (1024.0 * 1024.0),
    )
    _emit(progress, 47, "R19 dense context: streaming all measured returns")

    for start in range(0, total, chunk_size):
        stop = min(start + chunk_size, total)
        points = cloud.las.points[start:stop]
        x = np.asarray(points.X, dtype=np.float64) * scales[0] + offsets[0]
        y = np.asarray(points.Y, dtype=np.float64) * scales[1] + offsets[1]
        z = np.asarray(points.Z, dtype=np.float64) * scales[2] + offsets[2]

        # Spatial authority is geometric and measured-return based. Avoid making
        # source LAS normals mandatory for the dense context itself.
        ptd_score, metrics = ptd_model._confidence(x, y, z, points=None)
        valid = (
            np.asarray(metrics["valid"], dtype=np.bool_)
            & np.isfinite(metrics["vertical_residual"])
            & np.isfinite(metrics["plane_distance"])
            & np.isfinite(x)
            & np.isfinite(y)
            & np.isfinite(z)
        )
        invalid = ~np.isfinite(x) | ~np.isfinite(y) | ~np.isfinite(z)
        withheld = _flag_dimension(points, "withheld")
        synthetic = _flag_dimension(points, "synthetic")
        if withheld is not None:
            invalid |= withheld
        if synthetic is not None:
            invalid |= synthetic

        grid.accumulate(
            x=x,
            y=y,
            z=z,
            ptd_score=ptd_score,
            vertical_residual=metrics["vertical_residual"],
            plane_distance=metrics["plane_distance"],
            valid_mask=~invalid,
            geometry_mask=valid,
            original_class=_dimension(points, "classification", dtype=np.uint8),
            return_number=_dimension(points, "return_number", dtype=np.uint8),
            number_of_returns=_dimension(points, "number_of_returns", dtype=np.uint8),
            intensity=_dimension(points, "intensity"),
            invalid_mask=invalid,
        )
        _emit(
            progress,
            47 + int(9 * stop / max(1, total)),
            f"R19 dense context: {stop:,}/{total:,} returns",
        )

    grid.finalize()
    LOGGER.info(
        "R19_DENSE_GRID_DONE processed=%d occupied=%d/%d coverage=%.4f "
        "ram_mb=%.1f measured=%d ptd_geometry=%d ptd_fraction=%.4f intensity_profile=%s",
        grid.processed_points,
        grid.occupied_cell_count,
        grid.cell_count,
        grid.coverage_fraction,
        grid.memory_bytes / (1024.0 * 1024.0),
        grid.measured_point_count,
        grid.geometry_point_count,
        grid.geometry_point_count / max(1, grid.measured_point_count),
        "yes" if grid.intensity_profile is not None else "no",
    )
    return grid
