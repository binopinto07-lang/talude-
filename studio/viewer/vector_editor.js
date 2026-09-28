(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const clone = (value) => JSON.parse(JSON.stringify(value));

  const editor = {
    document: null,
    objects: new Map(),
    handleObject: null,
    selectedFeatureId: null,
    selectedVertexIndex: 0,
    undo: [],
    redo: [],
    dirty: false,
    saving: false,
    savePending: false,
    saveTimer: null,
    mutationVersion: 0,
    draft: null,
    lastExportDir: null,
    activeLayerId: "CRISTA",
    collapsedGroups: new Set(),
  };

  function shell() {
    return window.TaludeShell || null;
  }

  function projectId() {
    const s = shell();
    return s && s.state && s.state.project ? s.state.project.id : null;
  }

  function status(text) {
    const el = byId("vectorStatus");
    if (el) el.textContent = text;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function layerById(id) {
    if (!editor.document) return null;
    return (editor.document.layers || []).find((layer) => String(layer.id) === String(id)) || null;
  }

  function featureById(id) {
    if (!editor.document) return null;
    return (editor.document.features || []).find((feature) => String(feature.id) === String(id)) || null;
  }

  function selectedFeature() {
    return featureById(editor.selectedFeatureId);
  }

  function selectedLayerLocked() {
    const feature = selectedFeature();
    if (!feature) return true;
    const layer = layerById(feature.layer_id);
    return Boolean(feature.locked || (layer && layer.locked));
  }

  function pushUndo() {
    if (!editor.document) return;
    editor.undo.push(clone(editor.document));
    if (editor.undo.length > 30) editor.undo.shift();
    editor.redo = [];
  }

  function markChanged(reason) {
    editor.dirty = true;
    editor.mutationVersion += 1;
    renderAll();
    scheduleSave(reason || "autosave");
  }

  function scheduleSave(reason) {
    window.clearTimeout(editor.saveTimer);
    editor.saveTimer = window.setTimeout(() => {
      saveNow(reason || "autosave").catch((error) => {
        const s = shell();
        if (s) s.toast("Autosave vetorial: " + (error.message || String(error)), 9000);
      });
    }, 900);
  }

  function disposeObject(object) {
    const s = shell();
    if (!s || !object) return;
    try {
      if (s.state && s.state.featureOverlayScene) {
        s.state.featureOverlayScene.remove(object);
      }
      s.disposeLineObject(object);
    } catch (_) {}
  }

  function clearObjects() {
    for (const object of editor.objects.values()) disposeObject(object);
    editor.objects.clear();
    if (editor.handleObject) {
      disposeObject(editor.handleObject);
      editor.handleObject = null;
    }
  }

  function featureColor(feature) {
    if (String(feature.id) === String(editor.selectedFeatureId)) return 0xff38cf;
    const layer = String(feature.layer_id || "");
    if (layer === "CRISTA") return 0xffd54a;
    if (layer === "PE_TALUDE") return 0x38d5ff;
    if (layer === "FACES") return 0x7cff4f;
    return 0xaebed0;
  }

  function featureVisible(feature) {
    const layer = layerById(feature.layer_id);
    return Boolean(
      feature.visible !== false &&
      (!layer || layer.visible !== false)
    );
  }

  function drawVertexHandles(feature) {
    const s = shell();
    if (!s || !feature || !s.state || !s.state.featureOverlayScene) return;
    const coords = (feature.geometry || {}).coordinates || [];
    if (!coords.length) return;

    // Keep Float32 positions local to the first vertex, exactly like the
    // line renderer, otherwise UTM-scale coordinates lose centimetric detail.
    const origin = coords[0];
    const positions = new Float32Array(coords.length * 3);
    for (let i = 0; i < coords.length; i++) {
      positions[3 * i] = Number(coords[i][0]) - Number(origin[0]);
      positions[3 * i + 1] = Number(coords[i][1]) - Number(origin[1]);
      positions[3 * i + 2] = Number(coords[i][2]) - Number(origin[2]);
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const material = new THREE.PointsMaterial({
      color: 0xff38cf,
      size: 8,
      sizeAttenuation: false,
      depthTest: false,
      depthWrite: false,
      transparent: true,
      opacity: 0.95,
    });
    const object = new THREE.Points(geometry, material);
    object.position.set(Number(origin[0]), Number(origin[1]), Number(origin[2]));
    object.renderOrder = 10050;
    object.frustumCulled = false;
    s.addFeatureOverlayObject(object);
    editor.handleObject = object;
  }

  function renderObjects() {
    const s = shell();
    if (!s || !editor.document) return;

    clearObjects();
    if (window.TaludeAuto && typeof window.TaludeAuto.setAutoVisibility === "function") {
      window.TaludeAuto.setAutoVisibility(false);
    }

    for (const feature of editor.document.features || []) {
      const geometry = feature.geometry || {};
      const coords = geometry.coordinates || [];
      if (geometry.type !== "LineString" || coords.length < 2) continue;

      const selected = String(feature.id) === String(editor.selectedFeatureId);
      const object = s.createLineObject(coords, {
        color: featureColor(feature),
        widthPx: selected ? 5.0 : 3.0,
        dashed: false,
      });
      object.visible = featureVisible(feature);
      s.addFeatureOverlayObject(object);
      editor.objects.set(String(feature.id), object);
    }

    const selected = selectedFeature();
    if (selected && featureVisible(selected)) drawVertexHandles(selected);
    s.updateWideLineResolution();
  }

  function qgisSymbolClass(layerId) {
    const id = String(layerId || "");
    if (id === "CRISTA") return "crest";
    if (id === "PE_TALUDE") return "toe";
    if (id === "FACES") return "face";
    if (id === "DEBUG") return "debug";
    return "debug";
  }

  function setAllVectorLayersVisible(visible) {
    if (!editor.document) return;
    pushUndo();
    for (const layer of editor.document.layers || []) {
      layer.visible = Boolean(visible);
    }
    markChanged(visible ? "layers_show_all" : "layers_hide_all");
  }

  function soloActiveLayer() {
    if (!editor.document || !editor.activeLayerId) return;
    pushUndo();
    for (const layer of editor.document.layers || []) {
      layer.visible = String(layer.id) === String(editor.activeLayerId);
    }
    markChanged("layer_solo");
  }

  function setCloudVisible(cloudId, visible) {
    const s = shell();
    if (!s || !s.state || !s.state.pointclouds) return;
    const pc = s.state.pointclouds.get(cloudId);
    if (pc) pc.visible = Boolean(visible);
    renderLayers();
  }

  function toggleGroup(groupId) {
    if (editor.collapsedGroups.has(groupId)) {
      editor.collapsedGroups.delete(groupId);
    } else {
      editor.collapsedGroups.add(groupId);
    }
    renderLayers();
  }

  function appendGroup(box, groupId, title, rows) {
    const group = document.createElement("div");
    group.className = "qgis-group";

    const header = document.createElement("div");
    header.className = "qgis-group-header";
    header.onclick = () => toggleGroup(groupId);

    const chevron = document.createElement("span");
    chevron.className = "qgis-group-chevron";
    chevron.textContent = editor.collapsedGroups.has(groupId) ? "▶" : "▼";

    const label = document.createElement("span");
    label.textContent = title;

    const count = document.createElement("span");
    count.className = "qgis-group-count";
    count.textContent = String(rows.length);

    header.appendChild(chevron);
    header.appendChild(label);
    header.appendChild(count);
    group.appendChild(header);

    const body = document.createElement("div");
    body.className = "qgis-group-body" +
      (editor.collapsedGroups.has(groupId) ? " collapsed" : "");
    for (const row of rows) body.appendChild(row);
    group.appendChild(body);
    box.appendChild(group);
  }

  function cloudLayerRows() {
    const s = shell();
    const rows = [];
    const project = s && s.state ? s.state.project : null;
    if (!project) return rows;

    for (const cloud of project.clouds || []) {
      const row = document.createElement("div");
      row.className = "qgis-layer-row";

      const visible = document.createElement("input");
      visible.type = "checkbox";
      const pc = s.state.pointclouds.get(cloud.id);
      visible.checked = pc ? pc.visible !== false : false;
      visible.title = "Visibilidade da nuvem";
      visible.onchange = () => setCloudVisible(cloud.id, visible.checked);

      const symbol = document.createElement("span");
      symbol.className = "qgis-symbol cloud";

      const main = document.createElement("button");
      main.className = "qgis-layer-main";
      const pts = Number(cloud.point_count || 0).toLocaleString("pt-PT");
      main.innerHTML =
        '<span class="qgis-layer-name">' + escapeHtml(cloud.name || "cloud") + '</span>' +
        '<span class="qgis-cloud-meta">' + pts + ' pts</span>';
      main.onclick = () => {
        const loaded = s.state.pointclouds.has(cloud.id);
        if (!loaded && s.state.project) {
          s.toast("Carregue a nuvem pelo projeto antes de a ativar.", 4500);
        }
      };

      const lock = document.createElement("button");
      lock.className = "qgis-mini-button locked";
      lock.textContent = "🔒";
      lock.title = "Nuvem é somente leitura";

      const spare = document.createElement("button");
      spare.className = "qgis-mini-button";
      spare.textContent = "◎";
      spare.title = "Enquadrar nuvem";
      spare.onclick = () => {
        try { s.state.viewer.fitToScreen(0.82); } catch (_) {}
      };

      row.appendChild(visible);
      row.appendChild(symbol);
      row.appendChild(main);
      row.appendChild(lock);
      row.appendChild(spare);
      rows.push(row);
    }
    return rows;
  }

  function vectorLayerRow(layer, counts) {
    const id = String(layer.id);
    const row = document.createElement("div");
    row.className = "qgis-layer-row" +
      (String(editor.activeLayerId) === id ? " active" : "");

    const visible = document.createElement("input");
    visible.type = "checkbox";
    visible.checked = layer.visible !== false;
    visible.title = "Visibilidade";
    visible.onchange = () => {
      pushUndo();
      layer.visible = visible.checked;
      markChanged("layer_visibility");
    };

    const symbol = document.createElement("span");
    symbol.className = "qgis-symbol " + qgisSymbolClass(id);

    const main = document.createElement("button");
    main.className = "qgis-layer-main";
    main.innerHTML =
      '<span class="qgis-layer-name">' + escapeHtml(layer.name || id) + '</span>' +
      '<span class="qgis-layer-count">' + String(counts[id] || 0) + '</span>';
    main.onclick = () => {
      editor.activeLayerId = id;
      const filter = byId("vectorLayerFilter");
      if (filter) filter.value = id;
      renderAll();
    };

    const lock = document.createElement("button");
    lock.className = "qgis-mini-button" + (layer.locked ? " locked" : "");
    lock.textContent = layer.locked ? "🔒" : "🔓";
    lock.title = layer.locked ? "Desbloquear layer" : "Bloquear layer";
    lock.onclick = () => {
      pushUndo();
      layer.locked = !layer.locked;
      markChanged("layer_lock");
    };

    const solo = document.createElement("button");
    solo.className = "qgis-mini-button";
    solo.textContent = "S";
    solo.title = "Mostrar apenas esta layer";
    solo.onclick = () => {
      editor.activeLayerId = id;
      soloActiveLayer();
    };

    row.appendChild(visible);
    row.appendChild(symbol);
    row.appendChild(main);
    row.appendChild(lock);
    row.appendChild(solo);
    return row;
  }

  function renderLayers() {
    const box = byId("vectorLayers");
    const filter = byId("vectorLayerFilter");
    if (!box || !filter) return;

    box.innerHTML = "";
    const oldFilter = filter.value;
    filter.innerHTML = '<option value="">Todas as layers</option>';

    const cloudRows = cloudLayerRows();
    if (cloudRows.length) appendGroup(box, "clouds", "NUVEM DE PONTOS", cloudRows);

    if (!editor.document) return;
    const counts = {};
    for (const feature of editor.document.features || []) {
      const key = String(feature.layer_id || "");
      counts[key] = (counts[key] || 0) + 1;
    }

    const groups = {
      breaklines: [],
      support: [],
      other: [],
    };

    for (const layer of editor.document.layers || []) {
      const id = String(layer.id);
      const row = vectorLayerRow(layer, counts);
      if (id === "CRISTA" || id === "PE_TALUDE") groups.breaklines.push(row);
      else if (id === "FACES" || id === "DEBUG") groups.support.push(row);
      else groups.other.push(row);

      const option = document.createElement("option");
      option.value = id;
      option.textContent = String(layer.name || id);
      filter.appendChild(option);
    }

    if (groups.breaklines.length) appendGroup(box, "breaklines", "BREAKLINES", groups.breaklines);
    if (groups.support.length) appendGroup(box, "support", "ANÁLISE / SUPORTE", groups.support);
    if (groups.other.length) appendGroup(box, "other", "OUTRAS", groups.other);

    if (Array.from(filter.options).some((option) => option.value === oldFilter)) {
      filter.value = oldFilter;
    } else if (editor.activeLayerId && Array.from(filter.options).some((option) => option.value === editor.activeLayerId)) {
      filter.value = editor.activeLayerId;
    }
  }

  function renderFeatures() {
    const box = byId("vectorFeatures");
    if (!box) return;
    box.innerHTML = "";
    if (!editor.document) return;

    const layerFilter = byId("vectorLayerFilter") ? byId("vectorLayerFilter").value : "";
    const search = (byId("vectorSearch") ? byId("vectorSearch").value : "").trim().toLowerCase();

    const items = (editor.document.features || []).filter((feature) => {
      if (layerFilter && String(feature.layer_id) !== layerFilter) return false;
      if (!search) return true;
      const props = feature.properties || {};
      const haystack = [
        feature.id,
        feature.layer_id,
        props.face_id,
        props.line_id,
        props.type,
        props.source,
      ].join(" ").toLowerCase();
      return haystack.includes(search);
    });

    for (const feature of items.slice(0, 400)) {
      const props = feature.properties || {};
      const row = document.createElement("button");
      row.className = "vector-feature-row" +
        (String(feature.id) === String(editor.selectedFeatureId) ? " selected" : "");
      const label = feature.layer_id === "CRISTA"
        ? "CRISTA"
        : feature.layer_id === "PE_TALUDE"
          ? "PÉ"
          : feature.layer_id;
      row.innerHTML =
        "<strong>" + escapeHtml(label) + "</strong>" +
        "<span>Face " + escapeHtml(props.face_id == null ? "—" : props.face_id) +
        " · " + escapeHtml(String(feature.id).slice(0, 18)) + "</span>";
      row.onclick = () => {
        editor.selectedFeatureId = String(feature.id);
        editor.selectedVertexIndex = 0;
        renderAll();
      };
      box.appendChild(row);
    }

    if (items.length > 400) {
      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent = "A mostrar 400 de " + items.length + " features. Use o filtro.";
      box.appendChild(hint);
    }
  }

  function renderEditBox() {
    const box = byId("vectorEditBox");
    const feature = selectedFeature();
    if (!box) return;

    if (!feature) {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");

    const coords = (feature.geometry || {}).coordinates || [];
    if (editor.selectedVertexIndex >= coords.length) editor.selectedVertexIndex = Math.max(0, coords.length - 1);

    const props = feature.properties || {};
    byId("vectorSelectedTitle").textContent =
      (feature.layer_id === "CRISTA" ? "CRISTA" : feature.layer_id === "PE_TALUDE" ? "PÉ" : feature.layer_id) +
      " · Face " + (props.face_id == null ? "—" : props.face_id);

    const select = byId("vectorVertexSelect");
    select.innerHTML = "";
    coords.forEach((point, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = "V" + String(index + 1).padStart(3, "0");
      select.appendChild(option);
    });
    select.value = String(editor.selectedVertexIndex);

    const point = coords[editor.selectedVertexIndex];
    byId("vectorVertexXYZ").textContent = point
      ? "X " + Number(point[0]).toFixed(3) +
        " · Y " + Number(point[1]).toFixed(3) +
        " · Z " + Number(point[2]).toFixed(3)
      : "X — · Y — · Z —";

    const locked = selectedLayerLocked();
    ["vectorMoveVertex", "vectorInsertVertex", "vectorDeleteVertex", "vectorDeleteLine"].forEach((id) => {
      const control = byId(id);
      if (control) control.disabled = locked;
    });
  }

  function renderToolbarState() {
    if (byId("vectorUndo")) byId("vectorUndo").disabled = editor.undo.length === 0;
    if (byId("vectorRedo")) byId("vectorRedo").disabled = editor.redo.length === 0;
    if (byId("vectorSave")) byId("vectorSave").disabled = !editor.document || editor.saving;
    if (byId("vectorExport")) byId("vectorExport").disabled = !editor.document;
    if (byId("vectorRecover")) byId("vectorRecover").disabled = !editor.document;
  }

  function renderAll() {
    renderLayers();
    renderFeatures();
    renderEditBox();
    renderObjects();
    renderToolbarState();

    if (editor.document) {
      status(
        "Rev. " + String(editor.document.revision || 1) +
        " · " + String((editor.document.features || []).length) + " features" +
        (editor.dirty ? " · alterações por guardar" : " · guardado")
      );
    }
  }

  async function loadDocument() {
    const s = shell();
    const pid = projectId();
    if (!s || !pid) {
      editor.document = null;
      clearObjects();
      renderAll();
      status("Sem projeto ativo.");
      return;
    }

    try {
      const document = await s.api(
        "/api/projects/" + encodeURIComponent(pid) + "/vector-document"
      );
      editor.document = document;
      if (!layerById(editor.activeLayerId)) editor.activeLayerId = "CRISTA";
      editor.selectedFeatureId = null;
      editor.selectedVertexIndex = 0;
      editor.undo = [];
      editor.redo = [];
      editor.dirty = false;
      editor.draft = null;
      renderAll();
    } catch (error) {
      editor.document = null;
      clearObjects();
      renderAll();
      status("Ainda não existe Vector Document. Execute CRISTA + PÉ.");
    }
  }

  async function saveNow(reason) {
    const s = shell();
    const pid = projectId();
    if (!s || !pid || !editor.document) return;
    if (!editor.dirty && reason !== "manual_save") return;
    if (editor.saving) {
      editor.savePending = true;
      return;
    }

    editor.saving = true;
    editor.savePending = false;
    renderToolbarState();

    const snapshot = clone(editor.document);
    const seq = editor.mutationVersion;
    const expected = Number(editor.document.revision || 1);

    try {
      const result = await s.api(
        "/api/projects/" + encodeURIComponent(pid) + "/vector-document/autosave",
        {
          method: "PUT",
          body: JSON.stringify({
            document: snapshot,
            expected_revision: expected,
            reason: reason || "autosave",
          }),
        }
      );

      if (editor.mutationVersion === seq) {
        editor.document = result.document;
        editor.dirty = false;
      } else {
        editor.document.revision = result.document.revision;
        editor.document.history = result.document.history || editor.document.history;
        editor.savePending = true;
      }
      renderAll();
    } catch (error) {
      if (String(error.message || error).includes("REVISION_CONFLICT")) {
        s.toast(
          "Conflito de revisão: o documento mudou no disco. Atualize antes de continuar.",
          10000
        );
      }
      throw error;
    } finally {
      editor.saving = false;
      renderToolbarState();
      if (editor.savePending) {
        editor.savePending = false;
        scheduleSave("autosave_followup");
      }
    }
  }

  function recalcFeature(feature) {
    const coords = (feature.geometry || {}).coordinates || [];
    let length = 0;
    for (let i = 1; i < coords.length; i++) {
      const dx = Number(coords[i][0]) - Number(coords[i - 1][0]);
      const dy = Number(coords[i][1]) - Number(coords[i - 1][1]);
      const dz = Number(coords[i][2]) - Number(coords[i - 1][2]);
      length += Math.hypot(dx, dy, dz);
    }
    feature.properties = feature.properties || {};
    feature.properties.length_m = length;
    feature.revision = Number(feature.revision || 1) + 1;
    feature.updated_at = new Date().toISOString();
  }

  function applyVertexPick(xyz, context) {
    const feature = selectedFeature();
    if (!feature || selectedLayerLocked()) return;
    const coords = feature.geometry.coordinates;

    pushUndo();

    if (context.action === "move") {
      const index = Number(context.vertex_index);
      if (index >= 0 && index < coords.length) {
        coords[index] = xyz.map(Number);
        editor.selectedVertexIndex = index;
      }
    } else if (context.action === "insert") {
      const index = Math.max(0, Math.min(coords.length - 1, Number(context.vertex_index)));
      coords.splice(index + 1, 0, xyz.map(Number));
      editor.selectedVertexIndex = index + 1;
    }

    recalcFeature(feature);
    markChanged("vertex_" + context.action);
  }

  function startNewLine() {
    const s = shell();
    if (!s || !editor.document) return;
    const type = byId("vectorNewType").value === "TOE" ? "TOE" : "CREST";
    editor.draft = { type, points: [] };
    byId("vectorDraftActions").classList.remove("hidden");
    status("Nova " + (type === "CREST" ? "CRISTA" : "PÉ") + " · clique pontos na nuvem.");
    s.armEditorPick({ action: "draft_add" });
  }

  function addDraftPoint(xyz) {
    const s = shell();
    if (!editor.draft || !s) return;
    editor.draft.points.push(xyz.map(Number));
    status(
      "Nova linha · " + editor.draft.points.length +
      " pontos · clique para continuar ou Concluir."
    );
    s.armEditorPick({ action: "draft_add" });
  }

  function finishDraft() {
    if (!editor.document || !editor.draft) return;
    if (editor.draft.points.length < 2) {
      const s = shell();
      if (s) s.toast("A nova linha precisa de pelo menos 2 pontos.", 5000);
      return;
    }

    pushUndo();
    const type = editor.draft.type;
    const id = "MANUAL-" + type + "-" + Date.now().toString(36) +
      "-" + Math.random().toString(36).slice(2, 8);
    const feature = {
      id,
      layer_id: type === "CREST" ? "CRISTA" : "PE_TALUDE",
      geometry: {
        type: "LineString",
        coordinates: editor.draft.points.map((point) => point.slice()),
        has_z: true,
      },
      properties: {
        type,
        source: "MANUAL",
        status: "EDITED",
        confidence: 1.0,
      },
      visible: true,
      locked: false,
      selected: false,
      revision: 1,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    recalcFeature(feature);
    editor.document.features.push(feature);
    editor.selectedFeatureId = id;
    editor.selectedVertexIndex = 0;
    editor.draft = null;
    byId("vectorDraftActions").classList.add("hidden");
    const s = shell();
    if (s) s.cancelEditorPick();
    markChanged("manual_line");
  }

  function cancelDraft() {
    editor.draft = null;
    const s = shell();
    if (s) s.cancelEditorPick();
    byId("vectorDraftActions").classList.add("hidden");
    renderAll();
  }

  function deleteVertex() {
    const feature = selectedFeature();
    if (!feature || selectedLayerLocked()) return;
    const coords = feature.geometry.coordinates;
    if (coords.length <= 2) {
      const s = shell();
      if (s) s.toast("Uma linha precisa de pelo menos 2 vértices.", 5000);
      return;
    }
    pushUndo();
    coords.splice(editor.selectedVertexIndex, 1);
    editor.selectedVertexIndex = Math.max(0, editor.selectedVertexIndex - 1);
    recalcFeature(feature);
    markChanged("delete_vertex");
  }

  function deleteLine() {
    const feature = selectedFeature();
    if (!feature || selectedLayerLocked() || !editor.document) return;
    pushUndo();
    editor.document.features = editor.document.features.filter(
      (item) => String(item.id) !== String(feature.id)
    );
    editor.selectedFeatureId = null;
    editor.selectedVertexIndex = 0;
    markChanged("delete_line");
  }

  function undo() {
    if (!editor.document || !editor.undo.length) return;
    editor.redo.push(clone(editor.document));
    editor.document = editor.undo.pop();
    editor.selectedFeatureId = null;
    editor.selectedVertexIndex = 0;
    editor.dirty = true;
    editor.mutationVersion += 1;
    renderAll();
    scheduleSave("undo");
  }

  function redo() {
    if (!editor.document || !editor.redo.length) return;
    editor.undo.push(clone(editor.document));
    editor.document = editor.redo.pop();
    editor.selectedFeatureId = null;
    editor.selectedVertexIndex = 0;
    editor.dirty = true;
    editor.mutationVersion += 1;
    renderAll();
    scheduleSave("redo");
  }

  async function recoverPrevious() {
    const s = shell();
    const pid = projectId();
    if (!s || !pid) return;
    await saveNow("pre_recovery");
    const result = await s.api(
      "/api/projects/" + encodeURIComponent(pid) + "/vector-document/recover",
      {
        method: "POST",
        body: JSON.stringify({ revision: null, autosave: false }),
      }
    );
    editor.document = result.document;
    editor.undo = [];
    editor.redo = [];
    editor.selectedFeatureId = null;
    editor.dirty = false;
    renderAll();
    s.toast("Revisão anterior recuperada como nova revisão.", 6500);
  }

  function exportFormats() {
    const formats = [];
    if (byId("exportDxf").checked) formats.push("dxf");
    if (byId("exportShp").checked) formats.push("shp");
    if (byId("exportGpkg").checked) formats.push("gpkg");
    if (byId("exportGeojson").checked) formats.push("geojson");
    return formats;
  }

  async function exportVectors() {
    const s = shell();
    const pid = projectId();
    if (!s || !pid || !editor.document) return;

    await saveNow("pre_export");
    const formats = exportFormats();
    if (!formats.length) {
      s.toast("Selecione pelo menos um formato.", 5000);
      return;
    }

    const selectedOnly = byId("exportSelectedOnly").checked;
    if (selectedOnly && !editor.selectedFeatureId) {
      s.toast("Selecione uma feature antes de exportar só a seleção.", 6000);
      return;
    }

    const filteredLayerOnly = byId("exportFilteredLayer").checked;
    const filteredLayer = byId("vectorLayerFilter").value;
    if (filteredLayerOnly && !filteredLayer) {
      s.toast("Escolha uma layer no filtro antes de exportar só essa layer.", 6500);
      return;
    }

    const result = await s.api(
      "/api/projects/" + encodeURIComponent(pid) + "/vector-document/export",
      {
        method: "POST",
        body: JSON.stringify({
          formats,
          layer_ids: filteredLayerOnly ? [filteredLayer] : null,
          feature_ids: selectedOnly ? [editor.selectedFeatureId] : null,
          visible_only: byId("exportVisibleOnly").checked,
          selected_only: false,
        }),
      }
    );

    editor.lastExportDir = result.output_dir;
    status(
      "Exportação concluída · " + result.feature_count +
      " features · " + formats.join(", ").toUpperCase()
    );
    s.toast("DXF/SHP/GPKG exportados para a pasta do projeto.", 7000);
    try {
      await s.bridgeCall("open_folder", result.output_dir);
    } catch (_) {}
  }

  function bind() {
    byId("vectorRefresh").onclick = () => loadDocument();
    byId("vectorSave").onclick = () => saveNow("manual_save");
    if (byId("layersShowAll")) byId("layersShowAll").onclick = () => setAllVectorLayersVisible(true);
    if (byId("layersHideAll")) byId("layersHideAll").onclick = () => setAllVectorLayersVisible(false);
    if (byId("layersSoloActive")) byId("layersSoloActive").onclick = soloActiveLayer;
    byId("vectorUndo").onclick = undo;
    byId("vectorRedo").onclick = redo;
    byId("vectorLayerFilter").onchange = () => {
      const value = byId("vectorLayerFilter").value;
      if (value) editor.activeLayerId = value;
      renderFeatures();
      renderLayers();
    };
    byId("vectorSearch").oninput = renderFeatures;

    byId("vectorVertexSelect").onchange = () => {
      editor.selectedVertexIndex = Number(byId("vectorVertexSelect").value || 0);
      renderEditBox();
    };

    byId("vectorMoveVertex").onclick = () => {
      const s = shell();
      if (!s || !selectedFeature() || selectedLayerLocked()) return;
      s.armEditorPick({
        action: "move",
        feature_id: editor.selectedFeatureId,
        vertex_index: editor.selectedVertexIndex,
      });
      status("Mover vértice · clique na nova posição sobre a nuvem.");
    };

    byId("vectorInsertVertex").onclick = () => {
      const s = shell();
      if (!s || !selectedFeature() || selectedLayerLocked()) return;
      s.armEditorPick({
        action: "insert",
        feature_id: editor.selectedFeatureId,
        vertex_index: editor.selectedVertexIndex,
      });
      status("Inserir vértice · clique na posição sobre a nuvem.");
    };

    byId("vectorDeleteVertex").onclick = deleteVertex;
    byId("vectorDeleteLine").onclick = deleteLine;
    byId("vectorNewLine").onclick = startNewLine;
    byId("vectorFinishLine").onclick = finishDraft;
    byId("vectorCancelLine").onclick = cancelDraft;
    byId("vectorRecover").onclick = () => {
      recoverPrevious().catch((error) => {
        const s = shell();
        if (s) s.toast(error.message || String(error), 9000);
      });
    };
    byId("vectorExport").onclick = () => {
      exportVectors().catch((error) => {
        const s = shell();
        if (s) s.toast(error.message || String(error), 12000);
      });
    };

    window.addEventListener("talude:editor-pick", (event) => {
      const detail = event.detail || {};
      const xyz = detail.xyz;
      const context = detail.context || {};
      if (!Array.isArray(xyz) || xyz.length < 3) return;

      if (context.action === "draft_add") {
        addDraftPoint(xyz);
        return;
      }
      if (
        String(context.feature_id || "") !== String(editor.selectedFeatureId || "")
      ) {
        return;
      }
      applyVertexPick(xyz, context);
    });

    window.addEventListener("talude:project-activated", () => {
      loadDocument();
    });

    window.addEventListener("talude:vector-document-updated", () => {
      window.setTimeout(loadDocument, 80);
    });
  }

  function waitForShell() {
    if (!shell()) {
      window.setTimeout(waitForShell, 100);
      return;
    }
    bind();
    if (projectId()) loadDocument();
    renderToolbarState();
  }

  window.VectorEditor = {
    loadDocument,
    saveNow,
    getDocument: () => editor.document,
    getSelectedFeatureId: () => editor.selectedFeatureId,
  };

  window.addEventListener("DOMContentLoaded", waitForShell);
})();
