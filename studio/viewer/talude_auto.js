(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let autoObjects = [];
  let running = false;
  let lastOutputDir = null;
  let currentJobId = null;

  function shell() {
    return window.TaludeShell || null;
  }

  function activeCloudId() {
    const s = shell();
    if (!s || !s.state || !s.state.project) return null;

    for (const cloud of s.state.project.clouds || []) {
      if (s.state.pointclouds && s.state.pointclouds.has(cloud.id)) {
        return cloud.id;
      }
    }
    const first = (s.state.project.clouds || [])[0];
    return first ? first.id : null;
  }

  function numberValue(id, fallback) {
    const value = Number(byId(id).value);
    return Number.isFinite(value) ? value : fallback;
  }

  function classesForEngine() {
    const s = shell();
    if (!s) return [2];

    const control = byId("engineUseVisibleClasses");
    if (!control || !control.checked) return null;

    if (s.state.classFilterMode === "all") return [];
    return Array.from(s.state.selectedClasses || []).sort((a, b) => a - b);
  }

  function showJob(job) {
    const box = byId("jobBox");
    if (!box) return;

    box.classList.remove("hidden");
    const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
    byId("jobText").textContent = job.message || job.title || "A processar…";
    byId("jobProgress").style.width = progress + "%";
    const cancel = byId("cancelJob");
    if (cancel) {
      const canCancel = !["completed", "failed", "cancelled"].includes(job.status);
      cancel.classList.toggle("hidden", !canCancel);
      cancel.disabled = job.status === "cancelling";
    }
  }

  function hideJobSoon() {
    window.setTimeout(() => {
      const box = byId("jobBox");
      if (box) box.classList.add("hidden");
    }, 1200);
  }

  function setAutoVisibility(visible) {
    for (const item of autoObjects) {
      if (item && item.object) item.object.visible = Boolean(visible);
    }
  }

  function clearAutoLines() {
    const s = shell();
    if (s && s.state && s.state.featureOverlayScene) {
      for (const item of autoObjects) {
        try {
          s.state.featureOverlayScene.remove(item.object);
          s.disposeLineObject(item.object);
        } catch (_) {}
      }
    }

    autoObjects = [];
    lastOutputDir = null;

    byId("facesCount").textContent = "0";
    byId("crestCount").textContent = "0";
    byId("toeCount").textContent = "0";
    byId("taludeFeatureList").innerHTML = "";
    byId("clearTaludeLines").disabled = true;
    byId("openTaludeResults").disabled = true;
  }

  function renderResultList() {
    const box = byId("taludeFeatureList");
    box.innerHTML = "";

    autoObjects.forEach((item, index) => {
      const data = item.data;
      const row = document.createElement("div");
      row.className = "layer talude-line-row";

      const kind = data.type === "CREST" ? "crest" : "toe";
      const label = data.type === "CREST" ? "CRISTA" : "PÉ";
      const confidence = Math.round(Number(data.quality_score ?? data.confidence ?? 0) * 100);
      const length = Number(data.length_m || 0);
      const source = String(data.source || "");
      const sourceTag = source.startsWith("V2_")
        ? " · V2"
        : source.includes("FALLBACK")
          ? " · fallback"
          : "";

      row.innerHTML =
        '<i class="talude-dot ' + kind + '"></i>' +
        '<div><strong>' + label + " " + String(index + 1).padStart(3, "0") +
        "</strong>Face " + String(data.face_id ?? "-") +
        " · " + length.toFixed(1) + " m" + sourceTag + "</div>" +
        "<span>" + confidence + "%</span>";

      row.onclick = () => {
        item.object.visible = !item.object.visible;
        row.style.opacity = item.object.visible ? "1" : ".42";
      };

      box.appendChild(row);
    });
  }

  function applyResult(result) {
    const s = shell();
    if (!s) return;

    clearAutoLines();

    const report = result.report || {};
    const lines = Array.isArray(result.lines) ? result.lines : [];
    lastOutputDir = result.output_dir || null;

    for (const data of lines) {
      if (!Array.isArray(data.vertices) || data.vertices.length < 2) continue;

      const isCrest = data.type === "CREST";
      const object = s.createLineObject(data.vertices, {
        color: isCrest ? 0xffd54a : 0x38d5ff,
        widthPx: isCrest ? 3.4 : 3.0,
        dashed: false
      });

      s.addFeatureOverlayObject(object);
      autoObjects.push({ object, data });
    }

    s.updateWideLineResolution();
    renderResultList();

    byId("facesCount").textContent = String(report.faces_detected || 0);
    byId("crestCount").textContent = String(report.crest_lines || 0);
    byId("toeCount").textContent = String(report.toe_lines || 0);

    byId("clearTaludeLines").disabled = autoObjects.length === 0;
    byId("openTaludeResults").disabled = !lastOutputDir;

    const elapsed = Number(report.elapsed_s || 0);
    const isV2 = result.engine === "v2-global" ||
      report.engine === "BREAKLINE_ENGINE_V2_GLOBAL_HYBRID";
    if (isV2) {
      s.setStatus(
        "AUTO V2 concluído · " +
        String(report.faces_detected || 0) + " faces · " +
        "V2 " + String(report.v2_success_faces || 0) + " · " +
        "fallback " + String(report.baseline_fallback_faces || 0) + " · " +
        elapsed.toFixed(1) + " s"
      );
      s.toast(
        "AUTO GLOBAL V2 concluído. As faces inseguras mantiveram a geometria baseline.",
        8000
      );
    } else {
      s.setStatus(
        "AUTO concluído · " +
        String(report.faces_detected || 0) + " faces · " +
        String(report.crest_lines || 0) + " cristas · " +
        String(report.toe_lines || 0) + " pés · " +
        elapsed.toFixed(1) + " s"
      );
      s.toast("CRISTA + PÉ calculados e visíveis sobre a nuvem 3D.", 6500);
    }

    window.dispatchEvent(new CustomEvent("talude:vector-document-updated", {
      detail: {
        project_id: s.state.project ? s.state.project.id : null,
        result: result
      }
    }));
  }

  async function monitorJob(jobId) {
    const s = shell();
    if (!s) throw new Error("TaludeShell indisponível.");

    running = true;
    currentJobId = jobId;
    byId("detectTalude").disabled = true;

    try {
      for (;;) {
        const job = await s.api("/api/jobs/" + encodeURIComponent(jobId));
        showJob(job);

        if (job.status === "completed") {
          applyResult(job.result || {});
          hideJobSoon();
          return;
        }

        if (job.status === "failed") {
          throw new Error(job.error || job.message || "Extração automática falhou.");
        }

        if (job.status === "cancelled") {
          s.setStatus("Processamento cancelado.");
          s.toast("AUTO GLOBAL V2 cancelado.", 5000);
          hideJobSoon();
          return;
        }

        await sleep(600);
      }
    } finally {
      running = false;
      currentJobId = null;
      const cancel = byId("cancelJob");
      if (cancel) cancel.classList.add("hidden");
      refreshEnabledState();
    }
  }

  async function runAuto() {
    const s = shell();
    if (!s || running) return;

    const project = s.state.project;
    const cloudId = activeCloudId();

    if (!project || !cloudId) {
      s.toast("Abra um projeto e carregue uma nuvem primeiro.", 7000);
      return;
    }

    clearAutoLines();
    const useV2 = s.state.geometryEngine === "v2";
    s.setStatus(
      useV2
        ? "AUTO GLOBAL V2 · descoberta de faces → ROI RAW Ground → TIN…"
        : "AUTO TALUDE baseline · a iniciar FACE_DETECTOR…"
    );

    const payload = {
      project_id: project.id,
      cloud_id: cloudId,
      selected_classes: classesForEngine(),
      cell_size: numberValue("cellSize", 0),
      slope_low_deg: numberValue("slopeLow", 0),
      slope_high_deg: numberValue("slopeHigh", 0),
      min_face_area_m2: numberValue("minArea", 4),
      min_line_length_m: numberValue("minLength", 2),
      line_smooth_window: numberValue("lineSmooth", 11)
    };

    if (useV2) {
      Object.assign(payload, {
        tin_spacing_m: 0.25,
        max_tin_points: 45000,
        max_triangle_edge_m: 2.25,
        graph_gap_m: 1.50,
        station_spacing_m: 1.00,
        patch_along_m: 2.50,
        patch_cross_m: 1.80
      });
    }

    const endpoint = useV2 ? "/api/v2/talude/auto" : "/api/talude/auto";
    const response = await s.api(endpoint, {
      method: "POST",
      body: JSON.stringify(payload)
    });

    await monitorJob(response.job_id);
  }

  function refreshEnabledState() {
    const button = byId("detectTalude");
    if (!button) return;
    button.disabled = running || !activeCloudId();
  }

  function bind() {
    byId("detectTalude").onclick = () => {
      runAuto().catch((error) => {
        const s = shell();
        if (s) {
          s.setStatus("Erro na extração automática.");
          s.toast(error.message || String(error), 12000);
        }
      });
    };

    byId("clearTaludeLines").onclick = clearAutoLines;

    if (byId("cancelJob")) {
      byId("cancelJob").onclick = async () => {
        const s = shell();
        if (!s || !currentJobId) return;
        byId("cancelJob").disabled = true;
        try {
          await s.api(
            "/api/jobs/" + encodeURIComponent(currentJobId) + "/cancel",
            { method: "POST", body: "{}" }
          );
          s.setStatus("A cancelar processamento…");
        } catch (error) {
          s.toast(error.message || String(error), 8000);
          byId("cancelJob").disabled = false;
        }
      };
    }

    byId("openTaludeResults").onclick = () => {
      const s = shell();
      if (!s || !lastOutputDir) return;
      s.bridgeCall("open_folder", lastOutputDir).catch((error) => s.toast(error.message));
    };
  }

  function waitForShell() {
    if (!shell()) {
      window.setTimeout(waitForShell, 100);
      return;
    }

    bind();

    // Talude starts in Ground class because the AUTO objective is terrain.
    try {
      shell().setClassificationPreset("ground");
    } catch (_) {}

    refreshEnabledState();
    window.setInterval(refreshEnabledState, 500);
  }

  window.TaludeAuto = {
    clearAutoLines,
    setAutoVisibility,
    getAutoObjects: () => autoObjects.slice()
  };

  window.addEventListener("DOMContentLoaded", waitForShell);
})();
