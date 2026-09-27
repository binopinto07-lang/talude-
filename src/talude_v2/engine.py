from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import Delaunay, QhullError, cKDTree

from .reasons import V2DetectionError, V2Reason


@dataclass(slots=True)
class V2Config:
    """Parameters for the experimental RAW-TIN geometry engine."""

    target_tin_spacing_m: float = 0.25
    max_tin_points: int = 45_000
    min_face_slope_deg: float = 14.0
    max_local_normal_change_deg: float = 32.0
    max_triangle_edge_m: float = 2.25
    boundary_side_cos_min: float = 0.42
    graph_gap_m: float = 1.50
    station_spacing_m: float = 1.00
    patch_along_m: float = 2.50
    patch_cross_m: float = 1.80
    patch_deadband_m: float = 0.12
    patch_min_points: int = 14
    max_refine_shift_m: float = 1.25
    refine_smooth_window: int = 5
    refine_bridge_stations: int = 2
    refine_support_target: int = 28

    # Phase 3 tiled AUTO defaults. These do not alter clicked-face behaviour;
    # they are consumed by the global tiled orchestration layer.
    tile_size_m: float = 60.0
    tile_halo_m: float = 10.0
    tile_min_fragment_m: float = 2.0
    tile_stitch_gap_m: float = 4.0
    tile_min_coverage_ratio: float = 0.55
    random_seed: int = 1701


