(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function proxyButton(label, targetId, title) {
    const button = el("button", "precision-proxy-button", label);
    button.type = "button";
    button.title = title || label;
    button.addEventListener("click", () => {
      const target = byId(targetId);
      if (target && !target.disabled) target.click();
    });
    return button;
  }

  function commandGroup(label) {
    const group = el("div", "precision-command-group");
    const caption = el("span", "precision-command-label", label);
    group.appendChild(caption);
    return group;
  }

  function moveChildren(from, to) {
    if (!from || !to) return;
    while (from.firstChild) to.appendChild(from.firstChild);
  }

  function updateProjectMirror() {
    const mirror = byId("precisionProjectMirror");
    const source = byId("projectName");
    if (!mirror || !source) return;
    const text = String(source.textContent || "").trim();
    mirror.textContent = text && text !== "Nenhum projeto aberto"
      ? text
      : "Sem projeto";
  }

  function makeApplicationChrome() {
    const body = document.body;
    const app = byId("app");
    if (!body || !app || byId("precisionApplicationBar")) return;

    const appBar = el("div");
    appBar.id = "precisionApplicationBar";

    const left = el("div");
    left.style.display = "flex";
    left.style.alignItems = "center";
    const brand = document.querySelector(".brand");
    if (brand) left.appendChild(brand);
    const projectMirror = el("span", "precision-project-mirror", "Sem projeto");
    projectMirror.id = "precisionProjectMirror";
    left.appendChild(projectMirror);
    appBar.appendChild(left);

    const meta = el("div", "precision-app-meta");
    meta.innerHTML = "<span>Precision Workspace</span><span>ETRS89 / PT-TM06</span>";
    appBar.appendChild(meta);

    const menu = el("div");
    menu.id = "precisionMenuBar";
    [
      "File", "Edit", "View", "Point Cloud", "Layer",
      "Feature", "Trace", "Tools", "Export", "Help"
    ].forEach((name) => menu.appendChild(el("span", "", name)));

    const command = el("div");
    command.id = "precisionCommandBar";

    const nav = commandGroup("Navigation");
    nav.appendChild(proxyButton("⌖", "fitView", "Enquadrar"));
    const navProfile = byId("navProfileButton");
    const pan = byId("panModeButton");
    const projection = byId("orthoModeButton");
    if (navProfile) nav.appendChild(navProfile);
    if (pan) nav.appendChild(pan);
    if (projection) nav.appendChild(projection);
    command.appendChild(nav);

    const cloud = commandGroup("Point Cloud");
    const addCloudProxy = proxyButton("+ Nuvem", "addCloud", "Adicionar LAS / LAZ / COPC");
    addCloudProxy.classList.add("precision-proxy-primary");
    cloud.appendChild(addCloudProxy);
    ["toggleEdl", "rgbMode", "elevationMode", "classificationMode"].forEach((id) => {
      const node = byId(id);
      if (node) cloud.appendChild(node);
    });
    command.appendChild(cloud);

    const views = commandGroup("Views");
    const standards = document.querySelector(".standard-views");
    if (standards) {
      standards.querySelectorAll("button[data-standard-view]").forEach((button) => {
        views.appendChild(button);
      });
    }
    command.appendChild(views);

    const snap = commandGroup("Snap");
    const snapButton = el("button", "active", "⌕ Snap");
    snapButton.id = "precisionSnapToggle";
    snapButton.title = "Snapping Point Cloud + Vértices";
    snapButton.addEventListener("click", () => snapButton.classList.toggle("active"));
    snap.appendChild(snapButton);
    command.appendChild(snap);

    body.insertBefore(appBar, app);
    body.insertBefore(menu, app);
    body.insertBefore(command, app);

    const source = byId("projectName");
    if (source) {
      new MutationObserver(updateProjectMirror).observe(source, {
        childList: true,
        characterData: true,
        subtree: true,
      });
    }
    updateProjectMirror();
  }

  function makeInspector() {
    const app = byId("app");
    if (!app || byId("precisionInspector")) return;

    const inspector = el("aside", "precision-inspector");
    inspector.id = "precisionInspector";

    const header = el("div", "precision-inspector-header");
    header.appendChild(el("div", "precision-inspector-kicker", "FEATURE / OPERATION"));
    header.appendChild(el("div", "precision-inspector-title", "Geometry Inspector"));
    inspector.appendChild(header);

    const featureSection = el("section", "precision-inspector-section");
    featureSection.appendChild(el("h3", "", "SELECTED FEATURE"));
    const editBox = byId("vectorEditBox");
    if (editBox) {
      editBox.classList.remove("hidden");
      featureSection.appendChild(editBox);
    }
    const newLine = document.querySelector(".vector-new-line");
    if (newLine) featureSection.appendChild(newLine);
    const draft = byId("vectorDraftActions");
    if (draft) featureSection.appendChild(draft);
    inspector.appendChild(featureSection);

    const snapSection = el("section", "precision-inspector-section");
    snapSection.appendChild(el("h3", "", "SNAPPING"));
    const snapGrid = el("div", "precision-snap-grid");
    [
      ["Point Cloud", true],
      ["Vertices", true],
      ["Segments", false],
      ["Intersections", false],
    ].forEach(([name, active]) => {
      snapGrid.appendChild(el("span", "", name));
      snapGrid.appendChild(el("i", "precision-snap-dot" + (active ? " on" : "")));
    });
    snapSection.appendChild(snapGrid);
    const tolerance = el("div", "precision-field-row");
    tolerance.innerHTML = "<span>Tolerance</span><strong>0.05 m</strong>";
    snapSection.appendChild(tolerance);
    inspector.appendChild(snapSection);

    const autoCard = document.querySelector(".auto-talude-card");
    if (autoCard) inspector.appendChild(autoCard);
    const resultsCard = document.querySelector(".talude-results-card");
    if (resultsCard) inspector.appendChild(resultsCard);

    const advanced = el("section", "precision-inspector-section");
    advanced.appendChild(el("h3", "", "TRACE"));
    const text = el("div", "hint",
      "AUTO principal: descoberta validada + perfis transversais + snap local. " +
      "Sem extrapolação terminal automática.");
    advanced.appendChild(text);
    inspector.appendChild(advanced);

    app.appendChild(inspector);
  }

  function makeEditRail() {
    const viewport = document.querySelector(".viewport-shell");
    if (!viewport || byId("precisionEditRail")) return;

    const rail = el("div", "precision-edit-rail");
    rail.id = "precisionEditRail";

    const buttons = [
      ["↖", null, "Selecionar", false],
      ["✎", "vectorNewLine", "Adicionar 3D Polyline", false],
      ["◇", "vectorMoveVertex", "Mover vértice", false],
      ["＋", "vectorInsertVertex", "Adicionar vértice", false],
      ["−", "vectorDeleteVertex", "Apagar vértice", false],
      ["×", "vectorDeleteLine", "Apagar feature", false],
      ["|", null, "", true],
      ["⌁", null, "Reshape Feature — próxima fase", true],
      ["⑂", null, "Split Feature — próxima fase", true],
      ["⇄", null, "Merge / Join — próxima fase", true],
      ["→", null, "Extend Line — próxima fase", true],
      ["⌫", null, "Trim Line — próxima fase", true],
      ["|", null, "", true],
      ["↶", "vectorUndo", "Undo", false],
      ["↷", "vectorRedo", "Redo", false],
    ];

    buttons.forEach(([label, target, title, disabled]) => {
      if (label === "|") {
        rail.appendChild(el("div", "precision-rail-separator"));
        return;
      }
      const button = el("button", "", label);
      button.type = "button";
      button.title = title;
      button.disabled = Boolean(disabled && !target);
      if (target) {
        button.addEventListener("click", () => {
          rail.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
          button.classList.add("active");
          const source = byId(target);
          if (source && !source.disabled) source.click();
        });
      }
      rail.appendChild(button);
    });
    viewport.appendChild(rail);
  }

  function makeDigitizingPanel() {
    const viewport = document.querySelector(".viewport-shell");
    if (!viewport || byId("precisionDigitizing")) return;

    const box = el("div", "precision-digitizing");
    box.id = "precisionDigitizing";
    box.innerHTML =
      '<div class="precision-digitizing-header">DIGITIZING</div>' +
      '<div class="precision-digitizing-body">' +
        '<div class="precision-digitizing-row"><span>Feature</span><span id="precisionFeatureName" class="precision-digitizing-value">—</span></div>' +
        '<div class="precision-digitizing-row"><span>Vertex</span><span id="precisionVertexXYZ" class="precision-digitizing-value">—</span></div>' +
        '<div class="precision-digitizing-row"><span>Distance</span><span class="precision-digitizing-value">—</span></div>' +
        '<div class="precision-digitizing-row"><span>Angle</span><span class="precision-digitizing-value">—</span></div>' +
        '<div class="precision-digitizing-row"><span>ΔZ</span><span class="precision-digitizing-value">—</span></div>' +
        '<div class="precision-digitizing-locks"><span>Distance ◇</span><span>Angle ◇</span><span>Z ◇</span></div>' +
      '</div>';
    viewport.appendChild(box);

    const sync = () => {
      const feature = byId("vectorSelectedTitle");
      const xyz = byId("vectorVertexXYZ");
      if (byId("precisionFeatureName")) {
        byId("precisionFeatureName").textContent = feature
          ? String(feature.textContent || "—").replace(" · Face ", " / ")
          : "—";
      }
      if (byId("precisionVertexXYZ")) {
        byId("precisionVertexXYZ").textContent = xyz
          ? String(xyz.textContent || "—")
          : "—";
      }
    };
    [byId("vectorSelectedTitle"), byId("vectorVertexXYZ")].filter(Boolean).forEach((node) => {
      new MutationObserver(sync).observe(node, {
        childList: true,
        characterData: true,
        subtree: true,
      });
    });
    sync();
  }

  function makeStatusBar() {
    const body = document.body;
    const status = byId("status");
    if (!body || !status || byId("precisionStatusBar")) return;

    const bar = el("div");
    bar.id = "precisionStatusBar";

    const left = el("div", "", "EPSG:3763 — ETRS89 / Portugal TM06");
    bar.appendChild(left);
    bar.appendChild(status);

    const flags = el("div", "precision-status-flags");
    flags.innerHTML =
      '<span><b>SNAP</b><i class="precision-status-dot"></i></span>' +
      '<span><b>EDIT</b><i class="precision-status-dot"></i></span>' +
      '<span><b>3D</b><i class="precision-status-dot"></i></span>';
    bar.appendChild(flags);
    body.appendChild(bar);
  }

  function simplifyLeftDock() {
    const sidebar = document.querySelector(".sidebar");
    if (!sidebar) return;
    const editorDetails = document.querySelector(".vector-editor-details");
    if (editorDetails) editorDetails.removeAttribute("open");
    const advanced = document.querySelector(".advanced-workflow");
    if (advanced) advanced.removeAttribute("open");

    // The old topbar now only carries compact viewer telemetry.
    const navHelp = document.querySelector(".nav-help");
    if (navHelp) navHelp.classList.add("precision-hidden-by-layout");
  }

  function boot() {
    if (document.body.classList.contains("precision-workspace-ready")) return;
    document.body.classList.add("precision-workspace-ready");
    makeApplicationChrome();
    makeInspector();
    makeEditRail();
    makeDigitizingPanel();
    makeStatusBar();
    simplifyLeftDock();
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
