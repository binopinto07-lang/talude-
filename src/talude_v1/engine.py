from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy import ndimage
from scipy.signal import savgol_filter
from scipy.spatial import cKDTree

from .config import ExtractConfig
from .io import (
    PointCloud,
    PointCloudInfo,
    inspect_point_cloud,
    iter_point_chunks,
    load_point_cloud,
    save_dxf,
    save_geojson,
    save_vertices_csv,
)

LOG = logging.getLogger("talude_v1")


@dataclass(slots=True)
class Grid:
    z: np.ndarray
    valid: np.ndarray
    x0: float
    y0: float
    cell: float

    def xy(self, rows: np.ndarray, cols: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return self.x0 + (cols + 0.5) * self.cell, self.y0 + (rows + 0.5) * self.cell


def _select_points(cloud: PointCloud, cfg: ExtractConfig) -> tuple[np.ndarray, bool]:
    xyz = cloud.xyz
    used_ground = False

    if cloud.classification is not None and cfg.classification_filter is not None:
        selected = tuple(int(v) for v in cfg.classification_filter)
        if selected:
            mask = np.isin(cloud.classification, np.asarray(selected, dtype=np.uint8))
            if int(mask.sum()) >= 50:
                xyz = xyz[mask]
                used_ground = selected == (cfg.ground_class,)
            else:
                raise ValueError(
                    "As classes selecionadas contêm poucos pontos para extrair taludes."
                )
    elif cfg.use_ground_class and cloud.classification is not None:
        mask = cloud.classification == cfg.ground_class
        if int(mask.sum()) >= max(100, int(0.01 * len(mask))):
            xyz = xyz[mask]
            used_ground = True

    if len(xyz) < 50:
        raise ValueError("A nuvem selecionada tem poucos pontos para extrair taludes.")
    return xyz, used_ground


def estimate_spacing(xyz: np.ndarray, cfg: ExtractConfig) -> float:
    rng = np.random.default_rng(cfg.random_seed)
    n = min(len(xyz), cfg.max_points_for_spacing)
    idx = rng.choice(len(xyz), size=n, replace=False) if n < len(xyz) else np.arange(len(xyz))
    sample = xyz[idx, :2]
    tree = cKDTree(sample)
    d, _ = tree.query(sample, k=2, workers=-1)
    vals = d[:, 1]
    vals = vals[np.isfinite(vals) & (vals > 0)]
    return float(np.median(vals)) if len(vals) else 0.20


def choose_cell_size(spacing: float, requested: float) -> float:
    if requested > 0:
        return requested
    return float(np.clip(spacing * 2.5, 0.05, 1.00))


def rasterize_mean(xyz: np.ndarray, cell: float) -> Grid:
    xmin, ymin = np.min(xyz[:, :2], axis=0)
    xmax, ymax = np.max(xyz[:, :2], axis=0)
    nx = max(2, int(math.floor((xmax - xmin) / cell)) + 1)
    ny = max(2, int(math.floor((ymax - ymin) / cell)) + 1)
    if nx * ny > 60_000_000:
        raise MemoryError(
            f"Grelha demasiado grande ({nx}x{ny}). Aumente --cell-size ou processe uma área menor."
        )

    eps = 1e-7
    col = np.clip(
        np.floor((xyz[:, 0] - xmin) / cell + eps).astype(np.int64),
        0,
        nx - 1,
    )
    row = np.clip(
        np.floor((xyz[:, 1] - ymin) / cell + eps).astype(np.int64),
        0,
        ny - 1,
    )
    flat = row * nx + col
    sums = np.bincount(flat, weights=xyz[:, 2], minlength=nx * ny)
    counts = np.bincount(flat, minlength=nx * ny)
    z = np.full(nx * ny, np.nan, dtype=np.float64)
    ok = counts > 0
    z[ok] = sums[ok] / counts[ok]
    z = z.reshape(ny, nx)
    valid = np.isfinite(z)

    if not valid.all():
        inds = ndimage.distance_transform_edt(
            ~valid,
            return_distances=False,
            return_indices=True,
        )
        z = z[tuple(inds)]

    return Grid(
        z=z,
        valid=valid,
        x0=float(xmin),
        y0=float(ymin),
        cell=float(cell),
    )



def _selected_classes(cfg: ExtractConfig) -> tuple[int, ...] | None:
    if cfg.classification_filter is not None:
        selected = tuple(sorted({int(v) for v in cfg.classification_filter}))
        return selected or None
    if cfg.use_ground_class:
        return (int(cfg.ground_class),)
    return None


def _filter_stream_chunk(
    xyz: np.ndarray,
    classification: np.ndarray | None,
    cfg: ExtractConfig,
) -> np.ndarray:
    selected = _selected_classes(cfg)
    if selected is None:
        return xyz
    if classification is None:
        raise ValueError(
            "Foi pedido filtro por classificação, mas a nuvem não possui Classification."
        )
    mask = np.isin(classification, np.asarray(selected, dtype=np.uint8))
    return xyz[mask]


def estimate_spacing_stream(
    info: PointCloudInfo,
    cfg: ExtractConfig,
) -> float:
    """Estima spacing sem carregar a nuvem completa."""
    rng = np.random.default_rng(cfg.random_seed)
    target = max(5_000, int(cfg.max_points_for_spacing))
    samples: list[np.ndarray] = []
    collected = 0

    for xyz, classification in iter_point_chunks(info.path, chunk_size=750_000):
        pts = _filter_stream_chunk(xyz, classification, cfg)
        if len(pts) == 0:
            continue

        remaining = target - collected
        if len(pts) > remaining:
            idx = rng.choice(len(pts), size=remaining, replace=False)
            pts = pts[idx]

        samples.append(np.asarray(pts[:, :2], dtype=np.float64))
        collected += len(pts)
        if collected >= target:
            break

    if collected < 50:
        selected = _selected_classes(cfg)
        raise ValueError(
            f"Poucos pontos nas classes selecionadas: {selected}. "
            "Experimente 'Todas' ou confirme a classificação LAS."
        )

    sample = np.concatenate(samples, axis=0)
    tree = cKDTree(sample)
    d, _ = tree.query(sample, k=2, workers=-1)
    vals = d[:, 1]
    vals = vals[np.isfinite(vals) & (vals > 0)]
    return float(np.median(vals)) if len(vals) else 0.20


def _fit_cell_to_budget(
    info: PointCloudInfo,
    cell: float,
    *,
    max_cells: int = 4_000_000,
) -> tuple[float, int, int, bool]:
    width = max(info.width, cell)
    height = max(info.height, cell)
    nx = max(2, int(math.floor(width / cell)) + 1)
    ny = max(2, int(math.floor(height / cell)) + 1)
    adjusted = False

    cells = nx * ny
    if cells > max_cells:
        factor = math.sqrt(cells / max_cells) * 1.02
        cell *= factor
        nx = max(2, int(math.floor(width / cell)) + 1)
        ny = max(2, int(math.floor(height / cell)) + 1)
        adjusted = True

    return float(cell), nx, ny, adjusted


def rasterize_mean_stream(
    info: PointCloudInfo,
    cell: float,
    cfg: ExtractConfig,
    progress=None,
) -> tuple[Grid, int, bool]:
    """Rasterização out-of-core: soma/contagem por célula em blocos."""
    cell, nx, ny, adjusted = _fit_cell_to_budget(info, cell)
    n_cells = nx * ny

    LOG.info(
        "STREAM raster: %dx%d = %d células | cell=%.4f m%s",
        nx,
        ny,
        n_cells,
        cell,
        " (ajustado ao orçamento de memória)" if adjusted else "",
    )

    sums = np.zeros(n_cells, dtype=np.float64)
    counts = np.zeros(n_cells, dtype=np.uint32)
    xmin, ymin = info.mins[0], info.mins[1]
    selected_count = 0
    raw_seen = 0
    eps = 1e-9

    for xyz, classification in iter_point_chunks(info.path, chunk_size=1_000_000):
        raw_seen += len(xyz)
        pts = _filter_stream_chunk(xyz, classification, cfg)
        if len(pts):
            selected_count += len(pts)
            col = np.clip(
                np.floor((pts[:, 0] - xmin) / cell + eps).astype(np.int64),
                0,
                nx - 1,
            )
            row = np.clip(
                np.floor((pts[:, 1] - ymin) / cell + eps).astype(np.int64),
                0,
                ny - 1,
            )
            flat = row * nx + col

            # A nuvem é muito mais densa do que a grelha. Agregamos apenas
            # as células presentes no chunk para evitar arrays gigantes temporários.
            unique, inverse = np.unique(flat, return_inverse=True)
            chunk_counts = np.bincount(inverse).astype(np.uint32, copy=False)
            chunk_sums = np.bincount(inverse, weights=pts[:, 2])
            counts[unique] += chunk_counts
            sums[unique] += chunk_sums

        if progress is not None and info.point_count:
            pct = 18.0 + 30.0 * min(raw_seen / info.point_count, 1.0)
            progress(pct, "Streaming: a construir grelha do terreno…")

    if selected_count < 50:
        selected = _selected_classes(cfg)
        raise ValueError(
            f"Não há pontos suficientes nas classes {selected}. "
            "Selecione outras classes no painel."
        )

    valid_flat = counts > 0
    z = np.full(n_cells, np.nan, dtype=np.float32)
    z[valid_flat] = (sums[valid_flat] / counts[valid_flat]).astype(np.float32)
    z = z.reshape(ny, nx)
    valid = np.isfinite(z)

    del sums, counts, valid_flat

    if not valid.all():
        if progress is not None:
            progress(50.0, "Streaming: a preencher pequenas lacunas da grelha…")
        inds = ndimage.distance_transform_edt(
            ~valid,
            return_distances=False,
            return_indices=True,
        )
        z = z[tuple(inds)].astype(np.float32, copy=False)
        del inds

    return (
        Grid(
            z=z,
            valid=valid,
            x0=float(xmin),
            y0=float(ymin),
            cell=float(cell),
        ),
        int(selected_count),
        adjusted,
    )


def _grid_z(grid: Grid, x: float, y: float) -> float:
    col = int(np.clip(math.floor((x - grid.x0) / grid.cell), 0, grid.z.shape[1] - 1))
    row = int(np.clip(math.floor((y - grid.y0) / grid.cell), 0, grid.z.shape[0] - 1))
    return float(grid.z[row, col])


def refine_lines_stream(
    lines: list[dict],
    info: PointCloudInfo,
    grid: Grid,
    cfg: ExtractConfig,
    progress=None,
) -> list[dict]:
    """Refina Z numa segunda passagem pela cloud, sem cKDTree de 241M pontos."""
    if not lines:
        return []

    all_xy_parts: list[np.ndarray] = []
    line_ranges: list[tuple[int, int]] = []
    cursor = 0
    for line in lines:
        xy = np.asarray(line["xy"], dtype=np.float64)
        all_xy_parts.append(xy)
        line_ranges.append((cursor, cursor + len(xy)))
        cursor += len(xy)

    all_xy = np.concatenate(all_xy_parts, axis=0)
    n_vertices = len(all_xy)
    vertex_tree = cKDTree(all_xy)
    radius = max(grid.cell * cfg.refine_radius_factor, grid.cell * 1.75)

    # Estatísticas suficientes do plano local z=a*u+b*v+c em torno de cada vértice.
    n = np.zeros(n_vertices, dtype=np.int64)
    su = np.zeros(n_vertices, dtype=np.float64)
    sv = np.zeros(n_vertices, dtype=np.float64)
    sz = np.zeros(n_vertices, dtype=np.float64)
    suu = np.zeros(n_vertices, dtype=np.float64)
    suv = np.zeros(n_vertices, dtype=np.float64)
    svv = np.zeros(n_vertices, dtype=np.float64)
    suz = np.zeros(n_vertices, dtype=np.float64)
    svz = np.zeros(n_vertices, dtype=np.float64)
    szz = np.zeros(n_vertices, dtype=np.float64)

    raw_seen = 0
    for xyz, classification in iter_point_chunks(info.path, chunk_size=750_000):
        raw_seen += len(xyz)
        pts = _filter_stream_chunk(xyz, classification, cfg)
        if len(pts):
            dist, idx = vertex_tree.query(
                pts[:, :2],
                k=1,
                distance_upper_bound=radius,
                workers=-1,
            )
            ok = np.isfinite(dist) & (idx < n_vertices)
            if np.any(ok):
                ids = idx[ok].astype(np.int64, copy=False)
                p = pts[ok]
                u = p[:, 0] - all_xy[ids, 0]
                v = p[:, 1] - all_xy[ids, 1]
                zz = p[:, 2]

                n += np.bincount(ids, minlength=n_vertices)
                su += np.bincount(ids, weights=u, minlength=n_vertices)
                sv += np.bincount(ids, weights=v, minlength=n_vertices)
                sz += np.bincount(ids, weights=zz, minlength=n_vertices)
                suu += np.bincount(ids, weights=u * u, minlength=n_vertices)
                suv += np.bincount(ids, weights=u * v, minlength=n_vertices)
                svv += np.bincount(ids, weights=v * v, minlength=n_vertices)
                suz += np.bincount(ids, weights=u * zz, minlength=n_vertices)
                svz += np.bincount(ids, weights=v * zz, minlength=n_vertices)
                szz += np.bincount(ids, weights=zz * zz, minlength=n_vertices)

        if progress is not None and info.point_count:
            pct = 66.0 + 20.0 * min(raw_seen / info.point_count, 1.0)
            progress(pct, "Streaming: refinamento 3D sobre a cloud original…")

    vertex_z = np.empty(n_vertices, dtype=np.float64)
    vertex_rmse = np.empty(n_vertices, dtype=np.float64)

    for i in range(n_vertices):
        if n[i] >= max(3, cfg.refine_min_points):
            mat = np.array(
                [
                    [suu[i], suv[i], su[i]],
                    [suv[i], svv[i], sv[i]],
                    [su[i], sv[i], float(n[i])],
                ],
                dtype=np.float64,
            )
            rhs = np.array([suz[i], svz[i], sz[i]], dtype=np.float64)
            try:
                coef = np.linalg.solve(mat, rhs)
                vertex_z[i] = float(coef[2])
                sse = float(
                    szz[i]
                    - 2.0 * np.dot(coef, rhs)
                    + np.dot(coef, mat @ coef)
                )
                vertex_rmse[i] = math.sqrt(max(sse / n[i], 0.0))
                continue
            except np.linalg.LinAlgError:
                pass

        vertex_z[i] = _grid_z(grid, all_xy[i, 0], all_xy[i, 1])
        vertex_rmse[i] = float("nan")

    out: list[dict] = []
    for line_id, (line, (start, end)) in enumerate(zip(lines, line_ranges), start=1):
        xy = all_xy[start:end]
        zz = vertex_z[start:end]
        xyz_line = np.column_stack((xy, zz))
        rmses = vertex_rmse[start:end]
        supports = n[start:end]
        finite_rmse = rmses[np.isfinite(rmses)]
        rmse_med = float(np.median(finite_rmse)) if len(finite_rmse) else float("nan")
        rmse_score = (
            math.exp(-rmse_med / max(grid.cell, 1e-6))
            if np.isfinite(rmse_med)
            else 0.45
        )
        confidence = float(
            np.clip(
                0.45 * line["scale_persistence"]
                + 0.35 * line["gradient_coherence"]
                + 0.20 * rmse_score,
                0.0,
                1.0,
            )
        )

        out.append(
            {
                "line_id": line_id,
                "face_id": line["face_id"],
                "type": line["type"],
                "xyz": xyz_line,
                "length_m": _line_length(xyz_line),
                "slope_mean_deg": line["slope_mean_deg"],
                "gradient_coherence": line["gradient_coherence"],
                "scale_persistence": line["scale_persistence"],
                "face_width_m": float(line.get("face_width_m", 0.0)),
                "face_height_m": float(line.get("face_height_m", 0.0)),
                "confidence": confidence,
                "median_rmse": rmse_med,
                "mean_support_points": float(np.mean(supports)) if len(supports) else 0.0,
                "vertex_rmse": rmses.tolist(),
            }
        )

    return out


def _slope_deg(
    z: np.ndarray,
    cell: float,
    sigma: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    smooth = (
        ndimage.gaussian_filter(z, sigma=sigma, mode="nearest")
        if sigma > 0
        else z
    )
    dzdy, dzdx = np.gradient(smooth, cell, cell)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    return slope, dzdx, dzdy


def _auto_slope_thresholds(
    base_slope: np.ndarray,
    valid: np.ndarray,
    cfg: ExtractConfig,
) -> tuple[float, float]:
    vals = base_slope[valid & np.isfinite(base_slope)]
    if len(vals) == 0:
        return 15.0, 25.0

    q70 = float(np.percentile(vals, 70))
    q85 = float(np.percentile(vals, 85))
    high = (
        cfg.slope_high_deg
        if cfg.slope_high_deg > 0
        else float(np.clip(max(q85, q70 + 5.0), 18.0, 55.0))
    )
    low = (
        cfg.slope_low_deg
        if cfg.slope_low_deg > 0
        else float(np.clip(min(q70, high * 0.72), 8.0, high - 2.0))
    )
    return low, high


def _remove_small(mask: np.ndarray, min_cells: int) -> np.ndarray:
    labels, n = ndimage.label(
        mask,
        structure=np.ones((3, 3), dtype=np.uint8),
    )
    if n == 0:
        return mask

    sizes = np.bincount(labels.ravel())
    keep = sizes >= min_cells
    keep[0] = False
    return keep[labels]


def detect_faces(grid: Grid, cfg: ExtractConfig) -> dict:
    slope_stack = []
    gx_stack = []
    gy_stack = []

    for sigma in cfg.smooth_sigmas_cells:
        slope, gx, gy = _slope_deg(grid.z, grid.cell, sigma)
        slope_stack.append(slope)
        gx_stack.append(gx)
        gy_stack.append(gy)

    slopes = np.stack(slope_stack)
    low, high = _auto_slope_thresholds(
        slopes[0],
        grid.valid,
        cfg,
    )

    weak_each = slopes >= low
    strong_each = slopes >= high
    persistence = weak_each.sum(axis=0).astype(np.uint8)

    weak = (
        persistence
        >= min(
            cfg.min_scale_persistence,
            len(cfg.smooth_sigmas_cells),
        )
    ) & grid.valid
    strong = strong_each.any(axis=0) & grid.valid

    face = ndimage.binary_propagation(
        strong,
        mask=weak,
        structure=np.ones((3, 3), dtype=bool),
    )

    radius = max(0, int(cfg.morphology_radius_cells))
    if radius:
        structure = ndimage.generate_binary_structure(2, 2)
        for _ in range(radius):
            face = ndimage.binary_closing(
                face,
                structure=structure,
            )
        face &= grid.valid

    min_cells = max(
        3,
        int(
            math.ceil(
                cfg.min_face_area_m2
                / (grid.cell * grid.cell)
            )
        ),
    )
    face = _remove_small(face, min_cells)

    gx = np.mean(np.stack(gx_stack), axis=0)
    gy = np.mean(np.stack(gy_stack), axis=0)
    slope = np.mean(slopes, axis=0)

    return {
        "mask": face,
        "persistence": persistence,
        "slope": slope,
        "gx": gx,
        "gy": gy,
        "slope_low": low,
        "slope_high": high,
    }


def _line_length(xyz: np.ndarray) -> float:
    if len(xyz) < 2:
        return 0.0
    return float(
        np.linalg.norm(
            np.diff(xyz[:, :2], axis=0),
            axis=1,
        ).sum()
    )


def _smooth_xy(xy: np.ndarray, window: int) -> np.ndarray:
    if len(xy) < 5 or window < 3:
        return xy

    win = min(
        window if window % 2 else window + 1,
        len(xy) if len(xy) % 2 else len(xy) - 1,
    )
    if win < 5:
        return xy

    out = xy.copy()
    out[:, 0] = savgol_filter(
        xy[:, 0],
        win,
        2,
        mode="interp",
    )
    out[:, 1] = savgol_filter(
        xy[:, 1],
        win,
        2,
        mode="interp",
    )
    return out


def _component_lines(
    grid: Grid,
    det: dict,
    cfg: ExtractConfig,
) -> list[dict]:
    labels, n = ndimage.label(
        det["mask"],
        structure=np.ones((3, 3), dtype=np.uint8),
    )
    lines: list[dict] = []
    face_id = 0

    # Slope/persistence encontra qualquer banda inclinada coerente. Em vinha,
    # isso inclui micro-relevos compridos e estreitos. Um talude útil precisa
    # também de largura física e desnível vertical mínimos.
    min_face_width = (
        float(cfg.min_face_width_m)
        if cfg.min_face_width_m > 0
        else max(2.0, grid.cell * 7.0)
    )
    min_face_height = (
        float(cfg.min_face_height_m)
        if cfg.min_face_height_m > 0
        else max(1.25, grid.cell * 5.0)
    )

    stats = {
        "components_total": int(n),
        "accepted_faces": 0,
        "rejected_small_component": 0,
        "rejected_low_coherence": 0,
        "rejected_short_boundary": 0,
        "rejected_narrow_face": 0,
        "rejected_low_relief": 0,
        "min_face_width_m": float(min_face_width),
        "min_face_height_m": float(min_face_height),
    }

    for label_id in range(1, n + 1):
        rows, cols = np.nonzero(labels == label_id)
        if len(rows) < 4:
            stats["rejected_small_component"] += 1
            continue

        gx = det["gx"][rows, cols]
        gy = det["gy"][rows, cols]
        mag = np.hypot(gx, gy)
        ok = mag > 1e-9
        if int(ok.sum()) < 4:
            stats["rejected_low_coherence"] += 1
            continue

        unit = np.column_stack(
            (
                -gx[ok] / mag[ok],
                -gy[ok] / mag[ok],
            )
        )
        downhill = unit.mean(axis=0)
        coherence = float(np.linalg.norm(downhill))
        if coherence < cfg.min_gradient_coherence:
            stats["rejected_low_coherence"] += 1
            continue

        downhill /= np.linalg.norm(downhill)
        tangent = np.array(
            [-downhill[1], downhill[0]]
        )

        x, y = grid.xy(rows, cols)
        xy = np.column_stack((x, y))
        along = xy @ tangent
        down = xy @ downhill
        bin_size = max(
            grid.cell * cfg.cross_section_bin_factor,
            grid.cell,
        )
        bins = np.floor(
            (along - along.min()) / bin_size
        ).astype(np.int64)

        crest_xy = []
        toe_xy = []
        persist_vals = []
        face_widths = []
        face_heights = []

        for bin_id in np.unique(bins):
            ids = np.flatnonzero(bins == bin_id)
            if len(ids) == 0:
                continue

            crest_idx = ids[np.argmin(down[ids])]
            toe_idx = ids[np.argmax(down[ids])]

            crest_xy.append(xy[crest_idx])
            toe_xy.append(xy[toe_idx])

            width = float(down[toe_idx] - down[crest_idx])
            z_crest = float(grid.z[rows[crest_idx], cols[crest_idx]])
            z_toe = float(grid.z[rows[toe_idx], cols[toe_idx]])
            height = z_crest - z_toe

            if np.isfinite(width) and width >= 0:
                face_widths.append(width)
            if np.isfinite(height):
                face_heights.append(height)

            persist_vals.append(
                float(
                    np.mean(
                        det["persistence"][
                            rows[ids],
                            cols[ids],
                        ]
                    )
                )
            )

        if len(crest_xy) < 2 or len(toe_xy) < 2:
            stats["rejected_short_boundary"] += 1
            continue

        median_width = (
            float(np.median(face_widths))
            if face_widths
            else 0.0
        )
        # Signed relief is intentional: crest must be above toe.
        median_height = (
            float(np.median(face_heights))
            if face_heights
            else 0.0
        )

        if median_width < min_face_width:
            stats["rejected_narrow_face"] += 1
            continue
        if median_height < min_face_height:
            stats["rejected_low_relief"] += 1
            continue

        crest_xy = _smooth_xy(
            np.asarray(crest_xy, dtype=float),
            cfg.line_smooth_window,
        )
        toe_xy = _smooth_xy(
            np.asarray(toe_xy, dtype=float),
            cfg.line_smooth_window,
        )

        if (
            np.linalg.norm(crest_xy[-1] - crest_xy[0])
            < cfg.min_line_length_m
        ):
            stats["rejected_short_boundary"] += 1
            continue
        if (
            np.linalg.norm(toe_xy[-1] - toe_xy[0])
            < cfg.min_line_length_m
        ):
            stats["rejected_short_boundary"] += 1
            continue

        face_id += 1
        stats["accepted_faces"] += 1
        persistence_score = float(
            np.clip(
                np.mean(persist_vals)
                / len(cfg.smooth_sigmas_cells),
                0,
                1,
            )
        )
        slope_mean = float(
            np.mean(det["slope"][rows, cols])
        )

        for feature_type, arr in (
            ("CREST", crest_xy),
            ("TOE", toe_xy),
        ):
            lines.append(
                {
                    "face_id": face_id,
                    "type": feature_type,
                    "xy": arr,
                    "slope_mean_deg": slope_mean,
                    "gradient_coherence": coherence,
                    "scale_persistence": persistence_score,
                    "face_width_m": median_width,
                    "face_height_m": median_height,
                }
            )

    det["component_stats"] = stats
    return lines

def _fit_plane_z(
    tree: cKDTree,
    xyz: np.ndarray,
    x: float,
    y: float,
    radius: float,
    min_points: int,
) -> tuple[float, float, int]:
    ids = tree.query_ball_point(
        [x, y],
        r=radius,
    )

    if len(ids) < min_points:
        k = min(
            max(min_points, 3),
            len(xyz),
        )
        _, idx = tree.query(
            [x, y],
            k=k,
        )
        ids = np.atleast_1d(idx).tolist()

    pts = xyz[
        np.asarray(ids, dtype=int)
    ]
    A = np.column_stack(
        (
            pts[:, 0],
            pts[:, 1],
            np.ones(len(pts)),
        )
    )
    coef, *_ = np.linalg.lstsq(
        A,
        pts[:, 2],
        rcond=None,
    )
    residual = pts[:, 2] - A @ coef
    med = np.median(residual)
    mad = np.median(
        np.abs(residual - med)
    )

    if mad > 1e-9 and len(pts) >= 10:
        keep = (
            np.abs(residual - med)
            <= 3.5 * 1.4826 * mad
        )
        if int(keep.sum()) >= min_points:
            A2 = A[keep]
            p2 = pts[keep]
            coef, *_ = np.linalg.lstsq(
                A2,
                p2[:, 2],
                rcond=None,
            )
            residual = (
                p2[:, 2] - A2 @ coef
            )
            pts = p2

    z = float(
        coef[0] * x
        + coef[1] * y
        + coef[2]
    )
    rmse = (
        float(
            np.sqrt(
                np.mean(residual**2)
            )
        )
        if len(residual)
        else float("nan")
    )
    return z, rmse, len(pts)


def refine_lines(
    lines: list[dict],
    xyz: np.ndarray,
    cell: float,
    cfg: ExtractConfig,
) -> list[dict]:
    tree = cKDTree(xyz[:, :2])
    radius = max(
        cell * cfg.refine_radius_factor,
        cell * 1.5,
    )
    out: list[dict] = []

    for line_id, line in enumerate(
        lines,
        start=1,
    ):
        verts = []
        rmses = []
        support = []

        for x, y in line["xy"]:
            z, rmse, n = _fit_plane_z(
                tree,
                xyz,
                float(x),
                float(y),
                radius,
                cfg.refine_min_points,
            )
            verts.append(
                (
                    float(x),
                    float(y),
                    z,
                )
            )
            rmses.append(rmse)
            support.append(n)

        xyz_line = np.asarray(
            verts,
            dtype=float,
        )
        rmse_med = (
            float(np.nanmedian(rmses))
            if rmses
            else float("nan")
        )
        rmse_score = (
            math.exp(
                -rmse_med
                / max(cell, 1e-6)
            )
            if np.isfinite(rmse_med)
            else 0.0
        )
        confidence = float(
            np.clip(
                0.45
                * line["scale_persistence"]
                + 0.35
                * line["gradient_coherence"]
                + 0.20
                * rmse_score,
                0.0,
                1.0,
            )
        )

        out.append(
            {
                "line_id": line_id,
                "face_id": line["face_id"],
                "type": line["type"],
                "xyz": xyz_line,
                "length_m": _line_length(
                    xyz_line
                ),
                "slope_mean_deg": line[
                    "slope_mean_deg"
                ],
                "gradient_coherence": line[
                    "gradient_coherence"
                ],
                "scale_persistence": line[
                    "scale_persistence"
                ],
                "face_width_m": float(
                    line.get("face_width_m", 0.0)
                ),
                "face_height_m": float(
                    line.get("face_height_m", 0.0)
                ),
                "confidence": confidence,
                "median_rmse": rmse_med,
                "mean_support_points": float(
                    np.mean(support)
                )
                if support
                else 0.0,
                "vertex_rmse": rmses,
            }
        )

    return out


def _write_ascii_grid(
    path: Path,
    arr: np.ndarray,
    grid: Grid,
    nodata: float = -9999.0,
) -> None:
    out = np.flipud(
        np.asarray(
            arr,
            dtype=float,
        )
    )
    out[~np.isfinite(out)] = nodata

    with path.open(
        "w",
        encoding="utf-8",
    ) as f:
        f.write(
            f"ncols {arr.shape[1]}\n"
        )
        f.write(
            f"nrows {arr.shape[0]}\n"
        )
        f.write(
            f"xllcorner {grid.x0}\n"
        )
        f.write(
            f"yllcorner {grid.y0}\n"
        )
        f.write(
            f"cellsize {grid.cell}\n"
        )
        f.write(
            f"NODATA_value {nodata}\n"
        )
        np.savetxt(
            f,
            out,
            fmt="%.6f",
        )


def _save_outputs_and_report(
    *,
    input_path: str | Path,
    output: Path,
    debug: Path,
    cfg: ExtractConfig,
    grid: Grid,
    det: dict,
    lines: list[dict],
    crs_wkt: str | None,
    points_total: int,
    points_processed: int,
    used_ground: bool,
    spacing: float,
    t0: float,
    engine_name: str,
    streaming_mode: bool,
    cell_adjusted_for_memory: bool = False,
) -> dict:
    lines = [
        line for line in lines
        if line["length_m"] >= cfg.min_line_length_m
    ]
    for line_id, line in enumerate(lines, start=1):
        line["line_id"] = line_id

    save_geojson(output / "talude_breaklines.geojson", lines, crs_wkt)
    save_vertices_csv(output / "talude_vertices.csv", lines)
    save_dxf(output / "talude_breaklines.dxf", lines)

    # ASCII gigantes podem ocupar centenas de MB. Em clouds massivas guardamos
    # os três debug layers num NPZ comprimido.
    if grid.z.size <= 1_500_000:
        _write_ascii_grid(debug / "01_slope.asc", det["slope"], grid)
        _write_ascii_grid(debug / "02_persistence.asc", det["persistence"], grid)
        _write_ascii_grid(debug / "03_face_mask.asc", det["mask"].astype(float), grid)
        debug_format = "ASC"
    else:
        np.savez_compressed(
            debug / "terrain_debug.npz",
            slope=np.asarray(det["slope"], dtype=np.float32),
            persistence=np.asarray(det["persistence"], dtype=np.uint8),
            face_mask=np.asarray(det["mask"], dtype=np.uint8),
            x0=np.float64(grid.x0),
            y0=np.float64(grid.y0),
            cell=np.float64(grid.cell),
        )
        debug_format = "NPZ"

    report = {
        "engine": engine_name,
        "input": str(input_path),
        "streaming_mode": bool(streaming_mode),
        "points_total": int(points_total),
        "points_processed": int(points_processed),
        "ground_class_used": bool(used_ground),
        "selected_classes": (
            list(_selected_classes(cfg))
            if _selected_classes(cfg) is not None
            else None
        ),
        "estimated_spacing_m": float(spacing),
        "cell_size_m": float(grid.cell),
        "cell_adjusted_for_memory": bool(cell_adjusted_for_memory),
        "grid_rows": int(grid.z.shape[0]),
        "grid_cols": int(grid.z.shape[1]),
        "debug_format": debug_format,
        "slope_low_deg": float(det["slope_low"]),
        "slope_high_deg": float(det["slope_high"]),
        "faces_detected": len({line["face_id"] for line in lines}),
        "crest_lines": sum(1 for line in lines if line["type"] == "CREST"),
        "toe_lines": sum(1 for line in lines if line["type"] == "TOE"),
        "face_filter": det.get("component_stats", {}),
        "elapsed_s": perf_counter() - t0,
        "config": cfg.to_dict(),
        "lines": [
            {
                k: v
                for k, v in line.items()
                if k not in {"xyz", "vertex_rmse"}
            }
            for line in lines
        ],
    }

    (output / "talude_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def extract(
    input_path: str | Path,
    output_dir: str | Path,
    cfg: ExtractConfig | None = None,
    progress=None,
) -> dict:
    cfg = cfg or ExtractConfig()
    input_path = Path(input_path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    debug = output / "debug"
    debug.mkdir(exist_ok=True)
    t0 = perf_counter()

    # LAS/LAZ passam SEMPRE pelo caminho out-of-core. Isto evita materializar
    # clouds de centenas de milhões de pontos na RAM.
    if input_path.suffix.lower() in {".las", ".laz"}:
        info = inspect_point_cloud(input_path)
        selected = _selected_classes(cfg)
        used_ground = selected == (int(cfg.ground_class),)

        if progress is not None:
            progress(10.0, f"Cloud massiva: {info.point_count:,} pontos · modo streaming…")

        spacing = estimate_spacing_stream(info, cfg)
        requested_cell = choose_cell_size(spacing, cfg.cell_size)

        if progress is not None:
            progress(16.0, f"Spacing ≈ {spacing:.3f} m · a construir terreno…")

        grid, selected_count, adjusted = rasterize_mean_stream(
            info,
            requested_cell,
            cfg,
            progress=progress,
        )

        if progress is not None:
            progress(52.0, "A calcular slope multiescala + persistence + hysteresis…")
        det = detect_faces(grid, cfg)

        if progress is not None:
            progress(62.0, "A extrair boundaries de CRISTA e PÉ…")
        approx = _component_lines(grid, det, cfg)

        lines = refine_lines_stream(
            approx,
            info,
            grid,
            cfg,
            progress=progress,
        )

        if progress is not None:
            progress(89.0, "A exportar DXF / GeoJSON / CSV e debug…")

        return _save_outputs_and_report(
            input_path=input_path,
            output=output,
            debug=debug,
            cfg=cfg,
            grid=grid,
            det=det,
            lines=lines,
            crs_wkt=info.crs_wkt,
            points_total=info.point_count,
            points_processed=selected_count,
            used_ground=used_ground,
            spacing=spacing,
            t0=t0,
            engine_name="BREAKLINE_ENGINE_V1_STREAMING",
            streaming_mode=True,
            cell_adjusted_for_memory=adjusted,
        )

    # XYZ/TXT/CSV pequenos continuam a usar o caminho em memória.
    cloud = load_point_cloud(input_path)
    xyz, used_ground = _select_points(cloud, cfg)
    spacing = estimate_spacing(xyz, cfg)
    cell = choose_cell_size(spacing, cfg.cell_size)

    if progress is not None:
        progress(25.0, "A rasterizar nuvem…")

    grid = rasterize_mean(xyz, cell)
    det = detect_faces(grid, cfg)
    approx = _component_lines(grid, det, cfg)
    lines = refine_lines(approx, xyz, cell, cfg)

    return _save_outputs_and_report(
        input_path=input_path,
        output=output,
        debug=debug,
        cfg=cfg,
        grid=grid,
        det=det,
        lines=lines,
        crs_wkt=cloud.crs_wkt,
        points_total=len(cloud.xyz),
        points_processed=len(xyz),
        used_ground=used_ground,
        spacing=spacing,
        t0=t0,
        engine_name="BREAKLINE_ENGINE_V1",
        streaming_mode=False,
    )
