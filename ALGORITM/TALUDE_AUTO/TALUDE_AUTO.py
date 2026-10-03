"""TALUDE AUTO V0.2.3 — ALGORITMO UNICO PARA QGIS 3.40.x.

Instalar apenas este TALUDE_AUTO.py como script de Processamento QGIS.
Entrada: MDT/DEM; resultados: GPKG, CSV e DXF 3D com layers
TALUDE_TOPO (crista) / TALUDE_BASE (pe), Z do MDT em cada vertice.
Motor geometrico V0.1.3.1 integrado e preservado neste mesmo ficheiro.
A geracao de contornos a partir do MDT continua EXPERIMENTAL.
EPSG:3763; nunca assume transformacao silenciosa dos dados de entrada.
"""
from __future__ import annotations

import csv
import os
from statistics import median

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QInputDialog, QMessageBox
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsFields, QgsGeometry,
    QgsPointXY, QgsProject, QgsRasterLayer, QgsVectorLayer, QgsVectorFileWriter,
)

VERSION = '0.2.3'
CORE_VERSION = '0.1.3.1'  # Motor aprovado, embebido neste script.
CRS = QgsCoordinateReferenceSystem('EPSG:3763')
MIN_Z_DIFFERENCE_M = 0.40
CREST_MIN_TOE_CLEARANCE_M = 1.00
CREST_MAX_END_TRIM_M = 2.00
CREST_TRIM_STEP_M = 0.10