def _finite_points(points_xyz: np.ndarray) -> np.ndarray:
    pts = np.asarray(points_xyz, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise V2DetectionError(
            V2Reason.NO_GROUND,
            "V2 esperava pontos Ground em matriz N x 3.",
        )
    pts = pts[np.all(np.isfinite(pts), axis=1)]
    if len(pts) < 100:
        raise V2DetectionError(
            V2Reason.LOW_GROUND_SUPPORT,
            "V2 recebeu poucos pontos para formar uma TIN local.",
        )
    return pts


def _xy_voxel_median(
    points: np.ndarray,
    spacing: float,
    max_points: int,
) -> tuple[np.ndarray, float]:
    """Reduce RAW points by XY cells using median XYZ, preserving terrain shape."""
    spacing = max(float(spacing), 0.05)
    pts = np.asarray(points, dtype=np.float64)

    for _ in range(7):
        x0, y0 = np.min(pts[:, :2], axis=0)
        ix = np.floor((pts[:, 0] - x0) / spacing).astype(np.int64)
        iy = np.floor((pts[:, 1] - y0) / spacing).astype(np.int64)
        key = np.rec.fromarrays((ix, iy), names=("x", "y"))
        _, inv = np.unique(key, return_inverse=True)
        groups = int(inv.max()) + 1 if len(inv) else 0

        if groups <= max_points:
            counts = np.bincount(inv, minlength=groups).astype(np.int64)
            mean_x = np.bincount(inv, weights=pts[:, 0], minlength=groups) / counts
            mean_y = np.bincount(inv, weights=pts[:, 1], minlength=groups) / counts

            # Robust Z without tens of thousands of Python-level group loops:
            # lexsort by (group, Z), then pick the middle observation per group.
            order = np.lexsort((pts[:, 2], inv))
            starts = np.r_[0, np.cumsum(counts[:-1])]
            median_pos = starts + (counts - 1) // 2
            median_z = pts[order[median_pos], 2]

            out = np.column_stack((mean_x, mean_y, median_z)).astype(
                np.float64,
                copy=False,
            )
            return out, spacing

        spacing *= math.sqrt(groups / max_points) * 1.05

    # Last-resort deterministic thinning if the scene is extraordinarily dense.
    step = max(1, int(math.ceil(len(pts) / max_points)))
    return pts[::step][:max_points].copy(), spacing


def _triangle_geometry(
    points: np.ndarray,
    simplices: np.ndarray,
) -> dict[str, np.ndarray]:
    tri = points[simplices]
    e1 = tri[:, 1] - tri[:, 0]
    e2 = tri[:, 2] - tri[:, 0]
    n = np.cross(e1, e2)
    norm = np.linalg.norm(n, axis=1)
    good = norm > 1e-10
    n[good] /= norm[good, None]
    flip = n[:, 2] < 0
    n[flip] *= -1.0

    nz = np.clip(n[:, 2], 0.0, 1.0)
    slope = np.degrees(np.arccos(nz))
    centroid = tri.mean(axis=1)

    # For z = ax + by + c and upward normal (-a,-b,1), downhill is (a,b)
    # which equals (-nx/nz, -ny/nz) with sign corrected below.
    downhill = np.zeros((len(n), 2), dtype=np.float64)
    stable = np.abs(n[:, 2]) > 1e-8
    downhill[stable, 0] = n[stable, 0] / n[stable, 2]
    downhill[stable, 1] = n[stable, 1] / n[stable, 2]
    dnorm = np.linalg.norm(downhill, axis=1)
    ok = dnorm > 1e-10
    downhill[ok] /= dnorm[ok, None]

    edges = np.stack(
        (
            np.linalg.norm(tri[:, 1, :2] - tri[:, 0, :2], axis=1),
            np.linalg.norm(tri[:, 2, :2] - tri[:, 1, :2], axis=1),
            np.linalg.norm(tri[:, 0, :2] - tri[:, 2, :2], axis=1),
        ),
        axis=1,
    )

    return {
        "normal": n,
        "slope": slope,
        "centroid": centroid,
        "downhill": downhill,
        "max_edge": edges.max(axis=1),
    }


def _seed_triangle(
    geom: dict[str, np.ndarray],
    seed: np.ndarray,
    cfg: V2Config,
) -> int:
    cent = geom["centroid"]
    dist = np.linalg.norm(cent[:, :2] - seed[:2], axis=1)
    slope = geom["slope"]
    eligible = (
        np.isfinite(slope)
        & (slope >= cfg.min_face_slope_deg)
        & (geom["max_edge"] <= cfg.max_triangle_edge_m)
    )
    ids = np.flatnonzero(eligible)
    if len(ids) == 0:
        raise V2DetectionError(
            V2Reason.NO_FACE,
            "V2 não encontrou triângulos inclinados junto ao clique.",
        )

    # Strongly prefer the nearest inclined triangle, but allow a tiny relief bias.
    cost = dist[ids] - 0.008 * slope[ids]
    best = int(ids[int(np.argmin(cost))])
    if float(dist[best]) > max(5.0, cfg.graph_gap_m * 3.0):
        raise V2DetectionError(
            V2Reason.NO_FACE,
            "O clique ficou demasiado longe de uma face TIN inclinada.",
        )
    return best


def _grow_face(
    tri: Delaunay,
    geom: dict[str, np.ndarray],
    seed_id: int,
    cfg: V2Config,
) -> np.ndarray:
    normals = geom["normal"]
    slopes = geom["slope"]
    max_edge = geom["max_edge"]
    selected = np.zeros(len(tri.simplices), dtype=bool)
    selected[seed_id] = True
    queue = [seed_id]
    head = 0
    cos_limit = math.cos(math.radians(cfg.max_local_normal_change_deg))

    while head < len(queue):
        current = queue[head]
        head += 1
        for nxt in tri.neighbors[current]:
            if nxt < 0 or selected[nxt]:
                continue
            if slopes[nxt] < cfg.min_face_slope_deg:
                continue
            if max_edge[nxt] > cfg.max_triangle_edge_m:
                continue

            dot = float(np.dot(normals[current], normals[nxt]))
            if dot < cos_limit:
                continue

            selected[nxt] = True
            queue.append(int(nxt))

    if int(selected.sum()) < 3:
        raise V2DetectionError(
            V2Reason.FACE_TOO_SMALL,
            "V2 encontrou uma face TIN demasiado pequena.",
        )
    return selected


def _local_face_coherence(
    tri: Delaunay,
    geom: dict[str, np.ndarray],
    selected: np.ndarray,
) -> dict[str, float]:
    """Measure coherence locally, never against one global mean direction.

    Curved and S-shaped taludes are valid when neighbouring TIN triangles remain
    locally compatible. Opposing directions at distant ends therefore do not
    cancel each other as they did in the legacy global-vector metric.
    """
    ids = np.flatnonzero(selected)
    if len(ids) < 2:
        return {
            "local_normal_coherence": 0.0,
            "local_direction_median_deg": 180.0,
            "local_direction_p95_deg": 180.0,
            "local_pairs": 0,
        }

    normals = geom["normal"]
    downhill = geom["downhill"]
    angles: list[float] = []
    normal_dots: list[float] = []

    for current in ids:
        for nxt in tri.neighbors[int(current)]:
            if nxt < 0 or not selected[int(nxt)] or int(nxt) <= int(current):
                continue

            dot_n = float(np.clip(np.dot(normals[current], normals[nxt]), -1.0, 1.0))
            normal_dots.append(max(dot_n, 0.0))

            a = downhill[current]
            b = downhill[nxt]
            na = float(np.linalg.norm(a))
            nb = float(np.linalg.norm(b))
            if na <= 1e-9 or nb <= 1e-9:
                continue
            dot_d = float(np.clip(np.dot(a / na, b / nb), -1.0, 1.0))
            angles.append(math.degrees(math.acos(dot_d)))

    if not normal_dots:
        return {
            "local_normal_coherence": 0.0,
            "local_direction_median_deg": 180.0,
            "local_direction_p95_deg": 180.0,
            "local_pairs": 0,
        }

    return {
        "local_normal_coherence": float(np.mean(normal_dots)),
        "local_direction_median_deg": (
            float(np.median(angles)) if angles else 0.0
        ),
        "local_direction_p95_deg": (
            float(np.percentile(angles, 95.0)) if angles else 0.0
        ),
        "local_pairs": int(len(normal_dots)),
    }


def _edge_incidence(simplices: np.ndarray) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """Map undirected TIN edge -> [(triangle_id, local_edge_id), ...]."""
    table: dict[tuple[int, int], list[tuple[int, int]]] = {}
    local_edges = ((0, 1), (1, 2), (2, 0))
    for tid, simplex in enumerate(simplices):
        for eid, (a, b) in enumerate(local_edges):
            va = int(simplex[a])
            vb = int(simplex[b])
            key = (va, vb) if va < vb else (vb, va)
            table.setdefault(key, []).append((tid, eid))
    return table


def _boundary_candidates(
    points: np.ndarray,
    tri: Delaunay,
    geom: dict[str, np.ndarray],
    selected: np.ndarray,
    cfg: V2Config,
) -> dict[str, list[tuple[int, int, float]]]:
    table = _edge_incidence(tri.simplices)
    out: dict[str, list[tuple[int, int, float]]] = {"CREST": [], "TOE": []}

    for edge, refs in table.items():
        inside = [tid for tid, _ in refs if selected[tid]]
        outside = [tid for tid, _ in refs if not selected[tid]]
        if len(inside) != 1:
            continue

        face_id = inside[0]
        a, b = edge
        pa = points[a]
        pb = points[b]
        mid = 0.5 * (pa + pb)
        edge_vec = pb[:2] - pa[:2]
        edge_len = float(np.linalg.norm(edge_vec))
        if edge_len <= 1e-8 or edge_len > cfg.max_triangle_edge_m * 1.4:
            continue

        downhill = geom["downhill"][face_id]
        dnorm = float(np.linalg.norm(downhill))
        if dnorm <= 1e-9:
            continue

        # Crest/toe edges are roughly transverse to downhill; side boundaries
        # run roughly parallel to it.
        edge_unit = edge_vec / edge_len
        transverse = abs(float(np.dot(edge_unit, downhill)))
        if transverse > cfg.boundary_side_cos_min:
            continue

        if outside:
            other = outside[0]
            delta = geom["centroid"][other, :2] - geom["centroid"][face_id, :2]
            d = float(np.linalg.norm(delta))
            if d <= 1e-9:
                continue
            side = float(np.dot(delta / d, downhill))
            angle = math.degrees(
                math.acos(
                    float(
                        np.clip(
                            np.dot(geom["normal"][face_id], geom["normal"][other]),
                            -1.0,
                            1.0,
                        )
                    )
                )
            )
            slope_contrast = max(
                0.0,
                float(geom["slope"][face_id] - geom["slope"][other]),
            )
            score = 0.7 * angle + 0.3 * slope_contrast
        else:
            # Convex-hull edge. Keep only if clearly aligned with uphill/downhill.
            face_center = geom["centroid"][face_id, :2]
            delta = mid[:2] - face_center
            d = float(np.linalg.norm(delta))
            if d <= 1e-9:
                continue
            side = float(np.dot(delta / d, downhill))
            score = 8.0

        if abs(side) < 0.25:
            continue

        kind = "TOE" if side > 0 else "CREST"
        out[kind].append((a, b, float(max(score, 0.1))))

    return out


def _connect_short_gaps(
    points: np.ndarray,
    edges: list[tuple[int, int, float]],
    gap_m: float,
) -> list[tuple[int, int, float]]:
    if not edges or gap_m <= 0:
        return edges

    degree: dict[int, int] = {}
    for a, b, _ in edges:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    endpoints = [v for v, d in degree.items() if d == 1]
    if len(endpoints) < 2:
        return edges

    ep_xy = points[endpoints, :2]
    tree = cKDTree(ep_xy)
    pairs = tree.query_pairs(r=float(gap_m))
    existing = {(min(a, b), max(a, b)) for a, b, _ in edges}
    extra: list[tuple[int, int, float]] = []

    for i, j in sorted(pairs):
        a = endpoints[i]
        b = endpoints[j]
        key = (min(a, b), max(a, b))
        if key in existing:
            continue
        dist = float(np.linalg.norm(points[a, :2] - points[b, :2]))
        if dist <= 1e-8:
            continue
        # Gap edges are deliberately expensive in the MST.
        extra.append((a, b, max(0.05, 0.15 / dist)))

    return edges + extra


def _kruskal_tree(
    points: np.ndarray,
    edges: list[tuple[int, int, float]],
) -> list[tuple[int, int]]:
    """Kruskal tree with low cost for strong, short candidate edges."""
    if not edges:
        return []

    vertices = sorted({a for a, _, _ in edges} | {b for _, b, _ in edges})
    parent = {v: v for v in vertices}
    rank = {v: 0 for v in vertices}

    def find(v: int) -> int:
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    def union(a: int, b: int) -> bool:
        ra = find(a)
        rb = find(b)
        if ra == rb:
            return False
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1
        return True

    weighted = []
    for a, b, score in edges:
        length = float(np.linalg.norm(points[a, :2] - points[b, :2]))
        cost = length / max(score, 0.05)
        weighted.append((cost, a, b))
    weighted.sort(key=lambda x: x[0])

    tree_edges: list[tuple[int, int]] = []
    for _, a, b in weighted:
        if union(a, b):
            tree_edges.append((a, b))
    return tree_edges


def _longest_tree_path(
    points: np.ndarray,
    tree_edges: list[tuple[int, int]],
) -> np.ndarray:
    if not tree_edges:
        return np.empty((0,), dtype=np.int64)

    adj: dict[int, list[tuple[int, float]]] = {}
    for a, b in tree_edges:
        w = float(np.linalg.norm(points[a, :2] - points[b, :2]))
        adj.setdefault(a, []).append((b, w))
        adj.setdefault(b, []).append((a, w))

    # Split forest into components and keep the component with the longest diameter.
    unseen = set(adj)
    best_path: list[int] = []
    best_length = -1.0

    def farthest(start: int, allowed: set[int]) -> tuple[int, dict[int, int], dict[int, float]]:
        parent = {start: -1}
        dist = {start: 0.0}
        stack = [start]
        while stack:
            node = stack.pop()
            for nxt, w in adj.get(node, []):
                if nxt not in allowed or nxt in parent:
                    continue
                parent[nxt] = node
                dist[nxt] = dist[node] + w
                stack.append(nxt)
        far = max(dist, key=dist.get)
        return far, parent, dist

    while unseen:
        root = next(iter(unseen))
        comp = set()
        stack = [root]
        while stack:
            node = stack.pop()
            if node in comp:
                continue
            comp.add(node)
            unseen.discard(node)
            stack.extend(n for n, _ in adj.get(node, []))
        a, _, _ = farthest(root, comp)
        b, parent, dist = farthest(a, comp)
        path = [b]
        cur = b
        while cur != a and cur in parent and parent[cur] >= 0:
            cur = parent[cur]
            path.append(cur)
        path.reverse()
        length = float(dist.get(b, 0.0))
        if length > best_length:
            best_length = length
            best_path = path

    return np.asarray(best_path, dtype=np.int64)


def _polyline_from_candidates(
    points: np.ndarray,
    edges: list[tuple[int, int, float]],
    cfg: V2Config,
) -> tuple[np.ndarray, dict[str, int]]:
    original_count = len(edges)
    connected = _connect_short_gaps(points, edges, cfg.graph_gap_m)
    gap_links = max(0, len(connected) - original_count)
    tree = _kruskal_tree(points, connected)
    ids = _longest_tree_path(points, tree)
    if len(ids) < 2:
        raise V2DetectionError(
            V2Reason.LOW_CONTINUITY,
            "V2 não conseguiu ordenar o boundary num caminho contínuo.",
        )
    return points[ids].copy(), {
        "candidate_edges": int(original_count),
        "graph_edges": int(len(connected)),
        "gap_links": int(gap_links),
        "mst_edges": int(len(tree)),
        "path_vertices": int(len(ids)),
    }


def _resample_polyline(xyz: np.ndarray, spacing: float) -> np.ndarray:
    pts = np.asarray(xyz, dtype=np.float64)
    if len(pts) < 2:
        return pts
    seg = np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1)
    keep = np.r_[True, seg > 1e-9]
    pts = pts[keep]
    if len(pts) < 2:
        return pts

    dist = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1))]
    total = float(dist[-1])
    if total <= spacing:
        return np.vstack((pts[0], pts[-1]))

    sample = np.arange(0.0, total, max(float(spacing), 0.20))
    if len(sample) == 0 or abs(sample[-1] - total) > 1e-9:
        sample = np.r_[sample, total]

    return np.column_stack(
        (
            np.interp(sample, dist, pts[:, 0]),
            np.interp(sample, dist, pts[:, 1]),
            np.interp(sample, dist, pts[:, 2]),
        )
    )


