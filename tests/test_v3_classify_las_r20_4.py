"""V3 contract tests for the vendored complete CLASSIFY LAS R20.4 module."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_complete_classifier_module_loads():
    from studio.backend.v3_algorithms import load_classifier
    module = load_classifier()
    assert module.API_VERSION == "2.0"
    assert module.ALGORITHM_ID == "LAS_CAFIISICA_UNIVERSAL_GROUND_R20_4"
    assert callable(module.classify_file)
    assert callable(module.create_mdt_from_classified)
    assert callable(module.inspect_source)


def test_obsolete_simplified_classifier_is_removed():
    assert not (ROOT / "ALGORITM" / "CLASSIFY").exists()
    assert (ROOT / "ALGORITM" / "CLASSIFY_LAS" / "portable_api.py").is_file()
    assert (
        ROOT / "ALGORITM" / "CLASSIFY_LAS" / "las_classifier"
        / "classifiers" / "universal_ground.py"
    ).is_file()


def _write_small_classified(path: Path):
    laspy = pytest.importorskip("laspy")
    from pyproj import CRS
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = [0.001, 0.001, 0.001]
    header.add_crs(CRS.from_epsg(3763))
    las = laspy.LasData(header)
    x = np.array([55650.0, 55650.25, 55650.50, 55650.75])
    y = np.array([162800.0, 162800.0, 162800.0, 162800.0])
    las.x, las.y = x, y
    las.z = np.array([240.0, 240.1, 240.2, 245.0])
    las.classification = np.array([2, 2, 2, 1], dtype=np.uint8)
    las.write(path)


def test_source_profile_does_not_require_sensor_selection(tmp_path):
    from studio.backend.v3_algorithms import load_classifier
    src = tmp_path / "p1_or_l3.las"
    _write_small_classified(src)
    profile = load_classifier().inspect_source(src)
    assert profile["crs"] == "EPSG:3763"
    assert profile["sensor_selection_required"] is False
    assert profile["processing_mode"] == "LAS_CAFIISICA_R20_4_UNIVERSAL"


def test_mdt_marks_interpolation_separately(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    from studio.backend.v3_algorithms import load_classifier
    src = tmp_path / "classified.las"
    _write_small_classified(src)
    out = tmp_path / "mdt.tif"
    module = load_classifier()
    settings = module.Settings(mdt_resolution_m=0.25, mdt_max_gap_m=0.30)
    info = module.create_mdt_from_classified(src, out, settings)
    assert Path(info["path"]).is_file()
    assert Path(info["observation_state"]).is_file()
    assert info["interpolated_is_measured_ground"] is False
    with rasterio.open(info["observation_state"]) as ds:
        state = ds.read(1)
        assert ds.crs.to_epsg() == 3763
        assert np.any(state == 1)
