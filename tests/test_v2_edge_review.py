from __future__ import annotations

from pathlib import Path

import numpy as np

from studio.backend.vector_export import select_features
from talude_v2.edge_profile import validate_edge_pair
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


def test_profile_edge_cache_version_invalidates_old_raw_tin_fragments():
    source = Path("src/talude_v2/tiled_auto.py").read_text(encoding="utf-8")
    assert "v2_fragment_cache_v2_profile_edge" in source
    assert '"v2_fragment_cache_v1"' not in source


def test_windows_builder_packages_profile_edge_engine():
    source = Path("scripts/build_windows.py").read_text(encoding="utf-8")
    assert '"talude_v2.edge_profile"' in source
    assert '"core.terrain_face"' in source
