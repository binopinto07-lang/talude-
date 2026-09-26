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
from .io import PointCloud, load_point_cloud, save_dxf, save_geojson, save_vertices_csv

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
        _, inds = ndimage.distance_transform_edt(
            ~valid,
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

    for label_id in range(1, n + 1):
        rows, cols = np.nonzero(labels == label_id)
        if len(rows) < 4:
            continue

        gx = det["gx"][rows, cols]
        gy = det["gy"][rows, cols]
        mag = np.hypot(gx, gy)
        ok = mag > 1e-9
        if int(ok.sum()) < 4:
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

        for bin_id in np.unique(bins):
            ids = np.flatnonzero(bins == bin_id)
            if len(ids) == 0:
                continue

            crest_idx = ids[np.argmin(down[ids])]
            toe_idx = ids[np.argmax(down[ids])]
            crest_xy.append(xy[crest_idx])
            toe_xy.append(xy[toe_idx])
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
            continue
        if (
            np.linalg.norm(toe_xy[-1] - toe_xy[0])
            < cfg.min_line_length_m
        ):
            continue

        face_id += 1
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
                }
            )

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


def extract(
    input_path: str | Path,
    output_dir: str | Path,
    cfg: ExtractConfig | None = None,
) -> dict:
    cfg = cfg or ExtractConfig()
    output = Path(output_dir)
    output.mkdir(
        parents=True,
        exist_ok=True,
    )
    debug = output / "debug"
    debug.mkdir(exist_ok=True)
    t0 = perf_counter()

    cloud = load_point_cloud(input_path)
    xyz, used_ground = _select_points(
        cloud,
        cfg,
    )
    spacing = estimate_spacing(
        xyz,
        cfg,
    )
    cell = choose_cell_size(
        spacing,
        cfg.cell_size,
    )

    LOG.info(
        "Pontos=%d | ground=%s | spacing=%.4f | cell=%.4f",
        len(xyz),
        used_ground,
        spacing,
        cell,
    )

    grid = rasterize_mean(
        xyz,
        cell,
    )
    det = detect_faces(
        grid,
        cfg,
    )
    approx = _component_lines(
        grid,
        det,
        cfg,
    )
    lines = refine_lines(
        approx,
        xyz,
        cell,
        cfg,
    )

    lines = [
        line
        for line in lines
        if line["length_m"]
        >= cfg.min_line_length_m
    ]
    for line_id, line in enumerate(
        lines,
        start=1,
    ):
        line["line_id"] = line_id

    save_geojson(
        output
        / "talude_breaklines.geojson",
        lines,
        cloud.crs_wkt,
    )
    save_vertices_csv(
        output / "talude_vertices.csv",
        lines,
    )
    save_dxf(
        output / "talude_breaklines.dxf",
        lines,
    )

    _write_ascii_grid(
        debug / "01_slope.asc",
        det["slope"],
        grid,
    )
    _write_ascii_grid(
        debug / "02_persistence.asc",
        det["persistence"],
        grid,
    )
    _write_ascii_grid(
        debug / "03_face_mask.asc",
        det["mask"].astype(float),
        grid,
    )

    report = {
        "engine": "BREAKLINE_ENGINE_V1",
        "input": str(input_path),
        "points_total": int(
            len(cloud.xyz)
        ),
        "points_processed": int(
            len(xyz)
        ),
        "ground_class_used": used_ground,
        "estimated_spacing_m": spacing,
        "cell_size_m": cell,
        "slope_low_deg": det[
            "slope_low"
        ],
        "slope_high_deg": det[
            "slope_high"
        ],
        "faces_detected": len(
            {
                line["face_id"]
                for line in lines
            }
        ),
        "crest_lines": sum(
            1
            for line in lines
            if line["type"] == "CREST"
        ),
        "toe_lines": sum(
            1
            for line in lines
            if line["type"] == "TOE"
        ),
        "elapsed_s": perf_counter() - t0,
        "config": cfg.to_dict(),
        "lines": [
            {
                k: v
                for k, v in line.items()
                if k
                not in {
                    "xyz",
                    "vertex_rmse",
                }
            }
            for line in lines
        ],
    }

    (
        output / "talude_report.json"
    ).write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report