def choose_vector(path):
    """Prefer the expected line layer if a GeoPackage has multiple tables."""
    from qgis.core import QgsProviderRegistry
    sublayers = QgsProviderRegistry.instance().querySublayers(path)
    if len(sublayers) > 1:
        candidates = [sub.name() for sub in sublayers]
        choice, ok = QInputDialog.getItem(None, 'Camada de contornos',
                                          'Escolher camada de contornos FECHADOS:',
                                          candidates, 0, False)
        if not ok:
            raise RuntimeError('Cancelado ao escolher camada')
        source = QgsVectorLayer(path + '|layername=' + choice, 'CONTORNOS_ENTRADA', 'ogr')
    else:
        source = QgsVectorLayer(path, 'CONTORNOS_ENTRADA', 'ogr')
    if not source.isValid():
        raise ValueError('Contornos invalidos: ' + path)
    if source.geometryType() != 1:
        raise ValueError('Os contornos de entrada devem ser do tipo linha (Polygon -> Lines)')
    authid = source.crs().authid().upper().strip()
    if authid and authid != 'EPSG:3763':
        raise ValueError('Entrada declara ' + authid + '; confirmar e reprojetar no QGIS')
    if authid != 'EPSG:3763':
        answer = QMessageBox.question(
            None, 'SRC das linhas nao identificado',
            'Confirmas que os valores X/Y das linhas JA estao em EPSG:3763?\n'
            'SIM atribui o SRC apenas em memoria, sem mover coordenadas.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            raise RuntimeError('SRC dos contornos nao confirmado')
        source.setCrs(CRS)
    return source


def choose_raster(path, source=None):
    raster = QgsRasterLayer(path, 'DEM')
    if not raster.isValid():
        raise ValueError('DEM invalido ou inacessivel: ' + path)
    authid = raster.crs().authid().upper().strip()
    if authid != 'EPSG:3763':
        wkt = raster.crs().toWkt().upper() if raster.crs().isValid() else ''
        desc = raster.crs().description().upper()
        other_epsg = authid.startswith('EPSG:') and authid != 'EPSG:3763'
        if other_epsg and not ('TM06' in wkt or 'TM06' in desc):
            raise ValueError('O DEM declara outro EPSG (' + authid + '). Nao sera alterado.')
        if source and not raster.extent().intersects(source.extent()):
            raise ValueError('DEM e contornos nao se sobrepoem nas coordenadas X/Y')
        decision = QMessageBox.question(
            None, 'Confirmar SRC personalizado do DEM',
            'O DEM nao declara literalmente EPSG:3763 (' + (authid or desc or 'indefinido') + ').\n'
            'Confirma que os valores X/Y JA correspondem a Portugal TM06?\n'
            'SIM apenas atribui EPSG:3763 a esta camada em memoria; NAO reprojeta o raster.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if decision != QMessageBox.Yes:
            raise RuntimeError('SRC do DEM nao confirmado')
        raster.setCrs(CRS)
    if source and not raster.extent().intersects(source.extent()):
        raise ValueError('O DEM nao intersecta os contornos')
    return raster


def dem_to_contours(raster_path, root, source_raster, feedback, context, threshold=35.0, min_area=2.0):
    """EXPERIMENTAL preprocess from DEM; retains each stage on disk.

    This single-input method has not yet been validated for all terrain.
    These parameters reproduce Script3's main structure but the threshold
    and min area are explicit and can differ from historical run settings.
    """
    import processing
    from qgis.analysis import QgsRasterCalculator, QgsRasterCalculatorEntry
    os.makedirs(root, exist_ok=True)
    feedback.pushInfo('DEM -> contornos: fase experimental, limite fixo 35 graus, area minima 2 m2.')
    feedback.pushInfo('O DEM original nao sera modificado; intermediarios escritos em disco.')
    def target(name):
        if feedback.isCanceled():
            raise RuntimeError('Processamento cancelado pelo utilizador')
        dest = os.path.join(root, name)
        if os.path.exists(dest):
            raise FileExistsError('Nao substituir resultado anterior: ' + dest)
        return dest
    slope = target('01_declive_graus.tif')
    feedback.pushInfo('1/13: slope em graus...')
    processing.run('gdal:slope', {
        'INPUT': raster_path, 'BAND': 1, 'SCALE': 1, 'AS_PERCENT': False,
        'COMPUTE_EDGES': True, 'ZEVENBERGEN': False, 'OUTPUT': slope,
    }, context=context, feedback=feedback, is_child_algorithm=True)
    slope_layer = QgsRasterLayer(slope, 'SLOPE_GRAUS')
    if not slope_layer.isValid():
        raise RuntimeError('O raster de declive nao foi criado corretamente')
    entry = QgsRasterCalculatorEntry()
    entry.ref = 'slope@1'
    entry.raster = slope_layer
    entry.bandNumber = 1
    binary = target('02_declive_binario.tif')
    feedback.pushInfo('2/13: raster binario...')
    calculator = QgsRasterCalculator(
        f'(slope@1 > {threshold}) * 1', binary, 'GTiff',
        slope_layer.extent(), slope_layer.width(), slope_layer.height(), [entry])
    if calculator.processCalculation() != 0:
        raise RuntimeError('Falha na calculadora raster')
    operations = [
        ('gdal:polygonize', '03_poligonos.gpkg', lambda p: {
            'INPUT': binary, 'BAND': 1, 'FIELD': 'value',
            'EIGHT_CONNECTEDNESS': False, 'EXTRA': '', 'OUTPUT': p,
        }),
        ('native:extractbyexpression', '04_apenas.gpkg', lambda p: {
            'INPUT': previous, 'EXPRESSION': '"value" = 1', 'OUTPUT': p,
        }),
        ('native:deleteholes', '05_sem_buracos.gpkg', lambda p: {
            'INPUT': previous, 'MIN_AREA': 0, 'OUTPUT': p,
        }),
        ('native:dissolve', '06_dissolvido.gpkg', lambda p: {
            'INPUT': previous, 'FIELD': [], 'SEPARATE_DISJOINT': False, 'OUTPUT': p,
        }),
        ('native:smoothgeometry', '07_suavizado.gpkg', lambda p: {
            'INPUT': previous, 'ITERATIONS': 3, 'OFFSET': 0.25,
            'MAX_ANGLE': 180, 'OUTPUT': p,
        }),
        ('native:multiparttosingleparts', '08_singleparts.gpkg', lambda p: {
            'INPUT': previous, 'OUTPUT': p,
        }),
        ('native:extractbyexpression', '09_area_min.gpkg', lambda p: {
            'INPUT': previous, 'EXPRESSION': f'area($geometry) >= {min_area}', 'OUTPUT': p,
        }),
        ('native:buffer', '10_buffer_pos.gpkg', lambda p: {
            'INPUT': previous, 'DISTANCE': 10, 'SEGMENTS': 500,
            'END_CAP_STYLE': 0, 'JOIN_STYLE': 0, 'MITER_LIMIT': 10,
            'DISSOLVE': False, 'SEPARATE_DISJOINT': False, 'OUTPUT': p,
        }),
        ('native:buffer', '11_buffer_neg.gpkg', lambda p: {
            'INPUT': previous, 'DISTANCE': -10, 'SEGMENTS': 500,
            'END_CAP_STYLE': 0, 'JOIN_STYLE': 0, 'MITER_LIMIT': 10,
            'DISSOLVE': False, 'SEPARATE_DISJOINT': False, 'OUTPUT': p,
        }),
        ('native:polygonstolines', '12_contornos.gpkg', lambda p: {
            'INPUT': previous, 'OUTPUT': p,
        }),
        ('native:addautoincrementalfield', '13_linhas_id.gpkg', lambda p: {
            'INPUT': previous, 'FIELD_NAME': 'row_num', 'START': 1,
            'GROUP_FIELDS': [], 'SORT_EXPRESSION': '', 'SORT_ASCENDING': True,
            'SORT_NULLS_FIRST': False, 'OUTPUT': p,
        }),
    ]
    previous = None
    for stage, (algorithm, filename, params) in enumerate(operations, 3):
        destination = target(filename)
        feedback.pushInfo(f'{stage}/13: {algorithm} -> {destination}')
        processing.run(algorithm, params(destination), context=context, feedback=feedback, is_child_algorithm=True)
        previous = destination
    return previous


def sample_median(raster, coords, count=21):
    geom = QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in coords])
    if geom.length() <= 0:
        return None
    vals = []
    for i in range(1, count+1):
        point = geom.interpolate(geom.length()*i/(count+1)).asPoint()
        val, ok = raster.dataProvider().sample(QgsPointXY(point), 1)
        if ok and val is not None and -1e5 < float(val) < 1e6:
            vals.append(float(val))
    return median(vals) if len(vals) >= count//2 else None


