from __future__ import annotations

from pathlib import Path

import laspy
import numpy as np

from talude_v1.config import ExtractConfig
from talude_v1.engine import extract


def _write_synthetic_las(path: Path) -> None:
    rng = np.random.default_rng(7)
    xs = np.arange(0.0, 30.0, 0.25)
    ys = np.arange(0.0, 20.0, 0.25)
    xx, yy = np.meshgrid(xs, ys)

    zz = np.where(
        xx <= 10.0,
        10.0,
        np.where(xx >= 15.0, 5.0, 10.0 - (xx - 10.0)),
    )
    zz += rng.normal(0.0, 0.01, size=zz.shape)

    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = np.array([0.001, 0.001, 0.001])
    las = laspy.LasData(header)
    las.x = xx.ravel()
    las.y = yy.ravel()
    las.z = zz.ravel()
    las.classification = np.full(xx.size, 2, dtype=np.uint8)
    las.write(path)


def test_las_uses_streaming_and_extracts_lines(tmp_path: Path) -> None:
    source = tmp_path / "talude.las"
    output = tmp_path / "out"
    _write_synthetic_las(source)

    cfg = ExtractConfig(
        cell_size=0.25,
        slope_low_deg=25.0,
        slope_high_deg=35.0,
        min_face_area_m2=8.0,
        min_line_length_m=8.0,
        smooth_sigmas_cells=(0.5, 1.0, 1.5),
        min_scale_persistence=2,
        min_gradient_coherence=0.8,
        classification_filter=(2,),
    )

    report = extract(source, output, cfg)

    assert report["streaming_mode"] is True
    assert report["engine"] == "BREAKLINE_ENGINE_V1_STREAMING"
    assert report["points_processed"] > 0
    assert report["crest_lines"] >= 1
    assert report["toe_lines"] >= 1
    assert (output / "talude_breaklines.dxf").exists()
    assert (output / "talude_breaklines.geojson").exists()