def _robust_plane(
    points: np.ndarray,
    min_points: int,
) -> tuple[np.ndarray, float, int, float] | None:
    """Robust local plane with explicit inlier support.

    Phase 4 uses support as a first-class quality signal. A numerically valid
    plane based on barely enough observations is intentionally treated as
    weaker than one supported by a dense, coherent patch.
    """
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < min_points:
        return None

    original_count = int(len(pts))
    work = pts
    coef = None
    rmse = float("inf")
    for _ in range(4):
        A = np.column_stack((work[:, 0], work[:, 1], np.ones(len(work))))
        try:
            coef, *_ = np.linalg.lstsq(A, work[:, 2], rcond=None)
        except np.linalg.LinAlgError:
            return None
        residual = work[:, 2] - A @ coef
        med = float(np.median(residual))
        mad = float(np.median(np.abs(residual - med)))
        sigma = max(1.4826 * mad, 0.008)
        keep = np.abs(residual - med) <= 3.0 * sigma
        kept = int(keep.sum())
        rmse = (
            float(math.sqrt(np.mean(residual[keep] ** 2)))
            if kept
            else float("inf")
        )
        if kept == len(work) or kept < min_points:
            break
        work = work[keep]

    if coef is None:
        return None

    support = int(len(work))
    inlier_ratio = float(np.clip(support / max(original_count, 1), 0.0, 1.0))
    return np.asarray(coef, dtype=np.float64), rmse, support, inlier_ratio


