"""Bounded-memory V3 streaming regression. Fake LAS adapter verifies IO semantics.

No point cloud file is committed. The same tests exercise the actual algorithms
on Windows, where laspy is installed. The fake adapter also runs offline here.
"""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
ORIGINAL = HERE / 'ALGORITM' / 'CLASSIFY' / 'classify_las_algorithm_ORIGINAL_V1.py'
STREAMING = HERE / 'ALGORITM' / 'CLASSIFY' / 'classify_streaming.py'


class Chunk:
    def __init__(self, x, y, z, classification, intensity, red, green, blue):
        self.x = x.copy()
        self.y = y.copy()
        self.z = z.copy()
        self.classification = classification.copy()
        self.intensity = intensity.copy()
        self.red = red.copy()
        self.green = green.copy()
        self.blue = blue.copy()

    def __len__(self):
        return len(self.z)


class FakeHeader:
    def __init__(self, chunks, *, epsg=3763, header_count=None):
        xyz = np.concatenate([np.column_stack((c.x,c.y,c.z)) for c in chunks])
        self.mins = xyz.min(axis=0)
        self.maxs = xyz.max(axis=0)
        self.point_count = len(xyz) if header_count is None else header_count
        self.epsg = epsg

    def parse_crs(self):
        return SimpleNamespace(to_epsg=lambda: self.epsg)


class FakeReader:
    def __init__(self, chunks, header, calls):
        self.header = header
        self.chunks = chunks
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def chunk_iterator(self, size):
        self.calls['reader_passes'] += 1
        for c in self.chunks:
            yield Chunk(c.x,c.y,c.z,c.classification,c.intensity,c.red,c.green,c.blue)


class FakeWriter:
    def __init__(self, path, header, saved):
        self.path, self.header, self.saved = path, header, saved
        self.saved['written'] = []

    def __enter__(self):
        self.path.write_bytes(b'FAKE-LAS-FOR-UNIT-TEST')
        self.saved['header'] = self.header
        return self

    def __exit__(self, *args):
        return False

    def write_points(self, chunk):
        self.saved['written'].append(chunk)


@pytest.fixture
def loaded(tmp_path, monkeypatch):
    rng = np.random.default_rng(407)
    x = rng.uniform(0, 20, 9000)
    y = rng.uniform(0, 8, 9000)
    z = 240 + 0.02 * x + rng.uniform(0, .08, len(x))
    z[np.arange(len(x)) % 43 == 0] += rng.uniform(0.6, 2, len(z[np.arange(len(x)) % 43 == 0]))
    classes = np.ones(len(x), dtype=np.uint8)
    classes[::151] = 7
    intensity = rng.integers(0, 65536, len(x), dtype=np.uint16)
    red = rng.integers(0, 65536, len(x), dtype=np.uint16)
    green = rng.integers(0, 65536, len(x), dtype=np.uint16)
    blue = rng.integers(0, 65536, len(x), dtype=np.uint16)
    # Multiple chunks allow proof that source is traversed twice without XYZ concat.
    chunks = [Chunk(*[v[i:i+750] for v in (x,y,z,classes,intensity,red,green,blue)])
              for i in range(0, len(x), 750)]
    calls = {'reader_passes': 0}
    saved = {}
    source = tmp_path / 'original.las'
    source.write_bytes(b'FAKE SOURCE')
    header = FakeHeader(chunks)
    def open_stub(path, mode='r', header=None):
        if mode == 'w':
            return FakeWriter(Path(path), header, saved)
        return FakeReader(chunks, saved.get('source_header', hdr), calls)
    hdr = header
    saved['source_header'] = header
    monkeypatch.setitem(sys.modules, 'laspy', SimpleNamespace(open=open_stub))
    def get_module(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, mod)
        spec.loader.exec_module(mod)
        return mod
    original = get_module('v3_original_for_stream_test', ORIGINAL)
    streamed = get_module('v3_streamed_for_stream_test', STREAMING)
    return SimpleNamespace(source=source, destination=tmp_path/'classified.las',
                           original=original, streamed=streamed,
                           chunks=chunks, hdr=hdr, saved=saved, calls=calls,
                           input=(x,y,z,classes,intensity,red,green,blue))


def test_streaming_v1_matches_original_point_classes_and_all_dimensions(loaded):
    inp = loaded.input
    expected_ground, _ = loaded.original.classify_arrays(*inp[:3])
    expected_class = np.where(expected_ground, 2, 1).astype(np.uint8)
    expected_class[inp[3] == 7] = 7
    result = loaded.streamed.classify_file_streamed(
        loaded.source, loaded.destination, original=loaded.original)
    assert loaded.calls['reader_passes'] == 2
    assert result['total_points'] == len(expected_ground)
    assert result['ground_points'] == int(np.count_nonzero(expected_class == 2))
    assert result['processing'] == 'bounded_memory_two_pass'
    assert loaded.saved['header'] is loaded.hdr
    output = loaded.saved['written']
    def glue(name):
        return np.concatenate([getattr(c, name) for c in output])
    np.testing.assert_array_equal(glue('classification'), expected_class)
    for i, key in enumerate(('x','y','z','intensity','red','green','blue')):
        np.testing.assert_array_equal(glue(key), inp[(0,1,2,4,5,6,7)[i]])


def test_cancel_cleans_partial_destination(loaded):
    with pytest.raises(InterruptedError, match='cancelada'):
        loaded.streamed.classify_file_streamed(loaded.source, loaded.destination,
                                               original=loaded.original,
                                               cancel_callback=lambda:True)
    assert not loaded.destination.exists()


