from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .environment import ensure_venv
from .logging_utils import append_log, new_log_file
from .models import ProjectConfig, Step
from .process_runner import ProcessRunner


class PipelineWorker(QThread):
    output = Signal(str)
    step_started = Signal(int, str)
    step_finished = Signal(int, bool, float)
    pipeline_finished = Signal(bool, str)

    def __init__(self, config: ProjectConfig, repo: Path, steps: list[Step], start_index: int = 0):
        super().__init__()
        self.config = config
        self.repo = repo
        self.steps = steps
        self.start_index = start_index
        self.runner = ProcessRunner()
        self._stop_requested = False
        self.log_path = new_log_file(repo)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def stop(self) -> None:
        self._stop_requested = True
        self.runner.stop()

    def _log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {text}"
        append_log(self.log_path, line)
        self.output.emit(line)

    def _expand(self, command: str, venv_python: Path) -> str:
        mapping = self.config.resolve(self.repo, venv_python=venv_python)
        mapping["%TIMESTAMP%"] = self.timestamp
        for token, value in mapping.items():
            command = command.replace(token, value.replace("'", "''"))
        return command

    def run(self) -> None:
        self._log("=" * 64)
        self._log(f"LOCAL BUILD MANAGER | {self.config.name}")
        self._log(f"Repo: {self.repo}")
        self._log(f"Log: {self.log_path}")
        self._log("=" * 64)

        all_ok = True
        active_profile: str | None = None
        active_python: Path | None = None
        env = os.environ.copy()

        for idx in range(self.start_index, len(self.steps)):
            if self._stop_requested:
                self._log("[CANCELADO] Pipeline interrompido pelo utilizador.")
                all_ok = False
                break
            step = self.steps[idx]

            profile = step.venv or "default"
            if active_profile != profile or active_python is None:
                ok, detail, python_exe = ensure_venv(self.config, self.repo, profile, on_progress=self._log)
                self._log(("[OK] " if ok else "[ERRO] ") + detail)
                if not ok:
                    all_ok = False
                    break
                active_profile = profile
                active_python = python_exe
                env = os.environ.copy()
                if profile.lower() not in {"none", "system"}:
                    venv_root = python_exe.parent.parent
                    env["PATH"] = str(python_exe.parent) + os.pathsep + env.get("PATH", "")
                    env["VIRTUAL_ENV"] = str(venv_root)

            step = self.steps[idx]
            self.step_started.emit(idx, step.name)
            self._log("")
            self._log(f">>> ETAPA {idx + 1}/{len(self.steps)}: {step.name}")
            command = self._expand(step.command, active_python)
            self._log(f"Comando: {command}")
            cwd = (self.repo / step.working_dir).resolve()
            if not cwd.exists():
                self._log(f"[ERRO] Diretório não existe: {cwd}")
                self.step_finished.emit(idx, False, 0.0)
                if step.required:
                    all_ok = False
                    break
                continue

            code, elapsed = self.runner.run(
                command,
                cwd=cwd,
                on_line=self._log,
                timeout_seconds=max(1, step.timeout_minutes) * 60,
                env=env,
            )
            success = code == 0
            self._log(f"{'[OK]' if success else '[ERRO]'} {step.name} | exit={code} | {elapsed:.1f}s")
            self.step_finished.emit(idx, success, elapsed)
            if not success and step.required:
                all_ok = False
                break

        self._log("")
        self._log("BUILD CONCLUÍDO COM SUCESSO" if all_ok else "PIPELINE TERMINADO COM ERROS")
        self.pipeline_finished.emit(all_ok, str(self.log_path))