def _closest_point_on_plane_intersection_xy(
    plane_a: np.ndarray,
    plane_b: np.ndarray,
    station_xy: np.ndarray,
) -> tuple[np.ndarray, float] | None:
    # z = ax + by + c. Equality of two planes gives A*x + B*y + C = 0.
    A = float(plane_a[0] - plane_b[0])
    B = float(plane_a[1] - plane_b[1])
    C = float(plane_a[2] - plane_b[2])
    denom = A * A + B * B
    if denom <= 1e-12:
        return None

    x0, y0 = float(station_xy[0]), float(station_xy[1])
    t = (A * x0 + B * y0 + C) / denom
    x = x0 - A * t
    y = y0 - B * t
    z1 = float(plane_a[0] * x + plane_a[1] * y + plane_a[2])
    z2 = float(plane_b[0] * x + plane_b[1] * y + plane_b[2])
    return np.array([x, y, 0.5 * (z1 + z2)], dtype=np.float64), abs(t) * math.sqrt(denom)


def _nearest_face_downhill(
    stations: np.ndarray,
    geom: dict[str, np.ndarray],
    selected: np.ndarray,
) -> np.ndarray:
    ids = np.flatnonzero(selected)
    cent = geom["centroid"][ids, :2]
    tree = cKDTree(cent)
    _, nearest = tree.query(stations[:, :2], k=1)
    return geom["downhill"][ids[np.asarray(nearest, dtype=np.int64)]]


