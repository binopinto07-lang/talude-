from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from talude_v2.endpoint_continuation import continue_edge_pair_to_face_ends
from talude_v2.global_auto import GlobalFaceCandidate, _routing_centerline


@dataclass
class Candidate:
    crest: np.ndarray
    toe: np.ndarray
    centerline: np.ndarray
    corridor_radius_m: float
    baseline_width_median: float
    baseline_width_p90: float


def _cloud() -> np.ndarray:
    x = np.arange(-6.0, 12.0, 0.15)
    y = np.arange(0.0, 40.0, 0.18)
    xx, yy = np.meshgrid(x, y)
    zz = np.where(
        xx <= 0.0,
        10.0,
        np.where(xx >= 5.0, 5.0, 10.0 - xx),
    )
    rng = np.random.default_rng(701)
    zz += rng.normal(0.0, 0.008, size=zz.shape)
    return np.column_stack((xx.ravel(), yy.ravel(), zz.ravel()))


def _core_pair() -> tuple[np.ndarray, np.ndarray, Candidate]:
    y = np.arange(8.0, 31.0, 1.0)
    crest = np.column_stack(
        (np.zeros_like(y), y, np.full_like(y, 10.0))
    )
    toe = np.column_stack(
        (np.full_like(y, 5.0), y, np.full_like(y, 5.0))
    )
    centerline = 0.5 * (crest + toe)
    candidate = Candidate(
        crest=crest,
        toe=toe,
        centerline=centerline,
        corridor_radius_m=6.0,
        baseline_width_median=5.0,
        baseline_width_p90=5.2,
    )
    return crest, toe, candidate


def test_endpoint_continuation_reaches_both_physical_face_ends():
    points = _cloud()
    crest, toe, candidate = _core_pair()

    crest_out, toe_out, meta = continue_edge_pair_to_face_ends(
        points,
        crest,
        toe,
        candidate,
        station_spacing_m=1.0,
        profile_bin_m=0.20,
    )

    assert meta["applied"] is True
    assert meta["crest_added"] >= 10
    assert meta["toe_added"] >= 10
    assert float(np.min(crest_out[:, 1])) < 2.5
    assert float(np.max(crest_out[:, 1])) > 37.0
    assert float(np.min(toe_out[:, 1])) < 2.5
    assert float(np.max(toe_out[:, 1])) > 37.0
    assert float(np.max(crest_out[:, 1])) < 41.0
    assert float(np.max(toe_out[:, 1])) < 41.0


def test_endpoint_continuation_bridges_short_longitudinal_gap():
    points = _cloud()
    # Simulate a shadow/vegetation hole immediately after the current endpoint.
    points = points[
        ~((points[:, 1] > 31.2) & (points[:, 1] < 34.3))
    ]
    crest, toe, candidate = _core_pair()

    crest_out, toe_out, meta = continue_edge_pair_to_face_ends(
        points,
        crest,
        toe,
        candidate,
        station_spacing_m=1.0,
        profile_bin_m=0.20,
        max_gap_m=4.0,
    )

    assert float(np.max(crest_out[:, 1])) > 37.0
    assert float(np.max(toe_out[:, 1])) > 37.0
    bridged = (
        meta["end"]["crest_gap_stations_bridged"]
        + meta["end"]["toe_gap_stations_bridged"]
    )
    assert bridged >= 2


def test_toe_can_continue_when_crest_side_is_occluded():
    points = _cloud()
    # Remove the upper bench near the terminal part. The toe still has the
    # steep face + lower bench transition and must be allowed to continue alone.
    points = points[
        ~((points[:, 1] > 34.0) & (points[:, 0] < 1.0))
    ]
    crest, toe, candidate = _core_pair()

    crest_out, toe_out, meta = continue_edge_pair_to_face_ends(
        points,
        crest,
        toe,
        candidate,
        station_spacing_m=1.0,
        profile_bin_m=0.20,
    )

    assert meta["end"]["independent_stations"] >= 1
    assert float(np.max(toe_out[:, 1])) > 37.0
    assert float(np.max(crest_out[:, 1])) < 36.0
    assert float(np.max(toe_out[:, 1])) > float(np.max(crest_out[:, 1])) + 1.5


def test_las_routing_axis_extends_beyond_discovery_endpoints():
    crest, toe, candidate = _core_pair()
    global_candidate = GlobalFaceCandidate(
        face_id=1,
        crest=crest,
        toe=toe,
        centerline=candidate.centerline,
        seed_xyz=candidate.centerline[len(candidate.centerline) // 2],
        corridor_radius_m=candidate.corridor_radius_m,
        baseline_width_median=candidate.baseline_width_median,
        baseline_width_p90=candidate.baseline_width_p90,
    )

    routing = _routing_centerline(global_candidate)

    assert len(routing) > len(global_candidate.centerline)
    assert float(np.min(routing[:, 1])) < float(np.min(global_candidate.centerline[:, 1])) - 8.0
    assert float(np.max(routing[:, 1])) > float(np.max(global_candidate.centerline[:, 1])) + 8.0
