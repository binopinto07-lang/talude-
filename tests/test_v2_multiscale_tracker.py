"""R20 coverage tests: no fabricated XYZ, no false CREST/TOE dependency."""
import numpy as np
import pytest

from talude_v2.breakline_seed import BreaklineSeed, SeedEvidence
from talude_v2.multiscale_discovery import MultiScaleConfig, discover_multiscale_ground
from talude_v2.curvature_seed import CurvatureSeedConfig
from talude_v2.independent_breakline_tracker import (
    IndependentTrackerConfig,
    trace_independent_breaklines,
)


def _bench():
    xs, ys = np.meshgrid(np.arange(0, 25.01, .25), np.arange(0, 12.01, .25))
    z = np.where(xs <= 10, 10., np.where(xs >= 15, 5., 20. - xs))
    return np.column_stack((xs.ravel(), ys.ravel(), z.ravel()))


def _e(kind, x, y, z=3., tangent=(0., 1.), quality=.85):
    return SeedEvidence(kind, [x, y, z], tangent, quality, "curvature")


def _chain(kind, x, yy, z=3.):
    return [_e(kind, x, float(y), z) for y in yy]


def test_multiscale_both_edges_discovered_from_ground():
    observations, report = discover_multiscale_ground(_bench())
    assert len(report.observations_by_scale) == 3
    assert report.merged_crest > 0 and report.merged_toe > 0
    for kind, x in (("CREST", 10.), ("TOE", 15.)):
        assert any(o.kind == kind and abs(o.xyz[0] - x) <= .8 for o in observations)


def test_multiscale_outputs_only_actual_input_xyz():
    original = _bench()
    observations, _ = discover_multiscale_ground(original)
    support = set(map(tuple, original.tolist()))
    assert all(tuple(o.xyz) in support for o in observations)


def test_multiscale_reports_failed_scales_not_silent_success():
    _, report = discover_multiscale_ground(
        _bench(), config=MultiScaleConfig(
            cell_sizes_m=(.15,.25),
            base=CurvatureSeedConfig(max_grid_cells=100),
        ),
    )
    assert len(report.failed_scales) == 2
    assert report.merged_crest == report.merged_toe == 0


def test_tracer_recovers_separate_crest_and_toe_observations():
    observed = _chain("CREST", 10., range(12), 10.) + _chain("TOE", 15., range(12), 5.)
    tracks, report = trace_independent_breaklines(observed)
    assert report.tracks_crest == report.tracks_toe == 1
    assert len(tracks) == 2
    assert all(len(track.xyz) >= 10 for track in tracks)
    assert all(track.status == "EXPERIMENTAL_REVIEW" for track in tracks)
    assert {tuple(row) for t in tracks for row in t.xyz} <= {tuple(o.xyz) for o in observed}


def test_crest_without_toe_still_forms_one_track():
    tracks, report = trace_independent_breaklines(_chain("CREST", 10., range(8)))
    assert len(tracks) == 1 and tracks[0].kind == "CREST" and report.tracks_toe == 0


def test_toe_without_crest_still_forms_one_track():
    tracks, report = trace_independent_breaklines(_chain("TOE", 15., range(8)))
    assert len(tracks) == 1 and tracks[0].kind == "TOE" and report.tracks_crest == 0


def test_gap_produces_two_segments_with_no_interpolated_vertices():
    observed = _chain("CREST", 10., [0, 1, 2, 3, 7, 8, 9, 10])
    tracks, _ = trace_independent_breaklines(observed)
    assert len(tracks) == 2
    assert all(not (3 < value < 7) for track in tracks for value in track.xyz[:, 1])
    assert {int(len(t.xyz)) for t in tracks} == {4}


def test_parallel_lines_do_not_jump_sideways():
    # This is stricter than the R19 one-metre NMS threshold: two nearby edges.
    observed = _chain("CREST", 10., range(8)) + _chain("CREST", 10.75, range(8))
    tracks, _ = trace_independent_breaklines(observed,
        config=IndependentTrackerConfig(max_lateral_m=.38, search_radius_m=.65))
    assert len(tracks) == 2
    assert all(np.ptp(t.xyz[:, 0]) < .1 for t in tracks)


