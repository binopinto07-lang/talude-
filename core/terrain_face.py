from __future__ import annotations

from dataclasses import dataclass
import heapq
import math

import numpy as np


@dataclass
class TerrainFaceResult:
    vertices: np.ndarray
    raw_vertices: int
    rough_vertices: int
    refined_vertices: int
    refinement_ratio: float
    snapped_vertices: int
    snap_ratio: float
    median_snap_offset_m: float
    confidence: float
    face_slope_deg: float
    low_slope_threshold_deg: float
    high_slope_threshold_deg: float
    grid_resolution: float
    source_points: int
    profile: str
    face_cells: int


def _shift(arr: np.ndarray, dx: int, dy: int, fill=False) -> np.ndarray:
    out = np.full_like(arr, fill)
    xs = slice(max(0, dx), min(arr.shape[0], arr.shape[0] + dx))
    ys = slice(max(0, dy), min(arr.shape[1], arr.shape[1] + dy))
    src_x = slice(max(0, -dx), min(arr.shape[0], arr.shape[0] - dx))
    src_y = slice(max(0, -dy), min(arr.shape[1], arr.shape[1] - dy))
    out[xs, ys] = arr[src_x, src_y]
    return out


def _fill_sparse(
    values: np.ndarray,
    valid: np.ndarray,
    iterations: int = 8,
) -> tuple[np.ndarray, np.ndarray]:
    filled = values.copy()
    mask = valid.copy()

    for _ in range(iterations):
        sums = np.zeros_like(filled, dtype=np.float64)
        counts = np.zeros_like(filled, dtype=np.int16)

        for dx, dy in (
            (-1, 0), (1, 0), (0, -1), (0, 1),
            (-1, -1), (-1, 1), (1, -1), (1, 1),
        ):
            shifted_mask = _shift(mask, dx, dy, False)
            shifted_values = _shift(filled, dx, dy, 0.0)
            sums += np.where(shifted_mask, shifted_values, 0.0)
            counts += shifted_mask.astype(np.int16)

        add = (~mask) & (counts >= 2)
        if not np.any(add):
            break

        filled[add] = sums[add] / counts[add]
        mask[add] = True

    return filled, mask


def _smooth5(values: np.ndarray, mask: np.ndarray, passes: int = 2) -> np.ndarray:
    kernel = (
        (0, 0, 6.0),
        (-1, 0, 4.0), (1, 0, 4.0),
        (0, -1, 4.0), (0, 1, 4.0),
        (-1, -1, 1.0), (-1, 1, 1.0),
        (1, -1, 1.0), (1, 1, 1.0),
    )

    current = values.copy()
    current_mask = mask.copy()

    for _ in range(passes):
        sums = np.zeros_like(current, dtype=np.float64)
        weights = np.zeros_like(current, dtype=np.float64)

        for dx, dy, weight in kernel:
            shifted_mask = _shift(current_mask, dx, dy, False)
            shifted_values = _shift(current, dx, dy, 0.0)
            sums += np.where(shifted_mask, shifted_values * weight, 0.0)
            weights += shifted_mask.astype(np.float64) * weight

        ok = weights > 0
        current[ok] = sums[ok] / weights[ok]

    return current