def test_wrong_crs_refuses_to_write(loaded):
    loaded.hdr.epsg = 4326
    with pytest.raises(ValueError, match='EPSG:3763'):
        loaded.streamed.classify_file_streamed(loaded.source, loaded.destination,
                                               original=loaded.original)
    assert not loaded.destination.exists()


def test_max_cells_enforced(loaded):
    with pytest.raises(MemoryError, match='limit'):
        loaded.streamed.classify_file_streamed(
            loaded.source, loaded.destination, original=loaded.original,
            settings=loaded.original.Settings(max_cells=2))
    assert not loaded.destination.exists()


def test_mismatched_header_does_not_publish_partial(loaded):
    loaded.hdr.point_count += 7
    with pytest.raises(ValueError, match='Header point count'):
        loaded.streamed.classify_file_streamed(loaded.source, loaded.destination,
                                               original=loaded.original)
    assert not loaded.destination.exists()


def test_class7_not_changed_even_if_ground(loaded):
    loaded.streamed.classify_file_streamed(loaded.source, loaded.destination,
                                           original=loaded.original)
    cls=np.concatenate([c.classification for c in loaded.saved['written']])
    assert (cls[loaded.input[3] == 7] == 7).all()


def test_studio_compatibility_wrapper_is_visible_without_rebuilding_exe(loaded):
    # Mimic the dynamic spec loader in studio/backend/v3_algorithms.py.
    import importlib.util
    from uuid import uuid4
    modified = HERE/'ALGORITM'/'CLASSIFY'/'classify_las_algorithm.py'
    spec = importlib.util.spec_from_file_location('talude_external_classify_'+uuid4().hex, modified)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        assert module.API_VERSION == '1.0'
        assert callable(module.classify_file_streamed)
    finally:
        del sys.modules[spec.name]
    stats = module.classify_file_streamed(loaded.source, loaded.destination)
    assert stats['total_points'] == sum(len(c) for c in loaded.chunks)
    assert loaded.calls['reader_passes'] == 2


def test_original_v1_remains_bit_identical():
    import hashlib
    reference = (HERE/'ALGORITM'/'CLASSIFY'/'classify_las_algorithm_ORIGINAL_V1.py').read_bytes()
    assert hashlib.sha256(reference).hexdigest() == '35ccf2940f8e2d3d4e09a229ff4bc056b0f79b12d9f35aaf70f472a15a607138'
    modified = (HERE/'ALGORITM'/'CLASSIFY'/'classify_las_algorithm.py').read_bytes()
    assert modified.startswith(reference)


def test_progress_is_monotonic_and_cancel_during_second_pass_cleans_partial(loaded):
    progress = []
    def callback(label, value):
        progress.append((label, value))
    with pytest.raises(InterruptedError, match='cancelada'):
        loaded.streamed.classify_file_streamed(loaded.source, loaded.destination,
                                               original=loaded.original,
                                               progress_callback=callback,
                                               cancel_callback=lambda:bool(progress and progress[-1][1]>=0.55))
    assert all(0 <= v <= 1 for _,v in progress)
    assert all(progress[i][1] <= progress[i+1][1] for i in range(len(progress)-1))
    assert not loaded.destination.exists()


def test_streaming_with_real_laspy_file_if_available(tmp_path):
    laspy = pytest.importorskip('laspy')
    from pyproj import CRS
    spec = importlib.util.spec_from_file_location('standalone_v1_real_las', ORIGINAL)
    original = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = original
    try:
        spec.loader.exec_module(original)
    finally:
        del sys.modules[spec.name]
    spec2 = importlib.util.spec_from_file_location('standalone_stream_real_las', STREAMING)
    streamed = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(streamed)
    rng=np.random.default_rng(731)
    header=laspy.LasHeader(point_format=3, version='1.2')
    header.scales = [0.001,0.001,0.001]
    header.offsets = [0,0,0]
    header.add_crs(CRS.from_epsg(3763))
    cloud=laspy.LasData(header)
    cloud.x=np.round(rng.uniform(55650,55660,5500),3)
    cloud.y=np.round(rng.uniform(162800,162806,5500),3)
    cloud.z=np.round(244+.02*(cloud.x-55650)+rng.uniform(0,.1,5500),3)
    cloud.classification=np.ones(5500,dtype=np.uint8)
    cloud.classification[::101]=7
    cloud.intensity=rng.integers(0,65536,5500,dtype=np.uint16)
    cloud.red=rng.integers(0,65536,5500,dtype=np.uint16)
    cloud.green=rng.integers(0,65536,5500,dtype=np.uint16)
    cloud.blue=rng.integers(0,65536,5500,dtype=np.uint16)
    src=tmp_path/'original.las'; dst=tmp_path/'classified.las'
    cloud.write(src)
    with laspy.open(src) as reader:
        actual=reader.read()
    settings=original.Settings(chunk_points=700)
    expected,_=original.classify_arrays(actual.x,actual.y,actual.z,settings=settings)
    expected_cls=np.where(expected,2,1).astype(np.uint8)
    expected_cls[np.asarray(actual.classification)==7]=7
    stats=streamed.classify_file_streamed(src,dst,original=original,settings=settings)
    with laspy.open(dst) as reader:
        out=reader.read()
        assert reader.header.parse_crs().to_epsg()==3763
    assert stats['total_points']==5500
    np.testing.assert_array_equal(np.asarray(out.classification),expected_cls)
    for dimension in ('X','Y','Z','red','green','blue','intensity'):
        np.testing.assert_array_equal(np.asarray(getattr(actual,dimension)),
                                      np.asarray(getattr(out,dimension)))
