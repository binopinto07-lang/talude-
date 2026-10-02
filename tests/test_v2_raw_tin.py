from __future__ import annotations

import numpy as np

from talude_v2 import V2Config, extract_face_raw_tin


def _synthetic_bench() -> tuple[np.ndarray, np.ndarray]:
    xs = np.arange(0.0, 30.0, 0.25)
    ys = np.arange(0.0, 20.0, 0.25)
    xx, yy = np.meshgrid(xs, ys)

    zz = np.where(
        xx <= 10.0,
        10.0,
        np.where(
            xx >= 15.0,
            5.0,
            10.0 - (xx - 10.0),
        ),
    )

    rng = np.random.default_rng(42)
    zz = zz + rng.normal(0.0, 0.008, size=zz.shape)
    xyz = np.column_stack((xx.ravel(), yy.ravel(), zz.ravel()))
    seed = np.array([12.5, 10.0, 7.5], dtype=float)
    return xyz, seed


def test_v2_raw_tin_extracts_crest_and_toe():
    xyz, seed = _synthetic_bench()
    result = extract_face_raw_tin(
        xyz,
        seed,
        config=V2Config(
            target_tin_spacing_m=0.25,
            max_tin_points=45000,
            station_spacing_m=1.0,
            graph_gap_m=1.0,
        ),
    )

    assert result["detector"] == "V2_RAW_TIN_MST"
    assert result["engine"] == "v2"
    assert result["tin_triangles"] > 100
    assert result["face_triangles"] > 20
    assert len(result["lines"]) == 2

    crest = np.asarray(result["crest"]["vertices"], dtype=float)
    toe = np.asarray(result["toe"]["vertices"], dtype=float)

    assert len(crest) >= 10
    assert len(toe) >= 10

    # Synthetic truth: crest x ~= 10 m, toe x ~= 15 m.
    assert abs(float(np.median(crest[:, 0])) - 10.0) < 0.65
    assert abs(float(np.median(toe[:, 0])) - 15.0) < 0.65

    # Output is intentionally CAD-like: roughly one station per metre.
    crest_steps = np.linalg.norm(np.diff(crest[:, :2], axis=0), axis=1)
    toe_steps = np.linalg.norm(np.diff(toe[:, :2], axis=0), axis=1)
    assert 0.65 <= float(np.median(crest_steps)) <= 1.35
    assert 0.65 <= float(np.median(toe_steps)) <= 1.35


def test_v2_pipeline_contains_tin_graph_and_plane_refinement():
    xyz, seed = _synthetic_bench()
    result = extract_face_raw_tin(xyz, seed)

    pipeline = result["pipeline"]
    assert "DELAUNAY" in pipeline
    assert "KRUSKAL_MST" in pipeline
    assert "ROBUST_PLANE_INTERSECTION" in pipeline
    assert result["crest_candidate_edges"] > 0
    assert result["toe_candidate_edges"] > 0
