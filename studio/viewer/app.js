(() => {
  "use strict";

  let desktopBridge = null;

  const CLASS_NAMES = {
    0: "Nunca classificado",
    1: "Não classificado",
    2: "Solo",
    3: "Vegetação baixa",
    4: "Vegetação média",
    5: "Vegetação alta",
    6: "Edifício",
    7: "Ruído baixo",
    8: "Key point",
    9: "Água",
    10: "Carril",
    11: "Superfície rodoviária",
    12: "Overlap",
    13: "Wire guard",
    14: "Wire conductor",
    15: "Transmission tower",
    16: "Wire connector",
    17: "Bridge deck",
    18: "Ruído alto"
  };
  const CLASS_CODES = Object.keys(CLASS_NAMES).map(Number);

  const state = {
    project: null,
    viewer: null,
    pointclouds: new Map(),
    featureMode: "guided",
    traceArmed: false,
    currentSeed: null,
    seedMarker: null,
    candidateObject: null,
    candidateData: null,
    acceptedObjects: [],
    selectedClasses: new Set(CLASS_CODES),
    classFilterMode: "all",
    discoveredClasses: new Map(),
    profileQueryActive: false,
    lineMaterials: new Set(),
    debugSequence: 0,
    traceRunId: 0,
    progressiveActive: false,
    featureOverlayScene: null,
    featureOverlayRenderHandler: null,
    waypointStart: null,
    waypointCloudId: null,
    waypointVertices: [],
    waypointSegmentCount: 0,
    waypointBusy: false,
    navPointerDown: null,
    panMode: false,
    panPointer: null,
  };

  const byId = (id) => document.getElementById(id);

  function setStatus(text) {
    byId("status").textContent = text;
  }

  function toast(text, timeout) {
    timeout = timeout || 3500;
    const el = byId("toast");
    el.textContent = text;
    el.classList.remove("hidden");
    window.clearTimeout(el._timer);
    el._timer = window.setTimeout(() => el.classList.add("hidden"), timeout);
  }

  function viewerSnapshot() {
    if (!state.viewer) return null;
    try {
      const camera = state.viewer.scene.getActiveCamera();
      return {
        camera_position: camera
          ? [camera.position.x, camera.position.y, camera.position.z]
          : null,
        edl: typeof state.viewer.getEDLEnabled === "function"
          ? state.viewer.getEDLEnabled()
          : null,
        point_budget: typeof state.viewer.getPointBudget === "function"
          ? state.viewer.getPointBudget()
          : null,
      };
    } catch (_) {
      return null;
    }
  }

  function debugLog(event, details, level) {
    if (!state.project || !state.project.id) return;
    const payload = {
      event: String(event),
      level: level || "INFO",
      source: "viewer",
      details: Object.assign({
        sequence: ++state.debugSequence,
        feature_mode: state.featureMode,
        profile: byId("profile") ? byId("profile").value : null,
        class_filter_mode: state.classFilterMode,
        selected_classes: Array.from(state.selectedClasses).sort((a, b) => a - b),
        viewer: viewerSnapshot(),
      }, details || {})
    };

    fetch(
      "/api/projects/" + encodeURIComponent(state.project.id) + "/debug-log",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }
    ).catch((error) => {
      console.warn("Falha ao escrever PROJECT_DEBUG.log", error);
    });
  }

  async function api(path, options) {
    options = options || {};
    const response = await fetch(path, Object.assign({}, options, {
      headers: Object.assign(
        { "Content-Type": "application/json" },
        options.headers || {}
      )
    }));

    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail || body.error || JSON.stringify(body);
      } catch (_) {}
      throw new Error(detail);
    }
    return response.json();
  }


  function updateClassActionButtons(mode) {
    ["classAll", "classGround", "classNone"].forEach((id) => {
      const el = byId(id);
      if (el) el.classList.remove("active");
    });
    const active = mode === "ground"
      ? "classGround"
      : mode === "none"
        ? "classNone"
        : mode === "all"
          ? "classAll"
          : null;
    if (active && byId(active)) byId(active).classList.add("active");
  }

  function classificationCodesForUi() {
    return Array.from(
      new Set([...CLASS_CODES, ...state.discoveredClasses.keys()])
    ).sort((a, b) => a - b);
  }

  function renderClassificationList() {
    const box = byId("classificationList");
    if (!box) return;
    box.innerHTML = "";

    for (const code of classificationCodesForUi()) {
      const row = document.createElement("label");
      row.className = "class-row";

      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked =
        state.classFilterMode === "all" || state.selectedClasses.has(code);
      input.dataset.classCode = String(code);
      input.onchange = () => {
        state.classFilterMode = "custom";
        if (input.checked) state.selectedClasses.add(code);
        else state.selectedClasses.delete(code);
        updateClassActionButtons("custom");
        applyClassificationVisibility();
        debugLog("classification.custom_changed", {
          class_code: code,
          visible: input.checked
        });
      };

      const codeEl = document.createElement("span");
      codeEl.className = "class-code";
      codeEl.textContent = String(code);

      const nameEl = document.createElement("span");
      const count = state.discoveredClasses.get(code);
      const name = CLASS_NAMES[code] || ("Classe " + code);
      nameEl.textContent =
        name + (count ? " · " + count.toLocaleString("pt-PT") : "");

      row.appendChild(input);
      row.appendChild(codeEl);
      row.appendChild(nameEl);
      box.appendChild(row);
    }
  }

  function safeClassificationColor(code) {
    const palette = [
      [0.55, 0.55, 0.55, 1.0],
      [0.72, 0.72, 0.72, 1.0],
      [0.63, 0.32, 0.18, 1.0],
      [0.20, 0.85, 0.20, 1.0],
      [0.10, 0.70, 0.20, 1.0],
      [0.00, 0.55, 0.12, 1.0],
      [1.00, 0.66, 0.00, 1.0],
      [1.00, 0.00, 1.00, 1.0],
      [1.00, 0.00, 0.00, 1.0],
      [0.00, 0.30, 1.00, 1.0],
      [0.70, 0.70, 0.15, 1.0],
      [0.55, 0.42, 0.30, 1.0],
      [1.00, 1.00, 0.00, 1.0],
      [0.90, 0.45, 0.15, 1.0],
      [0.80, 0.35, 0.15, 1.0],
      [0.75, 0.22, 0.22, 1.0],
      [0.55, 0.25, 0.75, 1.0],
      [0.25, 0.65, 0.85, 1.0],
      [0.95, 0.25, 0.25, 1.0]
    ];
    return palette[Math.abs(Number(code) || 0) % palette.length].slice();
  }

  function ensureClassificationDefinition(code) {
    if (!state.viewer) return;

    const classifications = state.viewer.classifications;
    let item = classifications[code];

    if (!item) {
      item = {
        visible: true,
        name: CLASS_NAMES[code] || ("Classe " + code),
        color: safeClassificationColor(code)
      };
      classifications[code] = item;
    } else {
      if (!item.color || item.color.length < 4) {
        item.color = safeClassificationColor(code);
      }
      if (typeof item.visible !== "boolean") {
        item.visible = true;
      }
      if (!item.name) {
        item.name = CLASS_NAMES[code] || ("Classe " + code);
      }
    }
  }

  function applyClassificationVisibility() {
    if (!state.viewer) return;

    const numericKeys = Object.keys(state.viewer.classifications || {})
      .filter((key) => key !== "DEFAULT" && Number.isFinite(Number(key)))
      .map(Number);

    const codes = Array.from(
      new Set([...numericKeys, ...classificationCodesForUi()])
    );

    // Potree's PointCloudMaterial.recomputeClassification() assumes every
    // explicit class has a RGBA color. setClassificationVisibility() creates
    // missing entries WITHOUT a color, which can crash rendering. Define
    // complete entries first.
    for (const code of codes) {
      ensureClassificationDefinition(code);

      const visible = state.classFilterMode === "all"
        ? true
        : state.selectedClasses.has(code);

      state.viewer.setClassificationVisibility(code, visible);
    }

    if (!state.viewer.classifications.DEFAULT) {
      state.viewer.classifications.DEFAULT = {
        visible: true,
        name: "default",
        color: [0.3, 0.6, 0.6, 0.5]
      };
    } else if (!state.viewer.classifications.DEFAULT.color) {
      state.viewer.classifications.DEFAULT.color = [0.3, 0.6, 0.6, 0.5];
    }

    state.viewer.setClassificationVisibility(
      "DEFAULT",
      state.classFilterMode === "all"
    );

    renderClassificationList();
  }

  function setClassificationPreset(mode) {
    state.classFilterMode = mode;

    if (mode === "ground") {
      state.selectedClasses = new Set([2]);
      setMaterialMode("classification");
    } else if (mode === "none") {
      state.selectedClasses = new Set();
    } else {
      state.selectedClasses = new Set(CLASS_CODES);
    }

    updateClassActionButtons(mode);
    applyClassificationVisibility();
    debugLog("classification.preset", {
      preset: mode,
      selected_classes: Array.from(state.selectedClasses).sort((a, b) => a - b)
    });
  }


  function noteClassification(code) {
    const key = Number(code);
    if (!Number.isFinite(key) || key < 0) return;
    state.discoveredClasses.set(
      key,
      (state.discoveredClasses.get(key) || 0) + 1
    );
  }

  function selectedClassesForEngine() {
    const control = byId("engineUseVisibleClasses");
    if (!control || !control.checked) return null;
    if (state.classFilterMode === "all") return null;
    return Array.from(state.selectedClasses).sort((a, b) => a - b);
  }

  function initViewer() {
    if (typeof Potree === "undefined") {
      toast("Potree não encontrado. Execute bootstrap_vendor.ps1.", 9000);
      setStatus("Potree em falta.");
      return;
    }

    const viewer = new Potree.Viewer(byId("potree_render_area"));
    viewer.setEDLEnabled(true);
    viewer.setFOV(60);
    viewer.setPointBudget(7500000);
    viewer.setBackground("black");

    // Navegação CAD simples:
    // - arrastar com botão esquerdo = rodar/orbitar
    // - botão direito = deslocar
    // - roda = zoom
    // EarthControls é ótimo para navegação geográfica, mas para inspecionar
    // taludes em 3D o OrbitControls é muito mais previsível.
    if (viewer.orbitControls) {
      viewer.setControls(viewer.orbitControls);
      if ("rotationSpeed" in viewer.orbitControls) {
        viewer.orbitControls.rotationSpeed = 6.0;
      }
      if ("fadeFactor" in viewer.orbitControls) {
        viewer.orbitControls.fadeFactor = 18.0;
      }
    } else {
      viewer.setControls(viewer.earthControls);
    }
    viewer.setMinNodeSize(30);

    state.viewer = viewer;

    viewer.loadGUI(() => {
      viewer.setLanguage("en");
      const sidebar = document.getElementById("potree_sidebar_container");
      if (sidebar) sidebar.style.display = "none";
    });

    // Potree renders EDL point clouds in a screen-space post-process after
    // viewer.scene.scene. Feature lines placed in the normal scene can therefore
    // be visually overwritten by the EDL pass even with depthTest=false.
    //
    // Potree's own MeasuringTool solves this by rendering a dedicated scene in
    // render.pass.perspective_overlay, after Potree clears the depth buffer.
    // Feature Lines use the same native render stage from Alpha 8.1 onward.
    state.featureOverlayScene = new THREE.Scene();
    state.featureOverlayScene.name = "scene_cloud_to_lines_feature_overlay";

    state.featureOverlayRenderHandler = () => {
      if (!state.viewer || !state.featureOverlayScene) return;
      const camera = state.viewer.scene.getActiveCamera();
      state.viewer.renderer.render(state.featureOverlayScene, camera);
    };

    viewer.addEventListener(
      "render.pass.perspective_overlay",
      state.featureOverlayRenderHandler
    );

    viewer.renderer.domElement.addEventListener("mousedown", onViewerNavMouseDown, true);
    viewer.renderer.domElement.addEventListener("mousemove", onViewerNavMouseMove, true);
    viewer.renderer.domElement.addEventListener("mouseup", onViewerNavMouseUp, true);
    viewer.renderer.domElement.addEventListener("mouseleave", onViewerNavMouseUp, true);
    viewer.addEventListener("update", updateWideLineResolution);
    setStatus("Potree pronto · Talude V1.1.6 · AUTO 1.1.2 + face clicada");
  }

  function configurePointcloud(pointcloud) {
    const m = pointcloud.material;
    m.size = 1;
    m.minSize = 2;
    m.maxSize = 12;
    m.pointSizeType = Potree.PointSizeType.ATTENUATED;
    m.shape = Potree.PointShape.CIRCLE;
  }

  function setMaterialMode(mode) {
    for (const pointcloud of state.pointclouds.values()) {
      const m = pointcloud.material;
      if (mode === "rgb") {
        m.activeAttributeName = "rgba";
      } else if (mode === "elevation") {
        m.activeAttributeName = "elevation";
      } else if (mode === "classification") {
        m.activeAttributeName = "classification";
      }
    }

    ["rgbMode", "elevationMode", "classificationMode"].forEach((id) => {
      byId(id).classList.remove("active");
    });

    const activeId =
      mode === "rgb"
        ? "rgbMode"
        : mode === "elevation"
          ? "elevationMode"
          : "classificationMode";

    byId(activeId).classList.add("active");
    debugLog("viewer.material_mode", { mode: mode });
  }

  async function loadCloud(projectId, cloud) {
    if (!state.viewer) throw new Error("Viewer Potree indisponível.");
    if (state.pointclouds.has(cloud.id)) return;

    const url =
      "/api/cloud-data/" +
      encodeURIComponent(projectId) +
      "/" +
      encodeURIComponent(cloud.id) +
      "/metadata.json";

    setStatus("A carregar " + cloud.name + "…");
    debugLog("cloud.load.started", {
      cloud_id: cloud.id,
      cloud_name: cloud.name,
      point_count: Number(cloud.point_count || 0)
    });

    await new Promise((resolve, reject) => {
      Potree.loadPointCloud(url, cloud.name, (event) => {
        try {
          if (!event || !event.pointcloud) {
            reject(new Error("Potree não devolveu pointcloud."));
            return;
          }

          const pc = event.pointcloud;
          configurePointcloud(pc);
          pc.visible = true;
          if (pc.material) {
            pc.material.opacity = 1.0;
            const hasRgba = typeof pc.getAttribute === "function" &&
              (pc.getAttribute("rgba") || pc.getAttribute("rgb"));
            if (hasRgba) pc.material.activeAttributeName = pc.getAttribute("rgba") ? "rgba" : "rgb";
          }

          state.viewer.scene.addPointCloud(pc);
          state.pointclouds.set(cloud.id, pc);

          // Apply classification only after the point cloud is fully registered.
          applyClassificationVisibility();

          renderCloudList();
          byId("emptyState").classList.add("hidden");
          byId("traceButton").disabled = false;

          // Fit again after Potree has had render frames to build its scene nodes.
          state.viewer.fitToScreen(0.8);
          window.requestAnimationFrame(() => {
            try { state.viewer.fitToScreen(0.8); } catch (_) {}
          });
          window.setTimeout(() => {
            try { state.viewer.fitToScreen(0.8); } catch (_) {}
          }, 350);

          setStatus(cloud.name + " carregada · LOD dinâmico");
          debugLog("cloud.load.completed", {
            cloud_id: cloud.id,
            cloud_name: cloud.name,
            point_count: Number(cloud.point_count || 0),
            active_attribute: pc.material ? pc.material.activeAttributeName : null
          });
          resolve();
        } catch (error) {
          debugLog("cloud.load.failed", {
            cloud_id: cloud.id,
            cloud_name: cloud.name,
            error: String(error && error.stack ? error.stack : error)
          }, "ERROR");
          reject(error);
        }
      });
    });
  }

  function clearViewer() {
    if (!state.viewer) return;

    if (state.candidateObject) {
      removeCandidate();
    }

    if (state.featureOverlayScene) {
      for (const item of state.acceptedObjects) {
        try {
          state.featureOverlayScene.remove(item.object);
          disposeLineObject(item.object);
        } catch (_) {}
      }
    }
    state.acceptedObjects = [];
    resetWaypointSession();
    renderFeatureList();

    for (const pc of state.pointclouds.values()) {
      try {
        state.viewer.scene.scenePointCloud.remove(pc);
      } catch (_) {}
    }

    state.pointclouds.clear();
    renderCloudList();
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function renderCloudList() {
    const box = byId("cloudList");
    box.innerHTML = "";
    if (!state.project) return;

    for (const cloud of state.project.clouds || []) {
      const el = document.createElement("div");
      el.className = "layer";
      const loaded = state.pointclouds.has(cloud.id);
      const pts = Number(cloud.point_count || 0).toLocaleString("pt-PT");

      el.innerHTML =
        "<strong>" +
        escapeHtml(cloud.name) +
        "</strong>" +
        pts +
        " pontos · " +
        (loaded ? "visível" : "disponível");

      el.onclick = () => {
        loadCloud(state.project.id, cloud).catch((e) => toast(e.message, 8000));
      };

      box.appendChild(el);
    }
  }

  async function activateProject(project) {
    clearViewer();
    state.project = project;

    byId("projectName").textContent = project.name + " · " + project.path;
    byId("addCloud").disabled = false;
    byId("addMdt").disabled = false;
    byId("addSlope").disabled = false;
    renderCloudList();
    renderTerrainStatus();
    debugLog("project.activated_in_viewer", {
      project_name: project.name,
      project_path: project.path,
      cloud_count: (project.clouds || []).length,
      terrain_raster_ready: terrainRasterReady(),
      terrain: project.terrain || {},
      feature_render_stage: "render.pass.perspective_overlay",
      feature_overlay_scene: Boolean(state.featureOverlayScene)
    });

    if ((project.clouds || []).length) {
      await loadCloud(project.id, project.clouds[0]);
    } else {
      byId("emptyState").classList.remove("hidden");
    }
  }

  function initDesktopBridge() {
    return new Promise((resolve) => {
      if (typeof QWebChannel === "undefined" ||
          !window.qt ||
          !window.qt.webChannelTransport) {
        resolve(null);
        return;
      }

      new QWebChannel(window.qt.webChannelTransport, (channel) => {
        desktopBridge = channel.objects.desktopBridge || null;
        resolve(desktopBridge);
      });
    });
  }

  function bridgeCall(method, ...args) {
    return new Promise((resolve, reject) => {
      if (!desktopBridge || typeof desktopBridge[method] !== "function") {
        reject(new Error("Bridge desktop indisponível."));
        return;
      }

      try {
        desktopBridge[method](...args, (result) => resolve(result || ""));
      } catch (error) {
        reject(error);
      }
    });
  }

  async function createProject() {
    const parent = await bridgeCall("choose_project_parent");
    if (!parent) return;

    const proposed = window.prompt("Nome do projeto:", "Novo_Projeto");
    if (!proposed) return;

    const project = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({
        parent_folder: parent,
        name: proposed
      })
    });

    await activateProject(project);
    toast("Projeto criado.");
  }

  async function openProject() {
    const path = await bridgeCall("choose_project_folder");
    if (!path) return;

    const project = await api("/api/projects/open", {
      method: "POST",
      body: JSON.stringify({ path: path })
    });

    await activateProject(project);
  }

  async function addCloud() {
    if (!state.project) return;

    const source = await bridgeCall("choose_cloud");
    if (!source) return;

    const result = await api("/api/clouds/import", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.project.id,
        source_path: source
      })
    });

    monitorJob(result.job_id).catch((e) => toast(e.message, 10000));
  }

  function terrainRasterReady() {
    const terrain = state.project && state.project.terrain;
    return Boolean(
      terrain &&
      terrain.mdt &&
      terrain.mdt.path
    );
  }

  function renderTerrainStatus() {
    const el = byId("terrainStatus");
    if (!el) return;

    if (!state.project) {
      el.textContent = "Raster terreno: não carregado.";
      return;
    }

    const terrain = state.project.terrain || {};
    const mdt = terrain.mdt || null;
    const slope = terrain.slope || null;

    if (mdt && slope) {
      const res = Array.isArray(mdt.resolution)
        ? Math.max(Number(mdt.resolution[0]), Number(mdt.resolution[1]))
        : null;
      el.textContent =
        "Raster terreno pronto · MDT: " + (mdt.name || "carregado") +
        " · Declive: " + (slope.name || "carregado") +
        (Number.isFinite(res) ? " · " + res.toFixed(3) + " m/px" : "");
      return;
    }

    if (mdt) {
      const res = Array.isArray(mdt.resolution)
        ? Math.max(Number(mdt.resolution[0]), Number(mdt.resolution[1]))
        : null;
      el.textContent =
        "Raster terreno pronto · MDT: " + (mdt.name || "carregado") +
        " · Declive automático calculado do MDT" +
        (Number.isFinite(res) ? " · " + res.toFixed(3) + " m/px" : "") +
        " · + Declive é opcional";
      return;
    }

    if (slope) {
      el.textContent = "Declive carregado · falta o MDT GeoTIFF.";
      return;
    }

    el.textContent = "Raster terreno: carregue MDT + Declive.";
  }

  async function addTerrainRaster(kind) {
    if (!state.project) return;

    const method = kind === "mdt" ? "choose_mdt" : "choose_slope";
    const source = await bridgeCall(method);
    if (!source) return;

    setStatus(
      kind === "mdt"
        ? "A registar MDT GeoTIFF…"
        : "A registar mapa de declives…"
    );

    const response = await api(
      "/api/projects/" +
        encodeURIComponent(state.project.id) +
        "/terrain-raster",
      {
        method: "POST",
        body: JSON.stringify({
          kind: kind,
          source_path: source
        })
      }
    );

    state.project = response.project;
    renderTerrainStatus();

    debugLog("terrain.raster_loaded", {
      kind: kind,
      path: response.raster ? response.raster.path : source,
      metadata: response.raster || null,
      terrain_raster_ready: terrainRasterReady()
    });

    toast(
      kind === "mdt"
        ? "MDT registado no projeto."
        : "Mapa de declives registado no projeto."
    );

    setStatus(
      terrainRasterReady()
        ? (
            (state.project.terrain || {}).slope
              ? "Talude V1.1.6 · motor MDT + Declive pronto."
              : "Talude V1.1.6 · MDT pronto · declive será calculado automaticamente."
          )
        : "Raster registado · falta o MDT GeoTIFF."
    );
  }

  async function traceRasterTerrain(hit) {
    const started = performance.now();
    const runId = ++state.traceRunId;
    const seed = hit.location.clone();
    const cloudId = findCloudIdForHit(hit);
    const profile = byId("profile").value;

    if (!state.project || !cloudId) {
      throw new Error("Projeto ou nuvem ativa não encontrados.");
    }
    if (!terrainRasterReady()) {
      throw new Error("Carregue o MDT GeoTIFF.");
    }

    removeCandidate();
    drawSeedMarker(seed);

    setStatus(
      (state.project.terrain || {}).slope
        ? "Talude V1.1.6 · a ler MDT + Declive…"
        : "Talude V1.1.6 · a ler MDT e calcular Declive automaticamente…"
    );
    byId("traceHint").textContent =
      "Motor raster: a identificar a face inteira do talude e a sua " +
      (profile === "ridge" ? "crista" : "base") +
      "…";

    debugLog("raster_terrain.started", {
      cloud_id: cloudId,
      profile: profile,
      mode: state.featureMode,
      seed: [seed.x, seed.y, seed.z],
      terrain: state.project.terrain || {}
    });

    const result = await api("/api/feature-lines/raster-terrain", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.project.id,
        cloud_id: cloudId,
        profile: profile,
        seed: [seed.x, seed.y, seed.z],
        mode: state.featureMode
      })
    });

    if (runId !== state.traceRunId) return;

    result.seed = [seed.x, seed.y, seed.z];
    result.cloud_id = cloudId;
    result.feature_mode = state.featureMode;
    result.trace_elapsed_ms =
      Math.round((performance.now() - started) * 10) / 10;

    drawCandidate(result);

    const confidence = Math.round(Number(result.confidence || 0) * 100);
    const snap = Math.round(Number(result.snap_ratio || 0) * 100);
    const length = Number(result.length_m || 0);

    byId("traceHint").textContent =
      (profile === "ridge" ? "Crista" : "Pé") +
      " raster · " +
      length.toFixed(1) + " m · " +
      result.vertices.length + " vértices · edge-lock " +
      snap + "% · confiança " + confidence + "%" +
      (result.slope_reprojected
        ? " · CRS do declive alinhado automaticamente"
        : "") +
      (Number.isFinite(Number(result.seed_to_edge_distance_m))
        ? " · clique→aresta " +
          Number(result.seed_to_edge_distance_m).toFixed(1) + " m"
        : "") +
      " · " + result.trace_elapsed_ms.toFixed(0) +
      " ms. Aceite ou rejeite.";

    setStatus(
      "Talude V1.1.6 · raster-terrain · " +
      result.vertices.length + " vértices · " +
      length.toFixed(1) + " m"
    );

    debugLog("raster_terrain.completed", {
      cloud_id: cloudId,
      profile: profile,
      mode: state.featureMode,
      seed: result.seed,
      source: result.source,
      slope_source: result.slope_source,
      source_crs: result.source_crs,
      mdt_crs: result.mdt_crs,
      slope_source_crs: result.slope_source_crs,
      slope_analysis_crs: result.slope_analysis_crs,
      slope_reprojected: result.slope_reprojected,
      analysis_resolution_m: result.analysis_resolution_m,
      face_cells: result.face_cells,
      seed_to_edge_distance_m: result.seed_to_edge_distance_m,
      edge_bridged: result.edge_bridged,
      raw_vertices: result.raw_vertices,
      snapped_vertices: result.snapped_vertices,
      snap_ratio: result.snap_ratio,
      median_snap_offset_m: result.median_snap_offset_m,
      simplified_vertices: result.vertices.length,
      length_m: result.length_m,
      confidence: result.confidence,
      elapsed_ms: result.trace_elapsed_ms
    });
  }

  async function monitorJob(jobId) {
    byId("jobBox").classList.remove("hidden");
    let lastDebugState = "";

    while (true) {
      const job = await api("/api/jobs/" + encodeURIComponent(jobId));
      byId("jobText").textContent = job.error || job.message || job.status;
      byId("jobProgress").style.width = String(job.progress || 0) + "%";

      const debugState = [job.status, job.progress, job.message, job.error].join("|");
      if (debugState !== lastDebugState) {
        lastDebugState = debugState;
        debugLog("cloud.import.job_status", {
          job_id: jobId,
          status: job.status,
          progress: job.progress,
          message: job.message,
          error: job.error || null
        }, job.status === "failed" ? "ERROR" : "INFO");
      }

      if (job.status === "completed") {
        state.project = await api(
          "/api/projects/" + encodeURIComponent(state.project.id)
        );

        renderCloudList();

        const cloud = state.project.clouds.find(
          (c) => c.id === job.result.cloud_id
        );

        if (cloud) await loadCloud(state.project.id, cloud);

        toast("Conversão Potree concluída.");
        window.setTimeout(() => byId("jobBox").classList.add("hidden"), 1200);
        return;
      }

      if (job.status === "failed") {
        toast(job.error || "Falha na importação.", 10000);
        return;
      }

      await new Promise((resolve) => window.setTimeout(resolve, 650));
    }
  }



  function extractProfileBatch(pointcloud, profileData, selectedSet, sink, classSink, maxPoints) {
    for (const segment of profileData.segments || []) {
      const data = segment.points && segment.points.data;
      if (!data || !data.position) continue;

      const positions = data.position;
      const classes = data.classification || null;
      const count = Math.floor(positions.length / 3);

      for (let i = 0; i < count; i++) {
        if (sink.length >= maxPoints) return;

        const cls = classes ? Number(classes[i]) : -1;
        if (cls >= 0) noteClassification(cls);

        if (selectedSet && classes && !selectedSet.has(cls)) {
          continue;
        }

        sink.push([
          Number(positions[3 * i + 0]) + pointcloud.position.x,
          Number(positions[3 * i + 1]) + pointcloud.position.y,
          Number(positions[3 * i + 2]) + pointcloud.position.z
        ]);
        classSink.push(cls);
      }
    }
  }

  function requestProfileStrip(
    pointcloud,
    start,
    end,
    width,
    selectedClasses,
    maxPoints,
    options
  ) {
    options = options || {};
    const softTimeoutMs = Number(options.softTimeoutMs || 9000);
    const hardTimeoutMs = Number(options.hardTimeoutMs || 12000);
    const earlyMinPoints = Number(options.earlyMinPoints || 0);
    const earlyAfterMs = Number(options.earlyAfterMs || 0);
    const requestStarted = performance.now();

    return new Promise((resolve, reject) => {
      const points = [];
      const classifications = [];
      const selectedSet = selectedClasses === null
        ? null
        : new Set(selectedClasses);

      const profile = {
        points: [start.clone(), end.clone()],
        width: width
      };

      let settled = false;
      let request = null;
      let cancelRequested = false;

      const finish = (reason) => {
        if (settled) return;
        settled = true;
        resolve({
          points: points.slice(),
          classifications: classifications.slice(),
          finish_reason: reason || "finish",
          elapsed_ms: Math.round((performance.now() - requestStarted) * 10) / 10
        });
      };

      const cancelAndFinish = (reason, eventRequest) => {
        if (settled) return;
        cancelRequested = true;
        const activeRequest = eventRequest || request;
        if (activeRequest) {
          try { activeRequest.finishLevelThenCancel(); } catch (_) {}
        }
        finish(reason);
      };

      try {
        request = pointcloud.getPointsInProfile(profile, null, {
          onProgress: (event) => {
            if (settled) return;

            extractProfileBatch(
              pointcloud,
              event.points,
              selectedSet,
              points,
              classifications,
              maxPoints
            );

            const elapsed = performance.now() - requestStarted;
            const enoughForEarlyFinish =
              earlyMinPoints > 0 &&
              points.length >= earlyMinPoints &&
              elapsed >= earlyAfterMs;

            if (points.length >= maxPoints) {
              cancelAndFinish("max_points", event.request);
              return;
            }

            if (enoughForEarlyFinish) {
              cancelAndFinish("early_density", event.request);
            }
          },
          onFinish: () => finish("potree_finish"),
          onCancel: () => finish("potree_cancel")
        });
      } catch (error) {
        reject(error);
        return;
      }

      window.setTimeout(() => {
        if (!settled) cancelAndFinish("soft_timeout", request);
      }, softTimeoutMs);

      window.setTimeout(() => {
        if (!settled) finish("hard_timeout");
      }, hardTimeoutMs);
    });
  }

  async function collectFullDensityNeighborhood(pointcloud, seed, selectedClasses) {
    if (!pointcloud) throw new Error("Point cloud Potree indisponível.");

    const started = performance.now();
    state.profileQueryActive = true;
    debugLog("profile_request.started", {
      seed: [seed.x, seed.y, seed.z],
      selected_classes: selectedClasses,
      half_m: 12.0,
      width_m: 5.0,
      max_per_strip: 70000,
      max_combined: 120000
    });
    try {
      // Two perpendicular ProfileRequests form a local cross-shaped spatial
      // query around the clicked feature. Unlike visibleNodes this explicitly
      // traverses/loads intersecting octree nodes, so the motor is independent
      // of camera zoom and current LOD visibility.
      const half = 12.0;
      const width = 5.0;
      const maxPerStrip = 70000;

      const horizontal = requestProfileStrip(
        pointcloud,
        new THREE.Vector3(seed.x - half, seed.y, seed.z),
        new THREE.Vector3(seed.x + half, seed.y, seed.z),
        width,
        selectedClasses,
        maxPerStrip,
        { softTimeoutMs: 5500, hardTimeoutMs: 8000 }
      );

      const vertical = requestProfileStrip(
        pointcloud,
        new THREE.Vector3(seed.x, seed.y - half, seed.z),
        new THREE.Vector3(seed.x, seed.y + half, seed.z),
        width,
        selectedClasses,
        maxPerStrip,
        { softTimeoutMs: 5500, hardTimeoutMs: 8000 }
      );

      const batches = await Promise.all([horizontal, vertical]);
      const points = [];
      const classifications = [];
      const seen = new Set();

      for (const batch of batches) {
        for (let i = 0; i < batch.points.length; i++) {
          const p = batch.points[i];
          const key =
            Math.round(p[0] * 1000) + ":" +
            Math.round(p[1] * 1000) + ":" +
            Math.round(p[2] * 1000);

          if (seen.has(key)) continue;
          seen.add(key);
          points.push(p);
          classifications.push(batch.classifications[i]);
          if (points.length >= 120000) break;
        }
        if (points.length >= 120000) break;
      }

      renderClassificationList();
      debugLog("profile_request.completed", {
        seed: [seed.x, seed.y, seed.z],
        selected_classes: selectedClasses,
        horizontal_points: batches[0].points.length,
        vertical_points: batches[1].points.length,
        deduplicated_points: points.length,
        elapsed_ms: Math.round((performance.now() - started) * 10) / 10
      });
      return { points, classifications };
    } catch (error) {
      debugLog("profile_request.failed", {
        seed: [seed.x, seed.y, seed.z],
        selected_classes: selectedClasses,
        elapsed_ms: Math.round((performance.now() - started) * 10) / 10,
        error: String(error && error.stack ? error.stack : error)
      }, "ERROR");
      throw error;
    } finally {
      state.profileQueryActive = false;
    }
  }


  function terrainClassesForEngine() {
    if (state.discoveredClasses.has(2) || state.discoveredClasses.size === 0) {
      return [2];
    }
    return selectedClassesForEngine();
  }

  function terrainTileOptions(selectedClasses, compact) {
    const groundOnly =
      Array.isArray(selectedClasses) &&
      selectedClasses.length === 1 &&
      Number(selectedClasses[0]) === 2;

    if (compact) {
      return {
        half: 9.5,
        maxPoints: groundOnly ? 9000 : 14000,
        earlyMinPoints: groundOnly ? 1400 : 2600,
        earlyAfterMs: 160,
        softTimeoutMs: 650,
        hardTimeoutMs: 1150
      };
    }

    return {
      half: 15.0,
      maxPoints: groundOnly ? 18000 : 24000,
      earlyMinPoints: groundOnly ? 2600 : 4800,
      earlyAfterMs: 180,
      softTimeoutMs: 850,
      hardTimeoutMs: 1500
    };
  }

  async function collectTerrainTile(
    pointcloud,
    seed,
    selectedClasses,
    options
  ) {
    options = options || {};
    const compact = Boolean(options.compact);
    const tuning = terrainTileOptions(selectedClasses, compact);
    const half = Number(options.half || tuning.half);
    const width = half * 2.0;
    const maxPoints = Number(
      options.maxPoints ||
      (half >= 40 ? 60000 : half >= 25 ? 36000 : tuning.maxPoints)
    );
    const earlyMinPoints = Number(
      options.earlyMinPoints ||
      Math.min(Math.max(tuning.earlyMinPoints, Math.round(maxPoints * 0.18)), 9000)
    );
    const softTimeoutMs = Number(
      options.softTimeoutMs ||
      (half >= 40 ? 2600 : half >= 25 ? 1900 : tuning.softTimeoutMs)
    );
    const hardTimeoutMs = Number(
      options.hardTimeoutMs ||
      (half >= 40 ? 4200 : half >= 25 ? 3200 : tuning.hardTimeoutMs)
    );
    const started = performance.now();

    debugLog("terrain_face.tile_started", {
      seed: [seed.x, seed.y, seed.z],
      selected_classes: selectedClasses,
      half_m: half,
      width_m: width,
      max_points: maxPoints,
      compact: compact
    });

    let batch = await requestProfileStrip(
      pointcloud,
      new THREE.Vector3(seed.x - half, seed.y, seed.z),
      new THREE.Vector3(seed.x + half, seed.y, seed.z),
      width,
      selectedClasses,
      maxPoints,
      {
        earlyMinPoints: earlyMinPoints,
        earlyAfterMs: tuning.earlyAfterMs,
        softTimeoutMs: softTimeoutMs,
        hardTimeoutMs: hardTimeoutMs
      }
    );

    const optimisticGround =
      Array.isArray(selectedClasses) &&
      selectedClasses.length === 1 &&
      Number(selectedClasses[0]) === 2;

    if (batch.points.length < 500 &&
        optimisticGround &&
        !state.discoveredClasses.has(2)) {
      const fallbackClasses = selectedClassesForEngine();
      const fallbackTuning = terrainTileOptions(fallbackClasses, compact);

      debugLog("terrain_face.tile_ground_fallback", {
        seed: [seed.x, seed.y, seed.z],
        first_points: batch.points.length,
        first_finish_reason: batch.finish_reason,
        fallback_classes: fallbackClasses
      });

      batch = await requestProfileStrip(
        pointcloud,
        new THREE.Vector3(seed.x - half, seed.y, seed.z),
        new THREE.Vector3(seed.x + half, seed.y, seed.z),
        width,
        fallbackClasses,
        fallbackTuning.maxPoints,
        {
          earlyMinPoints: fallbackTuning.earlyMinPoints,
          earlyAfterMs: fallbackTuning.earlyAfterMs,
          softTimeoutMs: fallbackTuning.softTimeoutMs,
          hardTimeoutMs: fallbackTuning.hardTimeoutMs
        }
      );
      batch.selected_classes = fallbackClasses;
    } else {
      batch.selected_classes = selectedClasses;
    }

    debugLog("terrain_face.tile_completed", {
      seed: [seed.x, seed.y, seed.z],
      selected_classes: batch.selected_classes,
      points: batch.points.length,
      finish_reason: batch.finish_reason,
      query_elapsed_ms: batch.elapsed_ms,
      elapsed_ms: Math.round((performance.now() - started) * 10) / 10,
      compact: compact
    });

    return batch;
  }

  function terrainEndpointDirection(vertices, side) {
    if (!Array.isArray(vertices) || vertices.length < 2) return null;
    const span = Math.min(3, vertices.length - 1);
    let a;
    let b;

    if (side === "left") {
      a = vertices[span];
      b = vertices[0];
    } else {
      a = vertices[vertices.length - 1 - span];
      b = vertices[vertices.length - 1];
    }

    const dx = Number(b[0]) - Number(a[0]);
    const dy = Number(b[1]) - Number(a[1]);
    const length = Math.hypot(dx, dy);
    if (!Number.isFinite(length) || length < 0.25) return null;
    return [dx / length, dy / length];
  }

  function distanceXY(a, b) {
    return Math.hypot(
      Number(a[0]) - Number(b[0]),
      Number(a[1]) - Number(b[1])
    );
  }

  function simplifyTerrainVertices(vertices, epsilon) {
    const pts = (vertices || []).map((v) => [
      Number(v[0]), Number(v[1]), Number(v[2])
    ]);
    if (pts.length <= 2) return pts;

    const keep = new Array(pts.length).fill(false);
    keep[0] = true;
    keep[pts.length - 1] = true;
    const stack = [[0, pts.length - 1]];
    const eps = Number(epsilon || 0.32);

    while (stack.length) {
      const pair = stack.pop();
      const start = pair[0];
      const end = pair[1];
      if (end <= start + 1) continue;

      const a = pts[start];
      const b = pts[end];
      const vx = b[0] - a[0];
      const vy = b[1] - a[1];
      const denom = vx * vx + vy * vy;

      let bestDistance = -1;
      let bestIndex = -1;

      for (let i = start + 1; i < end; i++) {
        let distance;
        if (denom <= 1e-12) {
          distance = distanceXY(pts[i], a);
        } else {
          const wx = pts[i][0] - a[0];
          const wy = pts[i][1] - a[1];
          const t = Math.max(0, Math.min(1, (wx * vx + wy * vy) / denom));
          const px = a[0] + t * vx;
          const py = a[1] + t * vy;
          distance = Math.hypot(pts[i][0] - px, pts[i][1] - py);
        }

        if (distance > bestDistance) {
          bestDistance = distance;
          bestIndex = i;
        }
      }

      if (bestDistance > eps && bestIndex > start && bestIndex < end) {
        keep[bestIndex] = true;
        stack.push([start, bestIndex], [bestIndex, end]);
      }
    }

    const simplified = pts.filter((_, index) => keep[index]);
    const output = [simplified[0]];

    for (let i = 1; i < simplified.length; i++) {
      const target = simplified[i];
      const previous = output[output.length - 1];
      const span = distanceXY(previous, target);

      if (span <= 12.0) {
        output.push(target);
        continue;
      }

      const pieces = Math.ceil(span / 12.0);
      let startIndex = 0;
      let endIndex = 0;
      for (let j = 1; j < pts.length; j++) {
        if (distanceXY(pts[j], previous) < distanceXY(pts[startIndex], previous)) {
          startIndex = j;
        }
        if (distanceXY(pts[j], target) < distanceXY(pts[endIndex], target)) {
          endIndex = j;
        }
      }

      if (endIndex <= startIndex + 1) {
        output.push(target);
        continue;
      }

      for (let k = 1; k < pieces; k++) {
        const idx = Math.round(
          startIndex + (endIndex - startIndex) * (k / pieces)
        );
        if (idx > startIndex && idx < endIndex) output.push(pts[idx]);
      }
      output.push(target);
    }

    return output;
  }

  function chooseTerrainExtension(current, segment, side) {
    if (!Array.isArray(segment) || segment.length < 2) return null;

    const anchor =
      side === "left" ? current[0] : current[current.length - 1];
    const desired = terrainEndpointDirection(current, side);
    if (!desired) return null;

    const clean = segment.map((v) => [
      Number(v[0]), Number(v[1]), Number(v[2])
    ]);

    function metrics(point) {
      const dx = point[0] - Number(anchor[0]);
      const dy = point[1] - Number(anchor[1]);
      return {
        forward: dx * desired[0] + dy * desired[1],
        lateral: Math.abs(dx * desired[1] - dy * desired[0]),
        distance: Math.hypot(dx, dy),
        dz: Math.abs(Number(point[2]) - Number(anchor[2]))
      };
    }

    const orientations = [clean, clean.slice().reverse()];
    let best = null;

    for (const oriented of orientations) {
      let joinIndex = -1;
      let joinScore = Infinity;

      for (let i = 0; i < oriented.length; i++) {
        const m = metrics(oriented[i]);

        // Edge-lock rule: the next tile must overlap the SAME physical break.
        // Nearby terraces are usually several metres away laterally or in Z.
        if (m.forward < -2.5 || m.forward > 3.5) continue;
        if (m.lateral > 1.65) continue;
        if (m.dz > 1.20) continue;

        const score =
          m.distance +
          Math.abs(m.forward) * 0.16 +
          m.lateral * 0.90 +
          m.dz * 0.35;

        if (score < joinScore) {
          joinScore = score;
          joinIndex = i;
        }
      }

      if (joinIndex < 0) continue;

      const join = metrics(oriented[joinIndex]);
      if (join.distance > 2.75) continue;

      const tangentEnd = Math.min(
        oriented.length - 1,
        joinIndex + Math.max(1, Math.min(3, oriented.length - 1 - joinIndex))
      );
      if (tangentEnd <= joinIndex) continue;

      const tx =
        Number(oriented[tangentEnd][0]) -
        Number(oriented[joinIndex][0]);
      const ty =
        Number(oriented[tangentEnd][1]) -
        Number(oriented[joinIndex][1]);
      const tangentLength = Math.hypot(tx, ty);
      if (tangentLength < 0.60) continue;

      const localAlignment =
        (tx / tangentLength) * desired[0] +
        (ty / tangentLength) * desired[1];

      if (localAlignment < 0.35) continue;

      // Validate only the first few metres against the old tangent. After that
      // the new segment is allowed to follow the real vineyard curve.
      let cumulative = 0.0;
      let last = oriented[joinIndex];
      let firstSixMetresValid = true;

      for (let i = joinIndex + 1; i < oriented.length; i++) {
        const p = oriented[i];
        cumulative += distanceXY(last, p);
        last = p;

        if (cumulative <= 6.0) {
          const m = metrics(p);
          if (m.forward < -0.60 || m.lateral > 2.10 || m.dz > 1.80) {
            firstSixMetresValid = false;
            break;
          }
        }
      }

      if (!firstSixMetresValid) continue;

      let firstNew = joinIndex + 1;
      let arc = 0.0;
      let previous = oriented[joinIndex];

      while (firstNew < oriented.length) {
        arc += distanceXY(previous, oriented[firstNew]);
        previous = oriented[firstNew];

        const m = metrics(oriented[firstNew]);
        if (
          arc >= 0.90 &&
          m.forward >= 0.45 &&
          m.lateral <= 1.80 &&
          m.dz <= 1.50
        ) {
          break;
        }
        firstNew++;
      }

      if (firstNew >= oriented.length) continue;

      const path = [anchor].concat(oriented.slice(firstNew));
      const growth = polylineLength3D(path);
      if (growth < 1.8) continue;

      const end = path[path.length - 1];
      const endMetrics = metrics(end);

      const score =
        growth * 1.25 +
        localAlignment * 8.0 -
        join.distance * 2.0 -
        join.lateral * 2.5 -
        join.dz * 0.8;

      if (!best || score > best.score) {
        best = {
          score: score,
          extension: path,
          nearest_distance_m: join.distance,
          join_forward_m: join.forward,
          join_lateral_m: join.lateral,
          join_z_delta_m: join.dz,
          forward_growth_m: endMetrics.forward,
          alignment: localAlignment
        };
      }
    }

    if (!best) return null;

    return {
      side: side,
      extension: best.extension,
      nearest_distance_m: best.nearest_distance_m,
      join_forward_m: best.join_forward_m,
      join_lateral_m: best.join_lateral_m,
      join_z_delta_m: best.join_z_delta_m,
      forward_growth_m: best.forward_growth_m,
      alignment: best.alignment
    };
  }

  async function requestTerrainFaceExtension(
    pointcloud,
    cloudId,
    profile,
    selectedClasses,
    currentVertices,
    side,
    referenceSlope,
    round,
    runId
  ) {
    const anchor =
      side === "left"
        ? currentVertices[0].slice()
        : currentVertices[currentVertices.length - 1].slice();
    const direction = terrainEndpointDirection(currentVertices, side);
    if (!direction) return null;

    // The tile MUST overlap the current endpoint. Talude V1.1.6 used lead=6.5 m
    // and then demanded a <=3 m join, which made the two rules contradictory.
    // Two cheap attempts handle both normal and tighter curved terraces.
    const attempts = [
      { lead: 2.0, half: 12.5, label: "overlap-forward" },
      { lead: 0.6, half: 13.5, label: "anchor-recovery" }
    ];

    const started = performance.now();

    for (let attemptIndex = 0; attemptIndex < attempts.length; attemptIndex++) {
      if (runId !== state.traceRunId) return null;

      const attempt = attempts[attemptIndex];
      const seed = new THREE.Vector3(
        Number(anchor[0]) + direction[0] * attempt.lead,
        Number(anchor[1]) + direction[1] * attempt.lead,
        Number(anchor[2])
      );

      debugLog("terrain_face.progressive_requested", {
        round: round,
        side: side,
        attempt: attemptIndex + 1,
        strategy: attempt.label,
        anchor: anchor,
        direction: direction,
        lead_m: attempt.lead,
        tile_half_m: attempt.half,
        predicted_seed: [seed.x, seed.y, seed.z],
        reference_slope_deg: referenceSlope
      });

      let tile;
      let result;

      try {
        tile = await collectTerrainTile(
          pointcloud,
          seed,
          selectedClasses,
          { compact: true, half: attempt.half }
        );

        if (runId !== state.traceRunId) return null;
        if (tile.points.length < 350) {
          debugLog("terrain_face.progressive_attempt_rejected", {
            round: round,
            side: side,
            attempt: attemptIndex + 1,
            reason: "insufficient_points",
            points: tile.points.length
          });
          continue;
        }

        result = await api("/api/feature-lines/terrain-face", {
          method: "POST",
          body: JSON.stringify({
            project_id: state.project.id,
            cloud_id: cloudId,
            profile: profile,
            seed: [seed.x, seed.y, seed.z],
            points: tile.points,
            classifications: tile.classifications,
            selected_classes: tile.selected_classes,
            grid_resolution: 0.20
          })
        });
      } catch (error) {
        debugLog("terrain_face.progressive_attempt_failed", {
          round: round,
          side: side,
          attempt: attemptIndex + 1,
          strategy: attempt.label,
          error: String(error && error.message ? error.message : error)
        });
        continue;
      }

      if (runId !== state.traceRunId) return null;

      const slope = Number(result.face_slope_deg || 0);
      const slopeDelta = Math.abs(
        slope - Number(referenceSlope || slope)
      );

      const confidence = Number(result.confidence || 0);
      const snapRatio = Number(result.snap_ratio || 0);

      if (
        slopeDelta > 12.0 ||
        confidence < 0.52 ||
        snapRatio < 0.20
      ) {
        let reason = "weak_edge_lock";
        if (slopeDelta > 12.0) reason = "face_slope_mismatch";
        else if (confidence < 0.52) reason = "low_confidence";

        debugLog("terrain_face.progressive_attempt_rejected", {
          round: round,
          side: side,
          attempt: attemptIndex + 1,
          reason: reason,
          face_slope_deg: slope,
          reference_slope_deg: referenceSlope,
          confidence: confidence,
          snap_ratio: snapRatio,
          median_snap_offset_m: result.median_snap_offset_m,
          slope_delta_deg: slopeDelta
        });
        continue;
      }

      const prepared = chooseTerrainExtension(
        currentVertices,
        result.vertices,
        side
      );

      debugLog("terrain_face.progressive_result", {
        round: round,
        side: side,
        attempt: attemptIndex + 1,
        strategy: attempt.label,
        points: tile.points.length,
        finish_reason: tile.finish_reason,
        candidate_vertices: result.vertices.length,
        candidate_start: result.vertices[0] || null,
        candidate_end:
          result.vertices[result.vertices.length - 1] || null,
        accepted_extension: Boolean(prepared),
        nearest_distance_m:
          prepared ? prepared.nearest_distance_m : null,
        join_forward_m:
          prepared ? prepared.join_forward_m : null,
        join_lateral_m:
          prepared ? prepared.join_lateral_m : null,
        join_z_delta_m:
          prepared ? prepared.join_z_delta_m : null,
        forward_growth_m:
          prepared ? prepared.forward_growth_m : null,
        alignment:
          prepared ? prepared.alignment : null,
        face_slope_deg: result.face_slope_deg,
        confidence: result.confidence,
        snap_ratio: result.snap_ratio,
        median_snap_offset_m: result.median_snap_offset_m,
        elapsed_ms:
          Math.round((performance.now() - started) * 10) / 10
      });

      if (prepared) return prepared;
    }

    return null;
  }

  async function progressivelyExtendTerrainFace(
    pointcloud,
    cloudId,
    result,
    profile,
    selectedClasses,
    runId
  ) {
    if (state.featureMode === "single") return result;

    let vertices = result.vertices.map((v) => v.slice());
    const referenceSlope = Number(result.face_slope_deg || 0);
    let leftActive = vertices.length >= 2;
    let rightActive = vertices.length >= 2;
    let rounds = 0;

    const aggressive = state.featureMode === "multiple";
    const maxRounds = aggressive ? 10 : 6;
    const maxTotalLength = aggressive ? 240.0 : 150.0;

    debugLog("terrain_face.progressive_started", {
      profile: profile,
      mode: state.featureMode,
      initial_vertices: vertices.length,
      initial_length_m: polylineLength3D(vertices),
      reference_slope_deg: referenceSlope,
      max_rounds: maxRounds,
      max_total_length_m: maxTotalLength
    });

    for (let round = 1; round <= maxRounds; round++) {
      if (runId !== state.traceRunId) break;
      if (!leftActive && !rightActive) break;
      if (polylineLength3D(vertices) >= maxTotalLength) break;

      const jobs = [];

      if (leftActive) {
        jobs.push({
          side: "left",
          promise: requestTerrainFaceExtension(
            pointcloud,
            cloudId,
            profile,
            selectedClasses,
            vertices,
            "left",
            referenceSlope,
            round,
            runId
          )
        });
      }

      if (rightActive) {
        jobs.push({
          side: "right",
          promise: requestTerrainFaceExtension(
            pointcloud,
            cloudId,
            profile,
            selectedClasses,
            vertices,
            "right",
            referenceSlope,
            round,
            runId
          )
        });
      }

      const settled = await Promise.all(
        jobs.map(async (job) => {
          try {
            return {
              side: job.side,
              value: await job.promise,
              error: null
            };
          } catch (error) {
            return {
              side: job.side,
              value: null,
              error: error
            };
          }
        })
      );

      if (runId !== state.traceRunId) break;

      let changed = false;
      const info = {};

      for (const item of settled) {
        if (item.error || !item.value) {
          if (item.side === "left") leftActive = false;
          else rightActive = false;

          info[item.side] = {
            extended: false,
            error: item.error
              ? String(item.error.message || item.error)
              : "no_compatible_face_after_recovery"
          };
          continue;
        }

        const extension = item.value.extension;

        if (item.side === "left") {
          vertices =
            extension.slice(1).reverse().concat(vertices);
        } else {
          vertices =
            vertices.concat(extension.slice(1));
        }

        changed = true;
        info[item.side] = {
          extended: true,
          extension_vertices: extension.length,
          extension_length_m: polylineLength3D(extension),
          nearest_distance_m: item.value.nearest_distance_m,
          join_forward_m: item.value.join_forward_m,
          join_lateral_m: item.value.join_lateral_m,
          join_z_delta_m: item.value.join_z_delta_m,
          forward_growth_m: item.value.forward_growth_m,
          alignment: item.value.alignment
        };
      }

      if (changed) {
        vertices = simplifyTerrainVertices(vertices, 0.32);
        result.vertices = vertices;
        result.simplified_vertices = vertices.length;
        result.terrain_progressive_rounds = round;
        result.terrain_progressive_length_m =
          polylineLength3D(vertices);
        drawCandidate(result);
      }

      rounds = round;

      debugLog("terrain_face.progressive_round_completed", {
        round: round,
        changed: changed,
        total_vertices: vertices.length,
        total_length_m: polylineLength3D(vertices),
        left_active: leftActive,
        right_active: rightActive,
        sides: info
      });

      if (!changed) break;
    }

    result.vertices = simplifyTerrainVertices(vertices, 0.32);
    result.simplified_vertices = result.vertices.length;
    result.terrain_progressive_rounds = rounds;
    result.terrain_progressive_length_m =
      polylineLength3D(result.vertices);

    debugLog("terrain_face.progressive_completed", {
      rounds: rounds,
      vertices: result.vertices.length,
      total_length_m: result.terrain_progressive_length_m,
      left_active: leftActive,
      right_active: rightActive,
      max_total_length_m: maxTotalLength
    });

    return result;
  }

  async function traceTerrainFace(hit) {
    const started = performance.now();
    const runId = ++state.traceRunId;
    const seed = hit.location.clone();
    const cloudId = findCloudIdForHit(hit);

    if (!cloudId || !state.project) {
      throw new Error("Nuvem ativa não encontrada.");
    }

    const pointcloud = state.pointclouds.get(cloudId);
    if (!pointcloud) {
      throw new Error("Octree Potree da nuvem ativa não encontrada.");
    }

    let selectedClasses = terrainClassesForEngine();

    resetWaypointSession();
    removeCandidate();
    drawSeedMarker(seed);

    const half =
      state.featureMode === "single"
        ? 14.0
        : state.featureMode === "multiple"
          ? 42.0
          : 26.0;

    debugLog("terrain_face.started", {
      cloud_id: cloudId,
      profile: "face",
      seed: [seed.x, seed.y, seed.z],
      selected_classes: selectedClasses,
      detector: "same-auto-1.1.2-clicked-face",
      half_m: half
    });

    setStatus("Talude V1.1.6 · AUTO 1.1.2 apenas na face clicada…");
    byId("traceHint").textContent =
      "A recolher a zona da face e executar o mesmo processo do AUTO: " +
      "slope multiescala → persistence/hysteresis → FACE_DETECTOR → CRISTA + PÉ…";

    const tile = await collectTerrainTile(
      pointcloud,
      seed,
      selectedClasses,
      { compact: false, half: half }
    );

    selectedClasses = tile.selected_classes;

    if (runId !== state.traceRunId) return;
    if (tile.points.length < 500) {
      throw new Error(
        "Poucos pontos nesta zona. Aproxime a vista da face ou confirme a classe Solo."
      );
    }

    const result = await api("/api/feature-lines/terrain-face", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.project.id,
        cloud_id: cloudId,
        profile: "face",
        seed: [seed.x, seed.y, seed.z],
        points: tile.points,
        classifications: tile.classifications,
        selected_classes: selectedClasses,
        grid_resolution: 0
      })
    });

    if (runId !== state.traceRunId) return;

    result.seed = [seed.x, seed.y, seed.z];
    result.cloud_id = cloudId;
    result.selected_classes = selectedClasses;
    result.feature_mode = state.featureMode;
    result.profile_query_points = tile.points.length;
    result.tile_finish_reason = tile.finish_reason;
    result.trace_elapsed_ms =
      Math.round((performance.now() - started) * 10) / 10;

    drawFacePairCandidate(result);

    const crest = result.crest || {};
    const toe = result.toe || {};
    const confidence = Math.round(Number(result.confidence || 0) * 100);

    byId("traceHint").textContent =
      "Face clicada · CRISTA " +
      Number(crest.length_m || 0).toFixed(1) + " m · PÉ " +
      Number(toe.length_m || 0).toFixed(1) + " m · " +
      "cell " + Number(result.grid_resolution || 0).toFixed(3) + " m · " +
      "confiança " + confidence + "% · " +
      result.trace_elapsed_ms.toFixed(0) + " ms. Aceite ou rejeite.";

    setStatus(
      "Talude V1.1.6 · face clicada · CRISTA + PÉ · " +
      result.trace_elapsed_ms.toFixed(0) + " ms"
    );

    debugLog("terrain_face.completed", {
      cloud_id: cloudId,
      seed: result.seed,
      selected_classes: selectedClasses,
      profile_query_points: tile.points.length,
      tile_finish_reason: tile.finish_reason,
      detector: result.detector,
      grid_resolution: result.grid_resolution,
      slope_low_deg: result.slope_low_deg,
      slope_high_deg: result.slope_high_deg,
      seed_to_face_distance_m: result.seed_to_face_distance_m,
      crest_vertices: crest.vertices ? crest.vertices.length : 0,
      toe_vertices: toe.vertices ? toe.vertices.length : 0,
      crest_length_m: crest.length_m,
      toe_length_m: toe.length_m,
      confidence: result.confidence,
      elapsed_ms: result.trace_elapsed_ms
    });
  }


  function polylineLength3D(vertices) {
    let total = 0;
    for (let i = 1; i < (vertices || []).length; i++) {
      const a = vertices[i - 1];
      const b = vertices[i];
      total += Math.hypot(
        Number(b[0]) - Number(a[0]),
        Number(b[1]) - Number(a[1]),
        Number(b[2]) - Number(a[2])
      );
    }
    return total;
  }

  function endpointDirection(vertices, side) {
    if (!vertices || vertices.length < 2) return null;
    const span = Math.min(5, vertices.length - 1);
    let a;
    let b;

    if (side === "left") {
      a = vertices[span];
      b = vertices[0];
    } else {
      a = vertices[vertices.length - 1 - span];
      b = vertices[vertices.length - 1];
    }

    const dx = Number(b[0]) - Number(a[0]);
    const dy = Number(b[1]) - Number(a[1]);
    const norm = Math.hypot(dx, dy);
    if (!Number.isFinite(norm) || norm < 1e-6) return null;
    return [dx / norm, dy / norm];
  }

  async function requestProgressiveContinuation(
    pointcloud,
    cloudId,
    profile,
    selectedClasses,
    referenceSignature,
    anchor,
    direction,
    side,
    round,
    runId
  ) {
    const corridorLength = profile === "curb" ? 9.0 : 13.0;
    const overlap = profile === "curb" ? 1.0 : 1.5;
    const width = profile === "curb" ? 2.0 : 4.0;
    const maxPoints = profile === "curb" ? 30000 : 45000;

    const start = new THREE.Vector3(
      Number(anchor[0]) - direction[0] * overlap,
      Number(anchor[1]) - direction[1] * overlap,
      Number(anchor[2])
    );
    const end = new THREE.Vector3(
      Number(anchor[0]) + direction[0] * corridorLength,
      Number(anchor[1]) + direction[1] * corridorLength,
      Number(anchor[2])
    );

    const started = performance.now();
    debugLog("progressive.corridor_requested", {
      round: round,
      side: side,
      anchor: anchor,
      direction: direction,
      corridor_length_m: corridorLength,
      width_m: width,
      max_points: maxPoints
    });

    const batch = await requestProfileStrip(
      pointcloud,
      start,
      end,
      width,
      selectedClasses,
      maxPoints,
      { softTimeoutMs: 3500, hardTimeoutMs: 5500 }
    );

    if (runId !== state.traceRunId) return null;
    if (batch.points.length < 40) {
      throw new Error("Corredor progressivo sem densidade suficiente.");
    }

    const result = await api("/api/feature-lines/continue", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.project.id,
        cloud_id: cloudId,
        profile: profile,
        anchor: anchor,
        direction: direction,
        reference_signature: referenceSignature,
        points: batch.points,
        classifications: batch.classifications,
        selected_classes: selectedClasses,
        corridor_radius: profile === "curb" ? 0.36 : 0.46,
        step: profile === "curb" ? 0.20 : 0.28,
        max_steps: profile === "curb" ? 70 : 90
      })
    });

    result.extension_length_m = polylineLength3D(result.vertices);
    debugLog("progressive.corridor_completed", {
      round: round,
      side: side,
      points: batch.points.length,
      vertices: result.vertices.length,
      extension_length_m: result.extension_length_m,
      detector: result.detector,
      confidence: result.confidence,
      stop_reason: result.stop_reason_forward,
      elapsed_ms: Math.round((performance.now() - started) * 10) / 10
    });
    return result;
  }

  async function progressivelyExtendCandidate(
    pointcloud,
    cloudId,
    initialResult,
    profile,
    selectedClasses,
    runId
  ) {
    if (!["ridge", "toe", "curb"].includes(profile)) return initialResult;
    if (state.featureMode === "single") return initialResult;

    state.progressiveActive = true;
    const result = initialResult;
    let vertices = result.vertices.map((v) => v.slice());
    let leftAlive = vertices.length >= 2;
    let rightAlive = vertices.length >= 2;
    let roundsCompleted = 0;
    const maxRounds = profile === "curb" ? 8 : 7;
    const referenceSignature = result.signature || null;
    const initialLength = polylineLength3D(vertices);

    debugLog("progressive.started", {
      cloud_id: cloudId,
      profile: profile,
      feature_mode: state.featureMode,
      initial_vertices: vertices.length,
      initial_length_m: initialLength,
      reference_signature: referenceSignature,
      max_rounds: maxRounds
    });

    try {
      for (let round = 1; round <= maxRounds; round++) {
        if (runId !== state.traceRunId) break;
        if (!leftAlive && !rightAlive) break;

        const jobs = [];

        if (leftAlive) {
          const direction = endpointDirection(vertices, "left");
          if (direction) {
            jobs.push({
              side: "left",
              promise: requestProgressiveContinuation(
                pointcloud,
                cloudId,
                profile,
                selectedClasses,
                referenceSignature,
                vertices[0].slice(),
                direction,
                "left",
                round,
                runId
              )
            });
          } else {
            leftAlive = false;
          }
        }

        if (rightAlive) {
          const direction = endpointDirection(vertices, "right");
          if (direction) {
            jobs.push({
              side: "right",
              promise: requestProgressiveContinuation(
                pointcloud,
                cloudId,
                profile,
                selectedClasses,
                referenceSignature,
                vertices[vertices.length - 1].slice(),
                direction,
                "right",
                round,
                runId
              )
            });
          } else {
            rightAlive = false;
          }
        }

        const settled = await Promise.all(
          jobs.map(async (job) => {
            try {
              return { side: job.side, value: await job.promise, error: null };
            } catch (error) {
              return { side: job.side, value: null, error: error };
            }
          })
        );

        if (runId !== state.traceRunId) break;

        let changed = false;
        const roundInfo = {};

        for (const item of settled) {
          if (item.error || !item.value) {
            if (item.side === "left") leftAlive = false;
            else rightAlive = false;
            roundInfo[item.side] = {
              extended: false,
              error: item.error ? String(item.error.message || item.error) : "cancelled"
            };
            continue;
          }

          const segment = item.value.vertices || [];
          const extensionLength = Number(item.value.extension_length_m || 0);
          const usable = segment.length >= 3 && extensionLength >= 0.80;

          roundInfo[item.side] = {
            extended: usable,
            vertices: segment.length,
            length_m: extensionLength,
            stop_reason: item.value.stop_reason_forward
          };

          if (!usable) {
            if (item.side === "left") leftAlive = false;
            else rightAlive = false;
            continue;
          }

          if (item.side === "left") {
            vertices = segment.slice(1).reverse().concat(vertices);
          } else {
            vertices = vertices.concat(segment.slice(1));
          }
          changed = true;

          // A short continuation means the physical/recognisable edge probably
          // ended. Long segments are queried again from their new endpoint,
          // regardless of an internal "no support" at the edge of this strip.
          if (extensionLength < 5.0) {
            if (item.side === "left") leftAlive = false;
            else rightAlive = false;
          }
        }

        roundsCompleted = round;
        result.vertices = vertices;
        result.progressive_rounds = roundsCompleted;
        result.progressive_length_m = polylineLength3D(vertices);
        result.progressive_left_active = leftAlive;
        result.progressive_right_active = rightAlive;

        if (changed) {
          drawCandidate(result);
          const extra = Math.max(0, result.progressive_length_m - initialLength);
          setStatus(
            "Alpha 10 · amarelo automático · " +
            result.progressive_length_m.toFixed(1) + " m"
          );
          byId("traceHint").textContent =
            "A seguir automaticamente a mesma aresta · +" +
            extra.toFixed(1) + " m · ronda " + round + "/" + maxRounds;
        }

        debugLog("progressive.round_completed", {
          round: round,
          changed: changed,
          total_vertices: vertices.length,
          total_length_m: result.progressive_length_m,
          left_active: leftAlive,
          right_active: rightAlive,
          sides: roundInfo
        });

        if (!changed) break;
      }
    } finally {
      if (runId === state.traceRunId) {
        state.progressiveActive = false;
      }
    }

    debugLog("progressive.completed", {
      rounds: roundsCompleted,
      vertices: vertices.length,
      total_length_m: polylineLength3D(vertices),
      left_active: leftAlive,
      right_active: rightAlive,
      cancelled: runId !== state.traceRunId
    });

    return result;
  }

  function bboxDistance2XY(box, point) {
    let dx = 0;
    let dy = 0;

    if (point.x < box.min.x) dx = box.min.x - point.x;
    else if (point.x > box.max.x) dx = point.x - box.max.x;

    if (point.y < box.min.y) dy = box.min.y - point.y;
    else if (point.y > box.max.y) dy = point.y - box.max.y;

    return dx * dx + dy * dy;
  }

  function collectLocalPotreePoints(seed, radius, maxPoints) {
    const points = [];
    const radius2 = radius * radius;
    const zLimit = Math.max(radius * 1.5, 8.0);
    const tmp = new THREE.Vector3();

    for (const pointcloud of state.pointclouds.values()) {
      pointcloud.updateMatrixWorld(true);

      const nodes = Array.from(pointcloud.visibleNodes || [])
        .filter((node) => node && node.sceneNode && node.sceneNode.geometry)
        .sort((a, b) => {
          const la = typeof a.getLevel === "function" ? a.getLevel() : 0;
          const lb = typeof b.getLevel === "function" ? b.getLevel() : 0;
          return lb - la;
        });

      for (const node of nodes) {
        if (points.length >= maxPoints) break;

        const worldBox = node.getBoundingBox().clone().applyMatrix4(pointcloud.matrixWorld);
        if (bboxDistance2XY(worldBox, seed) > radius2) continue;
        if (seed.z < worldBox.min.z - zLimit || seed.z > worldBox.max.z + zLimit) continue;

        const attribute = node.sceneNode.geometry.attributes.position;
        if (!attribute || !attribute.array) continue;

        // Use the same transform Potree's own ProfileRequest uses. Point
        // positions are local to the node bbox; the pointcloud matrix carries
        // the survey offset/world transform.
        const nodeMatrix = new THREE.Matrix4().makeTranslation(
          node.getBoundingBox().min.x,
          node.getBoundingBox().min.y,
          node.getBoundingBox().min.z
        );
        const matrix = new THREE.Matrix4().multiplyMatrices(
          pointcloud.matrixWorld,
          nodeMatrix
        );

        const array = attribute.array;
        const count = attribute.count || Math.floor(array.length / 3);

        for (let i = 0; i < count; i++) {
          if (points.length >= maxPoints) break;

          tmp.set(
            array[3 * i + 0],
            array[3 * i + 1],
            array[3 * i + 2]
          );
          tmp.applyMatrix4(matrix);

          const dx = tmp.x - seed.x;
          const dy = tmp.y - seed.y;
          const dz = tmp.z - seed.z;

          if ((dx * dx + dy * dy) <= radius2 && Math.abs(dz) <= zLimit) {
            points.push([tmp.x, tmp.y, tmp.z]);
          }
        }
      }
    }

    return points;
  }

  function updateWideLineResolution() {
    if (!state.viewer || !state.viewer.renderer) return;
    const size = state.viewer.renderer.getSize(new THREE.Vector2());
    for (const material of Array.from(state.lineMaterials)) {
      if (!material || !material.resolution) {
        state.lineMaterials.delete(material);
        continue;
      }
      material.resolution.copy(size);
    }
  }

  function disposeLineObject(object) {
    if (!object) return;
    try {
      if (object.geometry && typeof object.geometry.dispose === "function") {
        object.geometry.dispose();
      }
      if (object.material) {
        const materials = Array.isArray(object.material)
          ? object.material
          : [object.material];
        for (const material of materials) {
          state.lineMaterials.delete(material);
          if (material && typeof material.dispose === "function") {
            material.dispose();
          }
        }
      }
    } catch (_) {}
  }

  function removeCandidate() {
    if (state.candidateObject && state.viewer) {
      const objects = Array.isArray(state.candidateObject)
        ? state.candidateObject
        : [state.candidateObject];

      for (const object of objects) {
        if (!object) continue;
        if (state.featureOverlayScene) {
          state.featureOverlayScene.remove(object);
        } else {
          state.viewer.scene.scene.remove(object);
        }
        disposeLineObject(object);
      }
    }

    state.candidateObject = null;
    state.candidateData = null;
    byId("traceActions").classList.add("hidden");
  }


  function createLineObject(vertices, style) {
    style = style || {};
    if (!Array.isArray(vertices) || vertices.length < 2) {
      throw new Error("Feature Line sem vértices suficientes para renderizar.");
    }

    // Local coordinates preserve centimetric precision with large survey XYZ.
    const origin = vertices[0];
    const positions = [];
    for (let i = 0; i < vertices.length; i++) {
      positions.push(
        Number(vertices[i][0]) - Number(origin[0]),
        Number(vertices[i][1]) - Number(origin[1]),
        Number(vertices[i][2]) - Number(origin[2])
      );
    }

    const color = style.color == null ? 0xff2aa8 : style.color;
    const widthPx = style.widthPx == null ? 2.5 : style.widthPx;
    const dashed = Boolean(style.dashed);
    const dashSize = Number(style.dashSize == null ? 0.85 : style.dashSize);
    const gapSize = Number(style.gapSize == null ? 0.45 : style.gapSize);
    let line;

    if (THREE.Line2 && THREE.LineGeometry && THREE.LineMaterial) {
      const geometry = new THREE.LineGeometry();
      geometry.setPositions(positions);

      const material = new THREE.LineMaterial({
        color: color,
        linewidth: widthPx,
        resolution: new THREE.Vector2(1, 1),
        transparent: false,
        opacity: 1.0,
        depthTest: false,
        depthWrite: false,
        dashed: dashed,
        dashSize: dashSize,
        gapSize: gapSize
      });
      material.depthTest = false;
      material.depthWrite = false;
      material.dashed = dashed;
      material.dashSize = dashSize;
      material.gapSize = gapSize;

      line = new THREE.Line2(geometry, material);
      if (dashed && typeof line.computeLineDistances === "function") {
        line.computeLineDistances();
      }
      state.lineMaterials.add(material);
      updateWideLineResolution();
      line.userData.ctlRenderer = "potree-overlay-line2";
      line.userData.ctlWidthPx = widthPx;
      line.userData.ctlDashed = dashed;
      line.userData.ctlRenderLayer = "render.pass.perspective_overlay";
    } else {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute(
        "position",
        new THREE.BufferAttribute(new Float32Array(positions), 3)
      );
      const material = new THREE.LineBasicMaterial({
        color: color,
        depthTest: false,
        depthWrite: false
      });
      line = new THREE.Line(geometry, material);
      line.userData.ctlRenderer = "fallback-linebasic";
      line.userData.ctlWidthPx = 1;
      line.userData.ctlDashed = false;
      line.userData.ctlRenderLayer = "render.pass.perspective_overlay";
    }

    line.position.set(Number(origin[0]), Number(origin[1]), Number(origin[2]));
    line.frustumCulled = false;
    line.renderOrder = 5000;
    return line;
  }

  function addFeatureOverlayObject(object) {
    if (!object || !state.viewer) return;
    if (state.featureOverlayScene) {
      state.featureOverlayScene.add(object);
    } else {
      // Fallback only. Normal Alpha 8.1 builds always create the overlay scene.
      state.viewer.scene.scene.add(object);
    }
  }

  function drawCandidate(result) {
    removeCandidate();

    const line = createLineObject(result.vertices, {
      color: 0xffcf24,
      widthPx: 3.0,
      dashed: true,
      dashSize: 0.90,
      gapSize: 0.50
    });
    addFeatureOverlayObject(line);

    state.candidateObject = line;
    state.candidateData = result;
    byId("traceActions").classList.remove("hidden");
    debugLog("feature.candidate_drawn", {
      detector: result.detector,
      vertices: result.vertices.length,
      confidence: result.confidence,
      mean_break_angle_deg: result.mean_break_angle_deg,
      renderer: line.userData.ctlRenderer,
      width_px: line.userData.ctlWidthPx,
      dashed: line.userData.ctlDashed,
      render_layer: line.userData.ctlRenderLayer,
      edl_safe_overlay: line.userData.ctlRenderLayer === "render.pass.perspective_overlay"
    });
  }

  function drawFacePairCandidate(result) {
    removeCandidate();

    const lines = Array.isArray(result.lines) ? result.lines : [];
    if (lines.length < 2) {
      throw new Error("A face clicada não devolveu CRISTA + PÉ.");
    }

    const objects = [];
    for (const data of lines) {
      const isCrest = data.type === "CREST";
      const line = createLineObject(data.vertices, {
        color: isCrest ? 0xffd54a : 0x38d5ff,
        widthPx: isCrest ? 3.4 : 3.1,
        dashed: true,
        dashSize: 0.95,
        gapSize: 0.45
      });
      addFeatureOverlayObject(line);
      objects.push(line);
    }

    state.candidateObject = objects;
    state.candidateData = result;
    byId("traceActions").classList.remove("hidden");

    debugLog("feature.face_pair_candidate_drawn", {
      detector: result.detector,
      line_count: lines.length,
      crest_vertices: result.crest && result.crest.vertices
        ? result.crest.vertices.length
        : 0,
      toe_vertices: result.toe && result.toe.vertices
        ? result.toe.vertices.length
        : 0,
      confidence: result.confidence,
      seed_to_face_distance_m: result.seed_to_face_distance_m
    });
  }

  function renderFeatureList() {
    const box = byId("featureList");
    box.innerHTML = "";

    for (let i = 0; i < state.acceptedObjects.length; i++) {
      const item = state.acceptedObjects[i];
      const el = document.createElement("div");
      el.className = "layer";
      const confidence = item.data.confidence == null
        ? ""
        : " · " + Math.round(item.data.confidence * 100) + "%";
      el.innerHTML =
        "<strong>Feature " + String(i + 1).padStart(3, "0") + "</strong>" +
        escapeHtml(item.data.profile || "feature") +
        confidence;
      box.appendChild(el);
    }
  }

  async function persistAcceptedFeature(data) {
    if (!state.project) return;

    const saved = await api(
      "/api/projects/" + encodeURIComponent(state.project.id) + "/state"
    );

    saved.feature_lines = Array.isArray(saved.feature_lines)
      ? saved.feature_lines
      : [];

    saved.feature_lines.push({
      id: "fl_" + Date.now(),
      profile: data.profile,
      detector: data.detector,
      vertices: data.vertices,
      confidence: data.confidence,
      mean_break_angle_deg: data.mean_break_angle_deg,
      stop_reason_forward: data.stop_reason_forward,
      stop_reason_backward: data.stop_reason_backward,
      seed: data.seed || null,
      signature: data.signature || null,
      cloud_id: data.cloud_id || null,
      selected_classes: data.selected_classes == null ? null : data.selected_classes,
      source_points: data.source_points,
      source_points_before_bound: data.source_points_before_bound,
      profile_query_points: data.profile_query_points,
      query_source: data.query_source,
      feature_mode: data.feature_mode || state.featureMode,
      progressive_rounds: data.progressive_rounds || 0,
      progressive_length_m: data.progressive_length_m || null,
      progressive_left_active: data.progressive_left_active,
      progressive_right_active: data.progressive_right_active,
      waypoint_count: data.waypoint_count || null,
      waypoint_segments: data.waypoint_segments || null,
      raw_vertices: data.raw_vertices || null,
      simplified_vertices: data.simplified_vertices || data.vertices.length,
      auto_ground_used: data.auto_ground_used || false,
      grid_resolution: data.grid_resolution || null,
      corridor_width: data.corridor_width || null,
      terrain_progressive_rounds: data.terrain_progressive_rounds || 0,
      terrain_progressive_length_m: data.terrain_progressive_length_m || null,
      tile_finish_reason: data.tile_finish_reason || null,
      created_at: new Date().toISOString()
    });

    await api(
      "/api/projects/" + encodeURIComponent(state.project.id) + "/state",
      {
        method: "PUT",
        body: JSON.stringify({ state: saved })
      }
    );
  }

  async function acceptCandidate() {
    if (!state.candidateData || !state.viewer) return;

    state.traceRunId += 1;
    state.progressiveActive = false;
    state.waypointBusy = false;
    const data = state.candidateData;

    if (Array.isArray(data.lines) && data.lines.length) {
      for (const lineData of data.lines) {
        const isCrest = lineData.type === "CREST";
        const persisted = Object.assign({}, lineData, {
          profile: isCrest ? "ridge" : "toe",
          detector: data.detector,
          seed: data.seed || null,
          cloud_id: data.cloud_id || null,
          selected_classes: data.selected_classes || null,
          feature_mode: data.feature_mode || state.featureMode,
          query_source: data.query_source || "potree-local-auto-face"
        });

        const accepted = createLineObject(lineData.vertices, {
          color: isCrest ? 0xffd54a : 0x38d5ff,
          widthPx: isCrest ? 3.2 : 3.0,
          dashed: false
        });
        addFeatureOverlayObject(accepted);
        state.acceptedObjects.push({ object: accepted, data: persisted });
        await persistAcceptedFeature(persisted);
      }

      debugLog("feature.face_pair_accepted", {
        detector: data.detector,
        line_count: data.lines.length,
        confidence: data.confidence
      });

      removeCandidate();
      resetWaypointSession();
      renderFeatureList();
      setStatus("Face aceite · CRISTA + PÉ guardados.");
      toast("CRISTA + PÉ da face guardados no projeto.");
      return;
    }

    const accepted = createLineObject(data.vertices, {
      color: 0xff2aa8,
      widthPx: 2.5,
      dashed: false
    });
    addFeatureOverlayObject(accepted);
    state.acceptedObjects.push({ object: accepted, data: data });

    await persistAcceptedFeature(data);
    debugLog("feature.accepted", {
      profile: data.profile,
      detector: data.detector,
      vertices: data.vertices.length,
      confidence: data.confidence,
      renderer: accepted.userData.ctlRenderer,
      width_px: accepted.userData.ctlWidthPx,
      dashed: accepted.userData.ctlDashed,
      render_layer: accepted.userData.ctlRenderLayer
    });
    removeCandidate();
    resetWaypointSession();
    renderFeatureList();
    setStatus("Feature Line aceite e guardada.");
    toast("Linha aceite e guardada no projeto.");
  }


  function rejectCandidate() {
    state.traceRunId += 1;
    state.progressiveActive = false;
    state.waypointBusy = false;

    if (state.candidateData) {
      debugLog("feature.rejected", {
        detector: state.candidateData.detector,
        pair: Array.isArray(state.candidateData.lines),
        line_count: Array.isArray(state.candidateData.lines)
          ? state.candidateData.lines.length
          : 1,
        confidence: state.candidateData.confidence
      });
    }

    removeCandidate();
    resetWaypointSession();
    setStatus("Candidato rejeitado.");
  }


  function resetWaypointSession() {
    state.waypointStart = null;
    state.waypointCloudId = null;
    state.waypointVertices = [];
    state.waypointSegmentCount = 0;
    state.waypointBusy = false;

    if (state.seedMarker && state.viewer) {
      try {
        if (state.featureOverlayScene) {
          state.featureOverlayScene.remove(state.seedMarker);
        } else {
          state.viewer.scene.scene.remove(state.seedMarker);
        }
        if (state.seedMarker.geometry) state.seedMarker.geometry.dispose();
        if (state.seedMarker.material) state.seedMarker.material.dispose();
      } catch (_) {}
      state.seedMarker = null;
    }
  }

  function waypointCorridorWidth(profile, length) {
    if (profile === "curb") {
      return Math.max(1.0, Math.min(2.2, 1.0 + length * 0.035));
    }
    return Math.max(1.6, Math.min(3.0, 1.45 + length * 0.050));
  }

  function waypointMaxPoints(profile, length) {
    const base = profile === "curb" ? 9000 : 12000;
    const perMeter = profile === "curb" ? 700 : 850;
    return Math.max(base, Math.min(32000, Math.round(base + length * perMeter)));
  }

  async function traceWaypointClick(hit) {
    if (state.waypointBusy) {
      toast("O troço anterior ainda está a ser calculado.");
      return;
    }

    const profile = byId("profile").value;
    if (!["ridge", "toe", "curb"].includes(profile)) {
      throw new Error("Waypoints automáticos disponíveis para Crista, Pé e Lancil.");
    }

    const cloudId = findCloudIdForHit(hit);
    const pointcloud = state.pointclouds.get(cloudId);
    if (!cloudId || !pointcloud || !state.project) {
      throw new Error("Nuvem ativa não encontrada.");
    }

    const end = [hit.location.x, hit.location.y, hit.location.z];

    if (!state.waypointStart) {
      state.waypointStart = end.slice();
      state.waypointCloudId = cloudId;
      state.waypointVertices = [];
      state.waypointSegmentCount = 0;
      drawSeedMarker(hit.location);

      debugLog("waypoint.start_set", {
        cloud_id: cloudId,
        profile: profile,
        start: state.waypointStart
      });

      byId("traceHint").textContent =
        "1.º ponto definido. Clique no FIM deste troço da mesma " +
        byId("profile").selectedOptions[0].text +
        ". Use troços curtos nas curvas.";
      setStatus("Alpha 10 · início definido · escolha o fim do troço");
      return;
    }

    if (state.waypointCloudId !== cloudId) {
      throw new Error("O segundo waypoint tem de estar na mesma nuvem.");
    }

    const start = state.waypointStart.slice();
    const dx = end[0] - start[0];
    const dy = end[1] - start[1];
    const length = Math.hypot(dx, dy);

    if (length < 0.80) {
      throw new Error("Os dois waypoints estão demasiado próximos.");
    }
    if (length > 50.0) {
      throw new Error(
        "Troço superior a 50 m. Use um waypoint intermédio: troços curtos são mais rápidos e mais fiéis à aresta."
      );
    }

    const selectedClasses = selectedClassesForEngine();
    if (selectedClasses && selectedClasses.length === 0) {
      throw new Error("Nenhuma classificação está ativa para o motor.");
    }

    const width = waypointCorridorWidth(profile, length);
    const maxPoints = waypointMaxPoints(profile, length);
    const started = performance.now();
    state.waypointBusy = true;

    debugLog("waypoint.segment_started", {
      cloud_id: cloudId,
      profile: profile,
      start: start,
      end: end,
      length_m: length,
      corridor_width_m: width,
      max_points: maxPoints,
      selected_classes: selectedClasses
    });

    try {
      setStatus(
        "Alpha 10 · caminho least-cost · " + length.toFixed(1) + " m"
      );
      byId("traceHint").textContent =
        "A calcular apenas o troço entre os dois waypoints…";

      const batch = await requestProfileStrip(
        pointcloud,
        new THREE.Vector3(start[0], start[1], start[2]),
        new THREE.Vector3(end[0], end[1], end[2]),
        width,
        selectedClasses,
        maxPoints,
        {
          earlyMinPoints: profile === "curb" ? 1400 : 2200,
          earlyAfterMs: 450,
          softTimeoutMs: 1700,
          hardTimeoutMs: 3200
        }
      );

      if (batch.points.length < 80) {
        throw new Error(
          "Poucos pontos no corredor. Aumente ligeiramente o troço ou use Todas."
        );
      }

      const segmentResult = await api("/api/feature-lines/waypoint-trace", {
        method: "POST",
        body: JSON.stringify({
          project_id: state.project.id,
          cloud_id: cloudId,
          profile: profile,
          start: start,
          end: end,
          points: batch.points,
          classifications: batch.classifications,
          selected_classes: selectedClasses,
          corridor_width: width
        })
      });

      const segment = segmentResult.vertices || [];
      if (segment.length < 2) {
        throw new Error("O motor não encontrou caminho entre os waypoints.");
      }

      let combined;
      if (state.waypointVertices.length >= 2) {
        combined = state.waypointVertices.concat(segment.slice(1));
      } else {
        combined = segment.map((v) => v.slice());
      }

      state.waypointVertices = combined.map((v) => v.slice());
      state.waypointStart = end.slice();
      state.waypointSegmentCount += 1;

      const result = Object.assign({}, segmentResult, {
        vertices: combined,
        cloud_id: cloudId,
        selected_classes: selectedClasses,
        feature_mode: state.featureMode,
        seed: combined.length ? combined[0].slice() : start,
        waypoint_count: state.waypointSegmentCount + 1,
        waypoint_segments: state.waypointSegmentCount,
        simplified_vertices: combined.length,
        trace_elapsed_ms:
          Math.round((performance.now() - started) * 10) / 10
      });

      drawCandidate(result);
      drawSeedMarker(hit.location);

      debugLog("waypoint.segment_completed", {
        cloud_id: cloudId,
        profile: profile,
        start: start,
        end: end,
        length_m: length,
        profile_points: batch.points.length,
        raw_vertices: segmentResult.raw_vertices,
        segment_vertices: segment.length,
        total_vertices: combined.length,
        auto_ground_used: segmentResult.auto_ground_used,
        confidence: segmentResult.confidence,
        grid_resolution: segmentResult.grid_resolution,
        corridor_width: segmentResult.corridor_width,
        elapsed_ms: result.trace_elapsed_ms
      });

      const confidence = segmentResult.confidence == null
        ? "—"
        : Math.round(segmentResult.confidence * 100) + "%";

      byId("traceHint").textContent =
        "Least-cost " +
        state.waypointSegmentCount +
        " troço(s) · " +
        combined.length +
        " vértices simplificados · confiança " +
        confidence +
        (segmentResult.auto_ground_used ? " · Solo automático" : "") +
        ". " +
        (state.featureMode === "multiple"
          ? "Clique no próximo waypoint ou Aceitar linha."
          : "Aceite ou rejeite.");

      setStatus(
        "Alpha 10 · " +
        combined.length +
        " vértices · " +
        result.trace_elapsed_ms.toFixed(0) +
        " ms"
      );

      if (state.featureMode !== "multiple") {
        state.traceArmed = false;
        byId("traceButton").classList.remove("active");
      }
    } catch (error) {
      debugLog("waypoint.segment_failed", {
        cloud_id: cloudId,
        profile: profile,
        start: start,
        end: end,
        length_m: length,
        error: String(error && error.stack ? error.stack : error),
        elapsed_ms: Math.round((performance.now() - started) * 10) / 10
      }, "ERROR");
      throw error;
    } finally {
      state.waypointBusy = false;
    }
  }

  function findCloudIdForHit(hit) {
    if (hit && hit.pointcloud) {
      for (const [id, pointcloud] of state.pointclouds.entries()) {
        if (pointcloud === hit.pointcloud) return id;
      }
    }
    const first = state.pointclouds.keys().next();
    return first.done ? null : first.value;
  }

  async function traceSeed(hit) {
    const traceStarted = performance.now();
    const runId = ++state.traceRunId;
    state.progressiveActive = false;
    const seed = hit.location.clone();
    const cloudId = findCloudIdForHit(hit);

    if (!cloudId || !state.project) {
      throw new Error("Nuvem ativa não encontrada.");
    }

    const pointcloud = state.pointclouds.get(cloudId);
    if (!pointcloud) {
      throw new Error("Octree Potree da nuvem ativa não encontrada.");
    }

    const profile = byId("profile").value;
    const selectedClasses = selectedClassesForEngine();

    if (selectedClasses && selectedClasses.length === 0) {
      throw new Error("Nenhuma classificação está ativa para o motor.");
    }

    debugLog("trace.started", {
      cloud_id: cloudId,
      seed: [seed.x, seed.y, seed.z],
      profile: profile,
      selected_classes: selectedClasses,
      corridor_radius: profile === "curb" ? 0.40 : 0.48,
      step: profile === "curb" ? 0.22 : 0.28
    });

    setStatus("Alpha 10 · consulta full-density da octree…");
    byId("traceHint").textContent =
      "A carregar pontos do corredor independentemente do zoom…";

    const local = await collectFullDensityNeighborhood(
      pointcloud,
      seed,
      selectedClasses
    );

    if (local.points.length < 80) {
      throw new Error(
        "Poucos pontos nas classes selecionadas junto ao clique. " +
        "Experimente Todas ou confirme se o Solo está classificado como classe 2."
      );
    }

    setStatus(
      "Alpha 10 · " +
      local.points.length.toLocaleString("pt-PT") +
      " pontos full-density · " +
      byId("profile").selectedOptions[0].text
    );

    const result = await api("/api/feature-lines/trace", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.project.id,
        cloud_id: cloudId,
        profile: profile,
        seed: [seed.x, seed.y, seed.z],
        points: local.points,
        classifications: local.classifications,
        selected_classes: selectedClasses,
        corridor_radius: profile === "curb" ? 0.40 : 0.48,
        step: profile === "curb" ? 0.22 : 0.28
      })
    });

    result.seed = [seed.x, seed.y, seed.z];
    result.cloud_id = cloudId;
    result.selected_classes = selectedClasses;
    result.profile_query_points = local.points.length;
    result.feature_mode = state.featureMode;

    debugLog("trace.completed", {
      cloud_id: cloudId,
      seed: result.seed,
      profile: profile,
      selected_classes: selectedClasses,
      profile_query_points: local.points.length,
      detector: result.detector,
      signature: result.signature,
      confidence: result.confidence,
      mean_break_angle_deg: result.mean_break_angle_deg,
      stop_reason_forward: result.stop_reason_forward,
      stop_reason_backward: result.stop_reason_backward,
      vertices: result.vertices.length,
      source_points: result.source_points,
      source_points_before_bound: result.source_points_before_bound,
      elapsed_ms: Math.round((performance.now() - traceStarted) * 10) / 10
    });

    drawCandidate(result);

    if (["ridge", "toe", "curb"].includes(profile) &&
        state.featureMode !== "single") {
      await progressivelyExtendCandidate(
        pointcloud,
        cloudId,
        result,
        profile,
        selectedClasses,
        runId
      );
      if (runId !== state.traceRunId) return;
    }

    const confidence = result.confidence == null
      ? "—"
      : Math.round(result.confidence * 100) + "%";

    const angle = result.mean_break_angle_deg == null
      ? "—"
      : result.mean_break_angle_deg.toFixed(1) + "°";

    byId("traceHint").textContent =
      "Candidato " + result.detector +
      " · " + result.vertices.length + " vértices" +
      " · confiança " + confidence +
      " · quebra " + angle +
      " · " +
      (result.progressive_length_m
        ? result.progressive_length_m.toFixed(1) + " m · "
        : "") +
      "fonte: ProfileRequest progressivo. Aceite ou rejeite.";

    setStatus(
      "Alpha 10 · " + result.detector +
      " · " + result.vertices.length +
      " vértices · confiança " + confidence
    );
  }

  function onViewerPickClick(event) {
    if (event.shiftKey) return;
    if (!state.traceArmed || event.button !== 0 || !state.viewer) return;

    const hit = Potree.Utils.getMousePointCloudIntersection(
      state.viewer.inputHandler.mouse,
      state.viewer.scene.getActiveCamera(),
      state.viewer,
      state.viewer.scene.pointclouds
    );

    if (!hit) {
      toast("Clique diretamente numa zona da nuvem.");
      return;
    }

    state.currentSeed = {
      x: hit.location.x,
      y: hit.location.y,
      z: hit.location.z
    };

    const profile = byId("profile").value;
    const useTerrainFace = ["face", "ridge", "toe"].includes(profile);
    const useWaypointAssist =
      state.featureMode !== "single" &&
      profile === "curb";

    debugLog("seed.clicked", {
      seed: [hit.location.x, hit.location.y, hit.location.z],
      cloud_id: findCloudIdForHit(hit),
      terrain_face: useTerrainFace,
      raster_terrain_ready: terrainRasterReady(),
      waypoint_assist: useWaypointAssist
    });

    if (useTerrainFace) {
      state.traceArmed = false;
      byId("traceButton").classList.remove("active");

      // Talude Studio V1 trabalha diretamente sobre a point cloud.
      // Projetos antigos do Cloud_to_lines podem conter MDT/Declive registados;
      // isso não deve desviar o clique para endpoints raster que não pertencem
      // ao Talude Studio.
      traceTerrainFace(hit).catch((error) => {
        debugLog(
          "terrain_face.failed",
          {
            seed: state.currentSeed,
            error: String(error && error.stack ? error.stack : error)
          },
          "ERROR"
        );
        setStatus("Feature Lines · falha na deteção do talude.");
        byId("traceHint").textContent = error.message;
        toast(error.message, 9000);
      });
      return;
    }

    if (useWaypointAssist) {
      traceWaypointClick(hit).catch((error) => {
        debugLog("waypoint.failed", {
          seed: state.currentSeed,
          error: String(error && error.stack ? error.stack : error)
        }, "ERROR");
        setStatus("Feature Lines · falha no troço assistido.");
        byId("traceHint").textContent = error.message;
        toast(error.message, 9000);
      });
      return;
    }

    state.traceArmed = false;
    byId("traceButton").classList.remove("active");
    removeCandidate();
    drawSeedMarker(hit.location);

    byId("traceHint").textContent =
      "Seed XYZ: " +
      hit.location.x.toFixed(3) +
      ", " +
      hit.location.y.toFixed(3) +
      ", " +
      hit.location.z.toFixed(3) +
      " · a processar…";

    traceSeed(hit).catch((error) => {
      debugLog("trace.failed", {
        seed: state.currentSeed,
        error: String(error && error.stack ? error.stack : error)
      }, "ERROR");
      setStatus("Feature Lines · falha no seguimento.");
      byId("traceHint").textContent = error.message;
      toast(error.message, 9000);
    });
  }

  function setPanMode(enabled) {
    state.panMode = Boolean(enabled);
    const button = byId("panModeButton");
    if (button) button.classList.toggle("active", state.panMode);

    const area = byId("potree_render_area");
    if (area) area.classList.toggle("pan-mode", state.panMode);

    debugLog("viewer.pan_mode", { enabled: state.panMode });
  }

  function onViewerNavMouseDown(event) {
    if (!state.viewer) return;

    const explicitPan =
      event.button === 1 ||
      (event.button === 0 && state.panMode);

    if (explicitPan) {
      state.panPointer = {
        button: event.button,
        x: event.clientX,
        y: event.clientY
      };

      const area = byId("potree_render_area");
      if (area) area.classList.add("panning");

      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }

    if (event.button !== 0) return;
    state.navPointerDown = {
      x: event.clientX,
      y: event.clientY,
      time: performance.now()
    };
  }

  function onViewerNavMouseMove(event) {
    if (!state.viewer || !state.panPointer) return;

    const controls = state.viewer.orbitControls;
    const element = state.viewer.renderer.domElement;
    if (!controls || !controls.panDelta || !element) return;

    const dx = event.clientX - state.panPointer.x;
    const dy = event.clientY - state.panPointer.y;
    state.panPointer.x = event.clientX;
    state.panPointer.y = event.clientY;

    controls.panDelta.x += dx / Math.max(1, element.clientWidth);
    controls.panDelta.y += dy / Math.max(1, element.clientHeight);
    if (typeof controls.stopTweens === "function") controls.stopTweens();

    event.preventDefault();
    event.stopImmediatePropagation();
  }

  function onViewerNavMouseUp(event) {
    if (state.panPointer) {
      const sameButton =
        event.type === "mouseleave" ||
        event.button === state.panPointer.button;

      if (sameButton) {
        state.panPointer = null;
        const area = byId("potree_render_area");
        if (area) area.classList.remove("panning");
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
    }

    if (event.button !== 0) return;

    const start = state.navPointerDown;
    state.navPointerDown = null;

    if (!state.traceArmed || !start || event.shiftKey || state.panMode) return;

    const moved = Math.hypot(
      event.clientX - start.x,
      event.clientY - start.y
    );

    // Clique curto = selecionar a face. Arrastar = OrbitControls.
    if (moved <= 5) {
      onViewerPickClick(event);
    }
  }


  function setStandardView(name) {
    if (!state.viewer) return;

    const viewer = state.viewer;
    const methods = {
      top: "setTopView",
      front: "setFrontView",
      back: "setBackView",
      left: "setLeftView",
      right: "setRightView"
    };

    const method = methods[name];
    if (method && typeof viewer[method] === "function") {
      viewer[method]();
    } else {
      const view = viewer.scene && viewer.scene.view;
      if (!view) return;

      const fallback = {
        top: { yaw: 0.0, pitch: -Math.PI / 2 + 0.001 },
        front: { yaw: 0.0, pitch: 0.0 },
        back: { yaw: Math.PI, pitch: 0.0 },
        left: { yaw: -Math.PI / 2, pitch: 0.0 },
        right: { yaw: Math.PI / 2, pitch: 0.0 }
      };

      const target = fallback[name];
      if (target) {
        view.yaw = target.yaw;
        view.pitch = target.pitch;
      }
    }

    try { viewer.fitToScreen(0.82); } catch (_) {}
    window.setTimeout(() => {
      try { viewer.fitToScreen(0.82); } catch (_) {}
    }, 60);

    debugLog("viewer.standard_view", { view: name });
  }

  function setIsoView() {
    if (!state.viewer || !state.viewer.scene || !state.viewer.scene.view) return;
    const view = state.viewer.scene.view;
    view.yaw = -Math.PI / 4;
    view.pitch = -Math.PI / 4;
    try { state.viewer.fitToScreen(0.82); } catch (_) {}
    window.setTimeout(() => {
      try { state.viewer.fitToScreen(0.82); } catch (_) {}
    }, 60);
    debugLog("viewer.standard_view", { view: "iso" });
  }

  function drawSeedMarker(position) {
    if (!state.viewer) return;

    if (state.seedMarker) {
      if (state.featureOverlayScene) {
        state.featureOverlayScene.remove(state.seedMarker);
      } else {
        state.viewer.scene.scene.remove(state.seedMarker);
      }
      try {
        state.seedMarker.geometry.dispose();
        state.seedMarker.material.dispose();
      } catch (_) {}
    }

    const geometry = new THREE.SphereGeometry(0.15, 16, 12);
    const material = new THREE.MeshBasicMaterial({
      color: 0xffd54a,
      depthTest: false,
      depthWrite: false
    });

    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.copy(position);
    mesh.renderOrder = 1000;
    if (state.featureOverlayScene) {
      state.featureOverlayScene.add(mesh);
    } else {
      state.viewer.scene.scene.add(mesh);
    }
    state.seedMarker = mesh;
  }

  function armTrace() {
    if (!state.viewer || !state.pointclouds.size) return;
    if (state.waypointBusy) {
      toast("Aguarde pelo cálculo atual.");
      return;
    }

    state.traceArmed = !state.traceArmed;
    byId("traceButton").classList.toggle("active", state.traceArmed);

    if (state.traceArmed) {
      setPanMode(false);
      resetWaypointSession();
    }

    debugLog("trace.arm_changed", {
      armed: state.traceArmed,
      feature_mode: state.featureMode,
      profile: "face",
      detector: "same-auto-1.1.2-clicked-face"
    });

    if (state.traceArmed) {
      const extent =
        state.featureMode === "single"
          ? "zona local"
          : state.featureMode === "multiple"
            ? "janela grande da face"
            : "janela média da face";

      byId("traceHint").textContent =
        "Clique curto aproximadamente no CENTRO da face inclinada. " +
        "O mesmo detector AUTO da 1.1.2 processará apenas essa " +
        extent + " e devolverá CRISTA + PÉ. Arraste para rodar.";
    } else {
      byId("traceHint").textContent =
        state.candidateData
          ? "CRISTA + PÉ candidatos prontos. Aceite ou rejeite."
          : "Seleção de face cancelada.";
    }
  }


  function bindUi() {
    document.querySelectorAll("[data-standard-view]").forEach((button) => {
      button.onclick = () => {
        const view = button.dataset.standardView;
        if (view === "iso") setIsoView();
        else setStandardView(view);
      };
    });

    if (byId("panModeButton")) {
      byId("panModeButton").onclick = () => setPanMode(!state.panMode);
    }

    byId("newProject").onclick = () => {
      createProject().catch((e) => toast(e.message, 8000));
    };

    byId("openProject").onclick = () => {
      openProject().catch((e) => toast(e.message, 8000));
    };

    byId("addCloud").onclick = () => {
      addCloud().catch((e) => toast(e.message, 8000));
    };

    byId("addMdt").onclick = () => {
      addTerrainRaster("mdt").catch((e) => toast(e.message, 10000));
    };

    byId("addSlope").onclick = () => {
      addTerrainRaster("slope").catch((e) => toast(e.message, 10000));
    };

    byId("traceButton").onclick = armTrace;
    byId("classAll").onclick = () => setClassificationPreset("all");
    byId("classGround").onclick = () => setClassificationPreset("ground");
    byId("classNone").onclick = () => setClassificationPreset("none");
    byId("acceptTrace").onclick = () => {
      acceptCandidate().catch((e) => toast(e.message, 8000));
    };
    byId("rejectTrace").onclick = rejectCandidate;

    document.querySelectorAll("#featureMode button").forEach((button) => {
      button.onclick = () => {
        document
          .querySelectorAll("#featureMode button")
          .forEach((b) => b.classList.remove("active"));

        button.classList.add("active");
        state.featureMode = button.dataset.mode;
        state.traceArmed = false;
        byId("traceButton").classList.remove("active");
        if (state.candidateObject) removeCandidate();
        resetWaypointSession();
        debugLog("feature.mode_changed", { mode: state.featureMode });
      };
    });

    byId("fitView").onclick = () => {
      if (!state.viewer) return;
      state.viewer.fitToScreen();
      debugLog("viewer.fit_to_screen");
    };

    byId("toggleEdl").onclick = () => {
      if (!state.viewer) return;
      const next = !state.viewer.getEDLEnabled();
      state.viewer.setEDLEnabled(next);
      byId("toggleEdl").classList.toggle("active", next);
      debugLog("viewer.edl_changed", { enabled: next });
    };

    byId("engineUseVisibleClasses").onchange = () => {
      debugLog("feature.engine_class_filter_changed", {
        enabled: byId("engineUseVisibleClasses").checked,
        selected_classes: selectedClassesForEngine()
      });
    };

    byId("profile").onchange = () => {
      state.traceArmed = false;
      byId("traceButton").classList.remove("active");
      if (state.candidateObject) removeCandidate();
      resetWaypointSession();
      debugLog("feature.profile_changed", {
        profile: byId("profile").value,
        label: byId("profile").selectedOptions[0].text
      });
    };

    byId("rgbMode").onclick = () => setMaterialMode("rgb");
    byId("elevationMode").onclick = () => setMaterialMode("elevation");
    byId("classificationMode").onclick = () =>
      setMaterialMode("classification");
  }

  window.addEventListener("DOMContentLoaded", async () => {
    bindUi();
    renderClassificationList();
    updateClassActionButtons("all");
    await initDesktopBridge();

    if (desktopBridge) {
      setStatus("Qt WebEngine ligado.");
    } else {
      setStatus("Bridge desktop indisponível.");
    }

    initViewer();

    try {
      const health = await api("/api/health");

      if (!health.potree_ready) {
        toast(
          "Potree ainda não foi instalado no Talude Studio. Execute bootstrap_vendor.ps1.",
          10000
        );
      }

      const projects = await api("/api/projects");
      const recent = (projects.projects || []).find((p) => p.exists);

      if (recent) {
        try {
          const project = await api(
            "/api/projects/" + encodeURIComponent(recent.id)
          );
          await activateProject(project);
          debugLog("application.ready", {
            app_version: health.version,
            potree_ready: health.potree_ready,
            wide_line_available: Boolean(
              THREE.Line2 && THREE.LineGeometry && THREE.LineMaterial
            )
          });
        } catch (_) {}
      }
    } catch (error) {
      toast(error.message, 8000);
    }
  });

  // Public bridge used by Talude Studio automatic crest/toe panel.
  window.TaludeShell = {
    state,
    api,
    bridgeCall,
    createLineObject,
    addFeatureOverlayObject,
    disposeLineObject,
    setStatus,
    toast,
    selectedClassesForEngine,
    setClassificationPreset,
    updateWideLineResolution
  };
})();