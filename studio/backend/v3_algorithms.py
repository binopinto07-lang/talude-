"""V3 external algorithm adapters. No QGIS imports, originals remain unmodified."""
from __future__ import annotations

import ast
from bisect import bisect_right
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from math import acos, hypot, degrees
from pathlib import Path
import os
import sys
import uuid

CLASSIFIER_API = '2.0'
CORE_API = '0.1.3.1'


class AlgorithmCompatibilityError(RuntimeError):
    """External module is absent or does not implement the documented contract."""


def algorithm_root() -> Path:
    explicit = os.environ.get('TALUDE_ALGORITM_DIR')
    if explicit:
        return Path(explicit).expanduser().resolve()
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).resolve().parent
        for candidate in (exe_dir.parent / 'ALGORITM', exe_dir / 'ALGORITM'):
            if candidate.is_dir():
                return candidate
        return exe_dir.parent / 'ALGORITM'
    return Path(__file__).resolve().parents[2] / 'ALGORITM'


def classifier_path() -> Path:
    return algorithm_root() / 'CLASSIFY_LAS' / 'portable_api.py'


def talude_auto_path() -> Path:
    return algorithm_root() / 'TALUDE_AUTO' / 'TALUDE_AUTO.py'


def _source(path: Path) -> str:
    if not path.is_file():
        raise AlgorithmCompatibilityError(f'Algoritmo externo em falta: {path}')
    return path.read_text(encoding='utf-8-sig')


def _literal_assignment(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            try:
                return ast.literal_eval(node.value)
            except (ValueError, TypeError):
                break
    raise AlgorithmCompatibilityError(f'Contrato externo inválido: {name} literal não encontrado.')


def load_classifier():
    """Reloads the replaceable original on every operation; no stale module cache."""
    path = classifier_path()
    source = _source(path)
    tree = ast.parse(source, filename=str(path))
    if _literal_assignment(tree, 'API_VERSION') != CLASSIFIER_API:
        raise AlgorithmCompatibilityError(f'Classificador incompatível: API_VERSION esperado {CLASSIFIER_API}.')
    module_root = str(path.parent)
    if module_root not in sys.path:
        # Vendored las_classifier package lives beside portable_api.py.
        sys.path.insert(0, module_root)
    spec = spec_from_file_location(f'talude_external_classify_{uuid.uuid4().hex}', path)
    if spec is None or spec.loader is None:
        raise AlgorithmCompatibilityError(f'Impossível carregar classificador: {path}')
    module = module_from_spec(spec)
    # Dataclasses need their module present during class declaration.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        for name in (
            'Settings', 'classify_file', 'create_mdt_from_classified',
            'inspect_source', 'ALGORITHM_ID',
        ):
            if not hasattr(module, name):
                raise AlgorithmCompatibilityError(f'Falta {name} no classificador {path.name}.')
        return module
    finally:
        sys.modules.pop(spec.name, None)


def load_talude_core():
    """Extract only the *existing* QGIS-free geometry AST from TALUDE_AUTO.py.

    The original V0.2.3 is retained byte-for-byte; importing it normally would
    require a QGIS runtime. The AST contract is checked at every invocation.
    Never executes the QGIS preprocessing, UI, or Processing classes.
    """
    path = talude_auto_path()
    tree = ast.parse(_source(path), filename=str(path))
    version = _literal_assignment(tree, 'CORE_VERSION')
    if version != CORE_API:
        raise AlgorithmCompatibilityError(f'TALUDE_AUTO CORE_VERSION {version!r} incompatível com adapter {CORE_API}.')
    needed = {'SplitResult', 'Ring', 'turn_angle', 'detect', 'cut_open_polyline'}
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in needed]
    if {node.name for node in nodes} != needed:
        raise AlgorithmCompatibilityError('TALUDE_AUTO sem núcleo geométrico compatível: ' + ', '.join(sorted(needed)))
    env = {'__name__': __name__, '__builtins__': __builtins__,
           'dataclass': dataclass, 'bisect_right': bisect_right,
           'acos': acos, 'hypot': hypot, 'degrees': degrees}
    isolated = ast.Module(body=nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(isolated), str(path), 'exec'), env, env)
    return env


def available_algorithms() -> dict:
    result = {}
    for name, loader, path in (
        ('CLASSIFY', load_classifier, classifier_path()),
        ('TALUDE_AUTO', load_talude_core, talude_auto_path()),
    ):
        try:
            module = loader()
            result[name] = {'available': True, 'path': str(path), 'version': str(module.API_VERSION if name == 'CLASSIFY' else CORE_API)}
        except Exception as exc:
            result[name] = {'available': False, 'path': str(path), 'error': str(exc)}
    return result
