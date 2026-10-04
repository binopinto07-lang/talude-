from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.config import (
    config_revision,
    discover_repo_local_configs,
    find_repo_local_config,
    load_project_config,
    load_project_configs,
    load_settings,
    save_settings,
)
from app.core.environment import run_check
from app.core.git_service import pull, repo_summary
from app.core.models import ProjectConfig, Step
from app.core.pipeline import PipelineWorker


class MainWindow(QMainWindow):
    def __init__(self, app_root: Path):
        super().__init__()
        self.app_root = app_root
        self.projects = load_project_configs(app_root / "projects")
        self.settings_path = app_root / "settings.json"
        self.settings = load_settings(self.settings_path)
        self._apply_embedded_repo_hint()
        self.current_config: ProjectConfig | None = None
        self.worker: PipelineWorker | None = None
        self.current_steps: list[Step] = []
        self.last_failed_index: int | None = None
        self.last_pipeline_key: str | None = None
        self.setWindowTitle("Local Build Manager")
        self.resize(1380, 860)
        self._build_ui()
        self._populate_projects()
        self._apply_styles()

    def _apply_embedded_repo_hint(self) -> None:
        """Preselect a repository when the manager is launched from inside it."""
        raw_repo = os.environ.get("LBM_EMBEDDED_REPO", "").strip()
        if not raw_repo:
            return

        repo = Path(raw_repo).resolve()
        if not repo.is_dir():
            return

        discovered = discover_repo_local_configs(repo)
        if not discovered:
            return

        preferred_id = os.environ.get("LBM_EMBEDDED_PROJECT_ID", "").strip()
        selected = None
        if preferred_id:
            selected = next((cfg for _, cfg in discovered if cfg.id == preferred_id), None)
        if selected is None:
            selected = discovered[0][1]

        for idx, current in enumerate(self.projects):
            if current.id == selected.id:
                if config_revision(selected) >= config_revision(current):
                    self.projects[idx] = selected
                break
        else:
            self.projects.append(selected)

        self.projects.sort(key=lambda cfg: cfg.name.casefold())
        self.settings.setdefault("project_paths", {})[selected.id] = str(repo)
        self.settings["last_project"] = selected.id
        save_settings(self.settings_path, self.settings)

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QMainWindow { background: #f4f6f8; }
            QFrame#card { background: white; border: 1px solid #dfe3e8; border-radius: 10px; }
            QLabel#title { font-size: 24px; font-weight: 700; color: #172b4d; }
            QLabel#muted { color: #6b778c; }
            QPushButton { min-height: 32px; padding: 4px 12px; border-radius: 6px; border: 1px solid #c7ccd1; background: white; }
            QPushButton:hover { background: #eef2f6; }
            QPushButton#primary { background: #1769e0; color: white; border: 0; font-weight: 600; }
            QPushButton#danger { background: #c9372c; color: white; border: 0; }
            QPlainTextEdit { background: #111827; color: #e5e7eb; border-radius: 8px; padding: 8px; }
            QTableWidget { background: white; border: 0; gridline-color: #edf0f3; }
            QHeaderView::section { background: #f7f8fa; border: 0; border-bottom: 1px solid #dfe3e8; padding: 6px; font-weight: 600; }
            QProgressBar { border: 1px solid #dfe3e8; border-radius: 6px; text-align: center; background: white; }
        """)

    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        f = QFrame()
        f.setObjectName("card")
        l = QVBoxLayout(f)
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(10)
        return f, l

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("LOCAL BUILD MANAGER")
        title.setObjectName("title")
        subtitle = QLabel("CI / Build local para Windows — sem depender de GitHub Actions")
        subtitle.setObjectName("muted")
        title_box = QVBoxLayout()
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        title_row.addLayout(title_box)
        title_row.addStretch()
        outer.addLayout(title_row)

        top_card, top = self._card()
        grid = QGridLayout()
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._project_changed)
        self.path_label = QLabel("Nenhuma pasta selecionada")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        browse = QPushButton("Selecionar pasta…")
        browse.clicked.connect(self._choose_repo)
        self.git_label = QLabel("Branch: —   Commit: —   Estado: —")
        self.git_label.setObjectName("muted")
        refresh = QPushButton("Atualizar estado")
        refresh.clicked.connect(self.refresh_git)
        pull_btn = QPushButton("Git Pull")
        pull_btn.clicked.connect(self.git_pull)
        grid.addWidget(QLabel("Projeto"), 0, 0)
        grid.addWidget(self.project_combo, 0, 1, 1, 3)
        grid.addWidget(QLabel("Pasta local"), 1, 0)
        grid.addWidget(self.path_label, 1, 1)
        grid.addWidget(browse, 1, 2)
        grid.addWidget(refresh, 1, 3)
        grid.addWidget(QLabel("Git"), 2, 0)
        grid.addWidget(self.git_label, 2, 1, 1, 2)
        grid.addWidget(pull_btn, 2, 3)
        top.addLayout(grid)
        outer.addWidget(top_card)

        actions = QHBoxLayout()
        self.check_btn = QPushButton("Verificar ambiente")
        self.check_btn.clicked.connect(self.check_environment)
        self.test_btn = QPushButton("TESTAR")
        self.test_btn.clicked.connect(lambda: self.start_pipeline("test"))
        self.build_btn = QPushButton("BUILD")
        self.build_btn.clicked.connect(lambda: self.start_pipeline("build"))
        self.full_btn = QPushButton("BUILD + TESTES")
        self.full_btn.setObjectName("primary")
        self.full_btn.clicked.connect(lambda: self.start_pipeline("full"))
        self.retry_btn = QPushButton("Repetir etapa falhada")
        self.retry_btn.setEnabled(False)
        self.retry_btn.clicked.connect(self.retry_failed)
        self.continue_btn = QPushButton("Continuar daqui")
        self.continue_btn.setEnabled(False)
        self.continue_btn.clicked.connect(self.continue_from_failed)
        self.publish_btn = QPushButton("PUBLICAR RELEASE")
        self.publish_btn.clicked.connect(lambda: self.start_pipeline("release"))
        self.stop_btn = QPushButton("PARAR")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_pipeline)
        for b in [self.check_btn, self.test_btn, self.build_btn, self.full_btn, self.retry_btn, self.continue_btn, self.publish_btn, self.stop_btn]:
            actions.addWidget(b)
        outer.addLayout(actions)

        splitter = QSplitter(Qt.Horizontal)
        left_card, left = self._card()
        left.addWidget(QLabel("Ambiente"))
        self.env_table = QTableWidget(0, 3)
        self.env_table.setHorizontalHeaderLabels(["Estado", "Componente", "Detalhe"])
        self.env_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.env_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.env_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.env_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.env_table.setSelectionMode(QAbstractItemView.NoSelection)
        left.addWidget(self.env_table)
        left.addWidget(QLabel("Pipeline"))
        self.pipeline_table = QTableWidget(0, 4)
        self.pipeline_table.setHorizontalHeaderLabels(["#", "Estado", "Etapa", "Tempo"])
        self.pipeline_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.pipeline_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.pipeline_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.pipeline_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.pipeline_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.pipeline_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        left.addWidget(self.pipeline_table)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        left.addWidget(self.progress)

        right_card, right = self._card()
        log_row = QHBoxLayout()
        log_row.addWidget(QLabel("Log em tempo real"))
        log_row.addStretch()
        open_builds = QPushButton("Abrir builds")
        open_builds.clicked.connect(self.open_builds)
        clear_log = QPushButton("Limpar")
        clear_log.clicked.connect(lambda: self.log.clear())
        log_row.addWidget(open_builds)
        log_row.addWidget(clear_log)
        right.addLayout(log_row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        right.addWidget(self.log)

        splitter.addWidget(left_card)
        splitter.addWidget(right_card)
        splitter.setSizes([600, 780])
        outer.addWidget(splitter, 1)

        self.setCentralWidget(root)

        menu = self.menuBar().addMenu("Projeto")
        import_action = QAction("Importar configuração JSON…", self)
        import_action.triggered.connect(self.import_config)
        menu.addAction(import_action)

    def _populate_projects(self) -> None:
        self.project_combo.clear()
        for cfg in self.projects:
            self.project_combo.addItem(cfg.name, cfg.id)
        last = self.settings.get("last_project")
        if last:
            for i in range(self.project_combo.count()):
                if self.project_combo.itemData(i) == last:
                    self.project_combo.setCurrentIndex(i)
                    break
        if self.projects:
            self._project_changed(self.project_combo.currentIndex())

    def _project_index_by_id(self, project_id: str) -> int | None:
        for i, cfg in enumerate(self.projects):
            if cfg.id == project_id:
                return i
        return None

    def _upsert_project(self, cfg: ProjectConfig) -> int:
        index = self._project_index_by_id(cfg.id)
        if index is None:
            self.projects.append(cfg)
            self.project_combo.addItem(cfg.name, cfg.id)
            return len(self.projects) - 1
        self.projects[index] = cfg
        self.project_combo.setItemText(index, cfg.name)
        self.project_combo.setItemData(index, cfg.id)
        return index

    def _discover_profiles_from_folder(self, repo: Path, *, announce: bool = True) -> list[ProjectConfig]:
        discovered: list[ProjectConfig] = []
        for path, cfg in discover_repo_local_configs(repo):
            self._upsert_project(cfg)
            discovered.append(cfg)
            if announce:
                self.log.appendPlainText(
                    f"✅ Perfil descoberto na pasta: {cfg.name} | {path}"
                )
        return discovered

    def _project_changed(self, index: int) -> None:
        if index < 0 or index >= len(self.projects):
            return
        self.current_config = self.projects[index]
        self.settings["last_project"] = self.current_config.id

        project_paths = self.settings.setdefault("project_paths", {})
        path = project_paths.get(self.current_config.id, "")
        # Talude V1 and V2 share the same source repository. Reuse the existing
        # folder when V2 is introduced by a manager upgrade, instead of forcing
        # the user to browse to it again.
        if not path and self.current_config.id == "talude_v2":
            legacy = project_paths.get("talude_v1", "")
            if legacy and Path(legacy).exists():
                path = legacy
                project_paths[self.current_config.id] = legacy

        save_settings(self.settings_path, self.settings)
        self.path_label.setText(path or "Nenhuma pasta selecionada")
        if path and Path(path).exists():
            self._refresh_config_from_repo(announce=False)
        self.refresh_git()
        self._load_pipeline_preview("full")

    def repo_path(self) -> Path | None:
        if not self.current_config:
            return None
        path = self.settings.get("project_paths", {}).get(self.current_config.id)
        if not path:
            return None
        p = Path(path)
        return p if p.exists() else None

    def _refresh_config_from_repo(self, *, announce: bool = True) -> bool:
        """Prefer the newest project profile committed inside the source folder.

        Works with both Git clones and GitHub ZIP extractions. V0.1.7 also
        understands the temporary historical state where Talude V2 still used
        the id ``talude_v1`` inside its experimental branch.
        """
        repo = self.repo_path()
        if not repo or not self.current_config:
            return False

        self._discover_profiles_from_folder(repo, announce=False)
        local_path = find_repo_local_config(repo, self.current_config.id)
        alias_mode = False

        if not local_path and self.current_config.id == "talude_v2":
            legacy = repo / "localbuild" / "talude_v1.json"
            if legacy.is_file():
                try:
                    legacy_cfg = load_project_config(legacy)
                    if (
                        legacy_cfg.branch == "v2-experimental-raw-tin-mst"
                        or "V2" in legacy_cfg.name
                    ):
                        local_path = legacy
                        alias_mode = True
                except Exception:
                    pass

        if not local_path:
            return False

        try:
            fresh = load_project_config(local_path)
        except Exception as exc:
            if announce:
                self.log.appendPlainText(f"⚠ Config local inválida: {local_path} | {exc}")
            return False

        if alias_mode:
            fresh.id = self.current_config.id
            fresh.raw["id"] = self.current_config.id
            fresh.name = self.current_config.name
            fresh.raw["name"] = self.current_config.name

        if fresh.id != self.current_config.id:
            if announce:
                self.log.appendPlainText(
                    f"⚠ Config local ignorada: id={fresh.id!r}, esperado={self.current_config.id!r}"
                )
            return False

        bundled_revision = config_revision(self.current_config)
        local_revision = config_revision(fresh)
        if local_revision < bundled_revision:
            if announce:
                self.log.appendPlainText(
                    f"⚠ Perfil local desatualizado: revisão {local_revision} < {bundled_revision}. "
                    "Mantido o perfil mais recente incluído no Local Build Manager."
                )
            return False

        self.current_config = fresh
        index = self._project_index_by_id(fresh.id)
        if index is not None:
            self.projects[index] = fresh
            self.project_combo.setItemText(index, fresh.name)
        if announce:
            suffix = " (compatibilidade V2)" if alias_mode else ""
            self.log.appendPlainText(
                f"✅ Config de build carregada da pasta: {local_path}{suffix}"
            )
        self._load_pipeline_preview("full")
        return True

    def _validate_required_paths(self, repo: Path) -> bool:
        if not self.current_config:
            return False

        required = list(self.current_config.raw.get("required_paths", []) or [])
        missing = [item for item in required if not (repo / item).exists()]
        if missing:
            lines = "\n".join(f"  - {item}" for item in missing)
            self.log.appendPlainText(
                "❌ Pasta de código desatualizada/incompleta. Faltam:\n" + lines
            )
            QMessageBox.warning(
                self,
                "Código V2 desatualizado",
                "Esta pasta não contém todos os ficheiros exigidos pelo perfil atual.\n\n"
                + lines
                + "\n\nDescarregue/atualize a branch V2 antes de executar o build.",
            )
            return False

        guard = self.current_config.raw.get("required_source_revision") or {}
        if isinstance(guard, dict) and guard.get("path") and guard.get("value"):
            marker_path = repo / str(guard["path"])
            expected = str(guard["value"]).strip()
            try:
                actual = marker_path.read_text(encoding="utf-8").strip()
            except Exception:
                actual = ""

            if actual != expected:
                self.log.appendPlainText(
                    "❌ SOURCE GUARD: pasta desatualizada. "
                    f"Esperado={expected!r} | encontrado={actual or 'AUSENTE'!r}"
                )
                QMessageBox.warning(
                    self,
                    "Código V2 desatualizado",
                    "O Local Build Manager detetou que esta pasta é uma cópia antiga "
                    "da branch V2.\n\n"
                    f"Revisão exigida: {expected}\n"
                    f"Revisão encontrada: {actual or 'AUSENTE'}\n\n"
                    "Não será iniciado TEST/BUILD com código antigo. "
                    "Descarregue novamente a branch V2 ou use um clone Git atualizado.",
                )
                return False

            self.log.appendPlainText(f"✅ SOURCE GUARD: {expected}")

        return True

    def _choose_repo(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Selecionar pasta do projeto")
        if not folder or not self.current_config:
            return

        repo = Path(folder)
        discovered = self._discover_profiles_from_folder(repo, announce=True)

        def _norm_repo_url(value: str) -> str:
            value = (value or "").strip().replace("\\", "/")
            if value.endswith(".git"):
                value = value[:-4]
            if value.startswith("git@github.com:"):
                value = "https://github.com/" + value[len("git@github.com:"):]
            return value.rstrip("/").lower()

        remote = ""
        git_branch = ""
        try:
            proc = subprocess.run(
                ["git", "-C", folder, "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            remote = proc.stdout.strip() if proc.returncode == 0 else ""
            branch_proc = subprocess.run(
                ["git", "-C", folder, "branch", "--show-current"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            git_branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else ""
        except Exception:
            pass

        selected_id = self.current_config.id

        # If the user explicitly selected a profile whose repository URL matches,
        # keep that selection. This fixes Talude V1/V2 sharing the same GitHub URL.
        if remote:
            wanted = _norm_repo_url(remote)
            current_matches = _norm_repo_url(self.current_config.repo_url) == wanted
            if not current_matches:
                matches = [
                    (i, cfg)
                    for i, cfg in enumerate(self.projects)
                    if _norm_repo_url(cfg.repo_url) == wanted
                ]
                chosen = None
                if git_branch:
                    chosen = next(
                        ((i, cfg) for i, cfg in matches if cfg.branch == git_branch),
                        None,
                    )
                if chosen is None and len(matches) == 1:
                    chosen = matches[0]
                if chosen is not None:
                    selected_id = chosen[1].id
        elif discovered:
            discovered_ids = {cfg.id for cfg in discovered}
            if self.current_config.id not in discovered_ids:
                # GitHub ZIP extraction: prefer an explicit V2 profile when the
                # folder itself contains one. Otherwise use a single discovered
                # profile, but never guess among unrelated projects.
                v2 = next((cfg for cfg in discovered if cfg.id == "talude_v2"), None)
                if v2 is not None:
                    selected_id = v2.id
                elif len(discovered) == 1:
                    selected_id = discovered[0].id

        selected_index = self._project_index_by_id(selected_id)
        if selected_index is not None and selected_index != self.project_combo.currentIndex():
            self.project_combo.setCurrentIndex(selected_index)

        if not self.current_config:
            return
        self.settings.setdefault("project_paths", {})[self.current_config.id] = folder
        save_settings(self.settings_path, self.settings)
        self.path_label.setText(folder)
        self._refresh_config_from_repo(announce=True)
        self.refresh_git()

    def refresh_git(self) -> None:
        repo = self.repo_path()
        if not repo:
            self.git_label.setText("Branch: —   Commit: —   Estado: selecione a pasta local")
            return
        s = repo_summary(repo)
        self.git_label.setText(f"Branch: {s['branch']}   Commit: {s['commit']}   Estado: {s['status']}")

    def git_pull(self) -> None:
        repo = self.repo_path()
        if not repo:
            self._need_repo()
            return
        code, out = pull(repo)
        self.log.appendPlainText(out)
        if code != 0:
            QMessageBox.warning(self, "Git Pull", "O git pull falhou. Consulte o log.")
        else:
            self._refresh_config_from_repo(announce=True)
        self.refresh_git()

    def check_environment(self) -> None:
        repo = self.repo_path()
        if not repo or not self.current_config:
            self._need_repo()
            return
        self._refresh_config_from_repo(announce=False)
        if not self._validate_required_paths(repo):
            return
        self.env_table.setRowCount(0)
        for check in self.current_config.checks:
            row = self.env_table.rowCount()
            self.env_table.insertRow(row)
            ok, detail = run_check(check, repo)
            status = "✅ OK" if ok else ("❌ FALTA" if check.required else "⚠ Opcional")
            self.env_table.setItem(row, 0, QTableWidgetItem(status))
            self.env_table.setItem(row, 1, QTableWidgetItem(check.name))
            concise = (detail.splitlines()[0] if detail else check.hint)[:240]
            self.env_table.setItem(row, 2, QTableWidgetItem(concise or check.hint))
            self.log.appendPlainText(f"{status} {check.name}: {detail or check.hint}")

    def _load_pipeline_preview(self, key: str) -> None:
        if not self.current_config:
            return
        steps = self.current_config.pipelines.get(key, [])
        self.pipeline_table.setRowCount(len(steps))
        for i, step in enumerate(steps):
            self.pipeline_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.pipeline_table.setItem(i, 1, QTableWidgetItem("○"))
            self.pipeline_table.setItem(i, 2, QTableWidgetItem(step.name))
            self.pipeline_table.setItem(i, 3, QTableWidgetItem("—"))
        self.progress.setMaximum(max(1, len(steps)))
        self.progress.setValue(0)

    def start_pipeline(self, key: str, start_index: int = 0) -> None:
        repo = self.repo_path()
        if not repo or not self.current_config:
            self._need_repo()
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Pipeline", "Já existe um pipeline em execução.")
            return
        self._refresh_config_from_repo(announce=True)
        if not self._validate_required_paths(repo):
            return
        steps = self.current_config.pipelines.get(key, [])
        if not steps:
            QMessageBox.warning(self, "Pipeline", f"O projeto não tem pipeline '{key}'.")
            return
        self.last_pipeline_key = key
        self.current_steps = steps
        self.last_failed_index = None
        self.retry_btn.setEnabled(False)
        self.continue_btn.setEnabled(False)
        self._load_pipeline_preview(key)
        if start_index:
            for i in range(start_index):
                self.pipeline_table.item(i, 1).setText("↷")
        self.worker = PipelineWorker(self.current_config, repo, steps, start_index=start_index)
        self.worker.output.connect(self.log.appendPlainText)
        self.worker.step_started.connect(self._step_started)
        self.worker.step_finished.connect(self._step_finished)
        self.worker.pipeline_finished.connect(self._pipeline_finished)
        self._set_running(True)
        self.worker.start()

    def _step_started(self, idx: int, name: str) -> None:
        self.pipeline_table.item(idx, 1).setText("⏳")
        self.pipeline_table.selectRow(idx)

    def _step_finished(self, idx: int, ok: bool, elapsed: float) -> None:
        self.pipeline_table.item(idx, 1).setText("✅" if ok else "❌")
        self.pipeline_table.item(idx, 3).setText(f"{elapsed:.1f}s")
        self.progress.setValue(idx + 1)
        if not ok:
            self.last_failed_index = idx

    def _pipeline_finished(self, ok: bool, log_path: str) -> None:
        self._set_running(False)
        if ok:
            self.log.appendPlainText(f"\n✅ Pipeline concluído. Log: {log_path}")
        else:
            self.log.appendPlainText(f"\n❌ Pipeline terminou com erros. Log: {log_path}")
            if self.last_failed_index is not None:
                self.retry_btn.setEnabled(True)
                self.continue_btn.setEnabled(True)
        self.refresh_git()

    def retry_failed(self) -> None:
        if self.last_failed_index is None or not self.last_pipeline_key:
            return
        self.start_pipeline(self.last_pipeline_key, self.last_failed_index)

    def continue_from_failed(self) -> None:
        if self.last_failed_index is None or not self.last_pipeline_key:
            return
        next_idx = min(self.last_failed_index + 1, len(self.current_steps) - 1)
        self.start_pipeline(self.last_pipeline_key, next_idx)

    def stop_pipeline(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()

    def _set_running(self, running: bool) -> None:
        for b in [self.check_btn, self.test_btn, self.build_btn, self.full_btn, self.publish_btn]:
            b.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def open_builds(self) -> None:
        repo = self.repo_path()
        if not repo:
            self._need_repo()
            return
        target = repo / "builds"
        target.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(target)])

    def import_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importar configuração", filter="JSON (*.json)")
        if not path:
            return
        target = self.app_root / "projects" / Path(path).name
        target.write_bytes(Path(path).read_bytes())
        self.projects = load_project_configs(self.app_root / "projects")
        self._populate_projects()

    def _need_repo(self) -> None:
        QMessageBox.information(self, "Pasta do projeto", "Selecione primeiro a pasta local do repositório.")