def safe_crest_endpoint_trim(core, crest_coords, toe_coords):
    """Unmodified V0.1.4 decision rule; cuts are made by the validated core."""
    crest_geom = QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in crest_coords])
    toe_geom = QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in toe_coords])
    length = crest_geom.length()
    retreats = [0.0, 0.0]
    notices = []
    for index, side in enumerate(('inicio', 'fim')):
        endpoint_distance = 0.0 if index == 0 else length
        point = crest_geom.interpolate(endpoint_distance)
        separation = point.distance(toe_geom)
        if separation >= CREST_MIN_TOE_CLEARANCE_M:
            continue
        found = None
        count = int(CREST_MAX_END_TRIM_M / CREST_TRIM_STEP_M)
        for step in range(1, count+1):
            retreat = step * CREST_TRIM_STEP_M
            if 2*retreat >= length-1.0:
                break
            distance_along = retreat if index == 0 else length-retreat
            trial = crest_geom.interpolate(distance_along).distance(toe_geom)
            if trial >= CREST_MIN_TOE_CLEARANCE_M:
                found = round(retreat, 3)
                break
        if found is None:
            notices.append(side + ': proximidade ao pe sem solucao no limite; rever')
        else:
            retreats[index] = found
            notices.append(side + ': recuo automatico ' + str(found) + ' m')
    if sum(retreats) > 0:
        crest_coords = core.cut_open_polyline(crest_coords, *retreats)
    revised = QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in crest_coords])
    if revised.length() < 1.0 or revised.isEmpty():
        raise ValueError('Protecao de extremos: crista degenerada')
    return crest_coords, retreats[0], retreats[1], '; '.join(notices)


def fields():
    result = QgsFields()
    for name, kind in [
        ('row_num', QVariant.LongLong), ('tipo', QVariant.String),
        ('estado', QVariant.String), ('comp_m', QVariant.Double),
        ('z_med', QVariant.Double), ('observacao', QVariant.String),
        ('rec_ini_m', QVariant.Double), ('rec_fim_m', QVariant.Double),
    ]:
        result.append(QgsField(name, kind))
    return result


class LineWriter:
    def __init__(self, folder, layer_name, schema):
        self.layer_name = layer_name
        self.path = os.path.join(folder, layer_name + '.gpkg')
        if os.path.exists(self.path):
            raise FileExistsError('Nao substituir ficheiros anteriores: ' + self.path)
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = 'GPKG'
        opts.layerName = layer_name
        opts.fileEncoding = 'UTF-8'
        self.writer = QgsVectorFileWriter.create(
            self.path, schema, QgsVectorLayer('LineString?crs=EPSG:3763','schema','memory').wkbType(),
            CRS, QgsProject.instance().transformContext(), opts)
        if self.writer.hasError() != QgsVectorFileWriter.NoError:
            raise RuntimeError('Nao foi possivel criar ' + self.path + ': ' + self.writer.errorMessage())
        self.schema = schema
        self.count = 0

    def add(self, coordinates, rid, role, status, z=None, note='', start=0.0, end=0.0):
        if len(coordinates) < 2:
            return
        geom = QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in coordinates])
        if geom.isEmpty() or geom.length() == 0:
            return
        feature = QgsFeature(self.schema)
        feature.setGeometry(geom)
        feature.setAttributes([rid, role, status, geom.length(), z, note[:1024], start, end])
        if not self.writer.addFeature(feature):
            raise RuntimeError('Falha de escrita em ' + self.path + ': ' + self.writer.errorMessage())
        self.count += 1

    def close(self):
        if self.writer is not None:
            del self.writer
            self.writer = None


def line_parts(g):
    if g.isMultipart():
        return [[(pt.x(), pt.y()) for pt in line] for line in g.asMultiPolyline()]
    return [[(pt.x(), pt.y()) for pt in g.asPolyline()]]


