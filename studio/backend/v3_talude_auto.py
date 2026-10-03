"""Raster adapter to the unmodified TALUDE_AUTO V0.2.3 pure geometry core.

Uses Rasterio/Shapely/NumPy, not QGIS. Experimental contour discovery is
kept separate from the proven Ring/detect/cut_open_polyline implementation.
"""
from __future__ import annotations

import json
from math import hypot
from pathlib import Path
from time import perf_counter

import numpy as np

from .v3_algorithms import load_talude_core


def _rings(geometry):
    from shapely.geometry import Polygon, MultiPolygon, GeometryCollection
    if geometry.is_empty:
        return
    if isinstance(geometry, Polygon):
        if geometry.area >= 2.0:
            yield list(geometry.exterior.coords)
    elif isinstance(geometry, (MultiPolygon, GeometryCollection)):
        for item in geometry.geoms:
            yield from _rings(item)


def _sample_line_z(dem, coordinates):
    vals = []
    for p in dem.sample((tuple(p) for p in coordinates), indexes=1, masked=True):
        z = p[0]
        if np.ma.is_masked(z) or not np.isfinite(float(z)):
            return None
        vals.append(float(z))
    return vals


def _median_z(dem, line):
    # Median from 21 evenly spaced samples, as in the original.
    from shapely.geometry import LineString
    shape = LineString(line)
    pts = [shape.interpolate(shape.length * k / 22.0).coords[0] for k in range(1, 22)]
    values = []
    for val in dem.sample(pts, indexes=1, masked=True):
        if not np.ma.is_masked(val[0]) and np.isfinite(float(val[0])):
            values.append(float(val[0]))
    return float(np.median(values)) if len(values) >= 10 else None