def test_ambiguous_next_observation_stops_instead_of_guessing():
    observed = [_e("CREST", 10., 0), _e("CREST", 10., 1),
                _e("CREST", 9.7, 2), _e("CREST", 10.3, 2)]
    seed = BreaklineSeed("CREST", [10., 0., 3.], [0., 1.], .5, "manual", None, 1)
    tracks, report = trace_independent_breaklines(
        observed, seeds=[seed], config=IndependentTrackerConfig(min_vertices=2, min_length_m=.5,
                                                                ambiguity_margin=.12)
    )
    assert report.ambiguous_sides >= 1
    assert all(not (np.any((t.xyz[:, 1] == 2))) for t in tracks)


def test_far_baseline_hint_is_not_published_as_cloud_geometry():
    observed = _chain("CREST", 10., range(7))
    hint = BreaklineSeed("CREST", [99., 99., 99.], [0., 1.], .5, "baseline", 1, 1)
    tracks, report = trace_independent_breaklines(observed, seeds=[hint])
    assert report.unsnapped_hints == 1
    assert all(np.max(t.xyz[:, 0]) < 12 for t in tracks)


def test_flat_no_evidence_yields_no_tracks():
    pts = _bench(); pts[:, 2] = 2.
    obs, _ = discover_multiscale_ground(pts)
    tracks, report = trace_independent_breaklines(obs)
    assert tracks == [] and report.tracks_crest == report.tracks_toe == 0


def test_inputs_validated():
    with pytest.raises(ValueError):
        discover_multiscale_ground(np.ones((6, 2)))
    with pytest.raises(ValueError):
        IndependentTrackerConfig(station_step_m=0)
    with pytest.raises(ValueError):
        MultiScaleConfig(cell_sizes_m=(0.,))


def test_curve_is_followed_locally_without_extrapolation():
    y = np.arange(0., 12., 1.)
    x = 10. + .003 * y ** 2
    dxdy = .006 * y
    observed = [_e("TOE", float(xx), float(yy), tangent=(float(d), 1.))
                for xx, yy, d in zip(x, y, dxdy)]
    tracks, _ = trace_independent_breaklines(observed)
    assert len(tracks) == 1
    assert len(tracks[0].xyz) == 12
    assert np.allclose(sorted(tracks[0].xyz[:, 0]), sorted(x))


def test_multiscale_end_to_end_retains_noisy_synthetic_edges():
    raw = _bench()
    raw[:, 2] += np.random.default_rng(20261001).normal(0, .015, len(raw))
    observations, _ = discover_multiscale_ground(raw)
    tracks, report = trace_independent_breaklines(observations)
    assert report.tracks_crest == report.tracks_toe == 1
    assert all(track.length_m >= 10. for track in tracks)
    assert {tuple(row) for t in tracks for row in t.xyz} <= set(map(tuple, raw.tolist()))


def test_ground_not_observed_does_not_create_fabricated_lines():
    observations, report = discover_multiscale_ground(np.empty((0, 3)))
    tracks, tracking = trace_independent_breaklines(observations)
    assert observations == [] and tracks == []
    assert report.merged_crest == report.merged_toe == 0
    assert tracking.tracks_crest == tracking.tracks_toe == 0


def test_preview_dxf_exports_only_reviewable_3d_vertices(tmp_path):
    import importlib.util
    from pathlib import Path
    runner = Path(__file__).resolve().parents[1] / "scripts" / "preview_r20_independent.py"
    spec = importlib.util.spec_from_file_location("r20_preview", runner)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tracks, _ = trace_independent_breaklines(_chain("CREST", 10., range(8)))
    destination = tmp_path / "r20.dxf"
    module.export_dxf_3d(destination, tracks)
    body = destination.read_text(encoding="ascii")
    assert "POLYLINE" in body and body.count("VERTEX\n") == 8
    assert "R20_CRISTA_REVISAO" in body and "10.000000" in body
