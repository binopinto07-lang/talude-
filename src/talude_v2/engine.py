from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import Delaunay, QhullError, cKDTree


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
    random_seed: int = 1701


def _finite_points(points_xyz: np.ndarray) -> np.ndarray:
    pts = np.asarray(points_xyz, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError("V2 esperava pontos N x 3.")
    pts = pts[np.all(np.isfinite(pts), axis=1)]
    if len(pts) < 100:
        raise ValueError("V2 recebeu poucos pontos para formar uma TIN local.")
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
        raise ValueError("V2 não encontrou triângulos inclinados junto ao clique.")

    # Strongly prefer the nearest inclined triangle, but allow a tiny relief bias.
    cost = dist[ids] - 0.008 * slope[ids]
    best = int(ids[int(np.argmin(cost))])
    if float(dist[best]) > max(5.0, cfg.graph_gap_m * 3.0):
        raise ValueError("O clique ficou demasiado longe de uma face TIN inclinada.")
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
        raise ValueError("V2 encontrou uma face TIN demasiado pequena.")
    return selected


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
) -> np.ndarray:
    edges = _connect_short_gaps(points, edges, cfg.graph_gap_m)
    tree = _kruskal_tree(points, edges)
    ids = _longest_tree_path(points, tree)
    if len(ids) < 2:
        raise ValueError("V2 não conseguiu ordenar o boundary num caminho contínuo.")
    return points[ids].copy()


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


def _robust_plane(points: np.ndarray, min_points: int) -> tuple[np.ndarray, float] | None:
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < min_points:
        return None

    work = pts
    coef = None
    rmse = float("inf")
    for _ in range(3):
        A = np.column_stack((work[:, 0], work[:, 1], np.ones(len(work))))
        try:
            coef, *_ = np.linalg.lstsq(A, work[:, 2], rcond=None)
        except np.linalg.LinAlgError:
            return None
        residual = work[:, 2] - A @ coef
        med = float(np.median(residual))
        mad = float(np.median(np.abs(residual - med)))
        sigma = max(1.4826 * mad, 0.01)
        keep = np.abs(residual - med) <= 3.0 * sigma
        rmse = float(math.sqrt(np.mean(residual[keep] ** 2))) if np.any(keep) else float("inf")
        if int(keep.sum()) == len(work) or int(keep.sum()) < min_points:
            break
        work = work[keep]

    if coef is None:
        return None
    return np.asarray(coef, dtype=np.float64), rmse


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
        return stations, {"refined": 0, "fallback": len(stations)}

    tree = cKDTree(raw_points[:, :2])
    downhill = _nearest_face_downhill(stations, geom, selected)
    out = stations.copy()
    refined = 0
    fallback = 0
    rmse_values: list[float] = []

    for i, station in enumerate(stations):
        if i == 0:
            tangent = stations[1, :2] - stations[0, :2]
        elif i == len(stations) - 1:
            tangent = stations[-1, :2] - stations[-2, :2]
        else:
            tangent = stations[i + 1, :2] - stations[i - 1, :2]
        tnorm = float(np.linalg.norm(tangent))
        if tnorm <= 1e-9:
            fallback += 1
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
            fallback += 1
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
            # TOE: face is on the uphill side, bottom platform downhill.
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
            fallback += 1
            continue

        point = _closest_point_on_plane_intersection_xy(
            fit_a[0],
            fit_b[0],
            station[:2],
        )
        if point is None:
            fallback += 1
            continue

        refined_point, shift = point
        if shift > cfg.max_refine_shift_m:
            fallback += 1
            continue

        out[i] = refined_point
        refined += 1
        rmse_values.extend((fit_a[1], fit_b[1]))

    # Fill fallback Z from nearest raw point so every output remains true 3D.
    _, nearest = tree.query(out[:, :2], k=1)
    nearest_z = raw_points[np.asarray(nearest, dtype=np.int64), 2]
    missing = ~np.isfinite(out[:, 2])
    out[missing, 2] = nearest_z[missing]

    return out, {
        "refined": int(refined),
        "fallback": int(fallback),
        "plane_rmse_median": (
            float(np.median(rmse_values)) if rmse_values else None
        ),
    }


def _length_xy(xyz: np.ndarray) -> float:
    if len(xyz) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(xyz[:, :2], axis=0), axis=1).sum())


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
        raise ValueError("Seed XYZ inválido.")

    tin_points, effective_spacing = _xy_voxel_median(
        raw,
        cfg.target_tin_spacing_m,
        cfg.max_tin_points,
    )
    if len(tin_points) < 30:
        raise ValueError("V2 ficou com poucos pontos depois da redução TIN.")

    try:
        tri = Delaunay(tin_points[:, :2], qhull_options="Qbb Qc Qz Q12")
    except QhullError as exc:
        raise ValueError(f"Falha Delaunay V2: {exc}") from exc

    geom = _triangle_geometry(tin_points, tri.simplices)
    seed_id = _seed_triangle(geom, seed, cfg)
    selected = _grow_face(tri, geom, seed_id, cfg)
    candidates = _boundary_candidates(
        tin_points,
        tri,
        geom,
        selected,
        cfg,
    )

    preliminary: dict[str, np.ndarray] = {}
    for kind in ("CREST", "TOE"):
        if len(candidates[kind]) < 2:
            raise ValueError(f"V2 não encontrou boundary {kind} suficiente.")
        preliminary[kind] = _polyline_from_candidates(
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
    refined_total = crest_refine["refined"] + toe_refine["refined"]
    station_total = max(1, len(crest) + len(toe))
    refine_ratio = float(np.clip(refined_total / station_total, 0.0, 1.0))

    def payload(kind: str, xyz: np.ndarray, meta: dict) -> dict:
        return {
            "type": kind,
            "profile": "ridge" if kind == "CREST" else "toe",
            "vertices": xyz.tolist(),
            "length_m": _length_xy(xyz),
            "confidence": float(
                np.clip(
                    0.55
                    + 0.25 * refine_ratio
                    + 0.20 * min(len(xyz) / 20.0, 1.0),
                    0.0,
                    0.98,
                )
            ),
            "station_spacing_m": float(cfg.station_spacing_m),
            "refined_stations": int(meta["refined"]),
            "fallback_stations": int(meta["fallback"]),
            "plane_rmse_median": meta["plane_rmse_median"],
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
        "confidence": float(
            0.5 * crest_payload["confidence"] + 0.5 * toe_payload["confidence"]
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
        "pipeline": (
            "RAW -> XY_MEDIAN -> DELAUNAY -> REGION_GROW -> "
            "BOUNDARY -> KNN_GAPS -> KRUSKAL_MST -> "
            "1M_STATIONS -> ROBUST_PLANE_INTERSECTION"
        ),
    }