def execute_talude_auto(mdt_path: str | Path, output_dir: str | Path, *, threshold_deg: float = 35.0,
                        min_area_m2: float = 2.0, closing_m: float = 10.0) -> dict:
    import rasterio
    from rasterio.features import shapes
    from scipy.ndimage import minimum_filter
    from shapely.geometry import shape
    from shapely.ops import unary_union
    import ezdxf

    start = perf_counter()
    core = load_talude_core()  # reload file on EVERY invocation
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=False)
    with rasterio.open(mdt_path) as dem:
        if dem.crs is None or dem.crs.to_epsg() != 3763:
            raise ValueError('TALUDE_AUTO exige MDT EPSG:3763 declarado.')
        if abs(dem.res[0] - dem.res[1]) > 1e-5:
            raise ValueError('MDT requer células quadradas neste adapter.')
        z = dem.read(1, masked=True)
        observed = (~np.ma.getmaskarray(z)) & np.isfinite(z.data)
        # The 3x3 derivative must never invent terrain across nodata holes.
        supported = minimum_filter(observed.astype('uint8'), size=3, mode='constant', cval=0).astype(bool)
        if not supported.any():
            raise ValueError('MDT sem área Ground contínua para derivadas 3x3.')
        data = z.filled(np.nan).astype('float64')
        # No gradients are accepted in a cell missing any adjacent observed point.
        dy, dx = np.gradient(np.nan_to_num(data, nan=0.0), abs(dem.res[1]), abs(dem.res[0]))
        slope = np.degrees(np.arctan(np.hypot(dx, dy)))
        steep = supported & (slope > threshold_deg)
        polygons = [shape(geom) for geom, val in shapes(steep.astype('uint8'), mask=steep, transform=dem.transform)
                    if val == 1]
        polygons = [poly for poly in polygons if poly.area >= min_area_m2]
        # Reproduce the original morphological closing; keep parameters explicit.
        merged = unary_union(polygons) if polygons else None
        contour_region = None
        if merged is not None and not merged.is_empty:
            # Original deletes holes, dissolves, smooths and buffers +10/-10.
            from shapely.geometry import Polygon
            polys = list(_rings(merged))
            without_holes = unary_union([Polygon(coords) for coords in polys]) if polys else merged
            contour_region = without_holes.buffer(closing_m, quad_segs=8).buffer(-closing_m, quad_segs=8) if closing_m else without_holes
        results = []
        review = []
        for idx, ring_coords in enumerate(_rings(contour_region) if contour_region is not None else (), start=1):
            ring_xy = [(float(x), float(y)) for x, y in ring_coords]
            try:
                ring = core['Ring'](ring_xy)
                detected = core['detect'](ring)
                if detected.status != 'CANDIDATE':
                    review.append({'contour_id': idx, 'reason': detected.reason})
                    continue
                margin_a = ring.section(*detected.margins[0]); margin_b = ring.section(*detected.margins[1])
                za, zb = _median_z(dem, margin_a), _median_z(dem, margin_b)
                if za is None or zb is None or abs(za-zb) < 0.40:
                    review.append({'contour_id': idx, 'reason': 'Cotas inconclusivas / diferença inferior a 0,40 m'})
                    continue
                crest, toe = (margin_a, margin_b) if za > zb else (margin_b, margin_a)
                # The core's original endpoint protection is applied only when
                # the 1m separation cannot be met by the original endpoints.
                crest_geom = __import__('shapely.geometry', fromlist=['LineString']).LineString(crest)
                toe_geom = __import__('shapely.geometry', fromlist=['LineString']).LineString(toe)
                trim = [0.0, 0.0]
                for end_index, endpoint in enumerate((crest_geom.interpolate(0), crest_geom.interpolate(crest_geom.length))):
                    if endpoint.distance(toe_geom) >= 1.0:
                        continue
                    for step in range(1, 21):
                        retreat = step * 0.1
                        d = retreat if end_index == 0 else crest_geom.length - retreat
                        if d <= 0 or d >= crest_geom.length:
                            break
                        if crest_geom.interpolate(d).distance(toe_geom) >= 1.0:
                            trim[end_index] = retreat
                            break
                    else:
                        review.append({'contour_id': idx, 'reason': 'Proximidade de extremidade sem solução segura'})
                if trim[0] or trim[1]:
                    crest = core['cut_open_polyline'](crest, *trim)
                for kind, coords in (('CREST', crest), ('TOE', toe)):
                    if len(coords) < 2 or sum(hypot(b[0]-a[0], b[1]-a[1]) for a,b in zip(coords,coords[1:])) < 1.0:
                        continue
                    altitudes = _sample_line_z(dem, coords)
                    if altitudes is None:
                        review.append({'contour_id': idx, 'kind': kind, 'reason': 'Linha com vértice sem Z no MDT (não exportada)'})
                        continue
                    results.append({'type': kind, 'face_id': idx,
                                    'vertices': [[float(x), float(y), zval] for (x,y), zval in zip(coords, altitudes)],
                                    'source': 'TALUDE_AUTO_V0.2.3_MDT', 'core_version': '0.1.3.1',
                                    'status': 'EXPERIMENTAL_REVIEW'})
            except Exception as exc:
                review.append({'contour_id': idx, 'reason': str(exc)})
                continue
    if not results:
        (destination / 'diagnostico.json').write_text(json.dumps({'review': review, 'steep_polygons': len(polygons), 'reason': 'Sem linhas 3D suportadas pelo MDT'}, ensure_ascii=False, indent=2), encoding='utf-8')
        raise ValueError('TALUDE_AUTO não encontrou linhas completas com Z válido. Consultar diagnostico.json; não foi declarada deteção bem-sucedida.')
    geojson = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {k: v for k, v in record.items() if k != 'vertices'},
         'geometry': {'type': 'LineString', 'coordinates': record['vertices']}} for record in results]}
    (destination / 'resultado.geojson').write_text(json.dumps(geojson, ensure_ascii=False), encoding='utf-8')
    doc = ezdxf.new('R2010')
    for layer, color in [('TALUDE_TOPO', 2), ('TALUDE_BASE', 4)]:
        doc.layers.new(layer, dxfattribs={'color': color})
    for item in results:
        doc.modelspace().add_polyline3d(item['vertices'], dxfattribs={
            'layer': 'TALUDE_TOPO' if item['type'] == 'CREST' else 'TALUDE_BASE'})
    doc.saveas(destination / 'resultado.dxf')
    report = {'algorithm': 'TALUDE_AUTO_V0.2.3', 'experimental': True,
              'mdt': str(Path(mdt_path).resolve()), 'epsg': 3763,
              'crest_lines': sum(x['type'] == 'CREST' for x in results),
              'toe_lines': sum(x['type'] == 'TOE' for x in results),
              'review': review, 'duration_s': perf_counter()-start,
              'output_dir': str(destination)}
    (destination / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'lines': results, 'report': report, 'output_dir': str(destination)}
