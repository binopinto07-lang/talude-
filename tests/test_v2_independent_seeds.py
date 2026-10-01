"""Isolated discovery contracts; the R18 AUTO path is intentionally unchanged."""
import numpy as np
import pytest

from talude_v2.breakline_seed import SeedEvidence, build_breakline_seeds
from talude_v2.curvature_seed import CurvatureSeedConfig, curvature_seed_candidates


def _line(x, z, kind, face_id=1):
    y = np.linspace(0, 12, 13)
    return {"type": kind, "face_id": face_id,
            "xyz": np.column_stack((np.full_like(y, x), y, np.full_like(y, z)))}


def _bench():
    xs = np.arange(0.0, 25.01, 0.25)
    ys = np.arange(0.0, 12.01, 0.25)
    x, y = np.meshgrid(xs, ys)
    z = np.where(x <= 10, 10.0, np.where(x >= 15, 5.0, 20.0 - x))
    return np.column_stack((x.ravel(), y.ravel(), z.ravel()))


def test_baseline_crest_only_produces_independent_seed():
    seeds = build_breakline_seeds([_line(10.0, 10.0, "CREST")])
    assert len(seeds) == 1
    assert seeds[0].kind == "CREST"
    assert seeds[0].source == "baseline"
    assert seeds[0].face_hint == 1
    assert seeds[0].seed_id == 1


def test_baseline_toe_only_produces_independent_seed():
    seeds = build_breakline_seeds([_line(15.0, 5.0, "TOE")])
    assert len(seeds) == 1 and seeds[0].kind == "TOE"


def test_crest_and_toe_are_not_required_to_be_paired():
    seeds = build_breakline_seeds([_line(10.0, 10.0, "CREST", 1),
                                   _line(15.0, 5.0, "TOE", 15)])
    assert {s.face_hint for s in seeds} == {1, 15}
    assert {s.kind for s in seeds} == {"CREST", "TOE"}


def test_curvature_finds_both_types_without_baseline():
    evidence = curvature_seed_candidates(_bench())
    crest = [e for e in evidence if e.kind == "CREST"]
    toe = [e for e in evidence if e.kind == "TOE"]
    assert crest, "Nenhuma CRISTA por curvatura."
    assert toe, "Nenhum PÉ por curvatura."
    assert min(abs(e.xyz[0] - 10.0) for e in crest) <= 0.80
    assert min(abs(e.xyz[0] - 15.0) for e in toe) <= 0.80
    assert all(e.source == "curvature" for e in evidence)
    seeds = build_breakline_seeds(independent_evidence=evidence)
    assert {s.kind for s in seeds} == {"CREST", "TOE"}


def test_flat_ground_without_evidence_has_no_seed():
    p = _bench()
    p[:, 2] = 4.0
    assert curvature_seed_candidates(p) == []
    assert build_breakline_seeds() == []


def test_missing_observations_are_not_interpolated_into_seeds():
    pts = _bench()
    pts = pts[~((pts[:, 0] >= 9.5) & (pts[:, 0] <= 11.5))]
    evidence = curvature_seed_candidates(pts)
    original_xyz = set(map(tuple, pts.tolist()))
    assert all(tuple(e.xyz) in original_xyz for e in evidence)
    assert all(not (9.5 <= e.xyz[0] <= 11.5) for e in evidence)


def test_same_location_opposite_type_does_not_dedupe():
    same = np.array([10., 6., 10.])
    e = [SeedEvidence("CREST", same, [0, 1], .7, "normal"),
         SeedEvidence("TOE", same, [0, 1], .7, "normal")]
    assert len(build_breakline_seeds(independent_evidence=e)) == 2


def test_relative_score_and_coordinates_are_validated():
    with pytest.raises(ValueError):
        SeedEvidence("CREST", [0, 1, float("nan")], None, .5, "curvature")
    with pytest.raises(ValueError):
        SeedEvidence("CREST", [0, 1, 2], None, 1.5, "curvature")
    with pytest.raises(ValueError):
        curvature_seed_candidates(_bench(), config=CurvatureSeedConfig(cell_size_m=-1))


def test_large_tile_fails_explicitly_instead_of_exhausting_memory():
    with pytest.raises(ValueError, match="subdividir"):
        curvature_seed_candidates(_bench(), config=CurvatureSeedConfig(max_grid_cells=100))


def test_direction_is_normalized_and_input_is_copied():
    xyz = np.array([1., 2., 3.])
    seed = SeedEvidence("toe", xyz, [10., 0.], .5, "manual")
    xyz[0] = 99
    assert seed.kind == "TOE"
    assert seed.xyz[0] == 1.
    assert np.allclose(seed.direction_xy, [1., 0.])
    assert not seed.xyz.flags.writeable


def test_separate_face_hints_not_collapsed_by_spatial_dedupe():
    e = [SeedEvidence("CREST", [10., 6., 10.], [0., 1.], .5, "baseline", 1),
         SeedEvidence("CREST", [10., 6., 10.], [0., 1.], .5, "baseline", 2)]
    seeds = build_breakline_seeds(independent_evidence=e)
    assert len(seeds) == 2
    assert [s.face_hint for s in seeds] == [1, 2]
