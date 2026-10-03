"""Bounded-memory Ground-only GeoTIFF MDT generated without QGIS.

No interpolation over unobserved cells: missing observation = nodata. EPSG:3763.
"""
from __future__ import annotations

from pathlib import Path
from math import ceil, sqrt
import os

import numpy as np

NODATA = -9999.0
EPSG = 3763


def generate_mdt(source: str | Path, destination: str | Path, *, resolution_m: float | None = None,
                 max_cells: int = 9_000_000) -> dict:
    import laspy
    import rasterio
    from rasterio.transform import from_origin

    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if source == destination:
        raise ValueError('O MDT não pode substituir a nuvem classificada.')
    if not source.is_file():
        raise FileNotFoundError(source)
    with laspy.open(source) as reader:
        crs = reader.header.parse_crs()
        if crs is None or crs.to_epsg() != EPSG:
            raise ValueError('O LAS classificado deve declarar EPSG:3763; sem reprojeção silenciosa.')
        total = int(reader.header.point_count)
        minx, miny = (float(reader.header.mins[i]) for i in (0, 1))
        maxx, maxy = (float(reader.header.maxs[i]) for i in (0, 1))
        dx, dy = maxx - minx, maxy - miny
        if dx <= 0 or dy <= 0:
            raise ValueError('Extensão XY do LAS inválida.')
        if resolution_m is None:
            # Approximate target population: conservative if not every point is Ground.
            candidate = max(0.10, min(1.0, 2.8 * sqrt(dx * dy / max(total, 1))))
            resolution_m = max(candidate, 1.03 * sqrt(dx * dy / max_cells))
        if not np.isfinite(resolution_m) or resolution_m <= 0:
            raise ValueError('Resolução do MDT inválida.')
        width, height = ceil(dx / resolution_m) + 1, ceil(dy / resolution_m) + 1
        if width * height > max_cells:
            raise ValueError('MDT excede limite de memória: aumentar a resolução ou dividir o levantamento.')
        sumz = np.zeros((height, width), dtype=np.float64)
        count = np.zeros((height, width), dtype=np.uint32)
        used = 0
        for chunk in reader.chunk_iterator(400_000):
            ground = np.asarray(chunk.classification) == 2
            if not ground.any():
                continue
            xs = np.asarray(chunk.x[ground], dtype=np.float64)
            ys = np.asarray(chunk.y[ground], dtype=np.float64)
            zs = np.asarray(chunk.z[ground], dtype=np.float64)
            good = np.isfinite(xs) & np.isfinite(ys) & np.isfinite(zs)
            ix = np.clip(np.floor((xs[good] - minx) / resolution_m).astype(np.int64), 0, width - 1)
            iy = np.clip(np.floor((maxy - ys[good]) / resolution_m).astype(np.int64), 0, height - 1)
            np.add.at(sumz, (iy, ix), zs[good])
            np.add.at(count, (iy, ix), 1)
            used += len(ix)
        if used == 0:
            raise ValueError('A nuvem não contém pontos de terreno (classe 2).')
    grid = np.full((height, width), NODATA, dtype=np.float32)
    observed = count > 0
    grid[observed] = (sumz[observed] / count[observed]).astype(np.float32)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.stem + '.partial.tif')
    try:
        with rasterio.open(tmp, 'w', driver='GTiff', width=width, height=height,
                           count=1, dtype='float32', crs=f'EPSG:{EPSG}',
                           transform=from_origin(minx, maxy, resolution_m, resolution_m),
                           nodata=NODATA, compress='deflate', predictor=3,
                           tiled=True, blockxsize=256, blockysize=256) as dst:
            dst.write(grid, 1)
            dst.write_mask((observed.astype('uint8') * 255))
        with rasterio.open(tmp) as check:
            if check.crs.to_epsg() != EPSG or check.count != 1:
                raise RuntimeError('GeoTIFF criado não passou a validação CRS/canal.')
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)
    return {'path': str(destination), 'epsg': EPSG, 'resolution_m': resolution_m,
            'width': width, 'height': height, 'ground_points_used': int(used),
            'observed_cells': int(observed.sum()), 'nodata_cells': int((~observed).sum()),
            'nodata': NODATA, 'algorithm': 'V3_OBSERVED_GROUND_MEAN_V1'}