def batch_process(core, source, raster, folder, limit, feedback):
    names = ('MARGENS_CANDIDATAS', 'FECHOS_DETETADOS', 'REVISAO_NECESSARIA')
    schema = fields()
    writers = [LineWriter(folder, name, schema) for name in names]
    margin_writer, cap_writer, review_writer = writers
    report_path = os.path.join(folder, 'RELATORIO_LOTE.csv')
    counters = dict(processed=0, candidate=0, review=0, ignored_short=0,
                    inconclusive=0, end_trimmed=0, failed=0)
    total = min(source.featureCount(), limit) if limit else source.featureCount()
    feedback.pushInfo('A processar {} contornos (escrita incremental).'.format(total))
    cancelled = False
    try:
        with open(report_path, 'w', encoding='utf-8-sig', newline='') as file:
            csv_writer = csv.writer(file, delimiter=';')
            csv_writer.writerow(['row_num','estado','motivo','perimetro_m','z_margem_a','z_margem_b',
                                 'tipo_a','tipo_b','recuo_inicio_m','recuo_fim_m'])
            has_row = source.fields().indexFromName('row_num') >= 0
            for idx, feat in enumerate(source.getFeatures()):
                if limit and idx >= limit:
                    break
                rid = feat['row_num'] if has_row and feat['row_num'] is not None else idx+1
                count_note = ''
                try:
                    parts = line_parts(feat.geometry())
                    if len(parts) != 1:
                        raise ValueError('Multipart: rever contorno antes de processar')
                    coords = parts[0]
                    if len(coords) < 4:
                        counters['ignored_short'] += 1
                        csv_writer.writerow([rid,'IGNORADO_CURTO','<4 vertices','','','','','','',''])
                        continue
                    perimeter = QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in coords]).length()
                    if perimeter < 20.0:
                        counters['ignored_short'] += 1
                        csv_writer.writerow([rid,'IGNORADO_CURTO','perimetro <20 m',round(perimeter,3),'','','','','',''])
                        continue
                    ring = core.Ring(coords)
                    result = core.detect(ring)
                    if result.status != 'CANDIDATE':
                        raise ValueError(result.reason)
                    a = ring.section(*result.margins[0]); b = ring.section(*result.margins[1])
                    za = sample_median(raster, a); zb = sample_median(raster, b)
                    roles = ['MARGEM_A','MARGEM_B']
                    state = 'COTAS_INCONCLUSIVAS'
                    if za is not None and zb is not None and abs(za-zb) >= MIN_Z_DIFFERENCE_M:
                        roles = ['TTALUDE','BTAL'] if za > zb else ['BTAL','TTALUDE']
                        state = 'CANDIDATO_VALIDAR'
                    else:
                        counters['inconclusive'] += 1
                    trim_start = trim_end = 0.0
                    trim_note = ''
                    if 'TTALUDE' in roles:
                        crest_is_a = roles[0] == 'TTALUDE'
                        crest, toe = (a,b) if crest_is_a else (b,a)
                        crest, trim_start, trim_end, trim_note = safe_crest_endpoint_trim(core,crest,toe)
                        if crest_is_a:
                            a = crest
                        else:
                            b = crest
                        if trim_start > 0 or trim_end > 0:
                            counters['end_trimmed'] += 1
                        if 'rever' in trim_note:
                            state = 'CANDIDATO_REVER_EXTREMOS'
                    note = 'Detecao automatica experimental; validar fechos'
                    if trim_note:
                        note += '; ' + trim_note
                    margin_writer.add(a,rid,roles[0],state,za,note,
                                      trim_start if roles[0]=='TTALUDE' else 0.0,
                                      trim_end if roles[0]=='TTALUDE' else 0.0)
                    margin_writer.add(b,rid,roles[1],state,zb,note,
                                      trim_start if roles[1]=='TTALUDE' else 0.0,
                                      trim_end if roles[1]=='TTALUDE' else 0.0)
                    for cap_num, interval in enumerate(result.caps, 1):
                        cap_writer.add(ring.section(*interval),rid,'FECHO_'+str(cap_num),
                                       state,note='Fecho separado; sem snap entre classes')
                    counters['candidate'] += 1
                    csv_writer.writerow([rid,state,trim_note,round(perimeter,3),za,zb,roles[0],roles[1],trim_start,trim_end])
                except Exception as err:
                    counters['review'] += 1
                    counters['failed'] += 1
                    try:
                        for part in line_parts(feat.geometry()):
                            review_writer.add(part,rid,'REVISAO','REVIEW',note=str(err)[:240])
                    except Exception:
                        pass
                    csv_writer.writerow([rid,'REVISAO',str(err)[:240],'','','','','','',''])
                finally:
                    counters['processed'] += 1
                    if (idx+1) % 100 == 0:
                        feedback.setProgress(100.0 * (idx+1) / max(total, 1))
                        file.flush()
                        if feedback.isCanceled():
                            cancelled = True
                            break
                    if (idx+1) % 500 == 0:
                        feedback.pushInfo('Processados: {} | candidatas: {} | revisao: {} | curtos: {}'.format(idx+1,counters['candidate'],counters['review'],counters['ignored_short']))
    finally:
        feedback.setProgress(100.0 * min(counters['processed'],total)/max(total,1))
        for writer in writers:
            writer.close()
        for writer in writers:
            layer = QgsVectorLayer(writer.path,writer.layer_name,'ogr')
            if layer.isValid() and layer.featureCount():
                QgsProject.instance().addMapLayer(layer)
    return counters, cancelled, report_path



# ==================== CORE GEOMETRICO ORIGINAL ====================
"""Automatic candidate splitting of narrow closed terrace contours.
Pure Python, no dependency on QGIS. All distance parameters are metres (EPSG:3763).
Experimental heuristic; ambiguous geometries must be reviewed, never silently classified.
"""

CORE_VERSION = '0.1.3.1'

from dataclasses import dataclass
from math import acos, hypot, degrees
from bisect import bisect_right

@dataclass(frozen=True)
class SplitResult:
    status: str
    reason: str
    caps: tuple[tuple[float,float], ...]
    margins: tuple[tuple[float,float], ...]
    candidates: tuple[float,...]
    scores: tuple[float,...]
    perimeter: float

