"""Preflight for writable volumes used by V3 dependencies and Windows packaging.

No deletion, installation or downloaded file is performed by this check. Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

GIB = 1024 ** 3


class DiskSpaceError(RuntimeError):
    """A volume needed by the V3 build lacks sufficient free space."""


def required_locations(repo: Path | None = None) -> dict[str, Path]:
    """Include pip temporary space AND the LBM environment/build volume."""
    home = Path.home()
    localapp = Path(os.environ.get('LOCALAPPDATA') or home)
    temp = Path(os.environ.get('TEMP') or os.environ.get('TMP') or localapp)
    return {
        'Repositório': repo or Path.cwd(),
        'Python/venv': Path(sys.executable).parent,
        'TEMP/pip': temp,
        'Build LBM': localapp,
    }


def check_disk_space(minimum_gib: float, *, locations: dict[str, Path] | None = None,
                     usage=shutil.disk_usage) -> list[str]:
    """Check each distinct volume, not only the repository's current drive.

    Keep usage injectable for deterministic low-disk regression tests.
    """
    if minimum_gib < 0:
        raise ValueError('minimum_gib não pode ser negativo')
    targets = locations if locations is not None else required_locations()
    seen: dict[str, tuple[list[str], int]] = {}
    for label, value in targets.items():
        path = Path(value).expanduser().absolute()
        while not path.exists() and path != path.parent:
            path = path.parent
        drive = path.anchor.casefold()
        if drive not in seen:
            free = int(usage(path).free)
            seen[drive] = ([label], free)
        else:
            seen[drive][0].append(label)
    lines = []
    failures = []
    for drive, (labels, free) in seen.items():
        label = ', '.join(labels)
        free_gib = free / GIB
        line = f"{drive or 'volume'} [{label}]: {free_gib:.2f} GiB livres; mínimo {minimum_gib:.2f} GiB"
        lines.append(line)
        if free_gib < minimum_gib:
            failures.append(line)
    if failures:
        raise DiskSpaceError(
            'Sem espaço suficiente para instalar/compilar o Talude Studio V3.\n'
            + '\n'.join(failures)
            + '\nLiberta espaço no(s) volume(s) indicado(s) antes do BUILD + TESTES. '
              'Recomendação prática: cerca de 15 GB livres no C:. '
              'Não apagar o LAS original, nem %LOCALAPPDATA%\\LBM\\v/py312. '
              'Podes inspecionar a cache com: python -m pip cache info.'
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Validação do espaço disponível V3 antes do pip.')
    parser.add_argument('--minimum-gb', type=float, default=8.0,
                        help='Espaço mínimo por volume em GiB (8 teste; 12 BUILD)')
    args = parser.parse_args(argv)
    try:
        result = check_disk_space(args.minimum_gb)
    except (DiskSpaceError, OSError, ValueError) as error:
        print('[ERRO] DISK_PREFLIGHT_V3: ' + str(error), flush=True)
        return 28
    for line in result:
        print('[OK] DISK_PREFLIGHT_V3: ' + line, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())