def _support_aware_smooth(
    stations: np.ndarray,
    refined: np.ndarray,
    valid: np.ndarray,
    support_weight: np.ndarray,
    window: int,
) -> np.ndarray:
    """Smooth refinement deltas only, preserving the detected line topology.

    The preliminary TIN stations remain the reference. Robust local deltas are
    combined with support weights so a weak patch cannot pull neighbouring
    well-supported stations into a spike.
    """
    out = np.asarray(refined, dtype=np.float64).copy()
    if len(out) < 3 or int(np.sum(valid)) < 2:
        return out

    win = max(3, int(window))
    if win % 2 == 0:
        win += 1
    half = win // 2
    delta = out - stations

    for i in np.flatnonzero(valid):
        lo = max(0, int(i) - half)
        hi = min(len(out), int(i) + half + 1)
        ids = np.flatnonzero(valid[lo:hi]) + lo
        if len(ids) < 2:
            continue

        local = delta[ids]
        med = np.median(local, axis=0)
        dev = np.linalg.norm(local[:, :2] - med[:2], axis=1)
        mad = float(np.median(np.abs(dev - np.median(dev))))
        gate = max(0.15, 3.0 * 1.4826 * mad)
        keep = dev <= gate
        ids = ids[keep]
        if len(ids) == 0:
            continue

        weights = np.maximum(support_weight[ids], 1e-3)
        mean_delta = np.average(delta[ids], axis=0, weights=weights)
        own_weight = float(np.clip(support_weight[i], 0.0, 1.0))
        blend = 0.35 + 0.45 * own_weight
        smoothed_delta = blend * delta[i] + (1.0 - blend) * mean_delta
        out[i] = stations[i] + smoothed_delta

    return out


