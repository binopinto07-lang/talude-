/* TALUDE STUDIO V3 simplified workflow; leaves R20 viewer/nav/engines intact. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const shell = () => window.TaludeShell;
  const sets = { studio: [], auto: [] };
  let mdtMesh = null;
  let busy = false;
  let currentStatus = null;
  let activeJob = null;
  const colors = { studio: { CREST: 0xffd54a, TOE: 0x38d5ff },
                   auto: { CREST: 0xff38cf, TOE: 0x42e57b } };

  function notice(message, error = false) {
    $('v3Status').textContent = String(message);
    $('v3Status').classList.toggle('v3-error', Boolean(error));
    if (shell()) shell().setStatus(String(message));
  }
  async function request(path, method='GET', body) {
    const options = { method };
    if (body !== undefined) options.body = JSON.stringify(body);
    return shell().api(path, options);
  }
  function projectId() { return shell()?.state.project?.id || null; }
  function matchingCloud(classified) {
    const target = String(classified?.path || '').toLowerCase();
    return (shell()?.state.project?.clouds || []).find(cloud => String(cloud.source_path || '').toLowerCase() === target);
  }
  function refreshButtons() {
    const hasProject = Boolean(projectId());
    const classified = Boolean(currentStatus?.has_classified);
    const converted = Boolean(matchingCloud(currentStatus?.classified)?.potree_path);
    const readyMdt = Boolean(currentStatus?.has_mdt);
    $('v3Classify').disabled = busy || !hasProject || !currentStatus?.algorithms?.CLASSIFY?.available;
    $('v3MDT').disabled = busy || !classified;
    $('v3Studio').disabled = busy || !converted;
    $('v3Auto').disabled = busy || !readyMdt || !currentStatus?.algorithms?.TALUDE_AUTO?.available;
    $('v3Auto').title = readyMdt ? 'Executar o motor independente com o MDT' : 'É necessário gerar primeiro o MDT da nuvem classificada.';
  }
  async function refresh() {
    if (!projectId()) { currentStatus = null; refreshButtons(); return; }
    currentStatus = await request('/api/v3/status/' + encodeURIComponent(projectId()));
    const pid = projectId();
    shell().state.project = await request('/api/projects/' + encodeURIComponent(pid));
    $('v3Project').textContent = shell().state.project.name;
    $('v3Original').disabled = !shell().state.project.clouds?.length;
    $('v3Original').checked = $('v3Original').checked && !$('v3Original').disabled;
    $('v3Classified').disabled = !currentStatus.has_classified;
    $('v3MdtLayer').disabled = !currentStatus.has_mdt;
    $('v3ClassHint').textContent = currentStatus.has_classified ? 'CLASSIFY LAS R20.4 disponível' : 'Classificação Ground em falta';
    $('v3MdtHint').textContent = currentStatus.has_mdt ? 'MDT ' + Number(currentStatus.mdt.resolution).toFixed(2) + ' m' : 'Gerar MDT antes de TALUDE AUTO';
    if (currentStatus.has_classified && !matchingCloud(currentStatus.classified)) {
      $('v3ClassHint').textContent = 'Aguarda conversão da nuvem classificada para Potree';
    }
    refreshButtons();
  }
  function setCloudsVisible() {
    if (!projectId()) return;
    const classifiedPath = String(currentStatus?.classified?.path || '').toLowerCase();
    const controls = [[$('v3Original'), false], [$('v3Classified'), true]];
    for (const [toggle, isClassified] of controls) {
      for (const cloud of shell().state.project.clouds || []) {
        const isThis = String(cloud.source_path || '').toLowerCase() === classifiedPath;
        if (isThis !== isClassified) continue;
        const pointcloud = shell().state.pointclouds.get(cloud.id);
        if (pointcloud) pointcloud.visible = toggle.checked;
      }
    }
  }
  async function loadClassifiedCloud() {
    await refresh();
    const record = matchingCloud(currentStatus.classified);
    if (!record || !record.potree_path || shell().state.pointclouds.has(record.id)) {
      setCloudsVisible(); return;
    }
    await new Promise((resolve, reject) => {
      Potree.loadPointCloud('/api/cloud-data/' + encodeURIComponent(projectId()) + '/' + encodeURIComponent(record.id) + '/metadata.json',
        record.name || 'Classificada', (event) => {
          if (!event?.pointcloud) { reject(new Error('Conversão Potree não carregou a nuvem classificada.')); return; }
          const cloud = event.pointcloud;
          cloud.visible = $('v3Classified').checked;
          shell().state.viewer.scene.addPointCloud(cloud);
          shell().state.pointclouds.set(record.id, cloud);
          resolve();
        });
    });
    setCloudsVisible();
  }
  function clearLines(group) {
    const scene = shell()?.state.featureOverlayScene;
    for (const item of sets[group]) {
      if (scene) scene.remove(item.object);
      shell()?.disposeLineObject(item.object);
    }
    sets[group] = [];
  }
  function syncLineVisibility() {
    for (const [group, lines] of Object.entries(sets)) {
      for (const item of lines) {
        const checkbox = $(group === 'studio'
          ? (item.kind === 'CREST' ? 'v3StudioCrest' : 'v3StudioToe')
          : (item.kind === 'CREST' ? 'v3AutoCrest' : 'v3AutoToe'));
        item.object.visible = $(group === 'studio' ? 'v3StudioGroup' : 'v3AutoGroup').checked && checkbox.checked;
      }
    }
    if (mdtMesh) mdtMesh.visible = $('v3MdtLayer').checked;
    setCloudsVisible();
  }
  function renderLines(group, result) {
    clearLines(group);
    const lines = Array.isArray(result.lines) ? result.lines : [];
    for (const data of lines) {
      const kind = String(data.type || '').toUpperCase();
      if (!['CREST', 'TOE'].includes(kind) || !Array.isArray(data.vertices) || data.vertices.length < 2) continue;
      const obj = shell().createLineObject(data.vertices, {color: colors[group][kind], widthPx: 3.1});
      shell().addFeatureOverlayObject(obj);
      sets[group].push({object: obj, kind});
    }
    shell().updateWideLineResolution();
    $(group === 'studio' ? 'v3StudioCount' : 'v3AutoCount').textContent = sets[group].length + ' linhas';
    syncLineVisibility();
  }
  function clearMdt() {
    if (!mdtMesh) return;
    shell()?.state.featureOverlayScene?.remove(mdtMesh);
    mdtMesh.geometry.dispose(); mdtMesh.material.dispose();
    mdtMesh = null;
  }
  async function showMdt() {
    clearMdt();
    if (!$('v3MdtLayer').checked || !currentStatus?.has_mdt) return;
    const raster = await request('/api/v3/mdt-preview/' + encodeURIComponent(projectId()));
    const {width: w, height: h, values, transform: t} = raster;
    const pos = [], indices = [], indexAt = new Map();
    let n = 0;
    // Local coordinates prevent Float32 losses from large Portuguese TM06 XY.
    for (let row = 0; row < h; ++row) for (let col = 0; col < w; ++col) {
      const z = values[row][col]; if (z === null || !Number.isFinite(z)) continue;
      pos.push(t[0]*(col+0.5) + t[1]*(row+0.5), t[3]*(col+0.5) + t[4]*(row+0.5), z);
      indexAt.set(row*w + col, n++);
    }
    for (let row = 0; row < h-1; ++row) for (let col = 0; col < w-1; ++col) {
      const a=indexAt.get(row*w+col), b=indexAt.get(row*w+col+1);
      const c=indexAt.get((row+1)*w+col), d=indexAt.get((row+1)*w+col+1);
      if ([a,b,c,d].some(value => value === undefined)) continue; // no bridging NoData
      indices.push(a,c,b,b,c,d);
    }
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); geom.setIndex(indices);
    geom.computeVertexNormals();
    const mat = new THREE.MeshBasicMaterial({color: 0x93bba4, side: THREE.DoubleSide, transparent: true,
      opacity: .58, depthWrite: false});
    mdtMesh = new THREE.Mesh(geom, mat);
    mdtMesh.position.set(t[2], t[5], 0);
    mdtMesh.frustumCulled = false; mdtMesh.renderOrder = 4000;
    shell().addFeatureOverlayObject(mdtMesh);
  }
  async function restoreLatest() {
    if (!projectId() || busy) return;
    const data = await request('/api/v3/latest/' + encodeURIComponent(projectId()));
    if (data.studio?.length) renderLines('studio', {lines:data.studio});
    if (data.auto?.length) renderLines('auto', {lines:data.auto});
    if (currentStatus?.has_mdt && $('v3MdtLayer').checked) await showMdt();
  }
  async function waitJob(jobId, label) {
    activeJob = jobId;
    for (;;) {
      const job = await request('/api/jobs/' + encodeURIComponent(jobId));
      $('v3Progress').value = Number(job.progress || 0);
      notice((job.message || label) + ' · ' + $('v3Progress').value + '%');
      if (job.status === 'completed') return job.result || {};
      if (job.status === 'failed') throw new Error(job.error || job.message || label + ' falhou.');
      if (job.status === 'cancelled') throw new Error(label + ' cancelado.');
      await sleep(750);
    }
  }
  async function operate(mode) {
    if (busy || !projectId()) return;
    busy = true; refreshButtons();
    $('v3Progress').value = 0;
    const label = {classify:'CLASSIFY LAS R20.4', mdt:'GERAR MDT R20.4', studio:'DETETAR CRISTA + PÉ', auto:'TALUDE AUTO'}[mode];
    notice(label + ' · a iniciar…');
    try {
      const path = {classify:'classify', mdt:'mdt', studio:'talude-studio', auto:'talude-auto'}[mode];
      const queued = await request('/api/v3/' + path, 'POST', {project_id: projectId()});
      const data = await waitJob(queued.job_id, label);
      if (mode === 'classify') {
        if (!data.import_job_id) throw new Error('Classificado, mas conversão Potree não foi agendada.');
        await waitJob(data.import_job_id, 'Importar nuvem classificada');
        $('v3Classified').checked = true;
        await loadClassifiedCloud();
        $('v3MdtLayer').checked = false;
        clearMdt(); clearLines('studio'); clearLines('auto');
      }
      if (mode === 'mdt') { $('v3MdtLayer').checked = true; await refresh(); await showMdt(); }
      if (mode === 'studio') renderLines('studio', data);
      if (mode === 'auto') renderLines('auto', data);
      await refresh();
      notice(label + ' concluído' + (mode === 'auto' || mode === 'studio' ? ' · conferir resultado na viewport' : ''));
    } catch (error) {
      notice(label + ' falhou: ' + (error.message || error), true);
      shell()?.toast(String(error.message || error), 12000);
    } finally { busy = false; activeJob = null; refreshButtons(); }
  }
  function init() {
    const sidebar = document.querySelector('.sidebar');
    const target = document.createElement('section'); target.className = 'v3-workflow';
    target.innerHTML = `
      <div class="v3-block"><h3>PROJETO</h3><div class="v3-row">
        <button id="v3New">Novo projeto</button><button id="v3Open">Abrir projeto</button></div>
        <p id="v3Project">Nenhum projeto aberto</p>
        <button id="v3Import" class="v3-wide">Importar LAS / LAZ / COPC</button></div>
      <div class="v3-block"><h3>PROCESSAMENTO</h3>
        <button id="v3Classify" class="v3-wide v3-action">1. CLASSIFICAR NUVEM</button><small id="v3ClassHint">Classificação em falta</small>
        <button id="v3MDT" class="v3-wide v3-action">2. GERAR MDT</button><small id="v3MdtHint">Gerar MDT antes de TALUDE AUTO</small>
        <button id="v3Studio" class="v3-wide v3-action">3. DETETAR CRISTA + PÉ</button>
        <button id="v3Auto" class="v3-wide v3-action">4. TALUDE AUTO (MDT)</button></div>
      <div class="v3-block"><h3>CAMADAS</h3>
        <label><input id="v3Original" type="checkbox" checked> Nuvem original</label>
        <label><input id="v3Classified" type="checkbox" checked disabled> Nuvem classificada</label>
        <label><input id="v3MdtLayer" type="checkbox" disabled> MDT (pré-visualização 3D)</label>
        <label class="v3-parent"><input id="v3StudioGroup" type="checkbox" checked> TALUDE STUDIO <span id="v3StudioCount">0 linhas</span></label>
        <label class="v3-child"><input id="v3StudioCrest" type="checkbox" checked> <i class="v3-dot v3-yellow"></i> Crista</label>
        <label class="v3-child"><input id="v3StudioToe" type="checkbox" checked> <i class="v3-dot v3-cyan"></i> Pé</label>
        <label class="v3-parent"><input id="v3AutoGroup" type="checkbox" checked> TALUDE AUTO <span id="v3AutoCount">0 linhas</span></label>
        <label class="v3-child"><input id="v3AutoCrest" type="checkbox" checked> <i class="v3-dot v3-magenta"></i> Crista</label>
        <label class="v3-child"><input id="v3AutoToe" type="checkbox" checked> <i class="v3-dot v3-green"></i> Pé</label></div>
      <div class="v3-block"><h3>ESTADO</h3><progress id="v3Progress" value="0" max="100"></progress><p id="v3Status" role="status">A aguardar projeto.</p></div>`;
    sidebar.querySelector('.brand').insertAdjacentElement('afterend', target);
    $('v3New').onclick = () => $('newProject').click();
    $('v3Open').onclick = () => $('openProject').click();
    $('v3Import').onclick = () => $('addCloud').click();
    for (const [id, action] of [['v3Classify','classify'],['v3MDT','mdt'],['v3Studio','studio'],['v3Auto','auto']]) {
      $(id).onclick = () => operate(action);
    }
    for (const id of ['v3Original','v3Classified','v3MdtLayer','v3StudioGroup','v3StudioCrest','v3StudioToe','v3AutoGroup','v3AutoCrest','v3AutoToe']) {
      $(id).onchange = () => {
        if (id === 'v3MdtLayer' && $(id).checked && !mdtMesh) showMdt().catch(e => notice(e.message,true));
        syncLineVisibility();
      };
    }
    window.addEventListener('talude:project-activated', () => {
      clearLines('studio'); clearLines('auto'); clearMdt(); refresh().then(restoreLatest).catch(e=>notice(e.message, true));
    });
    refresh().catch(e => notice(e.message, true));
  }
  window.addEventListener('DOMContentLoaded', init);
})();
