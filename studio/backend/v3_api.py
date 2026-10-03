"""Independent V3 processing HTTP router, preserving all V2 routes."""
from __future__ import annotations

import json
import os
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .converter import jobs, start_import
from .v3_algorithms import algorithm_root, available_algorithms, load_classifier
from .v3_mdt import generate_mdt
from .v3_talude_auto import execute_talude_auto


class ProjectAction(BaseModel):
    project_id: str


def _time_stamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')


def make_router(store):
    router = APIRouter(prefix='/api/v3')

    def _run_worker(project_id, title, operation):
        project = store.get(project_id)  # validate project before creating job
        job_id = jobs.create(title)
        log = project.path / 'logs' / f'v3_{job_id}.log'

        def work():
            jobs.update(job_id, status='running', progress=2, message=title)
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('w', encoding='utf-8') as out:
                out.write(f'[{_time_stamp()}] {title}\n')
                out.write(f'ALGORITM={algorithm_root()}\n')
                out.write(f'AVAILABLE={json.dumps(available_algorithms(), ensure_ascii=False)}\n')
                try:
                    result = operation(project_id, job_id, log)
                    if jobs.is_cancel_requested(job_id):
                        jobs.update(job_id, status='cancelled', message='Cancelado; resultado não publicado.', progress=0)
                        out.write('CANCELLED\n')
                        return
                    jobs.update(job_id, status='completed', progress=100, message=title + ' concluído', result=result)
                    out.write('COMPLETED: ' + json.dumps(result, ensure_ascii=False, default=str) + '\n')
                except Exception as exc:
                    tb = traceback.format_exc()
                    out.write('FAILED: ' + tb + '\n')
                    jobs.update(job_id, status='failed', message='Etapa falhou. Consultar log ' + str(log), error=f'{type(exc).__name__}: {exc}', result={'log': str(log)})
        threading.Thread(target=work, name='talude-v3-' + job_id[:8], daemon=True).start()
        return {'job_id': job_id, 'log': str(log)}

    @router.get('/status/{project_id}')
    def status(project_id: str):
        manifest = store.manifest(project_id)
        classified = manifest.get('v3_classified') or {}
        terrain = (manifest.get('terrain') or {}).get('mdt') or {}
        return {'algorithms': available_algorithms(), 'classified': classified,
                'mdt': terrain, 'has_classified': Path(classified.get('path', '')).is_file() if classified.get('path') else False,
                'has_mdt': Path(terrain.get('path', '')).is_file() if terrain.get('path') else False}

    def do_classify(project_id, job_id, log):
        import laspy
        manifest = store.manifest(project_id)
        clouds = manifest.get('clouds', [])
        # Never select an already-classified file as the new original input.
        original = next((cloud for cloud in clouds if Path(cloud.get('source_path', '')).is_file() and not cloud.get('v3_classified')), None)
        if original is None:
            raise ValueError('Importar primeiro uma nuvem LAS/LAZ original.')
        source = Path(original['source_path']).resolve()
        with laspy.open(source) as reader:
            crs = reader.header.parse_crs()
            if crs is None or crs.to_epsg() != 3763:
                raise ValueError('A nuvem de entrada tem de declarar EPSG:3763.')
            count = reader.header.point_count
        module = load_classifier()
        if count > 25_000_000 and not callable(getattr(module, 'classify_file_streamed', None)):
            raise RuntimeError('Classificador Standalone V1 usa XYZ em RAM; nuvem com mais de 25 milhões de pontos. É necessária uma atualização compatível com classify_file_streamed antes de executar a Soalheira inteira. A nuvem original não foi alterada.')
        project = store.get(project_id)
        filename = f'{_time_stamp()}_classified.las'
        dst = project.path / 'classified' / filename
        dst.parent.mkdir(parents=True, exist_ok=True)
        partial = dst.with_name(dst.stem + '.partial.las')
        jobs.update(job_id, progress=8, message='CLASSIFY V1 · a classificar nuvem original (sem LAS-CAFIISICA)')
        try:
            if count > 25_000_000:
                statistics = module.classify_file_streamed(source, partial, overwrite=False)
            else:
                statistics = module.classify_file(source, partial, overwrite=False)
            if jobs.is_cancel_requested(job_id):
                return {'cancelled': True}
            with laspy.open(partial) as reader:
                if reader.header.point_count != count or reader.header.parse_crs().to_epsg() != 3763:
                    raise ValueError('Classificação não preservou a quantidade de pontos ou o CRS.')
            if statistics.get('ground_points', 0) < 1:
                raise ValueError('Classificador não obteve Ground; ficheiro não será publicado.')
            os.replace(partial, dst)
        finally:
            partial.unlink(missing_ok=True)
        # Provenance is committed only after successful validation of the whole LAS.
        manifest = store.manifest(project_id)
        manifest['v3_classified'] = {'path': str(dst), 'algorithm': module.ALGORITHM_ID,
                                      'api_version': module.API_VERSION, 'source': str(source),
                                      'points': count, 'statistics': statistics, 'original_cloud_id': original['id']}
        manifest.setdefault('v3_runs', []).append({'operation': 'CLASSIFY', 'job_id': job_id, 'path': str(dst)})
        # Old MDT does not apply to a new classification.
        manifest.setdefault('terrain', {}).pop('mdt', None)
        store.save_manifest(project_id, manifest)
        jobs.update(job_id, progress=91, message='Classificação validada; a preparar visualização Potree…')
        import_id = start_import(store, project_id, str(dst))
        return {'classified': manifest['v3_classified'], 'import_job_id': import_id,
                'output': str(dst), 'next': 'Aguardar importação Potree antes da deteção TALUDE STUDIO.'}

    @router.post('/classify')
    def classify(req: ProjectAction):
        return _run_worker(req.project_id, 'CLASSIFICAR NUVEM', do_classify)

    def _get_classification(project_id):
        manifest = store.manifest(project_id)
        classified = manifest.get('v3_classified') or {}
        path = Path(classified.get('path', ''))
        if not classified.get('path') or not path.is_file():
            raise ValueError('É necessário classificar primeiro a nuvem.')
        return manifest, classified, path

    def do_mdt(project_id, job_id, log):
        _, classified, source = _get_classification(project_id)
        output = store.get(project_id).path / 'terrain' / f'{_time_stamp()}_MDT.tif'
        jobs.update(job_id, progress=8, message='GERAR MDT · apenas classe 2 Ground; NoData nas lacunas')
        info = generate_mdt(source, output)
        if jobs.is_cancel_requested(job_id):
            output.unlink(missing_ok=True)
            return {'cancelled': True}
        store.register_terrain_raster(project_id, 'mdt', output, {'crs': 'EPSG:3763',
            'resolution': info['resolution_m'], 'width': info['width'], 'height': info['height'],
            'algorithm': info['algorithm'], 'classified_source': str(source), 'statistics': info})
        return info

    @router.post('/mdt')
    def mdt(req: ProjectAction):
        return _run_worker(req.project_id, 'GERAR MDT', do_mdt)

    @router.post('/talude-studio')
    def studio(req: ProjectAction):
        from .auto_extract_v2 import start_auto_extract_v2
        manifest, classified, path = _get_classification(req.project_id)
        cloud = next((x for x in manifest.get('clouds', []) if Path(x.get('source_path', '')).resolve() == path.resolve()
                      and x.get('potree_path') and (Path(x['potree_path']) / 'metadata.json').is_file()), None)
        if not cloud:
            raise HTTPException(409, 'Aguardar conclusão da conversão Potree da nuvem classificada.')
        # Existing R20 engine is untouched; only change the input cloud.
        return {'job_id': start_auto_extract_v2(store, req.project_id, cloud['id'],
                                              selected_classes=[2], performance_mode='balanced')}

    def do_auto(project_id, job_id, log):
        manifest, classified, cloud = _get_classification(project_id)
        terrain = (manifest.get('terrain') or {}).get('mdt') or {}
        if not terrain.get('path') or not Path(terrain['path']).is_file():
            raise ValueError('É necessário gerar primeiro o MDT da nuvem classificada.')
        if Path(terrain.get('classified_source', '')).resolve() != cloud.resolve():
            raise ValueError('MDT desatualizado: gerar novamente a partir da nuvem classificada atual.')
        output = store.get(project_id).path / 'exports' / 'TALUDE_AUTO' / (_time_stamp() + '_' + job_id[:8])
        jobs.update(job_id, progress=7, message='TALUDE AUTO · declive → contorno → duas margens → DXF 3D')
        data = execute_talude_auto(terrain['path'], output)
        manifest = store.manifest(project_id)
        manifest.setdefault('v3_runs', []).append({'operation': 'TALUDE_AUTO', 'job_id': job_id,
                                                   'algorithm': 'TALUDE_AUTO_V0.2.3', 'path': str(output)})
        store.save_manifest(project_id, manifest)
        return data

    @router.post('/talude-auto')
    def talude_auto(req: ProjectAction):
        return _run_worker(req.project_id, 'TALUDE AUTO', do_auto)

    @router.get('/mdt-preview/{project_id}')
    def mdt_preview(project_id: str):
        import rasterio
        from rasterio.enums import Resampling
        import numpy as np
        manifest = store.manifest(project_id)
        md = (manifest.get('terrain') or {}).get('mdt') or {}
        if not md.get('path') or not Path(md['path']).is_file():
            raise HTTPException(404, 'MDT em falta.')
        with rasterio.open(md['path']) as ds:
            if ds.crs is None or ds.crs.to_epsg() != 3763:
                raise HTTPException(400, 'MDT não está em EPSG:3763.')
            stride = max(1, int(np.ceil(max(ds.width, ds.height)/180)))
            width = int(np.ceil(ds.width/stride)); height = int(np.ceil(ds.height/stride))
            grid = ds.read(1, out_shape=(height, width), masked=True, resampling=Resampling.average)
            # A preview pixel is supported ONLY when all underlying cells have
            # observations; nearest-neighbour previews could hide nodata gaps.
            coverage = ds.read_masks(1, out_shape=(height, width), resampling=Resampling.average)
            t = ds.transform * ds.transform.scale(ds.width/width, ds.height/height)
            result = grid.filled(np.nan).astype(float)
            result[coverage < 255] = np.nan
            # JSON must not contain nonstandard NaN.
            values = [[float(v) if np.isfinite(v) else None for v in row] for row in result]
            return {'width': width, 'height': height,
                    'transform': [t.a,t.b,t.c,t.d,t.e,t.f], 'values': values,
                    'epsg': 3763, 'full_resolution_m': ds.res[0], 'downsampled_preview': stride > 1}

    @router.get('/latest/{project_id}')
    def latest(project_id: str):
        """Most recent results of the CURRENT classified cloud/MDT only."""
        manifest = store.manifest(project_id)
        root = store.get(project_id).path / 'exports'
        current = (manifest.get('v3_classified') or {}).get('path')
        terrain = ((manifest.get('terrain') or {}).get('mdt') or {}).get('path')
        result = {'studio': [], 'auto': []}
        if current:
            for folder in sorted(root.glob('talude_auto_v2_*'), reverse=True):
                metadata = folder / 'talude_report.json'
                path = folder / 'talude_breaklines.geojson'
                if not metadata.is_file() or not path.is_file():
                    continue
                try:
                    report = json.loads(metadata.read_text(encoding='utf-8'))
                    if Path(report.get('input','')).resolve() != Path(current).resolve():
                        continue
                    features = json.loads(path.read_text(encoding='utf-8')).get('features',[])
                    result['studio'] = [{'type': f.get('properties',{}).get('type'),
                        'vertices': f.get('geometry',{}).get('coordinates', [])} for f in features]
                    break
                except (ValueError, KeyError, OSError):
                    continue
        if terrain:
            for folder in sorted((root/'TALUDE_AUTO').glob('*'), reverse=True):
                metadata = folder / 'report.json'
                path = folder / 'resultado.geojson'
                if not metadata.is_file() or not path.is_file():
                    continue
                try:
                    report = json.loads(metadata.read_text(encoding='utf-8'))
                    if Path(report.get('mdt','')).resolve() != Path(terrain).resolve():
                        continue
                    features = json.loads(path.read_text(encoding='utf-8')).get('features',[])
                    result['auto'] = [{'type': f.get('properties',{}).get('type'),
                        'vertices': f.get('geometry',{}).get('coordinates', [])} for f in features]
                    break
                except (ValueError, KeyError, OSError):
                    continue
        return result

    return router
