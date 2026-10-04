"""Regression: disk-full Errno 28 should be caught BEFORE downloading PySide6."""
from __future__ import annotations

import importlib.util
import json
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_checker():
    spec = importlib.util.spec_from_file_location('check_disk_space_v3', ROOT / 'scripts/check_disk_space_v3.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_low_space_is_explicit_and_does_not_delete_anything(tmp_path):
    check = load_checker()
    fake = namedtuple('Usage', 'total used free')
    source = tmp_path / 'important_data.las'
    source.write_bytes(b'KEEP')
    try:
        check.check_disk_space(8, locations={'TEMP/pip': tmp_path},
                               usage=lambda path: fake(10 * check.GIB, 9 * check.GIB, check.GIB))
    except check.DiskSpaceError as exc:
        assert 'Sem espaço suficiente' in str(exc)
        assert 'TEMP/pip' in str(exc)
        assert 'LBM' in str(exc)
    else:
        raise AssertionError('Low disk must fail before pip')
    assert source.read_bytes() == b'KEEP'


def test_sufficient_space_passes(tmp_path):
    check = load_checker()
    fake = namedtuple('Usage', 'total used free')
    lines = check.check_disk_space(8, locations={'Build LBM': tmp_path},
                                   usage=lambda path: fake(20 * check.GIB, 5 * check.GIB, 15 * check.GIB))
    assert len(lines) == 1 and '15.00 GiB' in lines[0]


def test_v3_profile_checks_disk_before_pip_and_preserves_v2():
    v3 = json.loads((ROOT / 'localbuild/talude_v3.json').read_text(encoding='utf-8'))
    copy = json.loads((ROOT / 'LocalBuildManager__TALUDE_STUDIO/projects/talude_v3.json').read_text(encoding='utf-8'))
    assert v3 == copy
    assert v3['required_source_revision']['value'] == (ROOT / 'localbuild/SOURCE_REVISION.txt').read_text().strip()
    for mode, preflight in (('test','disk-test'), ('build','disk-build'), ('full','disk-full')):
        steps = v3['pipelines'][mode]
        assert steps[0]['id'] == preflight
        assert 'check_disk_space_v3.py' in steps[0]['command']
        assert '--upgrade pip' not in '\n'.join(step['command'] for step in steps)
        assert steps[1]['id'] in {'deps-test','deps-build'}
    assert v3['branch'] == 'v3-modular'
    assert (ROOT / 'localbuild/talude_v2.json').exists()