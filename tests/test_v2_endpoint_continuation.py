from __future__ import annotations

from pathlib import Path

import numpy as np

from talude_v2.global_auto import GlobalFaceCandidate, _routing_centerline


def _candidate() -> GlobalFaceCandidate:
    y = np.arange(8.0, 31.0, 1.0)
    crest = np.column_stack(
        (np.zeros_like(y), y, np.full_like(y, 10.0))
    )
    toe = np.column_stack(
        (np.full_like(y, 5.0), y, np.full_like(y, 5.0))
    )
    centerline = 0.5 * (crest + toe)
    return GlobalFaceCandidate(
        face_id=1,
        crest=crest,
        toe=toe,
        centerline=centerline,
        seed_xyz=centerline[len(centerline) // 2],
        corridor_radius_m=6.0,
        baseline_width_median=5.0,
        baseline_width_p90=5.2,
    )


def test_production_profile_edge_does_not_publish_endpoint_extrapolation():
    source = Path("src/talude_v2/edge_profile.py").read_text(encoding="utf-8")
    active = source.split("def extract_profile_edge_pair(", 1)[1]
    assert "continue_edge_pair_to_face_ends(" not in active
    assert "DISABLED_V2_8_SAFE_CORE" in active
    assert '"endpoint_continuation_applied"' in active


def test_las_routing_axis_does_not_extend_beyond_discovery_geometry():
    candidate = _candidate()
    routing = _routing_centerline(candidate)
    assert np.array_equal(routing, candidate.centerline)
    assert len(routing) == len(candidate.centerline)


def test_experimental_endpoint_module_remains_available_for_research_only():
    source = Path("src/talude_v2/endpoint_continuation.py").read_text(
        encoding="utf-8"
    )
    assert "def continue_edge_pair_to_face_ends(" in source
    assert "ENDPOINT_CONTINUATION_GAP_BRIDGE" in source