class Ring:
    def __init__(self, points):
        pts=[(float(x),float(y)) for x,y,*_ in points]
        if len(pts)<4:raise ValueError('A closed ring needs at least 4 points')
        if hypot(pts[0][0]-pts[-1][0],pts[0][1]-pts[-1][1])>1e-5:
            raise ValueError('The line is not closed; no automatic split attempted')
        self.points=pts
        self.cumulative=[0.0]
        for a,b in zip(pts,pts[1:]):self.cumulative.append(self.cumulative[-1]+hypot(b[0]-a[0],b[1]-a[1]))
        self.length=self.cumulative[-1]
        if self.length<20:raise ValueError('Contour perimeter is below 20 m')

    def at(self,d):
        d=d%self.length
        i=max(0,min(len(self.points)-2,bisect_right(self.cumulative,d)-1))
        lo,hi=self.cumulative[i],self.cumulative[i+1]
        t=(d-lo)/(hi-lo) if hi>lo else 0
        a,b=self.points[i],self.points[i+1]
        return (a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1]))

    def section(self,a,b):
        """Return part of ring between increasing unwrapped distances a,b with exact original vertices."""
        L=self.length
        assert b>a and b-a<L
        k0=int(a//L);k1=int(b//L)
        out=[self.at(a)]
        for k in range(k0,k1+1):
            for j,d in enumerate(self.cumulative[1:-1],1):
                dd=d+k*L
                if a+1e-8<dd<b-1e-8:
                    p=self.points[j]
                    if p!=out[-1]:out.append(p)
        end=self.at(b)
        if hypot(out[-1][0]-end[0],out[-1][1]-end[1])>1e-8:out.append(end)
        return out

def turn_angle(r,d,w=1.0):
    pa=r.at(d-w);p=r.at(d);pb=r.at(d+w)
    u=(p[0]-pa[0],p[1]-pa[1]);v=(pb[0]-p[0],pb[1]-p[1]);nu=hypot(*u);nv=hypot(*v)
    if nu<1e-9 or nv<1e-9:return 0.0
    return degrees(acos(max(-1,min(1,(u[0]*v[0]+u[1]*v[1])/(nu*nv)))))

def detect(r,step=0.5,threshold_degrees=28.0,cluster_gap_m=13.0,cap_pad_m=0.5):
    """Detect 2 endcap clusters by high local turning angle, far apart on ring.
    Returns candidate margins and caps in contour arclength (not named crest/toe).
    """
    L=r.length
    n=max(24,int(L/step));dx=L/n
    events=[]
    for i in range(n):
        d=i*dx; t=turn_angle(r,d,1.0)
        if t>=threshold_degrees:events.append((d,t))
    if not events:return SplitResult('REVIEW','No strong bends detected',(),(),(),(),L)
    # cluster close corner events, accounting for circular wrap
    groups=[]
    for d,t in events:
        if groups and d-groups[-1][-1][0] <= cluster_gap_m:groups[-1].append((d,t))
        else:groups.append([(d,t)])
    if len(groups)>1 and (groups[0][0][0]+L-groups[-1][-1][0])<=cluster_gap_m:
        first=groups.pop(0);groups[-1].extend([(d+L,t) for d,t in first])
    scored=[]
    for group in groups:
        center=max(group,key=lambda x:x[1])[0]
        start=min(d for d,t in group)-cap_pad_m
        end=max(d for d,t in group)+cap_pad_m
        score=sum(t for d,t in group)
        scored.append((score,center,start,end))
    scored.sort(reverse=True)
    if len(scored)<2:return SplitResult('REVIEW','Only one distinct endcap cluster',(),(),tuple(x[1]%L for x in scored),tuple(x[0] for x in scored),L)
    # Select pair with strong bending, separated around contour (not adjacent corners).
    candidates=[]
    for i,a in enumerate(scored):
        for b in scored[i+1:]:
            sep=abs(a[1]-b[1])%L;sep=min(sep,L-sep)
            if sep>=0.20*L:candidates.append((a[0]+b[0],a,b))
    if not candidates:return SplitResult('REVIEW','No two well-separated bends',(),(),tuple(x[1]%L for x in scored[:5]),tuple(x[0] for x in scored[:5]),L)
    _,a,b=max(candidates,key=lambda x:x[0])
    aa=sorted([a,b],key=lambda x:x[1]%L)
    a,b=aa
    # unwrap each interval around chosen turning centers
    ca=a[1]%L;cb=b[1]%L
    def unwrap(rec,center):
        _,c,s,e=rec
        while s-center < -L/2:s+=L;e+=L
        while s-center > L/2:s-=L;e-=L
        return s,e
    as_,ae=unwrap(a,ca);bs,be=unwrap(b,cb)
    if as_<0:as_+=L;ae+=L
    if bs<=ae:bs+=L;be+=L
    cap_a=(as_,ae);cap_b=(bs,be)
    margin_a=(ae,bs);margin_b=(be,as_+L if as_+L>be else as_+2*L)
    intervals=[cap_a,cap_b,margin_a,margin_b]
    if min(v-u for u,v in intervals)<0.75 or sum(v-u for u,v in [margin_a,margin_b])<0.70*L:
        return SplitResult('REVIEW','Candidate cuts too short or caps too extensive',(),(),(ca,cb),(a[0],b[0]),L)
    return SplitResult('CANDIDATE','Two opposing bends detected (manual geometry validation required)',(cap_a,cap_b),(margin_a,margin_b),(ca,cb),(a[0],b[0]),L)


def cut_open_polyline(coords, start_trim_m=0.0, end_trim_m=0.0):
    """Cut measured metres from the two ends without moving remaining vertices.

    Keeps exact original interior vertices; creates at most two interpolated
    endpoint coordinates. Unlike removing one vertex, works with dense data.
    """
    pts = [(float(x), float(y)) for x, y, *_ in coords]
    if len(pts) < 2:
        raise ValueError('Linha com menos de dois pontos')
    cum = [0.0]
    for p, q in zip(pts, pts[1:]):
        cum.append(cum[-1] + hypot(q[0]-p[0], q[1]-p[1]))
    total = cum[-1]
    begin, finish = max(0.0, start_trim_m), total-max(0.0, end_trim_m)
    if total <= 0 or finish-begin < 1.0:
        raise ValueError('Recuo deixa menos de um metro de crista')

    def point_at(distance):
        j = max(0, min(len(pts)-2, bisect_right(cum, distance)-1))
        span = cum[j+1]-cum[j]
        ratio = (distance-cum[j])/span if span > 0 else 0.0
        a, b = pts[j], pts[j+1]
        return (a[0]+ratio*(b[0]-a[0]), a[1]+ratio*(b[1]-a[1]))

    out = [point_at(begin)]
    for distance, pt in zip(cum[1:-1], pts[1:-1]):
        if begin+1e-8 < distance < finish-1e-8 and hypot(
            out[-1][0]-pt[0], out[-1][1]-pt[1]
        ) > 1e-9:
            out.append(pt)
    end = point_at(finish)
    if hypot(out[-1][0]-end[0], out[-1][1]-end[1]) > 1e-9:
        out.append(end)
    return out


# ==================== INTERFACE QGIS PROCESSING ====================
from types import SimpleNamespace
from datetime import datetime
from qgis.core import (QgsProcessingAlgorithm, QgsProcessingParameterRasterLayer,
                       QgsProcessingException)


def local_core_embedded():
    return SimpleNamespace(CORE_VERSION=CORE_VERSION, Ring=Ring,
                           detect=detect, cut_open_polyline=cut_open_polyline)


# ==================== EXPORTADOR DXF 3D INCORPORADO ====================
"""ASCII DXF R12 export of 3D POLYLINEs, no optional CAD package required.

X/Y coordinates are *already* ETRS89 / Portugal TM06 EPSG:3763 in metres.
The DXF R12 format cannot reliably embed EPSG metadata, so the companion
.PRJ and explanatory README must travel with the DXF.
"""

import csv
import math
import os
from pathlib import Path

LAYER_NAME = {'TTALUDE': 'TALUDE_TOPO', 'BTAL': 'TALUDE_BASE'}


class Dxf3dWriter:
    """Incremental legacy-compatible 3D polyline writer (flag 70=8)."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.tmp_path = Path(str(self.path) + '.part')
        if self.path.exists():
            raise FileExistsError('O DXF de destino ja existe: ' + str(self.path))
        self.stream = open(self.tmp_path, 'w', encoding='ascii', newline='\n')
        self._write_header()
        self.counts = {layer: 0 for layer in LAYER_NAME.values()}
        self.finished = False

    def pair(self, code, value):
        self.stream.write(f'{code}\n{value}\n')

    def _write_header(self):
        self.pair(0, 'SECTION'); self.pair(2, 'HEADER')
        self.pair(9, '$ACADVER'); self.pair(1, 'AC1009')
        self.pair(9, '$LUNITS'); self.pair(70, 2)  # Decimal display
        self.pair(0, 'ENDSEC')
        self.pair(0, 'SECTION'); self.pair(2, 'TABLES')
        self.pair(0, 'TABLE'); self.pair(2, 'LAYER'); self.pair(70, 2)
        for name, color in (('TALUDE_TOPO', 1), ('TALUDE_BASE', 3)):
            self.pair(0, 'LAYER'); self.pair(2, name)
            self.pair(70, 0); self.pair(62, color); self.pair(6, 'CONTINUOUS')
        self.pair(0, 'ENDTAB'); self.pair(0, 'ENDSEC')
        self.pair(0, 'SECTION'); self.pair(2, 'ENTITIES')

    def add_polyline(self, layer, xyz):
        if layer not in self.counts:
            raise ValueError('Layer CAD nao permitida: ' + str(layer))
        if len(xyz) < 2:
            raise ValueError('Uma 3D polyline exige >=2 vertices')
        if not all(len(p) == 3 and all(math.isfinite(float(v)) for v in p) for p in xyz):
            raise ValueError('XYZ com cota inexistente ou invalida')
        # POLYLINE bit 8 = 3D polyline; VERTEX bit 32 = 3D vertex.
        self.pair(0, 'POLYLINE'); self.pair(8, layer)
        self.pair(66, 1); self.pair(70, 8)
        self.pair(10, 0.0); self.pair(20, 0.0); self.pair(30, 0.0)
        for x, y, z in xyz:
            self.pair(0, 'VERTEX'); self.pair(8, layer)
            self.pair(10, f'{float(x):.8f}')
            self.pair(20, f'{float(y):.8f}')
            self.pair(30, f'{float(z):.5f}')
            self.pair(70, 32)
        self.pair(0, 'SEQEND'); self.pair(8, layer)
        self.counts[layer] += 1

    def finish(self):
        if self.finished:
            return
        self.pair(0, 'ENDSEC'); self.pair(0, 'EOF')
        self.stream.close()
        os.replace(self.tmp_path, self.path)
        self.finished = True

    def abort(self):
        if not self.stream.closed:
            self.stream.close()
        if self.tmp_path.exists():
            self.tmp_path.unlink()


def export_from_qgis(margins_path, dem, destination, feedback=None):
    """Use QGIS DEM sampling for Z at each original vertex; skip incomplete lines.

    Returns metadata, creates RELATORIO_DXF_3D.csv and source .PRJ. Does not
    interpolate/make up missing Z or alter the original 2D GPKG/DEM.
    """
    from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY, QgsVectorLayer
    from qgis.PyQt.QtCore import QCoreApplication

    if not dem.isValid():
        raise ValueError('MDT/DEM invalido no momento da exportacao')
    layer = QgsVectorLayer(str(margins_path), 'MARGENS_PARA_DXF', 'ogr')
    if not layer.isValid() or layer.geometryType() != 1:
        raise ValueError('MARGENS_CANDIDATAS.gpkg invalido ou nao e uma camada de linhas')
    if layer.crs().authid().upper() != 'EPSG:3763':
        raise ValueError('O GeoPackage das margens tem de estar definido como EPSG:3763')
    dst = Path(destination)
    if dst.exists():
        raise FileExistsError('O DXF ja existe: ' + str(dst))
    source_crs = QgsCoordinateReferenceSystem('EPSG:3763')
    csv_path = dst.with_name('RELATORIO_DXF_3D.csv')
    if csv_path.exists():
        raise FileExistsError('Relatorio DXF ja existe: ' + str(csv_path))
    provider = dem.dataProvider()
    writer = Dxf3dWriter(dst)
    cache = {}
    statuses = []
    skipped = 0
    listed = 0
    count_warn = 0
    try:
        for i, feat in enumerate(layer.getFeatures()):
            if feedback is not None and feedback.isCanceled():
                writer.abort()
                raise RuntimeError('Exportacao DXF cancelada; nao foi gravado um ficheiro parcial')
            role = str(feat['tipo'])
            rid = str(feat['row_num'])
            state = str(feat['estado'])
            if role not in LAYER_NAME:
                statuses.append((rid, role, state, 'NAO_CLASSIFICADO', 'Margem sem classe TTALUDE/BTAL', 0))
                skipped += 1
                continue
            geom = feat.geometry()
            if geom.isEmpty() or geom.isMultipart():
                statuses.append((rid, role, state, 'OMITIDO', 'Geometria vazia/multipart', 0))
                skipped += 1
                continue
            points = geom.asPolyline()
            if len(points) < 2:
                statuses.append((rid, role, state, 'OMITIDO', 'Menos de dois vertices', 0))
                skipped += 1
                continue
            xyz = []
            missing = 0
            for p in points:
                xy = (float(p.x()), float(p.y()))
                if xy not in cache:
                    z, ok = provider.sample(QgsPointXY(*xy), 1)
                    cache[xy] = float(z) if ok and z is not None and math.isfinite(float(z)) and -1e5 < float(z) < 1e6 else None
                z = cache[xy]
                if z is None:
                    missing += 1
                else:
                    xyz.append((xy[0], xy[1], z))
            if missing:
                skipped += 1
                statuses.append((rid, role, state, 'OMITIDO_SEM_Z', f'{missing}/{len(points)} vertices sem cota; linha inteira omitida', len(points)))
                continue
            try:
                writer.add_polyline(LAYER_NAME[role], xyz)
            except ValueError as exc:
                skipped += 1
                statuses.append((rid, role, state, 'OMITIDO', str(exc), len(points)))
                continue
            listed += 1
            if 'REVER' in state:
                count_warn += 1
            statuses.append((rid, role, state, 'DXF_EXPORTADO', 'Verificar estado original no relatorio de lote', len(points)))
            if feedback is not None and i % 25 == 0:
                feedback.pushInfo(f'DXF 3D: {i+1} margens examinadas, {listed} exportadas, {skipped} omitidas')
                QCoreApplication.processEvents()  # Responsive during DXF stage, not heavy preprocessing
        if listed == 0:
            raise ValueError('Nenhuma linha exportavel com Z valido em todos os vertices; verifique o DEM.')
        # CSV before finalizing DXF; if CSV fails, do not leave a supposedly complete DXF.
        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as report:
            cw = csv.writer(report, delimiter=';')
            cw.writerow(['row_num','tipo_origem','estado_origem','situacao_exportacao','detalhe','n_vertices'])
            cw.writerows(statuses)
        writer.finish()
    except Exception:
        writer.abort()
        raise
    prj = dst.with_suffix('.prj')
    prj.write_text(source_crs.toWkt(), encoding='utf-8')
    note = dst.with_name('LEIA_DXF_3D.txt')
    note.write_text(
        'TALUDE AUTO - DXF 3D em metros / ETRS89 Portugal TM06 EPSG:3763\n'
        'Geometrias CAD: 3D POLYLINE (nao 2D LWPOLYLINE), Z de cada vertice amostrado do MDT.\n'
        'Layers: TALUDE_TOPO (TTALUDE), TALUDE_BASE (BTAL). Sem snap entre ambas.\n'
        'DXF R12 nao garante metadados EPSG: associar o ficheiro .PRJ no software de destino.\n'
        'Fechos e margens sem classe nao sao exportados. Elementos assinalados para revisao, se classificados, podem constar do DXF: ver CSV.\n'
        'Uma linha com qualquer vertice sem cota e integralmente omitida (nunca usa Z=0).\n', encoding='utf-8')
    return dict(dxf=str(dst), report=str(csv_path), counted=writer.counts,
                omitted=skipped, flagged_review=count_warn, unique_vertices_sampled=len(cache))


class TaludeAutoMDTAlgorithm(QgsProcessingAlgorithm):
    """Executa o processo MDT completo com um unico parametro visivel."""
    DEM = 'DEM'

    def name(self):
        return 'talude_auto_mdt_v023'

    def displayName(self):
        return 'TALUDE AUTO - MDT para DXF 3D (V0.2.3)'

    def group(self):
        return 'Taludes'

    def groupId(self):
        return 'taludes'

    def shortHelpString(self):
        return (
            'Selecionar apenas o MDT/DEM. Gera contornos por declive >35 graus, '
            'filtra area minima 2 m2, aplica buffers +10/-10, separa as margens, '
            'classifica TTALUDE e BTAL pelas cotas, gera fechos/relatorio e DXF 3D. '
            'Saida automatica numa pasta nova junto do MDT. EPSG:3763. '
            'A geracao dos contornos a partir do MDT e ainda EXPERIMENTAL; '
            'o motor de separacao e o da V0.1.4 validada visualmente. '
            'Nao se faz snap crista-pe. Nao modifica o MDT original.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, 'MDT / DEM - ETRS89 Portugal TM06 (EPSG:3763)'))

    def processAlgorithm(self, parameters, context, feedback):
        dem = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem is None or not dem.isValid():
            raise QgsProcessingException('MDT/DEM invalido.')
        source_path = dem.source().split('|')[0]
        if not os.path.isfile(source_path):
            raise QgsProcessingException('Esta versao exige MDT/DEM guardado num ficheiro local ou caminho de rede acessivel.')
        try:
            # choose_raster validates the real metadata and asks for explicit confirmation
            # if a customized WKT resembles EPSG:3763. It never reprojects pixels.
            raster = choose_raster(source_path)
            if feedback.isCanceled():
                return {}
            root = os.path.dirname(os.path.abspath(source_path))
            tag = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            destination = os.path.join(root, 'TALUDE_AUTO_MDT_' + tag)
            os.makedirs(destination, exist_ok=False)
            feedback.pushInfo('Saida: ' + destination)
            feedback.pushWarning('Pre-processamento apenas MDT ainda em validacao. Pode ocupar varios GB.')
            contours_file = dem_to_contours(
                source_path, os.path.join(destination, '00_PREPROCESSAMENTO_DEM'),
                raster, feedback, context, 35.0, 2.0)
            if feedback.isCanceled():
                feedback.pushWarning('Cancelado; dados ja gravados em: ' + destination)
                return {'PASTA_RESULTADOS': destination}
            source = QgsVectorLayer(contours_file, 'CONTORNOS_AUTO', 'ogr')
            if not source.isValid() or source.geometryType() != 1:
                raise QgsProcessingException('Camada de contornos nao foi gerada como linhas.')
            if not raster.extent().intersects(source.extent()):
                raise QgsProcessingException('MDT e contornos nao se sobrepoem.')
            # Output from the source raster has the same XY values. The source
            # projection has already been explicitly checked above.
            if source.crs().authid() != 'EPSG:3763':
                source.setCrs(CRS)
            core = local_core_embedded()
            report, cancelled, csv_path = batch_process(core, source, raster, destination, 0, feedback)
            feedback.pushInfo('Resumo lote: ' + repr(report))
            feedback.pushInfo('Relatorio: ' + csv_path)
            if cancelled or feedback.isCanceled():
                feedback.pushWarning('Interrompido; resultados parciais preservados. DXF nao sera criado.')
                return {'PASTA_RESULTADOS': destination, 'RELATORIO_CSV': csv_path}
            feedback.pushInfo('A exportar DXF 3D: TALUDE_TOPO / TALUDE_BASE; Z de cada vertice do MDT...')
            dxf_path = os.path.join(destination, 'TALUDES_3D_ETRS3763.dxf')
            dxf_result = export_from_qgis(
                os.path.join(destination, 'MARGENS_CANDIDATAS.gpkg'),
                raster, dxf_path, feedback)
            feedback.pushInfo('DXF 3D concluido: ' + dxf_result['dxf'])
            feedback.pushInfo('Contagens CAD: ' + repr(dxf_result['counted']))
            feedback.pushInfo('Nao exportadas: ' + str(dxf_result['omitted']))
            feedback.pushInfo('Classificadas mas com aviso de revisao: ' + str(dxf_result['flagged_review']))
            return {'PASTA_RESULTADOS': destination, 'RELATORIO_CSV': csv_path,
                    'DXF_3D': dxf_result['dxf'], 'RELATORIO_DXF': dxf_result['report']}
        except Exception as exc:
            feedback.reportError('TALUDE AUTO: ' + str(exc))
            raise QgsProcessingException(str(exc)) from exc

    def createInstance(self):
        return TaludeAutoMDTAlgorithm()
