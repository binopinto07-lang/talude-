from __future__ import annotations

import numpy as np

from talude_v2 import V2Config
from talude_v2.engine import boundary_reference_guard
from talude_v2.global_auto import (
    _PriorityReservoir,
    _build_face_candidates,
    _normalize_face_pair,
    refine_face_candidate_v2,
)


def _synthetic_bench() -> np.ndarray:
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
    return np.column_stack((xx.ravel(), yy.ravel(), zz.ravel()))


def _baseline_pair() -> tuple[np.ndarray, np.ndarray]:
    y = np.linspace(0.5, 19.5, 24)
    crest = np.column_stack((np.full_like(y, 10.0), y, np.full_like(y, 10.0)))
    toe = np.column_stack((np.full_like(y, 15.0), y, np.full_like(y, 5.0)))
    return crest, toe


def test_global_candidate_is_built_from_baseline_crest_and_toe():
    crest, toe = _baseline_pair()
    candidate = _normalize_face_pair(
        7,
        crest,
        toe,
        corridor_margin_m=3.0,
    )

    assert candidate.face_id == 7
    assert 4.8 <= candidate.baseline_width_median <= 5.2
    assert candidate.corridor_radius_m > 5.0
    assert 12.0 <= candidate.seed_xyz[0] <= 13.0


def test_candidate_builder_preserves_unpaired_baseline_lines():
    crest, toe = _baseline_pair()
    lines = [
        {"face_id": 1, "type": "CREST", "xyz": crest},
        {"face_id": 1, "type": "TOE", "xyz": toe},
        {"face_id": 2, "type": "CREST", "xyz": crest + np.array([40.0, 0.0, 0.0])},
    ]

    candidates, unpaired = _build_face_candidates(lines, corridor_margin_m=3.0)

    assert len(candidates) == 1
    assert candidates[0].face_id == 1
    assert len(unpaired) == 1
    assert unpaired[0]["face_id"] == 2


def test_priority_reservoir_is_bounded_and_counts_all_seen_points():
    reservoir = _PriorityReservoir(max_points=500, seed=1701)
    a = np.column_stack(
        (
            np.arange(0, 1000, dtype=float),
            np.zeros(1000),
            np.ones(1000),
        )
    )
    b = a + np.array([1000.0, 0.0, 0.0])

    reservoir.add(a)
    reservoir.add(b)

    assert reservoir.seen == 2000
    assert reservoir.array().shape == (500, 3)
    assert float(np.max(reservoir.array()[:, 0])) > 1000.0


def test_auto_global_face_refinement_uses_profile_edges_and_returns_pair():
    raw = _synthetic_bench()
    crest, toe = _baseline_pair()
    candidate = _normalize_face_pair(
        1,
        crest,
        toe,
        corridor_margin_m=3.0,
    )

    lines, record = refine_face_candidate_v2(
        candidate,
        raw,
        v2_config=V2Config(
            target_tin_spacing_m=0.25,
            max_tin_points=45000,
            station_spacing_m=1.0,
            graph_gap_m=1.0,
        ),
        min_line_length_m=2.0,
    )

    assert record["status"] == "SUCCESS"
    assert record["reason"] == "SUCCESS"
    assert len(lines) == 2
    assert {line["type"] for line in lines} == {"CREST", "TOE"}
    assert all(line["source"] == "V2_PROFILE_EDGE" for line in lines)
    assert all(line["status"] == "AUTO_VALIDATED" for line in lines)
    assert all(line["review_state"] == "APPROVED_AUTO" for line in lines)
    assert all(line["length_m"] > 10.0 for line in lines)

    by_type = {line["type"]: np.asarray(line["xyz"]) for line in lines}
    assert np.median(np.abs(by_type["CREST"][:, 0] - 10.0)) < 0.9
    assert np.median(np.abs(by_type["TOE"][:, 0] - 15.0)) < 0.9
    assert np.median(by_type["CREST"][:, 2]) > np.median(by_type["TOE"][:, 2]) + 3.0
    assert record["detector"] == "PROFILE_EDGE_FLAT_FACE_FLAT"
    assert record["geometry"]["accepted"] is True


def test_profile_edge_ignores_shifted_baseline_position():
    raw = _synthetic_bench()
    crest, toe = _baseline_pair()
    shifted_crest = crest + np.array([1.25, 0.0, -1.25])
    shifted_toe = toe + np.array([1.25, 0.0, 0.0])
    candidate = _normalize_face_pair(
        9,
        shifted_crest,
        shifted_toe,
        corridor_margin_m=4.0,
    )

    lines, record = refine_face_candidate_v2(
        candidate,
        raw,
        v2_config=V2Config(
            target_tin_spacing_m=0.25,
            max_tin_points=45000,
            station_spacing_m=1.0,
            graph_gap_m=1.0,
        ),
        min_line_length_m=2.0,
    )

    by_type = {line["type"]: np.asarray(line["xyz"]) for line in lines}
    crest_x = float(np.median(by_type["CREST"][:, 0]))
    toe_x = float(np.median(by_type["TOE"][:, 0]))
    assert abs(crest_x - 10.0) < 0.9
    assert abs(toe_x - 15.0) < 0.9
    assert abs(crest_x - 11.25) > 0.5
    assert abs(toe_x - 16.25) > 0.5
    assert record["local_face"]["seed_to_face_distance_m"] >= 0.0


def test_edge_guard_accepts_real_edge_and_rejects_face_centre():
    crest, toe = _baseline_pair()

    near_crest = crest.copy()
    near_crest[:, 0] += 0.35
    good = boundary_reference_guard(
        near_crest,
        crest,
        toe,
        face_width_m=5.0,
    )
    assert good["accepted"] is True
    assert good["own_median_m"] < 0.5

    middle = crest.copy()
    middle[:, 0] = 12.5
    middle[:, 2] = 7.5
    bad = boundary_reference_guard(
        middle,
        crest,
        toe,
        face_width_m=5.0,
    )
    assert bad["accepted"] is False
    assert bad["side_ratio_median"] > 0.45


def test_edge_guard_keeps_crest_and_toe_on_their_own_side():
    crest, toe = _baseline_pair()
    wrong_for_crest = toe.copy()
    wrong_for_crest[:, 0] -= 0.25

    result = boundary_reference_guard(
        wrong_for_crest,
        crest,
        toe,
        face_width_m=5.0,
    )

    assert result["accepted"] is False
    assert result["own_median_m"] > 4.0
