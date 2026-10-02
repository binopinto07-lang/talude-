from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from talude_v2.section_edge_tracker import extract_section_edge_pair


@dataclass
class Candidate:
    crest: np.ndarray
    toe: np.ndarray
    corridor_radius_m: float
    baseline_width_median: float
    baseline_width_p90: float


def _bench() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xs = np.arange(0.0, 30.0, 0.25)
    ys = np.arange(0.0, 20.0, 0.25)
    xx, yy = np.meshgrid(xs, ys)
    zz = np.where(
        xx <= 10.0,
        10.0,
        np.where(xx >= 15.0, 5.0, 10.0 - (xx - 10.0)),
    )
    rng = np.random.default_rng(99)
    zz = zz + rng.normal(0.0, 0.006, size=zz.shape)
    raw = np.column_stack((xx.ravel(), yy.ravel(), zz.ravel()))
    y = np.linspace(0.5, 19.5, 24)
    crest = np.column_stack(
        (np.full_like(y, 10.0), y, np.full_like(y, 10.0))
    )
    toe = np.column_stack(
        (np.full_like(y, 15.0), y, np.full_like(y, 5.0))
    )
    return raw, crest, toe


def test_section_tracker_locks_onto_true_flat_face_flat_intersections():
    raw, crest, toe = _bench()
    candidate = Candidate(crest, toe, 5.9, 5.0, 5.0)

    found_crest, found_toe, meta = extract_section_edge_pair(
        raw,
        candidate,
    )

    assert abs(float(np.median(found_crest[:, 0])) - 10.0) < 0.15
    assert abs(float(np.median(found_toe[:, 0])) - 15.0) < 0.15
    assert meta["coverage_ratio"] > 0.8
    assert meta["source"] == "CROSS_SECTION_THREE_PLANE_TRACKER"
    assert "seed_to_face_distance_m" in meta
    assert meta["seed_to_face_distance_m"] >= 0.0


def test_section_tracker_does_not_follow_shifted_discovery_baseline():
    raw, crest, toe = _bench()
    candidate = Candidate(
        crest + np.array([1.25, 0.0, -1.25]),
        toe + np.array([1.25, 0.0, 0.0]),
        7.0,
        5.0,
        5.0,
    )

    found_crest, found_toe, _ = extract_section_edge_pair(
        raw,
        candidate,
    )

    crest_x = float(np.median(found_crest[:, 0]))
    toe_x = float(np.median(found_toe[:, 0]))
    assert abs(crest_x - 10.0) < 0.15
    assert abs(toe_x - 15.0) < 0.15
    assert abs(crest_x - 11.25) > 0.9
    assert abs(toe_x - 16.25) > 0.9


def test_section_tracker_follows_curved_breakline_in_plan():
    xs = np.arange(0.0, 30.0, 0.25)
    ys = np.arange(0.0, 40.0, 0.25)
    xx, yy = np.meshgrid(xs, ys)
    true_crest = 10.0 + 0.8 * np.sin(yy / 8.0)
    true_toe = true_crest + 5.0
    zz = np.where(
        xx <= true_crest,
        10.0 + 0.02 * yy,
        np.where(
            xx >= true_toe,
            5.0 + 0.02 * yy,
            10.0 + 0.02 * yy - (xx - true_crest),
        ),
    )
    rng = np.random.default_rng(7)
    zz = zz + rng.normal(0.0, 0.008, size=zz.shape)
    raw = np.column_stack((xx.ravel(), yy.ravel(), zz.ravel()))

    y = np.linspace(0.5, 39.5, 50)
    crest = np.column_stack(
        (11.1 + 0.8 * np.sin(y / 8.0), y, 9.0 + 0.02 * y)
    )
    toe = np.column_stack(
        (16.1 + 0.8 * np.sin(y / 8.0), y, 5.0 + 0.02 * y)
    )
    candidate = Candidate(crest, toe, 7.0, 5.0, 5.0)

    found_crest, found_toe, meta = extract_section_edge_pair(
        raw,
        candidate,
    )

    truth = 10.0 + 0.8 * np.sin(found_crest[:, 1] / 8.0)
    assert float(
        np.median(np.abs(found_crest[:, 0] - truth))
    ) < 0.15
    assert float(
        np.median(np.abs(found_toe[:, 0] - (truth + 5.0)))
    ) < 0.15
    assert meta["coverage_ratio"] > 0.65


def test_section_tracker_is_rotation_invariant_with_sloping_benches():
    across_values = np.arange(-8.0, 14.0, 0.18)
    along_values = np.arange(0.0, 40.0, 0.22)
    uu, vv = np.meshgrid(across_values, along_values)
    zz = np.where(
        uu <= 0.0,
        12.0 - 0.05 * uu,
        np.where(
            uu >= 5.0,
            8.0 - 0.03 * (uu - 5.0),
            12.0 - 0.8 * uu,
        ),
    )
    zz += 0.015 * np.sin(vv / 3.0)
    rng = np.random.default_rng(3)
    zz += rng.normal(0.0, 0.01, size=zz.shape)

    theta = np.deg2rad(32.0)
    across_axis = np.array([np.cos(theta), np.sin(theta)])
    tangent_axis = np.array([-np.sin(theta), np.cos(theta)])
    origin = np.array([1000.0, 2000.0])
    xy = (
        uu.ravel()[:, None] * across_axis[None, :]
        + vv.ravel()[:, None] * tangent_axis[None, :]
        + origin[None, :]
    )
    raw = np.column_stack((xy, zz.ravel()))

    along = np.linspace(0.5, 39.5, 45)
    crest_xy = (
        0.9 * across_axis[None, :]
        + along[:, None] * tangent_axis[None, :]
        + origin[None, :]
    )
    toe_xy = (
        5.9 * across_axis[None, :]
        + along[:, None] * tangent_axis[None, :]
        + origin[None, :]
    )
    crest = np.column_stack(
        (crest_xy, np.full_like(along, 11.0))
    )
    toe = np.column_stack(
        (toe_xy, np.full_like(along, 8.0))
    )
    candidate = Candidate(crest, toe, 7.0, 5.0, 5.0)

    found_crest, found_toe, meta = extract_section_edge_pair(
        raw,
        candidate,
        profile_bin_m=0.18,
    )

    local_crest = (
        (found_crest[:, :2] - origin[None, :]) @ across_axis
    )
    local_toe = (
        (found_toe[:, :2] - origin[None, :]) @ across_axis
    )
    assert float(np.median(np.abs(local_crest))) < 0.10
    assert float(np.median(np.abs(local_toe - 5.0))) < 0.10
    assert meta["coverage_ratio"] > 0.90
