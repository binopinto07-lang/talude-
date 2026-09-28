from __future__ import annotations

from pathlib import Path

import numpy as np

from studio.backend.vector_export import select_features
from talude_v2.edge_profile import _trim_endpoint_hooks, validate_edge_pair
from talude_v2.vector_document import create_vector_document


def _pair() -> tuple[np.ndarray, np.ndarray]:
    y = np.linspace(0.0, 30.0, 31)
    crest = np.column_stack((np.zeros_like(y), y, np.full_like(y, 10.0)))
    toe = np.column_stack((np.full_like(y, 5.0), y, np.full_like(y, 5.0)))
    return crest, toe


def test_pair_geometry_rejects_collapsed_midface_lines():
    crest, toe = _pair()
    good = validate_edge_pair(
        crest,
        toe,
        expected_width_m=5.0,
        min_line_length_m=2.0,
    )
    assert good["accepted"] is True

    middle_a = crest.copy()
    middle_b = toe.copy()
    middle_a[:, 0] = 2.4
    middle_b[:, 0] = 2.6
    middle_a[:, 2] = 7.6
    middle_b[:, 2] = 7.4
    bad = validate_edge_pair(
        middle_a,
        middle_b,
        expected_width_m=5.0,
        min_line_length_m=2.0,
    )
    assert bad["accepted"] is False
    assert bad["reason"] == "PAIR_COLLAPSED"


def test_review_lines_are_separated_from_approved_breaklines():
    crest, toe = _pair()
    doc = create_vector_document(
        [
            {
                "face_id": 1,
                "type": "CREST",
                "xyz": crest,
                "source": "V2_PROFILE_EDGE",
                "status": "AUTO_VALIDATED",
                "review_state": "APPROVED_AUTO",
            },
            {
                "face_id": 1,
                "type": "TOE",
                "xyz": toe,
                "source": "BASELINE_1_1_7_REVIEW_TECHNICAL",
                "status": "REVIEW_REQUIRED",
                "review_state": "PENDING",
            },
        ],
        crs_wkt="LOCAL_TEST",
    )

    layers = {feature["layer_id"] for feature in doc["features"]}
    assert "CRISTA" in layers
    assert "PE_TALUDE_REVIEW" in layers

    default_export = select_features(doc)
    assert len(default_export) == 1
    assert default_export[0]["layer_id"] == "CRISTA"

    explicit_review = select_features(doc, layer_ids=["PE_TALUDE_REVIEW"])
    assert len(explicit_review) == 1
    assert explicit_review[0]["layer_id"] == "PE_TALUDE_REVIEW"


def test_endpoint_hook_is_trimmed_without_moving_line_body():
    reference = np.asarray(
        [
            [0.0, 0.0, 10.0],
            [5.0, 0.0, 10.0],
            [10.0, 0.0, 10.0],
            [15.0, 0.0, 10.0],
            [20.0, 0.0, 10.0],
        ],
        dtype=float,
    )
    hooked = np.asarray(
        [
            [-5.0, 5.0, 10.0],  # lateral closure tail
            [0.0, 0.0, 10.0],
            [5.0, 0.0, 10.0],
            [10.0, 0.0, 10.0],
            [15.0, 0.0, 10.0],
            [20.0, 0.0, 10.0],
        ],
        dtype=float,
    )
    trimmed = _trim_endpoint_hooks(hooked, reference)
    assert len(trimmed) == 5
    assert np.allclose(trimmed[0], [0.0, 0.0, 10.0])
    assert np.allclose(trimmed[-1], [20.0, 0.0, 10.0])


def test_failed_faces_do_not_publish_baseline_review_geometry():
    source = Path("src/talude_v2/global_auto.py").read_text(encoding="utf-8")
    assert '"review_geometry": False' in source
    assert "STREAM_FACE_GLOBAL" in source
    assert "process_candidates_tiled(" not in source


def test_face_and_edge_seeds_are_decoupled():
    source = Path("core/terrain_face.py").read_text(encoding="utf-8")
    edge = Path("src/talude_v2/edge_profile.py").read_text(encoding="utf-8")
    assert "face_seed_xyz=None" in source
    assert "face_seed_cell" in source
    assert "edge_seed_cell" in source
    assert "edge_seed_max_distance_m" in source
    assert "_extract_face_centered_edges" in edge
    assert "_component_lines(" not in edge
    assert "detect_faces(" not in edge


def test_windows_builder_packages_profile_edge_engine():
    source = Path("scripts/build_windows.py").read_text(encoding="utf-8")
    assert '"talude_v2.edge_profile"' in source
    assert '"core.terrain_face"' in source
