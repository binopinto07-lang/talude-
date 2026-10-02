from __future__ import annotations

import numpy as np

from talude_v2.plane_edge_snap import snap_edge_pair_to_local_planes


def _bench(*, curved: bool, angle_deg: float, seed_shift: float = 0.20):
    u = np.arange(-6.0, 12.0, 0.12)
    v = np.arange(0.0, 40.0, 0.16)
    uu, vv = np.meshgrid(u, v)
    crest_true = (
        0.8 * np.sin(vv / 8.0)
        if curved
        else np.zeros_like(vv)
    )
    toe_true = crest_true + 5.0
    z = np.where(
        uu <= crest_true,
        10.0,
        np.where(
            uu >= toe_true,
            5.0,
            10.0 - (uu - crest_true),
        ),
    )
    rng = np.random.default_rng(123)
    z += rng.normal(0.0, 0.008, size=z.shape)

    theta = np.deg2rad(angle_deg)
    across = np.array([np.cos(theta), np.sin(theta)])
    tangent = np.array([-np.sin(theta), np.cos(theta)])
    origin = np.array([1000.0, 2000.0])
    xy = (
        uu.ravel()[:, None] * across[None, :]
        + vv.ravel()[:, None] * tangent[None, :]
        + origin[None, :]
    )
    points = np.column_stack((xy, z.ravel()))

    stations = np.arange(0.8, 39.3, 1.0)
    base = (
        0.8 * np.sin(stations / 8.0)
        if curved
        else np.zeros_like(stations)
    )
    crest_xy = (
        (base + seed_shift)[:, None] * across[None, :]
        + stations[:, None] * tangent[None, :]
        + origin[None, :]
    )
    toe_xy = (
        (base + 5.0 + seed_shift)[:, None] * across[None, :]
        + stations[:, None] * tangent[None, :]
        + origin[None, :]
    )
    crest = np.column_stack(
        (crest_xy, np.full_like(stations, 10.0 - seed_shift))
    )
    toe = np.column_stack(
        (toe_xy, np.full_like(stations, 5.0))
    )
    return points, crest, toe, across, tangent, origin


def _errors(args, *, curved: bool):
    points, crest, toe, across, tangent, origin = args
    found_crest, found_toe, meta = snap_edge_pair_to_local_planes(
        points,
        crest,
        toe,
    )
    crest_v = (found_crest[:, :2] - origin) @ tangent
    toe_v = (found_toe[:, :2] - origin) @ tangent
    true_crest = (
        0.8 * np.sin(crest_v / 8.0)
        if curved
        else np.zeros_like(crest_v)
    )
    true_toe = (
        0.8 * np.sin(toe_v / 8.0) + 5.0
        if curved
        else np.full_like(toe_v, 5.0)
    )
    crest_error = np.abs(
        (found_crest[:, :2] - origin) @ across - true_crest
    )
    toe_error = np.abs(
        (found_toe[:, :2] - origin) @ across - true_toe
    )
    return crest_error, toe_error, meta


def test_plane_snap_recovers_breaks_from_shifted_section_seed():
    result = _errors(
        _bench(curved=False, angle_deg=0.0),
        curved=False,
    )
    crest_error, toe_error, meta = result
    assert meta["source"] == "ADAPTIVE_LOCAL_PLANE_SNAP"
    assert meta["coverage_ratio"] > 0.90
    assert 0.20 < meta["search_radius_m"] < 0.80
    assert float(np.percentile(crest_error, 95.0)) < 0.03
    assert float(np.percentile(toe_error, 95.0)) < 0.03


def test_plane_snap_tracks_curved_rotated_breaks():
    crest_error, toe_error, meta = _errors(
        _bench(curved=True, angle_deg=29.0),
        curved=True,
    )
    assert meta["coverage_ratio"] > 0.90
    assert float(np.percentile(crest_error, 95.0)) < 0.04
    assert float(np.percentile(toe_error, 95.0)) < 0.04


def test_plane_snap_survives_thinning_and_residual_outliers():
    points, crest, toe, across, tangent, origin = _bench(
        curved=True,
        angle_deg=23.0,
    )
    rng = np.random.default_rng(2026)
    points = points[rng.random(len(points)) < 0.55]
    count = int(len(points) * 0.03)
    ids = rng.choice(len(points), count, replace=False)
    outliers = points[ids].copy()
    outliers[:, 2] += rng.uniform(0.30, 2.0, count)
    points = np.vstack((points, outliers))

    crest_error, toe_error, meta = _errors(
        (points, crest, toe, across, tangent, origin),
        curved=True,
    )
    assert meta["coverage_ratio"] > 0.75
    assert float(np.percentile(crest_error, 95.0)) < 0.06
    assert float(np.percentile(toe_error, 95.0)) < 0.06
