"""V3 isolated contract tests, no QGIS and no GUI required."""
from __future__ import annotations
import ast
import os
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.backend.v3_algorithms import (algorithm_root, load_talude_core,
    AlgorithmCompatibilityError, available_algorithms)
from studio.backend.v3_talude_auto import _rings
from studio.backend.v3_mdt import generate_mdt


def test_core_loads_without_qgis():
    sys.modules.pop('qgis', None)
    core = load_talude_core()
    assert all(k in core for k in ('Ring','detect','SplitResult','cut_open_polyline'))
    assert 'qgis' not in sys.modules


def test_ring_detects_closed_contour():
    core = load_talude_core()
    pts = [(0,0),(20,0),(24,0),(25,1),(25,3),(24,4),(20,4),(0,4),(-1,3),(-1,1),(0,0)]
    ring = core['Ring'](pts)
    r = core['detect'](ring)
    assert r.status in ('CANDIDATE', 'REVIEW')
    if r.status == 'CANDIDATE':
        assert len(r.margins) == 2
        assert all(len(ring.section(*v)) >= 2 for v in r.margins)


def test_geometry_ring_does_not_allow_open_contour():
    core = load_talude_core()
    with pytest.raises(ValueError):
        core['Ring']([(0,0),(20,0),(20,2),(0,2)])


def test_external_originals_present_and_compatible():
    assert (algorithm_root()/'TALUDE_AUTO'/'TALUDE_AUTO.py').exists()
    assert available_algorithms()['TALUDE_AUTO']['available']
    script = (algorithm_root()/'CLASSIFY'/'classify_las_algorithm.py').read_text()
    tree = ast.parse(script)
    assert any(isinstance(x, ast.Assign) and any(isinstance(t,ast.Name) and t.id=='API_VERSION' for t in x.targets) for x in tree.body)
    assert 'LAS-CAFIISICA' in script


def test_fail_early_if_algorithm_missing(tmp_path, monkeypatch):
    monkeypatch.setenv('TALUDE_ALGORITM_DIR', str(tmp_path))
    with pytest.raises(AlgorithmCompatibilityError, match='em falta'):
        load_talude_core()


def test_polygon_boundaries_only_exteriors():
    from shapely.geometry import Polygon
    poly = Polygon([(0,0),(20,0),(20,5),(0,5)], holes=[[(3,1),(5,1),(5,2),(3,2)]])
    rings = list(_rings(poly))
    assert len(rings) == 1
    assert rings[0][0] == rings[0][-1]


def test_mdt_rejects_missing_source(tmp_path):
    with pytest.raises((FileNotFoundError, ModuleNotFoundError)):
        generate_mdt(tmp_path/'missing.las', tmp_path/'test.tif')


def test_frontend_buttons_and_real_visibility():
    js = (ROOT/'studio/viewer/v3.js').read_text()
    for key in ('v3Classify','v3MDT','v3Studio','v3Auto', 'v3StudioCrest', 'v3StudioToe', 'v3AutoCrest', 'v3AutoToe'):
        assert key in js
    assert 'object.visible' in js
    assert 'pointcloud.visible' in js
    assert 'mdtMesh.visible' in js


def test_no_qgis_imports_in_studio_adapters():
    for name in ('v3_algorithms.py','v3_mdt.py','v3_talude_auto.py','v3_api.py'):
        source = (ROOT/'studio/backend'/name).read_text()
        t = ast.parse(source)
        assert not any(isinstance(x, ast.ImportFrom) and x.module and x.module.split('.')[0]=='qgis' for x in ast.walk(t))


def test_classify_guard_prevents_large_original_in_memory():
    source = (ROOT/'studio/backend/v3_api.py').read_text()
    assert '25_000_000' in source
    assert 'a nuvem original não foi alterada' in source.lower()


def test_mdt_ground_only_and_nodata(tmp_path, monkeypatch):
    """Integration of GeoTIFF generation with a minimal in-memory LAS reader stub."""
    from types import SimpleNamespace
    class FakeCRS:
        def to_epsg(self): return 3763
    class Reader:
        def __init__(self):
            self.header = SimpleNamespace(mins=[0.,0.,0.], maxs=[4.,4.,10.], point_count=5,
                                          parse_crs=lambda: FakeCRS())
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def chunk_iterator(self, size):
            yield SimpleNamespace(x=np.array([.5,1.5,2.5,3.5,.5]),
                                  y=np.array([.5,1.5,2.5,3.5,1.5]),
                                  z=np.array([5.,6.,7.,8.,100.]),
                                  classification=np.array([2,2,2,2,5]))
    import types
    monkeypatch.setitem(sys.modules,'laspy',types.SimpleNamespace(open=lambda path: Reader()))
    source = tmp_path / 'input.las'; source.write_bytes(b'MOCK')
    output = tmp_path / 'MDT.tif'
    report = generate_mdt(source, output, resolution_m=1.)
    import rasterio
    with rasterio.open(output) as ds:
        data = ds.read(1,masked=True)
        assert ds.crs.to_epsg() == 3763
        assert ds.nodata == -9999.0
        assert int(np.count_nonzero(~np.ma.getmaskarray(data))) == 4
        assert not np.isclose(np.ma.getdata(data),100.).any()
        assert report['ground_points_used'] == 4
        assert report['nodata_cells'] > 0


def test_geotiff_raster_adapter_refuses_missing_observation(tmp_path):
    import rasterio
    from rasterio.transform import from_origin
    from studio.backend.v3_talude_auto import execute_talude_auto
    source = tmp_path / 'empty.tif'
    with rasterio.open(source,'w',driver='GTiff',width=32,height=32,count=1,
                       dtype='float32',crs='EPSG:3763',nodata=-9999.,
                       transform=from_origin(55000,162000,.5,.5)) as dst:
        dst.write(np.full((32,32),-9999.,dtype='float32'),1)
    with pytest.raises(ValueError,match='sem área Ground'):
        execute_talude_auto(source,tmp_path/'output')
