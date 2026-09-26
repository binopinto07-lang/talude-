from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from talude_v1.config import ExtractConfig
from talude_v1.engine import extract


def make_slope_cloud(path: Path) -> None:
    rng = np.random.default_rng(42)
    xs = np.arange(0.0, 30.0, 0.20)
    ys = np.arange(0.0, 20.0, 0.20)
    xx, yy = np.meshgrid(xs, ys)

    zz = np.where(
        xx <= 10.0,
        10.0,
        np.where(xx >= 15.0, 5.0, 10.0 - (xx - 10.0)),
    )
    zz += rng.normal(0.0, 0.015, size=zz.shape)

    pts = np.column_stack((xx.ravel(), yy.ravel(), zz.ravel()))
    np.savetxt(path, pts, fmt="%.4f")


def test_extracts_crest_and_toe(tmp_path: Path) -> None:
    inp = tmp_path / "slope.xyz"
    out = tmp_path / "out"
    make_slope_cloud(inp)

    cfg = ExtractConfig(
        cell_size=0.20,
        slope_low_deg=25.0,
        slope_high_deg=35.0,
        min_face_area_m2=10.0,
        min_line_length_m=8.0,
        smooth_sigmas_cells=(0.5, 1.0, 1.5),
        min_scale_persistence=2,
        min_gradient_coherence=0.8,
        use_ground_class=False,
    )
    report = extract(inp, out, cfg)

    assert report["crest_lines"] >= 1
    assert report["toe_lines"] >= 1

    gj = json.loads(
        (out / "talude_breaklines.geojson").read_text(encoding="utf-8")
    )
    crest = [
        f
        for f in gj["features"]
        if f["properties"]["type"] == "CREST"
    ][0]
    toe = [
        f
        for f in gj["features"]
        if f["properties"]["type"] == "TOE"
    ][0]

    crest_x = np.median(
        [p[0] for p in crest["geometry"]["coordinates"]]
    )
    toe_x = np.median(
        [p[0] for p in toe["geometry"]["coordinates"]]
    )

    assert abs(crest_x - 10.0) < 0.8
    assert abs(toe_x - 15.0) < 0.8