def _bridge_short_refine_gaps(
    stations: np.ndarray,
    refined: np.ndarray,
    valid: np.ndarray,
    max_gap: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate only short unsupported runs bounded by trusted stations."""
    out = np.asarray(refined, dtype=np.float64).copy()
    bridged = np.zeros(len(out), dtype=bool)
    max_gap = max(0, int(max_gap))
    if max_gap <= 0 or len(out) < 3:
        return out, bridged

    i = 0
    while i < len(out):
        if valid[i]:
            i += 1
            continue
        start = i
        while i < len(out) and not valid[i]:
            i += 1
        end = i
        gap = end - start

        if (
            gap <= max_gap
            and start > 0
            and end < len(out)
            and valid[start - 1]
            and valid[end]
        ):
            a = out[start - 1]
            b = out[end]
            for j in range(gap):
                t = (j + 1) / (gap + 1)
                out[start + j] = (1.0 - t) * a + t * b
                bridged[start + j] = True

    return out, bridged


def _refine_by_surface_intersection(
    preliminary: np.ndarray,
    raw_points: np.ndarray,
    kind: str,
    geom: dict[str, np.ndarray],
    selected: np.ndarray,
    cfg: V2Config,
) -> tuple[np.ndarray, dict]:
    stations = _resample_polyline(preliminary, cfg.station_spacing_m)
    if len(stations) < 2:
        return stations, {
            "refined": 0,
            "bridged": 0,
            "fallback": len(stations),
            "support_ratio": 0.0,
            "support_median": None,
            "support_p10": None,
            "plane_rmse_median": None,
            "mean_shift_m": None,
            "p95_shift_m": None,
            "max_shift_m": None,
        }

    tree = cKDTree(raw_points[:, :2])
    downhill = _nearest_face_downhill(stations, geom, selected)
    out = stations.copy()
    valid = np.zeros(len(stations), dtype=bool)
    support_weight = np.zeros(len(stations), dtype=np.float64)
    rmse_values: list[float] = []
    shifts: list[float] = []
    supports: list[int] = []

    for i, station in enumerate(stations):
        if i == 0:
            tangent = stations[1, :2] - stations[0, :2]
        elif i == len(stations) - 1:
            tangent = stations[-1, :2] - stations[-2, :2]
        else:
            tangent = stations[i + 1, :2] - stations[i - 1, :2]
        tnorm = float(np.linalg.norm(tangent))
        if tnorm <= 1e-9:
            continue
        tangent /= tnorm

        cross = downhill[i].copy()
        cnorm = float(np.linalg.norm(cross))
        if cnorm <= 1e-9:
            cross = np.array([-tangent[1], tangent[0]])
        else:
            cross /= cnorm

        radius = math.hypot(cfg.patch_along_m * 0.5, cfg.patch_cross_m) + 0.25
        ids = tree.query_ball_point(station[:2], r=radius)
        if len(ids) < cfg.patch_min_points * 2:
            continue
        pts = raw_points[np.asarray(ids, dtype=np.int64)]
        rel = pts[:, :2] - station[:2]
        u = rel @ tangent
        v = rel @ cross
        along = np.abs(u) <= cfg.patch_along_m * 0.5

        if kind == "CREST":
            side_a = (
                along
                & (v <= -cfg.patch_deadband_m)
                & (v >= -cfg.patch_cross_m)
            )
            side_b = (
                along
                & (v >= cfg.patch_deadband_m)
                & (v <= cfg.patch_cross_m)
            )
        else:
            side_a = (
                along
                & (v >= cfg.patch_deadband_m)
                & (v <= cfg.patch_cross_m)
            )
            side_b = (
                along
                & (v <= -cfg.patch_deadband_m)
                & (v >= -cfg.patch_cross_m)
            )

        fit_a = _robust_plane(pts[side_a], cfg.patch_min_points)
        fit_b = _robust_plane(pts[side_b], cfg.patch_min_points)
        if fit_a is None or fit_b is None:
            continue

        point = _closest_point_on_plane_intersection_xy(
            fit_a[0],
            fit_b[0],
            station[:2],
        )
        if point is None:
            continue

        refined_point, shift = point
        if shift > cfg.max_refine_shift_m:
            continue

        combined_support = min(int(fit_a[2]), int(fit_b[2]))
        inlier_ratio = min(float(fit_a[3]), float(fit_b[3]))
        support_score = min(
            combined_support / max(int(cfg.refine_support_target), 1),
            1.0,
        )
        rmse_score = math.exp(
            -0.5 * (float(fit_a[1]) + float(fit_b[1]))
            / max(cfg.target_tin_spacing_m, 0.05)
        )
        weight = float(
            np.clip(0.55 * support_score + 0.25 * inlier_ratio + 0.20 * rmse_score, 0.0, 1.0)
        )

        out[i] = refined_point
        valid[i] = True
        support_weight[i] = weight
        shifts.append(float(shift))
        supports.append(combined_support)
        rmse_values.extend((float(fit_a[1]), float(fit_b[1])))

    out = _support_aware_smooth(
        stations,
        out,
        valid,
        support_weight,
        cfg.refine_smooth_window,
    )
    out, bridged = _bridge_short_refine_gaps(
        stations,
        out,
        valid,
        cfg.refine_bridge_stations,
    )

    refined = int(np.sum(valid))
    bridged_count = int(np.sum(bridged))
    fallback = int(max(0, len(stations) - refined - bridged_count))

    # Preserve real 3D for unsupported ends/runs using the closest RAW point Z.
    _, nearest = tree.query(out[:, :2], k=1)
    nearest_z = raw_points[np.asarray(nearest, dtype=np.int64), 2]
    unsupported = ~(valid | bridged)
    if np.any(unsupported):
        out[unsupported, 2] = nearest_z[unsupported]

    support_ratio = float(
        np.clip((refined + bridged_count) / max(len(stations), 1), 0.0, 1.0)
    )
    return out, {
        "refined": refined,
        "bridged": bridged_count,
        "fallback": fallback,
        "support_ratio": support_ratio,
        "support_median": float(np.median(supports)) if supports else None,
        "support_p10": float(np.percentile(supports, 10.0)) if supports else None,
        "plane_rmse_median": (
            float(np.median(rmse_values)) if rmse_values else None
        ),
        "mean_shift_m": float(np.mean(shifts)) if shifts else None,
        "p95_shift_m": float(np.percentile(shifts, 95.0)) if shifts else None,
        "max_shift_m": float(np.max(shifts)) if shifts else None,
    }

def _polyline_lengths(xyz: np.ndarray) -> tuple[float, float]:
    """Accumulated 2D/3D polyline lengths, never endpoint distance."""
    pts = np.asarray(xyz, dtype=np.float64)
    if len(pts) < 2:
        return 0.0, 0.0
    delta = np.diff(pts, axis=0)
    length_2d = float(np.linalg.norm(delta[:, :2], axis=1).sum())
    length_3d = float(np.linalg.norm(delta, axis=1).sum())
    return length_2d, length_3d


def _length_xy(xyz: np.ndarray) -> float:
    # Compatibility alias used by existing callers.
    return _polyline_lengths(xyz)[0]


def extract_face_raw_tin(
    points_xyz,
    seed_xyz,
    *,
    config: V2Config | None = None,
) -> dict:
    """Extract one clicked slope face with RAW-TIN + graph + plane refinement."""
    cfg = config or V2Config()
    raw = _finite_points(np.asarray(points_xyz, dtype=np.float64))
    seed = np.asarray(seed_xyz, dtype=np.float64)
    if seed.shape != (3,):
        raise V2DetectionError(V2Reason.INTERNAL_ERROR, "Seed XYZ inválido.")

    tin_points, effective_spacing = _xy_voxel_median(
        raw,
        cfg.target_tin_spacing_m,
        cfg.max_tin_points,
    )
    if len(tin_points) < 30:
        raise V2DetectionError(
            V2Reason.LOW_GROUND_SUPPORT,
            "V2 ficou com poucos pontos depois da redução TIN.",
        )

    try:
        tri = Delaunay(tin_points[:, :2], qhull_options="Qbb Qc Qz Q12")
    except QhullError as exc:
        raise V2DetectionError(
            V2Reason.INVALID_TIN,
            f"Falha Delaunay V2: {exc}",
        ) from exc

    geom = _triangle_geometry(tin_points, tri.simplices)
    seed_id = _seed_triangle(geom, seed, cfg)
    selected = _grow_face(tri, geom, seed_id, cfg)
    local_coherence = _local_face_coherence(tri, geom, selected)
    candidates = _boundary_candidates(
        tin_points,
        tri,
        geom,
        selected,
        cfg,
    )

    preliminary: dict[str, np.ndarray] = {}
    topology: dict[str, dict[str, int]] = {}
    for kind in ("CREST", "TOE"):
        if len(candidates[kind]) < 2:
            reason = (
                V2Reason.CREST_NOT_FOUND
                if kind == "CREST"
                else V2Reason.TOE_NOT_FOUND
            )
            raise V2DetectionError(
                reason,
                f"V2 não encontrou boundary {kind} suficiente.",
            )
        preliminary[kind], topology[kind] = _polyline_from_candidates(
            tin_points,
            candidates[kind],
            cfg,
        )

    crest, crest_refine = _refine_by_surface_intersection(
        preliminary["CREST"],
        raw,
        "CREST",
        geom,
        selected,
        cfg,
    )
    toe, toe_refine = _refine_by_surface_intersection(
        preliminary["TOE"],
        raw,
        "TOE",
        geom,
        selected,
        cfg,
    )

    crest = _resample_polyline(crest, cfg.station_spacing_m)
    toe = _resample_polyline(toe, cfg.station_spacing_m)

    face_ids = np.flatnonzero(selected)
    face_slope = float(np.median(geom["slope"][face_ids]))
    supported_total = (
        crest_refine["refined"]
        + crest_refine["bridged"]
        + toe_refine["refined"]
        + toe_refine["bridged"]
    )
    station_total = max(1, len(crest) + len(toe))
    refine_ratio = float(np.clip(supported_total / station_total, 0.0, 1.0))

    def payload(kind: str, xyz: np.ndarray, meta: dict) -> dict:
        length_2d, length_3d = _polyline_lengths(xyz)
        top = topology[kind]
        quality_score = float(
            np.clip(
                0.40 * local_coherence["local_normal_coherence"]
                + 0.35 * refine_ratio
                + 0.25 * min(len(xyz) / 20.0, 1.0),
                0.0,
                0.98,
            )
        )
        return {
            "type": kind,
            "profile": "ridge" if kind == "CREST" else "toe",
            "vertices": xyz.tolist(),
            "length_m": length_2d,
            "length_2d_m": length_2d,
            "length_3d_m": length_3d,
            "quality_score": quality_score,
            # Kept for current viewer compatibility; semantically this is now
            # a quality score rather than a calibrated probability.
            "confidence": quality_score,
            "station_spacing_m": float(cfg.station_spacing_m),
            "refined_stations": int(meta["refined"]),
            "bridged_stations": int(meta["bridged"]),
            "fallback_stations": int(meta["fallback"]),
            "support_ratio": float(meta["support_ratio"]),
            "support_median": meta["support_median"],
            "support_p10": meta["support_p10"],
            "plane_rmse_median": meta["plane_rmse_median"],
            "mean_shift_m": meta["mean_shift_m"],
            "p95_shift_m": meta["p95_shift_m"],
            "max_shift_m": meta["max_shift_m"],
            "candidate_edges": top["candidate_edges"],
            "graph_edges": top["graph_edges"],
            "gap_links": top["gap_links"],
            "mst_edges": top["mst_edges"],
            "path_vertices": top["path_vertices"],
        }

    crest_payload = payload("CREST", crest, crest_refine)
    toe_payload = payload("TOE", toe, toe_refine)

    return {
        "detector": "V2_RAW_TIN_MST",
        "engine": "v2",
        "experimental": True,
        "seed": seed.tolist(),
        "lines": [crest_payload, toe_payload],
        "crest": crest_payload,
        "toe": toe_payload,
        "status": "SUCCESS",
        "reason": V2Reason.SUCCESS.value,
        "quality_score": float(
            0.5 * crest_payload["quality_score"] + 0.5 * toe_payload["quality_score"]
        ),
        "confidence": float(
            0.5 * crest_payload["quality_score"] + 0.5 * toe_payload["quality_score"]
        ),
        "raw_points": int(len(raw)),
        "tin_points": int(len(tin_points)),
        "tin_triangles": int(len(tri.simplices)),
        "face_triangles": int(selected.sum()),
        "tin_spacing_m": float(effective_spacing),
        "face_slope_median_deg": face_slope,
        "crest_candidate_edges": int(len(candidates["CREST"])),
        "toe_candidate_edges": int(len(candidates["TOE"])),
        "station_spacing_m": float(cfg.station_spacing_m),
        "refine_ratio": refine_ratio,
        **local_coherence,
        "metrics": {
            "raw_points": int(len(raw)),
            "tin_points": int(len(tin_points)),
            "tin_triangles": int(len(tri.simplices)),
            "face_triangles": int(selected.sum()),
            "face_slope_median_deg": face_slope,
            "local_normal_coherence": local_coherence["local_normal_coherence"],
            "local_direction_median_deg": local_coherence["local_direction_median_deg"],
            "local_direction_p95_deg": local_coherence["local_direction_p95_deg"],
            "crest_candidate_edges": int(len(candidates["CREST"])),
            "toe_candidate_edges": int(len(candidates["TOE"])),
            "crest_mst_edges": topology["CREST"]["mst_edges"],
            "toe_mst_edges": topology["TOE"]["mst_edges"],
            "crest_gap_links": topology["CREST"]["gap_links"],
            "toe_gap_links": topology["TOE"]["gap_links"],
            "crest_length_2d_m": crest_payload["length_2d_m"],
            "crest_length_3d_m": crest_payload["length_3d_m"],
            "toe_length_2d_m": toe_payload["length_2d_m"],
            "toe_length_3d_m": toe_payload["length_3d_m"],
            "crest_refined_stations": crest_payload["refined_stations"],
            "toe_refined_stations": toe_payload["refined_stations"],
            "crest_bridged_stations": crest_payload["bridged_stations"],
            "toe_bridged_stations": toe_payload["bridged_stations"],
            "crest_fallback_stations": crest_payload["fallback_stations"],
            "toe_fallback_stations": toe_payload["fallback_stations"],
            "crest_support_ratio": crest_payload["support_ratio"],
            "toe_support_ratio": toe_payload["support_ratio"],
            "crest_support_median": crest_payload["support_median"],
            "toe_support_median": toe_payload["support_median"],
            "crest_plane_rmse_median": crest_payload["plane_rmse_median"],
            "toe_plane_rmse_median": toe_payload["plane_rmse_median"],
            "crest_mean_shift_m": crest_payload["mean_shift_m"],
            "toe_mean_shift_m": toe_payload["mean_shift_m"],
            "crest_p95_shift_m": crest_payload["p95_shift_m"],
            "toe_p95_shift_m": toe_payload["p95_shift_m"],
            "crest_max_shift_m": crest_payload["max_shift_m"],
            "toe_max_shift_m": toe_payload["max_shift_m"],
        },
        "pipeline": (
            "RAW -> XY_MEDIAN -> DELAUNAY -> REGION_GROW -> "
            "BOUNDARY -> KNN_GAPS -> KRUSKAL_MST -> "
            "1M_STATIONS -> SUPPORT_AWARE_ROBUST_PLANE_INTERSECTION -> "
            "SHORT_GAP_BRIDGE -> ROBUST_DELTA_SMOOTH"
        ),
    }
