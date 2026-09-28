from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from talude_v2.engine import (
    V2Config,
    _bridge_short_refine_gaps,
    _robust_plane,
    _support_aware_smooth,
)
from talude_v2.global_auto import _normalize_face_pair
from talude_v2.tiled_auto import _build_tile_jobs, stitch_fragments
from talude_v2.vector_document import (
    SCHEMA,
    create_vector_document,
    load_vector_document,
    save_vector_document,
    vector_document_summary,
)


def _reference_pair(length: float = 120.0) -> tuple[np.ndarray, np.ndarray]:
    # Synthetic stations are 1 m apart so slice indices used by stitching
    # tests represent metres consistently for every requested reference length.
    count = max(2, int(round(float(length))) + 1)
    y = np.linspace(0.0, length, count)
    crest = np.column_stack((np.full_like(y, 10.0), y, np.full_like(y, 10.0)))
    toe = np.column_stack((np.full_like(y, 15.0), y, np.full_like(y, 5.0)))
    return crest, toe


def test_phase3_builds_multiple_tile_jobs_for_long_face():
    crest, toe = _reference_pair()
    candidate = _normalize_face_pair(1, crest, toe, corridor_margin_m=3.0)
    info = SimpleNamespace(
        width=40.0,
        height=140.0,
        mins=(0.0, 0.0, 0.0),
    )
    cfg = V2Config(tile_size_m=40.0, tile_halo_m=8.0)

    jobs, by_tile, shape = _build_tile_jobs([candidate], info, cfg)

    assert shape[0] >= 1
    assert shape[1] >= 3
    assert len(jobs) >= 3
    assert len(by_tile) >= 3
    assert {job.face_id for job in jobs} == {1}


def test_phase3_stitches_overlapping_fragments_without_duplicates():
    crest, _ = _reference_pair(100.0)
    cfg = V2Config(
        station_spacing_m=1.0,
        tile_stitch_gap_m=5.0,
        tile_min_coverage_ratio=0.80,
    )

    fragments = []
    for start, end, quality in (
        (0, 42, 0.80),
        (38, 72, 0.92),
        (68, 100, 0.86),
    ):
        frag = crest[start : end + 1].copy()
        # Small tile-specific sub-centimetric perturbation to make overlap real.
        frag[:, 0] += (quality - 0.85) * 0.04
        fragments.append(
            {
                "xyz": frag,
                "length_m": float(end - start),
                "quality_score": quality,
            }
        )

    stitched, meta = stitch_fragments(fragments, crest, cfg)

    assert meta["fragment_count"] == 3
    assert meta["coverage_ratio"] >= 0.95
    assert len(stitched) >= 90
    assert np.max(np.linalg.norm(np.diff(stitched[:, :2], axis=0), axis=1)) < 2.0
    assert np.median(np.abs(stitched[:, 0] - 10.0)) < 0.05


def test_phase4_robust_plane_reports_support_and_rejects_outliers():
    rng = np.random.default_rng(123)
    xy = rng.uniform(-2.0, 2.0, size=(200, 2))
    z = 0.4 * xy[:, 0] - 0.2 * xy[:, 1] + 7.5
    z += rng.normal(0.0, 0.01, size=len(z))
    pts = np.column_stack((xy, z))
    outliers = np.column_stack(
        (
            rng.uniform(-2.0, 2.0, size=(20, 2)),
            rng.uniform(12.0, 20.0, size=20),
        )
    )
    fit = _robust_plane(np.vstack((pts, outliers)), min_points=14)

    assert fit is not None
    coef, rmse, support, inlier_ratio = fit
    assert coef[0] == pytest.approx(0.4, abs=0.03)
    assert coef[1] == pytest.approx(-0.2, abs=0.03)
    assert coef[2] == pytest.approx(7.5, abs=0.04)
    assert rmse < 0.05
    assert support >= 180
    assert 0.75 <= inlier_ratio <= 1.0


def test_phase4_support_smoothing_and_short_gap_bridge_remove_single_spike():
    stations = np.column_stack(
        (
            np.arange(7, dtype=float),
            np.zeros(7),
            np.zeros(7),
        )
    )
    refined = stations.copy()
    refined[:, 1] = 0.20
    refined[3, 1] = 1.50
    valid = np.ones(7, dtype=bool)
    weights = np.ones(7, dtype=float)
    weights[3] = 0.10

    smoothed = _support_aware_smooth(stations, refined, valid, weights, 5)
    assert smoothed[3, 1] < 0.8

    valid_gap = valid.copy()
    valid_gap[3] = False
    bridged_line, bridged = _bridge_short_refine_gaps(
        stations,
        smoothed,
        valid_gap,
        max_gap=1,
    )
    assert bridged[3]
    assert bridged_line[3, 0] == pytest.approx(3.0)


def test_phase5_vector_document_wraps_engine_lines_and_roundtrips(tmp_path):
    crest, toe = _reference_pair(20.0)
    lines = [
        {
            "line_id": 1,
            "face_id": 10,
            "type": "CREST",
            "xyz": crest,
            "source": "V2_TILED_STITCHED",
            "quality_score": 0.91,
        },
        {
            "line_id": 2,
            "face_id": 10,
            "type": "TOE",
            "xyz": toe,
            "source": "V2_TILED_STITCHED",
            "quality_score": 0.89,
        },
    ]

    document = create_vector_document(
        lines,
        crs_wkt="LOCAL_TEST_CRS",
        source={"engine": "BREAKLINE_ENGINE_V2_GLOBAL_HYBRID"},
    )
    summary = vector_document_summary(document)

    assert document["schema"] == SCHEMA
    assert summary["feature_count"] == 2
    assert summary["layer_counts"]["CRISTA"] == 1
    assert summary["layer_counts"]["PE_TALUDE"] == 1
    assert all(feature["geometry"]["has_z"] for feature in document["features"])

    path = tmp_path / "vector_document.json"
    save_vector_document(path, document)
    loaded = load_vector_document(path)

    assert loaded["document_id"] == document["document_id"]
    assert len(loaded["features"]) == 2
    assert loaded["features"][0]["geometry"]["coordinates"][0][2] == pytest.approx(10.0)