def _rasterize_dem(
    points: np.ndarray,
    resolution: float,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    x0 = float(np.min(points[:, 0]))
    y0 = float(np.min(points[:, 1]))
    x1 = float(np.max(points[:, 0]))
    y1 = float(np.max(points[:, 1]))

    nx = max(8, int(math.ceil((x1 - x0) / resolution)) + 1)
    ny = max(8, int(math.ceil((y1 - y0) / resolution)) + 1)

    # Keep pathological payloads bounded even if an incorrect profile arrives.
    if nx * ny > 250_000:
        scale = math.sqrt((nx * ny) / 250_000.0)
        resolution *= scale
        nx = max(8, int(math.ceil((x1 - x0) / resolution)) + 1)
        ny = max(8, int(math.ceil((y1 - y0) / resolution)) + 1)

    ix = np.clip(
        np.floor((points[:, 0] - x0) / resolution).astype(np.int32),
        0,
        nx - 1,
    )
    iy = np.clip(
        np.floor((points[:, 1] - y0) / resolution).astype(np.int32),
        0,
        ny - 1,
    )

    cell = ix.astype(np.int64) * ny + iy.astype(np.int64)
    order = np.argsort(cell, kind="mergesort")
    ids = cell[order]
    z = points[order, 2]

    unique, starts, counts = np.unique(
        ids,
        return_index=True,
        return_counts=True,
    )

    dem = np.zeros((nx, ny), dtype=np.float64)
    valid = np.zeros((nx, ny), dtype=bool)

    for cid, start, count in zip(unique, starts, counts):
        gx = int(cid // ny)
        gy = int(cid % ny)
        values = z[start:start + count]
        dem[gx, gy] = float(np.median(values))
        valid[gx, gy] = True

    return dem, valid, x0, y0


def _label_components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    seen = np.zeros_like(mask, dtype=bool)
    components: list[list[tuple[int, int]]] = []
    nx, ny = mask.shape

    for sx, sy in np.argwhere(mask):
        sx = int(sx)
        sy = int(sy)
        if seen[sx, sy]:
            continue

        stack = [(sx, sy)]
        seen[sx, sy] = True
        comp: list[tuple[int, int]] = []

        while stack:
            x, y = stack.pop()
            comp.append((x, y))

            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    xx = x + dx
                    yy = y + dy
                    if (
                        0 <= xx < nx
                        and 0 <= yy < ny
                        and mask[xx, yy]
                        and not seen[xx, yy]
                    ):
                        seen[xx, yy] = True
                        stack.append((xx, yy))

        components.append(comp)

    return components


def _hysteresis_face_mask(
    slope: np.ndarray,
    valid: np.ndarray,
) -> tuple[np.ndarray, float, float]:
    usable = slope[valid]
    if len(usable) < 100:
        raise ValueError("DTM local sem células suficientes.")

    p70 = float(np.percentile(usable, 70.0))
    p85 = float(np.percentile(usable, 85.0))

    high = max(17.0, min(38.0, 0.55 * p70 + 0.45 * p85))
    low = max(8.0, min(high - 4.0, high * 0.52))

    support = valid & (slope >= low)
    strong = valid & (slope >= high)

    selected = np.zeros_like(support, dtype=bool)
    for comp in _label_components(support):
        if len(comp) < 20:
            continue
        if any(strong[x, y] for x, y in comp):
            for x, y in comp:
                selected[x, y] = True

    return selected, low, high


def _choose_face_component(
    face_mask: np.ndarray,
    seed_cell: tuple[float, float],
    resolution: float,
) -> np.ndarray:
    components = _label_components(face_mask)
    if not components:
        raise ValueError("Não foi encontrada uma face de talude junto ao clique.")

    sx, sy = seed_cell
    minimum_cells = max(18, int(round(1.0 / (resolution * resolution))))

    best = None
    best_score = float("inf")

    for comp in components:
        if len(comp) < minimum_cells:
            continue

        arr = np.asarray(comp, dtype=np.float64)
        d = np.sqrt(
            np.square(arr[:, 0] - sx)
            + np.square(arr[:, 1] - sy)
        )
        distance_m = float(np.min(d)) * resolution

        # Prefer the nearby face, but gently favor a coherent larger face over
        # a tiny vineyard-row artefact at exactly the clicked pixel.
        area_m2 = len(comp) * resolution * resolution
        score = distance_m + 0.60 / math.sqrt(max(area_m2, 0.25))

        if score < best_score:
            best_score = score
            best = comp

    if best is None or best_score > 5.0:
        raise ValueError(
            "O clique não está suficientemente perto de uma face de talude."
        )

    out = np.zeros_like(face_mask, dtype=bool)
    for x, y in best:
        out[x, y] = True
    return out


def _edge_candidates(
    component: np.ndarray,
    gx: np.ndarray,
    gy: np.ndarray,
    profile: str,
) -> np.ndarray:
    nx, ny = component.shape
    candidate = np.zeros_like(component, dtype=bool)

    # Work from inner face-boundary pixels. Gradient of Z points uphill.
    eroded = component.copy()
    for dx, dy in (
        (-1, 0), (1, 0), (0, -1), (0, 1),
        (-1, -1), (-1, 1), (1, -1), (1, 1),
    ):
        eroded &= _shift(component, dx, dy, False)

    boundary = component & (~eroded)

    for x, y in np.argwhere(boundary):
        x = int(x)
        y = int(y)
        gradient = np.asarray([gx[x, y], gy[x, y]], dtype=np.float64)
        norm = float(np.linalg.norm(gradient))
        if not np.isfinite(norm) or norm < 1e-8:
            continue
        gradient /= norm

        sx = int(np.sign(gradient[0]))
        sy = int(np.sign(gradient[1]))
        if sx == 0 and sy == 0:
            continue

        ux = int(np.clip(x + 2 * sx, 0, nx - 1))
        uy = int(np.clip(y + 2 * sy, 0, ny - 1))
        dx = int(np.clip(x - 2 * sx, 0, nx - 1))
        dy = int(np.clip(y - 2 * sy, 0, ny - 1))

        uphill_inside = bool(component[ux, uy])
        downhill_inside = bool(component[dx, dy])

        if profile == "ridge":
            # Upper break: leaving the steep face in the uphill direction.
            if (not uphill_inside) and downhill_inside:
                candidate[x, y] = True
        else:
            # Lower break: leaving the steep face in the downhill direction.
            if uphill_inside and (not downhill_inside):
                candidate[x, y] = True

    return candidate


def _candidate_graph(
    mask: np.ndarray,
    radius: int = 2,
) -> tuple[list[tuple[int, int]], list[list[tuple[int, float]]]]:
    coords = [tuple(map(int, p)) for p in np.argwhere(mask)]
    index = {p: i for i, p in enumerate(coords)}
    adjacency: list[list[tuple[int, float]]] = [[] for _ in coords]

    offsets = []
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            distance = math.hypot(dx, dy)
            if distance <= radius + 0.35:
                offsets.append((dx, dy, distance))

    for i, (x, y) in enumerate(coords):
        for dx, dy, distance in offsets:
            j = index.get((x + dx, y + dy))
            if j is not None:
                adjacency[i].append((j, distance))

    return coords, adjacency


def _graph_components(
    adjacency: list[list[tuple[int, float]]],
) -> list[list[int]]:
    seen = set()
    components: list[list[int]] = []

    for start in range(len(adjacency)):
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        comp = []
        while stack:
            node = stack.pop()
            comp.append(node)
            for nxt, _ in adjacency[node]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(comp)

    return components


def _dijkstra(
    adjacency: list[list[tuple[int, float]]],
    start: int,
    allowed: set[int],
) -> tuple[np.ndarray, np.ndarray]:
    n = len(adjacency)
    dist = np.full(n, np.inf, dtype=np.float64)
    previous = np.full(n, -1, dtype=np.int32)
    dist[start] = 0.0
    heap = [(0.0, start)]

    while heap:
        current_distance, node = heapq.heappop(heap)
        if current_distance != dist[node]:
            continue

        for nxt, weight in adjacency[node]:
            if nxt not in allowed:
                continue
            trial = current_distance + weight
            if trial < dist[nxt]:
                dist[nxt] = trial
                previous[nxt] = node
                heapq.heappush(heap, (trial, nxt))

    return dist, previous


def _ordered_candidate_path(
    candidate: np.ndarray,
    seed_cell: tuple[float, float],
    resolution: float,
) -> np.ndarray:
    coords, adjacency = _candidate_graph(candidate, radius=2)
    if len(coords) < 5:
        raise ValueError("A aresta do talude ficou demasiado fragmentada.")

    components = _graph_components(adjacency)
    sx, sy = seed_cell

    best_comp = None
    best_distance = float("inf")

    for comp in components:
        if len(comp) < 5:
            continue
        d = min(
            math.hypot(coords[i][0] - sx, coords[i][1] - sy)
            for i in comp
        ) * resolution
        if d < best_distance:
            best_distance = d
            best_comp = comp

    if best_comp is None or best_distance > 4.0:
        raise ValueError("Não foi encontrada a aresta pedida junto ao clique.")

    allowed = set(best_comp)
    arbitrary = best_comp[0]
    distances, _ = _dijkstra(adjacency, arbitrary, allowed)
    finite = [i for i in best_comp if np.isfinite(distances[i])]
    a = max(finite, key=lambda i: distances[i])

    distances, previous = _dijkstra(adjacency, a, allowed)
    finite = [i for i in best_comp if np.isfinite(distances[i])]
    b = max(finite, key=lambda i: distances[i])

    path = [b]
    current = b
    while current != a:
        current = int(previous[current])
        if current < 0:
            break
        path.append(current)

    path.reverse()
    if len(path) < 5:
        raise ValueError("A aresta detetada é demasiado curta.")

    return np.asarray([coords[i] for i in path], dtype=np.int32)


def _rdp(points: np.ndarray, epsilon: float) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) <= 2:
        return pts.copy()

    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = True
    keep[-1] = True
    stack = [(0, len(pts) - 1)]

    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue

        a = pts[start]
        b = pts[end]
        ab = b - a
        denom = float(np.dot(ab, ab))
        segment = pts[start + 1:end]
        if not len(segment):
            continue

        if denom <= 1e-15:
            distances = np.linalg.norm(segment - a, axis=1)
        else:
            t = np.clip(((segment - a) @ ab) / denom, 0.0, 1.0)
            projection = a + t[:, None] * ab
            distances = np.linalg.norm(segment - projection, axis=1)

        local = int(np.argmax(distances))
        if float(distances[local]) > epsilon:
            idx = start + 1 + local
            keep[idx] = True
            stack.append((start, idx))
            stack.append((idx, end))

    return pts[keep]


def _bilinear_sample(grid: np.ndarray, x: float, y: float) -> float:
    nx, ny = grid.shape
    if x < 0.0 or y < 0.0 or x > nx - 1 or y > ny - 1:
        return float("nan")

    x0 = int(math.floor(x))
    y0 = int(math.floor(y))
    x1 = min(x0 + 1, nx - 1)
    y1 = min(y0 + 1, ny - 1)
    tx = float(x - x0)
    ty = float(y - y0)

    a = float(grid[x0, y0])
    b = float(grid[x1, y0])
    c = float(grid[x0, y1])
    d = float(grid[x1, y1])

    return (
        a * (1.0 - tx) * (1.0 - ty)
        + b * tx * (1.0 - ty)
        + c * (1.0 - tx) * ty
        + d * tx * ty
    )


def _local_uphill_direction(
    gx: np.ndarray,
    gy: np.ndarray,
    ix: int,
    iy: int,
    radius: int = 2,
) -> np.ndarray | None:
    x0 = max(0, ix - radius)
    x1 = min(gx.shape[0], ix + radius + 1)
    y0 = max(0, iy - radius)
    y1 = min(gx.shape[1], iy + radius + 1)

    vx = float(np.median(gx[x0:x1, y0:y1]))
    vy = float(np.median(gy[x0:x1, y0:y1]))
    vector = np.asarray([vx, vy], dtype=np.float64)
    norm = float(np.linalg.norm(vector))

    if not np.isfinite(norm) or norm < 1e-8:
        return None
    return vector / norm


def _snap_profile_transition(
    rough: np.ndarray,
    smooth: np.ndarray,
    slope: np.ndarray,
    gx: np.ndarray,
    gy: np.ndarray,
    x0: float,
    y0: float,
    resolution: float,
    profile: str,
    low_threshold: float,
    high_threshold: float,
) -> tuple[np.ndarray, int, float]:
    """Lock the polyline onto the actual flat/steep break in cross-section.

    The slope mask is excellent for selecting the correct talude, but its
    boundary can sit a few decimetres inside the face after raster smoothing.
    Each rough vertex is therefore scanned along the local cross-slope normal.
    The best location is where the requested side behaves like a steep face and
    the opposite side behaves like the adjacent bench.
    """

    pts = np.asarray(rough, dtype=np.float64)
    if len(pts) < 2:
        return pts.copy(), 0, 0.0

    snapped = pts.copy()
    success = 0
    offsets: list[float] = []

    sample_step = max(0.06, resolution * 0.40)
    search = np.arange(
        -1.10,
        1.10 + sample_step * 0.5,
        sample_step,
        dtype=np.float64,
    )
    side_offsets = np.asarray(
        [0.28, 0.42, 0.60, 0.82],
        dtype=np.float64,
    )

    face_min = max(
        low_threshold + 3.0,
        min(high_threshold * 0.68, high_threshold - 2.0),
    )
    bench_max = max(10.0, min(low_threshold + 4.0, 24.0))

    for i in range(len(pts)):
        before = pts[max(0, i - 2), :2]
        after = pts[min(len(pts) - 1, i + 2), :2]
        tangent = after - before
        tangent_norm = float(np.linalg.norm(tangent))
        if tangent_norm < 1e-6:
            continue
        tangent /= tangent_norm

        ix = int(
            np.clip(
                round((pts[i, 0] - x0) / resolution),
                0,
                gx.shape[0] - 1,
            )
        )
        iy = int(
            np.clip(
                round((pts[i, 1] - y0) / resolution),
                0,
                gx.shape[1] - 1,
            )
        )

        uphill_hint = _local_uphill_direction(gx, gy, ix, iy, radius=2)
        if uphill_hint is None:
            continue

        normal = np.asarray([-tangent[1], tangent[0]], dtype=np.float64)
        if float(np.dot(normal, uphill_hint)) < 0.0:
            normal *= -1.0

        best_score = -float("inf")
        best_xy = None
        best_z = None
        best_offset = 0.0

        for t in search:
            candidate_xy = pts[i, :2] + float(t) * normal
            face_values = []
            bench_values = []

            for offset in side_offsets:
                if profile == "ridge":
                    face_xy = candidate_xy - float(offset) * normal
                    bench_xy = candidate_xy + float(offset) * normal
                else:
                    face_xy = candidate_xy + float(offset) * normal
                    bench_xy = candidate_xy - float(offset) * normal

                fx = (face_xy[0] - x0) / resolution
                fy = (face_xy[1] - y0) / resolution
                bx = (bench_xy[0] - x0) / resolution
                by = (bench_xy[1] - y0) / resolution

                face_value = _bilinear_sample(slope, fx, fy)
                bench_value = _bilinear_sample(slope, bx, by)

                if np.isfinite(face_value):
                    face_values.append(face_value)
                if np.isfinite(bench_value):
                    bench_values.append(bench_value)

            if len(face_values) < 3 or len(bench_values) < 3:
                continue

            face_slope = float(np.median(face_values))
            bench_slope = float(np.median(bench_values))
            contrast = face_slope - bench_slope

            if face_slope < face_min:
                continue
            if contrast < 5.0:
                continue
            if bench_slope > max(bench_max, face_slope - 5.0):
                continue

            # Prefer a clean steep/flat split close to the raster boundary.
            # The proximity penalty is intentionally modest: the real break
            # can sit 20-50 cm away from the thresholded slope-mask boundary.
            score = (
                1.45 * contrast
                + 0.18 * min(face_slope, 50.0)
                - 0.28 * max(bench_slope - low_threshold, 0.0)
                - 0.55 * abs(float(t))
            )

            if score <= best_score:
                continue

            sx = (candidate_xy[0] - x0) / resolution
            sy = (candidate_xy[1] - y0) / resolution
            z = _bilinear_sample(smooth, sx, sy)
            if not np.isfinite(z):
                continue

            best_score = score
            best_xy = candidate_xy
            best_z = z
            best_offset = float(t)

        if best_xy is None:
            continue

        snapped[i, 0] = best_xy[0]
        snapped[i, 1] = best_xy[1]
        snapped[i, 2] = float(best_z)
        success += 1
        offsets.append(abs(best_offset))

    median_offset = float(np.median(offsets)) if offsets else 0.0
    return snapped, success, median_offset


def _robust_plane(points: np.ndarray) -> np.ndarray | None:
    if len(points) < 6:
        return None

    design = np.column_stack(
        (points[:, 0], points[:, 1], np.ones(len(points)))
    )
    try:
        coef, *_ = np.linalg.lstsq(design, points[:, 2], rcond=None)
    except np.linalg.LinAlgError:
        return None

    residual = points[:, 2] - design @ coef
    median = float(np.median(residual))
    mad = float(np.median(np.abs(residual - median)))
    if mad > 1e-6:
        keep = np.abs(residual - median) <= max(0.05, 2.8 * 1.4826 * mad)
        if int(np.count_nonzero(keep)) >= 6:
            design2 = design[keep]
            z2 = points[keep, 2]
            try:
                coef, *_ = np.linalg.lstsq(design2, z2, rcond=None)
            except np.linalg.LinAlgError:
                return None

    return np.asarray(coef, dtype=np.float64)


def _refine_plane_intersections(
    rough: np.ndarray,
    source_points: np.ndarray,
    gx: np.ndarray,
    gy: np.ndarray,
    x0: float,
    y0: float,
    resolution: float,
    profile: str,
) -> tuple[np.ndarray, int]:
    if len(rough) < 2:
        return rough, 0

    refined = rough.copy()
    success = 0
    nx, ny = gx.shape

    for i in range(len(rough)):
        before = rough[max(0, i - 1), :2]
        after = rough[min(len(rough) - 1, i + 1), :2]
        tangent = after - before
        tangent_norm = float(np.linalg.norm(tangent))
        if tangent_norm < 1e-6:
            continue
        tangent /= tangent_norm

        ix = int(np.clip(round((rough[i, 0] - x0) / resolution), 0, nx - 1))
        iy = int(np.clip(round((rough[i, 1] - y0) / resolution), 0, ny - 1))

        uphill = np.asarray([gx[ix, iy], gy[ix, iy]], dtype=np.float64)
        uphill_norm = float(np.linalg.norm(uphill))
        if uphill_norm < 1e-8:
            continue
        uphill /= uphill_norm

        delta = source_points[:, :2] - rough[i, :2]
        along = delta @ tangent
        cross = delta @ uphill
        local = np.abs(along) <= 1.25

        if profile == "ridge":
            bench_mask = local & (cross >= 0.35) & (cross <= 2.20)
            face_mask = local & (cross <= -0.20) & (cross >= -2.20)
        else:
            face_mask = local & (cross >= 0.20) & (cross <= 2.20)
            bench_mask = local & (cross <= -0.35) & (cross >= -2.20)

        bench = source_points[bench_mask]
        face = source_points[face_mask]

        if len(bench) < 8 or len(face) < 8:
            continue

        p_bench = _robust_plane(bench)
        p_face = _robust_plane(face)
        if p_bench is None or p_face is None:
            continue

        a = float(p_bench[0] - p_face[0])
        b = float(p_bench[1] - p_face[1])
        c = float(p_bench[2] - p_face[2])

        denominator = a * uphill[0] + b * uphill[1]
        if abs(denominator) < 1e-8:
            continue

        t = -(
            a * rough[i, 0]
            + b * rough[i, 1]
            + c
        ) / denominator

        if not np.isfinite(t) or abs(float(t)) > 0.55:
            continue

        xy = rough[i, :2] + float(t) * uphill
        z_bench = p_bench[0] * xy[0] + p_bench[1] * xy[1] + p_bench[2]
        z_face = p_face[0] * xy[0] + p_face[1] * xy[1] + p_face[2]

        refined[i, 0] = xy[0]
        refined[i, 1] = xy[1]
        refined[i, 2] = 0.5 * (z_bench + z_face)
        success += 1

    return refined, success


def _smooth_polyline(
    points: np.ndarray,
    passes: int = 1,
) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 5 or passes <= 0:
        return pts.copy()

    current = pts.copy()
    weights = np.asarray([1.0, 2.0, 3.0, 2.0, 1.0], dtype=np.float64)
    weights /= float(np.sum(weights))

    for _ in range(int(passes)):
        output = current.copy()
        for i in range(2, len(current) - 2):
            output[i] = np.sum(
                current[i - 2:i + 3] * weights[:, None],
                axis=0,
            )
        output[0] = current[0]
        output[-1] = current[-1]
        current = output

    return current


def _limit_segment_length(
    simplified: np.ndarray,
    source: np.ndarray,
    maximum: float = 7.5,
) -> np.ndarray:
    if len(simplified) <= 1:
        return simplified

    output = [simplified[0]]
    for target in simplified[1:]:
        previous = output[-1]
        distance = float(np.linalg.norm(target[:2] - previous[:2]))
        if distance <= maximum:
            output.append(target)
            continue

        count = int(math.ceil(distance / maximum))
        d = np.linalg.norm(source[:, :2] - previous[:2], axis=1)
        start = int(np.argmin(d))
        d = np.linalg.norm(source[:, :2] - target[:2], axis=1)
        end = int(np.argmin(d))

        if end <= start + 1:
            output.append(target)
            continue

        for k in range(1, count):
            idx = int(round(start + (end - start) * (k / count)))
            idx = min(max(idx, start + 1), end - 1)
            output.append(source[idx])
        output.append(target)

    return np.asarray(output, dtype=np.float64)


def extract_terrain_face_edge(
    points_xyz: np.ndarray,
    seed_xyz,
    *,
    profile: str = "ridge",
    grid_resolution: float = 0.20,
) -> TerrainFaceResult:
    """Extract crest/toe from the boundaries of a steep terrain face.

    Geometric model:
        upper bench  ______
                           \
                            \\   steep face
                              ______ lower bench

    The face is first found as a connected high-slope DTM region. Crest and toe
    are its uphill/downhill boundaries. Approximate boundary nodes are then
    refined by intersecting a locally fitted bench plane with the face plane.
    """

    points = np.asarray(points_xyz, dtype=np.float64)
    seed = np.asarray(seed_xyz, dtype=np.float64)
    key = str(profile).strip().lower()

    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("Pontos de terreno inválidos; esperado N x 3.")
    if len(points) < 500:
        raise ValueError("Poucos pontos de terreno na janela local.")
    if seed.shape != (3,):
        raise ValueError("Seed XYZ inválido.")
    if key not in {"ridge", "toe"}:
        raise ValueError("O extrator de face suporta Crista ou Pé.")

    requested_resolution = float(np.clip(grid_resolution, 0.12, 0.35))

    # A janela enviada pelo Potree usa LOD: ao afastar a câmara podem chegar
    # poucas centenas de pontos para uma área grande. Uma grelha demasiado fina
    # ficaria artificialmente vazia e fazia o motor falhar antes de analisar a
    # geometria. Escolhemos portanto uma resolução local adaptada à densidade,
    # mantendo a grelha fina quando a amostra é densa.
    span_x = max(float(np.ptp(points[:, 0])), requested_resolution)
    span_y = max(float(np.ptp(points[:, 1])), requested_resolution)
    window_area = max(span_x * span_y, requested_resolution * requested_resolution)
    density_resolution = math.sqrt(
        max(0.0, 0.18 * window_area / max(len(points), 1))
    )
    resolution = float(
        np.clip(
            max(requested_resolution, density_resolution),
            0.12,
            0.75,
        )
    )

    dem, valid, x0, y0 = _rasterize_dem(points, resolution)
    coverage = float(np.count_nonzero(valid)) / float(valid.size)

    # Segunda tentativa automática para LOD especialmente esparso.
    if coverage < 0.06 and resolution < 0.95:
        factor = math.sqrt(0.085 / max(coverage, 1e-6))
        retry_resolution = float(np.clip(resolution * factor, resolution, 0.95))
        if retry_resolution > resolution * 1.05:
            resolution = retry_resolution
            dem, valid, x0, y0 = _rasterize_dem(points, resolution)
            coverage = float(np.count_nonzero(valid)) / float(valid.size)

    if coverage < 0.035:
        raise ValueError(
            "Poucos pontos locais para formar a superfície do talude. "
            "Aproxime a vista da face e clique novamente."
        )

    filled, filled_mask = _fill_sparse(dem, valid, iterations=8)
    smooth = _smooth5(filled, filled_mask, passes=2)

    gx, gy = np.gradient(smooth, resolution, resolution)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    slope = np.where(filled_mask, slope, 0.0)

    face_mask, low_threshold, high_threshold = _hysteresis_face_mask(
        slope,
        filled_mask,
    )

    seed_cell = (
        (seed[0] - x0) / resolution,
        (seed[1] - y0) / resolution,
    )
    component = _choose_face_component(
        face_mask,
        seed_cell,
        resolution,
    )

    candidate = _edge_candidates(component, gx, gy, key)
    path_cells = _ordered_candidate_path(
        candidate,
        seed_cell,
        resolution,
    )

    rough = np.empty((len(path_cells), 3), dtype=np.float64)
    rough[:, 0] = x0 + (path_cells[:, 0] + 0.5) * resolution
    rough[:, 1] = y0 + (path_cells[:, 1] + 0.5) * resolution
    rough[:, 2] = smooth[path_cells[:, 0], path_cells[:, 1]]

    # Alpha 10.4 edge-lock:
    # 1) remove raster stair-stepping without moving surviving vertices;
    # 2) snap each control point across the slope profile to the actual
    #    flat<->steep transition;
    # 3) use the plane intersection only as a sub-cell refinement and clamp it
    #    tightly to the snapped breakline so it cannot drift into the bench or
    #    into the talude face;
    # 4) simplify strongly while retaining source-edge control points.
    rough_reduced = _rdp(rough, max(0.26, resolution * 1.30))

    snapped, snap_success, median_snap_offset = _snap_profile_transition(
        rough_reduced,
        smooth,
        slope,
        gx,
        gy,
        x0,
        y0,
        resolution,
        key,
        low_threshold,
        high_threshold,
    )

    plane_candidate, plane_success = _refine_plane_intersections(
        snapped,
        points,
        gx,
        gy,
        x0,
        y0,
        resolution,
        key,
    )

    refined = snapped.copy()
    plane_offsets = np.linalg.norm(
        plane_candidate[:, :2] - snapped[:, :2],
        axis=1,
    )
    accepted_plane = (
        np.isfinite(plane_offsets)
        & (plane_offsets > 1e-7)
        & (plane_offsets <= 0.40)
    )
    refined[accepted_plane] = plane_candidate[accepted_plane]
    success = int(np.count_nonzero(accepted_plane))

    final = _rdp(refined, max(0.30, resolution * 1.50))
    final = _limit_segment_length(
        final,
        refined,
        maximum=12.0,
    )

    if len(final) < 2:
        raise ValueError("A aresta final ficou demasiado curta.")

    face_slopes = slope[component]
    face_slope = float(np.median(face_slopes)) if len(face_slopes) else 0.0
    refinement_ratio = success / max(len(rough_reduced), 1)
    snap_ratio = snap_success / max(len(rough_reduced), 1)

    contrast = np.clip(
        (face_slope - low_threshold) / max(high_threshold - low_threshold, 6.0),
        0.0,
        1.0,
    )
    confidence = float(
        np.clip(
            0.48 * contrast
            + 0.22 * refinement_ratio
            + 0.20 * snap_ratio
            + 0.10 * min(1.0, len(path_cells) * resolution / 12.0),
            0.0,
            1.0,
        )
    )

    return TerrainFaceResult(
        vertices=final.astype(np.float64, copy=False),
        raw_vertices=int(len(path_cells)),
        rough_vertices=int(len(rough_reduced)),
        refined_vertices=int(success),
        refinement_ratio=float(refinement_ratio),
        snapped_vertices=int(snap_success),
        snap_ratio=float(snap_ratio),
        median_snap_offset_m=float(median_snap_offset),
        confidence=confidence,
        face_slope_deg=face_slope,
        low_slope_threshold_deg=float(low_threshold),
        high_slope_threshold_deg=float(high_threshold),
        grid_resolution=float(resolution),
        source_points=int(len(points)),
        profile=key,
        face_cells=int(np.count_nonzero(component)),
    )
