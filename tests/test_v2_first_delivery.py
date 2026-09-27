from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from talude_v2.engine import (
    _local_face_coherence,
    _polyline_lengths,
    extract_face_raw_tin,
)
from talude_v2.reasons import V2DetectionError, V2Reason


def _chain_neighbors(count: int) -> np.ndarray:
    neighbors = np.full((count, 3), -1, dtype=np.int64)
    for i in range(count):
        slot = 0
        if i > 0:
            neighbors[i, slot] = i - 1
            slot += 1
        if i + 1 < count:
            neighbors[i, slot] = i + 1
    return neighbors


def _geom_from_angles(angles_deg: list[float]) -> dict[str, np.ndarray]:
    angle = np.radians(np.asarray(angles_deg, dtype=float))
    downhill = np.column_stack((np.cos(angle), np.sin(angle)))

    # Upward unit normals with a fixed vertical component. Only neighbour-to-
    # neighbour compatibility matters for this regression test.
    z = np.full(len(angle), 0.78, dtype=float)
    h = np.sqrt(1.0 - z * z)
    normals = np.column_stack(
        (
            h * np.cos(angle),
            h * np.sin(angle),
            z,
        )
    )
    return {
        "normal": normals,
        "downhill": downhill,
    }


@pytest.mark.parametrize(
    ("name", "angles"),
    [
        ("straight", [0, 0, 0, 0, 0, 0]),
        ("curved", [0, 12, 24, 36, 48, 60, 72]),
        # Distant directions largely cancel globally, while adjacent triangles
        # remain locally coherent. This is the regression that motivated V2.
        ("s_curve", [0, 45, 90, 135, 180, 225, 270, 315]),
    ],
)
def test_local_coherence_accepts_straight_curved_and_s(name, angles):
    tri = SimpleNamespace(neighbors=_chain_neighbors(len(angles)))
    geom = _geom_from_angles(angles)
    selected = np.ones(len(angles), dtype=bool)

    metrics = _local_face_coherence(tri, geom, selected)

    assert metrics["local_pairs"] == len(angles) - 1
    assert metrics["local_normal_coherence"] > 0.70
    assert metrics["local_direction_p95_deg"] <= 45.1

    if name == "s_curve":
        global_vector = np.mean(geom["downhill"], axis=0)
        assert float(np.linalg.norm(global_vector)) < 1e-12


def test_polyline_length_is_accumulated_not_endpoint_distance():
    line = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [5.0, 0.0, 1.0],
            [5.0, 5.0, 2.0],
            [0.0, 5.0, 3.0],
            [0.0, 1.0, 4.0],
        ],
        dtype=float,
    )

    length_2d, length_3d = _polyline_lengths(line)
    endpoint_distance = float(np.linalg.norm(line[-1, :2] - line[0, :2]))

    assert length_2d == pytest.approx(19.0)
    assert length_2d > endpoint_distance * 10.0
    assert length_3d > length_2d


def test_v2_returns_explicit_success_reason_and_quality_metrics():
    xs = np.arange(0.0, 24.0, 0.30)
    ys = np.arange(0.0, 18.0, 0.30)
    xx, yy = np.meshgrid(xs, ys)
    zz = np.where(
        xx <= 8.0,
        10.0,
        np.where(xx >= 13.0, 5.0, 10.0 - (xx - 8.0)),
    )
    xyz = np.column_stack((xx.ravel(), yy.ravel(), zz.ravel()))

    result = extract_face_raw_tin(xyz, [10.5, 9.0, 7.5])

    assert result["status"] == "SUCCESS"
    assert result["reason"] == V2Reason.SUCCESS.value
    assert 0.0 <= result["quality_score"] <= 1.0
    assert "metrics" in result
    assert result["metrics"]["local_normal_coherence"] >= 0.0
    assert result["crest"]["length_2d_m"] > 0.0
    assert result["crest"]["length_3d_m"] >= result["crest"]["length_2d_m"]
    assert result["toe"]["length_2d_m"] > 0.0
    assert result["toe"]["length_3d_m"] >= result["toe"]["length_2d_m"]


def test_v2_expected_rejection_has_reason_code():
    with pytest.raises(V2DetectionError) as caught:
        extract_face_raw_tin(np.zeros((20, 3), dtype=float), [0.0, 0.0, 0.0])

    assert caught.value.reason in {
        V2Reason.NO_GROUND,
        V2Reason.LOW_GROUND_SUPPORT,
    }
